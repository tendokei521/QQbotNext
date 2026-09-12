"""bilibili_parser 业务链路单测（离线：假 API + 假 Bot）。

覆盖：合并转发（简介节点 + 视频节点）、只下载第一个视频、降级文案、
旧版引用回复模式、无链接/开关关闭时不回复。
"""

from types import SimpleNamespace

from app.domain.message import MessageSegment
from module.modules.bilibili_parser import bilibili_api as bapi
from module.modules.bilibili_parser import service

INFO = {
    "bvid": "BV1zq836rEbk",
    "cid": 41052212697,
    "title": "测试视频标题",
    "pic": "https://i0.hdslb.com/x.jpg",
    "desc": "简介内容",
    "owner": {"name": "UP主"},
    "stat": {"view": 100},
}

PLAY = {
    "quality": 64,
    "format": "mp4720",
    "timelength": 125120,
    "durl": [{"url": "http://cdn/x", "size": 1024}],
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
        "enable_link_video": True,
        "enable_json_video": True,
        "enable_bv_dedup": False,
        "show_cover": False,
        "use_forward_msg": True,
        "enable_video_download": True,
        "video_quality": "720",
        "video_max_mb": 80,
        "video_download_timeout": 30,
        "video_cache_enabled": False,
        "timeout": 10,
        "cookie": "",
        "max_parse_count": 3,
        "is_reply": True,
    }
    data.update(config)
    return SimpleNamespace(
        bot_id=10001,
        name="B站视频解析",
        module_name="bilibili_parser",
        config=FakeConfig(data),
        ctx=SimpleNamespace(services=SimpleNamespace(task_manager=None), bot=bot),
    )


class FakeAPI:
    """替身 API：按 vid 返回预设信息，playurl 返回预设结果，下载写固定字节。"""

    def __init__(self, infos: dict, play=None, download_error: Exception | None = None,
                 pagelist: list | None = None) -> None:
        self._infos = infos
        self._play = play
        self._download_error = download_error
        self._pagelist = pagelist or []
        self.downloads: list[str] = []
        self.playurl_calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def extract_b23(self, bv_list):
        return bv_list

    async def get_video_info(self, vid, timeout=10, cookie=""):
        return self._infos.get(vid)

    async def get_pagelist(self, bvid, timeout=10):
        return self._pagelist

    async def get_playurl_single(self, bvid, cid, qn=64, cookie="", timeout=10):
        self.playurl_calls.append((bvid, cid))
        return self._play

    async def download_durl(self, segments, dest, timeout=60):
        if self._download_error:
            raise self._download_error
        with open(dest, "wb") as fh:
            fh.write(b"fake-mp4")
        self.downloads.append(dest)
        return dest


def _patch_api(monkeypatch, fake: FakeAPI) -> None:
    monkeypatch.setattr(bapi, "BilibiliAPI", lambda *a, **kw: fake)


def _patch_data_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "get_data_path", lambda name, create=True: str(tmp_path))


async def test_handle_sends_forward_with_intro_and_video(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"BV1zq836rEbk": INFO}, play=PLAY)
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    module = _make_module({})
    event = FakeEvent("看看这个 https://www.bilibili.com/video/BV1zq836rEbk", bot)

    await service.handle(module, event)

    assert len(bot.forward_calls) == 1
    call = bot.forward_calls[0]
    assert call["group_id"] == 30001
    nodes = call["msgdata"]
    assert len(nodes) == 2
    assert nodes[0]["data"]["content"][-1]["type"] == "text"  # 简介
    assert nodes[1]["data"]["content"][0]["type"] == "video"
    assert fake.downloads and fake.downloads[0].endswith("BV1zq836rEbk_p1.mp4")
    assert event.llm.stopped is True  # 已接管，跳过 LLM


async def test_handle_falls_back_when_playurl_fails(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"BV1zq836rEbk": INFO}, play=None)  # 风控/取流失败
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.bilibili.com/video/BV1zq836rEbk", bot)

    await service.handle(_make_module({}), event)

    nodes = bot.forward_calls[0]["msgdata"]
    assert len(nodes) == 2
    assert nodes[1]["data"]["content"][0]["type"] == "text"
    assert "获取失败" in nodes[1]["data"]["content"][0]["data"]["text"]


