"""douyin_parser 业务链路单测（离线：假 API + 假 Bot）。

覆盖：合并转发的两个节点、只下载第一个作品、图集与 Live 图、体积超限降级、
缓存命中、旧版引用回复、开关关闭/无链接时不回复、作品去重。
"""

from types import SimpleNamespace

import pytest

from app.domain.message import MessageSegment
from app.plugins.douyin_parser import douyin_api as api_mod
from app.plugins.douyin_parser import service


@pytest.fixture(autouse=True)
def _reset_dedup_cache():
    """作品去重是类级缓存，逐用例清空避免互相干扰。"""
    api_mod.DouyinAPI._aweme_cache = {}
    yield
    api_mod.DouyinAPI._aweme_cache = {}

DETAIL_VIDEO = {
    "aweme_detail": {
        "aweme_id": "7534291258635816251",
        "desc": "测试文案",
        "aweme_type": 0,
        "author": {"nickname": "作者名"},
        "video": {
            "duration": 12500,
            "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpeg"]},
            "bit_rate": [
                {"bit_rate": 800_000, "quality_type": {"name": "540p"},
                 "play_addr": {"url_list": ["https://cdn.example/540.mp4"]}},
                {"bit_rate": 2_500_000, "quality_type": {"name": "1080p"},
                 "play_addr": {"url_list": ["https://cdn.example/1080.mp4"]}},
            ],
        },
    }
}

DETAIL_ALBUM = {
    "aweme_detail": {
        "aweme_id": "7000000000000000001",
        "desc": "图集文案",
        "aweme_type": 68,
        "author": {"nickname": "图集作者"},
        "images": [
            {"url_list": ["https://p3.douyinpic.com/a.jpeg"]},
            {"url_list": ["https://p3.douyinpic.com/b.webp"]},
        ],
    }
}


class FakeConfig:
    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, key: str, default=None):
        return self.data.get(key, default)


class FakeLlm:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeBot:
    def __init__(self) -> None:
        self.forward_calls: list[dict] = []
        self.msg_calls: list[dict] = []

    async def send_forward_msg(self, group_id: int = 0, user_id: int = 0, msgdata=None) -> dict:
        self.forward_calls.append({"group_id": group_id, "user_id": user_id, "msgdata": msgdata})
        return {"status": "ok"}

    async def send_msg(self, message_type: str, message, user_id=None, group_id=None,
                       auto_escape: bool = False) -> dict:
        self.msg_calls.append({"message_type": message_type, "message": message, "group_id": group_id})
        return {"status": "ok"}


class FakeEvent:
    def __init__(self, text: str, bot: FakeBot, message_type: str = "group") -> None:
        self.message = [MessageSegment.text(text)] if text else []
        self.message_id = 999
        self.message_type = message_type
        self.user_id = 20001
        self.self_id = 10001
        self.group = SimpleNamespace(group_id=30001 if message_type == "group" else 0)
        self.bot = bot
        self.llm = FakeLlm()
        self._at_me = False

    def is_at_me(self) -> bool:
        return self._at_me


def _make_module(config: dict, bot: FakeBot | None = None) -> SimpleNamespace:
    data = {
        "enable_auto_parse": True,
        "enable_link_parse": True,
        "enable_json_parse": True,
        "enable_aweme_dedup": False,
        "show_cover": False,
        "use_forward_msg": True,
        "enable_video_download": True,
        "video_quality": "highest",
        "video_max_mb": 80,
        "video_download_timeout": 30,
        "enable_album_download": True,
        "album_max_images": 9,
        "video_cache_enabled": False,
        "video_cache_ttl_minutes": 720,
        "video_cache_max_mb": 1024,
        "timeout": 10,
        "cookie": "",
        "user_agent": "",
        "max_parse_count": 3,
        "is_reply": True,
    }
    data.update(config)
    return SimpleNamespace(
        bot_id=10001,
        name="抖音视频解析",
        module_name="douyin_parser",
        config=FakeConfig(data),
        ctx=SimpleNamespace(services=SimpleNamespace(task_manager=None), bot=bot),
    )


class FakeAPI:
    """替身 API：按 aweme_id 返回预设详情，下载写固定字节（可注入失败 / 超限）。"""

    def __init__(self, details: dict, download_error: Exception | None = None,
                 resolve_map: dict | None = None, oversize: bool = False) -> None:
        self._details = details
        self._download_error = download_error
        self._resolve_map = resolve_map or {}
        self._oversize = oversize
        self.downloads: list[str] = []
        self.detail_calls: list[str] = []
        self.max_bytes_seen: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def resolve_aweme_id(self, link, timeout=10):
        if link in self._resolve_map:
            return self._resolve_map[link]
        from app.plugins.douyin_parser import media

        return media.aweme_id_from_url(link)

    async def get_aweme_detail(self, aweme_id, timeout=10, cookie=""):
        self.detail_calls.append(aweme_id)
        return self._details.get(str(aweme_id))

    async def download(self, url, dest, timeout=60, headers=None, max_bytes=0):
        if self._download_error:
            raise self._download_error
        self.max_bytes_seen.append(max_bytes)
        if self._oversize:
            raise api_mod.DownloadTooLarge("体积超过上限")
        with open(dest, "wb") as fh:
            fh.write(b"fake-media")
        self.downloads.append(dest)
        return dest


def _patch_api(monkeypatch, fake: FakeAPI) -> None:
    monkeypatch.setattr(service, "DouyinAPI", lambda *a, **kw: fake)


def _patch_data_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "get_data_path", lambda name, create=True: str(tmp_path))


# ==================== 视频 ====================


async def test_handle_sends_forward_with_intro_and_video(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("看看这个 https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({}), event)

    assert len(bot.forward_calls) == 1
    call = bot.forward_calls[0]
    assert call["group_id"] == 30001
    nodes = call["msgdata"]
    assert len(nodes) == 2
    assert nodes[0]["data"]["content"][-1]["type"] == "text"  # 文案节点
    assert nodes[1]["data"]["content"][0]["type"] == "video"
    assert fake.downloads and fake.downloads[0].endswith("7534291258635816251.mp4")
    assert event.llm.stopped is True  # 已接管，跳过 LLM


async def test_handle_downloads_highest_quality_by_default(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({}), event)

    assert len(fake.downloads) == 1  # 只下载选中的那一档


async def test_handle_falls_back_when_no_quality(monkeypatch, tmp_path):
    """作品没有可用视频地址（受限/被删）→ 节点2 降级为文本。"""
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": {"aweme_detail": {"aweme_id": "7534291258635816251",
                                                             "aweme_type": 0, "video": {}}}})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({}), event)

    nodes = bot.forward_calls[0]["msgdata"]
    assert len(nodes) == 2
    assert nodes[1]["data"]["content"][0]["type"] == "text"
    assert "媒体获取失败" in nodes[1]["data"]["content"][0]["data"]["text"]


async def test_handle_reports_oversize_without_attaching(monkeypatch, tmp_path):
    """体积超限：不带视频，节点2 降级成文本说明。"""
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO}, oversize=True)
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({"video_max_mb": 5}), event)

    assert fake.downloads == []
    nodes = bot.forward_calls[0]["msgdata"]
    assert len(nodes) == 2
    text = nodes[1]["data"]["content"][0]["data"]["text"]
    assert "体积超过上限" in text


async def test_video_size_cap_is_passed_to_download(monkeypatch, tmp_path):
    """video_max_mb 必须换算成字节上限传下去（边下边卡）。"""
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({"video_max_mb": 5}), event)

    assert fake.max_bytes_seen == [5 * 1024 * 1024]


async def test_video_size_cap_zero_means_unlimited(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({"video_max_mb": 0}), event)

    assert fake.max_bytes_seen == [0]  # 0 = 不限制


async def test_handle_download_failure_degrades(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO}, download_error=RuntimeError("boom"))
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({}), event)

    text = bot.forward_calls[0]["msgdata"][1]["data"]["content"][0]["data"]["text"]
    assert "视频下载失败" in text


async def test_only_first_aweme_is_downloaded(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    second = {"aweme_detail": {"aweme_id": "7000000000000000009", "aweme_type": 0,
                               "video": DETAIL_VIDEO["aweme_detail"]["video"]}}
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO, "7000000000000000009": second})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent(
        "https://www.douyin.com/video/7534291258635816251 https://www.douyin.com/video/7000000000000000009", bot
    )

    await service.handle(_make_module({}), event)

    assert len(bot.forward_calls) == 2
    assert len(fake.downloads) == 1  # 只下第一个
    assert len(bot.forward_calls[0]["msgdata"]) == 2  # 文案 + 视频
    assert len(bot.forward_calls[1]["msgdata"]) == 1  # 仅文案


# ==================== 图集 ====================


async def test_handle_album_downloads_images_into_one_node(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7000000000000000001": DETAIL_ALBUM})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/note/7000000000000000001", bot)

    await service.handle(_make_module({}), event)

    nodes = bot.forward_calls[0]["msgdata"]
    assert len(nodes) == 2
    images = nodes[1]["data"]["content"]
    assert [seg["type"] for seg in images] == ["image", "image"]
    assert images[0]["data"]["file"].endswith("7000000000000000001_001.jpg")
    assert images[1]["data"]["file"].endswith("7000000000000000001_002.webp")


async def test_handle_album_respects_max_images(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7000000000000000001": DETAIL_ALBUM})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/note/7000000000000000001", bot)

    await service.handle(_make_module({"album_max_images": 1}), event)

    images = bot.forward_calls[0]["msgdata"][1]["data"]["content"]
    assert len(images) == 1


async def test_handle_album_disabled_keeps_intro_only(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7000000000000000001": DETAIL_ALBUM})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/note/7000000000000000001", bot)

    await service.handle(_make_module({"enable_album_download": False}), event)

    assert len(bot.forward_calls[0]["msgdata"]) == 1
    assert fake.downloads == []


# ==================== 短链 / 去重 / 开关 ====================