async def test_handle_skips_download_when_oversize(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    big = {"quality": 64, "format": "mp4720", "durl": [{"url": "http://cdn/x", "size": 200 * 1024 * 1024}]}
    fake = FakeAPI({"BV1zq836rEbk": INFO}, play=big)
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("BV1zq836rEbk", bot)

    await service.handle(_make_module({}), event)

    assert fake.downloads == []  # 未下载
    text = bot.forward_calls[0]["msgdata"][1]["data"]["content"][0]["data"]["text"]
    assert "超过上限" in text


async def test_only_first_video_is_downloaded(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    info2 = dict(INFO, bvid="BV2aaaaaaaaa", cid=2)
    fake = FakeAPI({"BV1zq836rEbk": INFO, "BV2aaaaaaaaa": info2}, play=PLAY)
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("BV1zq836rEbk BV2aaaaaaaaa", bot)

    await service.handle(_make_module({}), event)

    assert len(bot.forward_calls) == 2
    assert fake.playurl_calls == [("BV1zq836rEbk", INFO["cid"])]  # 只对第一个视频取流
    assert len(bot.forward_calls[0]["msgdata"]) == 2  # 简介 + 视频
    assert len(forward_calls_items := bot.forward_calls[1]["msgdata"]) == 1  # 仅简介


async def test_page_param_selects_cid_and_cache_name(monkeypatch, tmp_path):
    """`?p=2` 应选第 2 P 的 cid，且缓存文件名带 p2。"""
    _patch_data_path(monkeypatch, tmp_path)
    info = dict(INFO, pages=[{"cid": 111, "page": 1}, {"cid": 222, "page": 2}])
    fake = FakeAPI({"BV1zq836rEbk": info}, play=PLAY)
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("https://www.bilibili.com/video/BV1zq836rEbk?p=2", bot)

    await service.handle(_make_module({}), event)

    assert fake.playurl_calls == [("BV1zq836rEbk", 222)]
    assert fake.downloads[0].endswith("BV1zq836rEbk_p2.mp4")


async def test_pagelist_fallback_when_view_has_no_cid(monkeypatch, tmp_path):
    """view 未带 cid 时用 pagelist 交叉校正（与参考实现一致）。"""
    _patch_data_path(monkeypatch, tmp_path)
    info = dict(INFO, cid=None, pages=[])
    fake = FakeAPI({"BV1zq836rEbk": info}, play=PLAY, pagelist=[{"cid": 333, "page": 1}])
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("BV1zq836rEbk", bot)

    await service.handle(_make_module({}), event)

    assert fake.playurl_calls == [("BV1zq836rEbk", 333)]
    assert len(bot.forward_calls[0]["msgdata"]) == 2


def test_extract_page_map_and_missing_page():
    texts = [
        "看这个 https://www.bilibili.com/video/BV1zq836rEbk?p=3 和 https://www.bilibili.com/video/BV2aaaaaaaaa/",
        "裸号 BV3bbbbbbbbb",
    ]
    assert bapi.extract_page_map(texts) == {"BV1ZQ836REBK": 3}
    assert bapi.extract_page_map([]) == {}


async def test_handle_legacy_mode_uses_reply_message(monkeypatch):
    fake = FakeAPI({"BV1zq836rEbk": INFO})
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("BV1zq836rEbk", bot)

    await service.handle(_make_module({"use_forward_msg": False}), event)

    assert bot.forward_calls == []
    assert len(bot.msg_calls) == 1
    chain = bot.msg_calls[0]["message"]
    assert chain[0]["type"] == "reply"
    assert chain[0]["data"]["id"] == 999
    assert bot.msg_calls[0]["group_id"] == 30001


async def test_handle_ignores_when_disabled_or_no_link(monkeypatch):
    fake = FakeAPI({"BV1zq836rEbk": INFO}, play=PLAY)
    _patch_api(monkeypatch, fake)

    bot = FakeBot()
    event = FakeEvent("BV1zq836rEbk", bot)
    await service.handle(_make_module({"enable_auto_parse": False}), event)
    assert bot.forward_calls == [] and event.llm.stopped is False

    bot2 = FakeBot()
    event2 = FakeEvent("今天天气不错", bot2)
    await service.handle(_make_module({}), event2)
    assert bot2.forward_calls == [] and event2.llm.stopped is False


async def test_handle_private_chat_uses_private_forward(monkeypatch, tmp_path):
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"BV1zq836rEbk": INFO}, play=PLAY)
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    event = FakeEvent("BV1zq836rEbk", bot, message_type="private")

    await service.handle(_make_module({}), event)

    call = bot.forward_calls[0]
    assert call["user_id"] == 20001
    assert call["group_id"] == 0


async def test_video_cache_hit_skips_playurl(monkeypatch, tmp_path):
    """缓存命中：不再取流，直接复用本地文件。"""
    _patch_data_path(monkeypatch, tmp_path)
    fake = FakeAPI({"BV1zq836rEbk": INFO}, play=PLAY)
    _patch_api(monkeypatch, fake)
    bot = FakeBot()
    module = _make_module({"video_cache_enabled": True})
    event = FakeEvent("BV1zq836rEbk", bot)

    await service.handle(module, event)  # 首次：下载
    await service.handle(module, event)  # 二次：命中缓存
    assert fake.playurl_calls == [("BV1zq836rEbk", INFO["cid"])]