async def test_handle_resolves_short_link(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI(
        {"7534291258635816251": DETAIL_VIDEO},
        resolve_map={"https://v.douyin.com/iRNBho6u/": "7534291258635816251"},
    )
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("7.53 复制打开抖音 https://v.douyin.com/iRNBho6u/", bot)

    await service.handle(_make_module({}), event)

    assert fake.detail_calls == ["7534291258635816251"]
    assert len(bot.forward_calls) == 1


async def test_handle_dedupes_repeated_aweme(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    module = _make_module({"enable_aweme_dedup": True})
    text = "https://www.douyin.com/video/7534291258635816251"

    await service.handle(module, FakeEvent(text, bot))
    await service.handle(module, FakeEvent(text, bot))

    assert fake.detail_calls == ["7534291258635816251"]  # 第二次被去重
    assert len(bot.forward_calls) == 1


async def test_handle_ignores_when_disabled_or_no_link(monkeypatch):
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)

    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)
    await service.handle(_make_module({"enable_auto_parse": False}), event)
    assert bot.forward_calls == [] and event.llm.stopped is False

    bot2 = FakeBot()
    event2 = FakeEvent("今天天气不错", bot2)
    await service.handle(_make_module({}), event2)
    assert bot2.forward_calls == [] and event2.llm.stopped is False


async def test_handle_legacy_mode_uses_reply_message(monkeypatch):
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(_make_module({"use_forward_msg": False}), event)

    assert bot.forward_calls == []
    assert len(bot.msg_calls) == 1
    chain = bot.msg_calls[0]["message"]
    assert chain[0]["type"] == "reply"
    assert chain[0]["data"]["id"] == 999
    assert bot.msg_calls[0]["group_id"] == 30001


async def test_handle_private_chat_uses_private_forward(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot, message_type="private")

    await service.handle(_make_module({}), event)

    call = bot.forward_calls[0]
    assert call["user_id"] == 20001
    assert call["group_id"] == 0


async def test_video_cache_hit_skips_download(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    module = _make_module({"video_cache_enabled": True})
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)

    await service.handle(module, event)  # 首次：下载
    await service.handle(module, event)  # 二次：命中缓存
    assert len(fake.downloads) == 1


async def test_handle_at_me_keeps_llm_running(monkeypatch, tmp_path):
    """群聊里 @ 了 bot（想问内容）时不接管 LLM。"""
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"7534291258635816251": DETAIL_VIDEO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.douyin.com/video/7534291258635816251", bot)
    event._at_me = True

    await service.handle(_make_module({}), event)

    assert event.llm.stopped is False


# ==================== 模块声明一致性 ====================


def test_config_defaults_match_schema():
    """代码默认与 WebUI schema 默认必须一致，否则「界面显示开、实际关」。"""
    from app.plugins.douyin_parser.config_schema import SCHEMA
    from app.plugins.douyin_parser.module import Module

    for key, default in Module.default_config.items():
        assert key in SCHEMA, f"{key} 缺少 WebUI schema"
        assert SCHEMA[key]["default"] == default, (
            f"{key} 默认值不一致：schema={SCHEMA[key]['default']} code={default}"
        )
    # schema 里不该有代码不认识的键（否则前端能存、后端读不到）
    assert set(SCHEMA) == set(Module.default_config)


def test_module_manifest_matches_module_dir():
    """module.json 的 name 必须与目录名一致，否则 WebUI 安装/卸载会对不上。"""
    import json
    from pathlib import Path

    module_dir = Path(__file__).resolve().parents[2] / "app" / "plugins" / "douyin_parser"
    manifest = json.loads((module_dir / "module.json").read_text(encoding="utf-8"))
    assert manifest["name"] == module_dir.name
    assert manifest["entry"] == "module.py"
    assert (module_dir / manifest["entry"]).is_file()


def test_module_declares_group_and_private_hooks():
    from app.plugins.douyin_parser.module import Module

    hooks, _llm_hooks = Module.collect_hooks()
    assert sorted(h["event_type"] for h in hooks) == ["message_group", "message_private"]


# ==================== 端到端：真注册表加载 ====================


async def test_plugin_loads_in_real_registry(container):
    """走真实链路：注册表指向仓库真实模块目录 → 能发现并加载本插件。"""
    from pathlib import Path

    from app.infrastructure.config.config_service import ConfigService
    from app.plugin.base import ServiceAccess
    from app.plugin.registry import ModuleRegistry

    repo_root = Path(__file__).resolve().parents[2]
    registry = ModuleRegistry(
        plugins_dir=repo_root / "app" / "plugins",  # 业务插件代码
        zip_plugins_dir=repo_root / "module" / "plugins",  # 外部 zip 安装
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
    )
    bot_id = 20260101

    assert await registry.load_single("douyin_parser", bot_id) is True, "插件应能从仓库真实目录加载"
    module = registry.get("douyin_parser", bot_id)
    assert module is not None and module.__class__.__name__ == "Module"
    assert module.name == "抖音视频解析" and module.sign == "DouyinParser"
    # 默认配置必须真的从 schema 灌进来（不是空 dict）
    assert module.config.get("enable_auto_parse") is True
    assert module.config.get("video_quality") == "highest"

    await registry.unload_single("douyin_parser", bot_id)
    assert registry.get("douyin_parser", bot_id) is None
