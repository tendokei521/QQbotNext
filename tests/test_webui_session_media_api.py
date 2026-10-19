"""会话图片（本地仓）与"删对话"语义的 WebUI API 测试。

两条线：

**图片**——会话里存的是本地指向（``data.media.sha``），前端看到的就是模型下一轮
会用的那份字节：``/api/media/<sha>.<ext>`` 读盘返回、``PUT/DELETE .../media/<seg>``
换图/移除图、``/api/sessions/<sid>/export`` 打 zip 备份（含图片）、
``/api/sessions/restore-bundle`` 连图片一起恢复。删除本地图前必须先把引用删掉。

**删对话**——线上反馈：会话里只剩一个对话时点删除会弹「该会话不存在」——
后端 400（活跃会话唯一对话不许删）+ 前端先刷新对话列表再刷新会话列表，两条一起
造成"删不掉还报错"。现在唯一对话的删除语义是"清空并继续会话"，会话列表里
那条记录随之归零（空列表，而不是 404）。
"""

from __future__ import annotations

import io
import zipfile

import pytest
from starlette.testclient import TestClient

from app.llm import image_media
from app.llm.session import SessionManager
from app.llm.storage import create_history_store
from app.webui.app import create_app

BOT = 778
SESSION = "group_466"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
PNG2 = b"\x89PNG\r\n\x1a\n" + b"1" * 64


@pytest.fixture
async def client(container):
    return TestClient(create_app(container))


@pytest.fixture(autouse=True)
def _fresh_session_manager(monkeypatch):
    """``SessionManager`` 是**进程内单例**（按 bot_id），它的 history store 绑在创建时的
    数据目录上。测试进程里每个用例的数据目录都不同，不清掉单例就会读到上一个用例的库。
    生产环境数据目录不变，不受影响。"""
    from app.llm.session import SessionManager

    monkeypatch.setattr(SessionManager, "_instances", {}, raising=False)


def _archive(task_id: str, media: dict | None, *, session_id: str = SESSION, text: str = "[图片]") -> None:
    segments = [{"type": "image", "data": {"media": dict(media)}}] if media else []
    store = create_history_store(BOT)
    try:
        store.import_conversation({
            "task_id": task_id,
            "session_id": session_id,
            "type": "group",
            "bot_id": BOT,
            "title": "测试对话",
            "messages": [{
                "role": "user",
                "content": text,
                "message_id": "1003",
                "base": {"time": 1792334953, "sender_id": "20002", "segments": segments},
            }],
        })
    finally:
        store.close()


def _params(**extra):
    return {"bot_id": BOT, **extra}


# ---------- GET /api/media/<sha>.<ext> ----------


def test_get_media_serves_the_same_bytes_and_is_cacheable(client):
    saved = image_media.save_bytes(BOT, PNG)

    resp = client.get(f"/api/media/{saved['sha']}.png", params=_params())

    assert resp.status_code == 200
    assert resp.content == PNG
    assert resp.headers["content-type"].startswith("image/png")
    # 内容寻址：同一 sha 永不改变 → 长缓存 + immutable
    assert "immutable" in resp.headers["cache-control"]


def test_get_media_without_extension_still_works(client):
    saved = image_media.save_bytes(BOT, PNG)

    resp = client.get(f"/api/media/{saved['sha']}", params=_params())

    assert resp.status_code == 200
    assert resp.content == PNG


def test_get_media_rejects_bad_names_and_missing_files(client):
    assert client.get("/api/media/not-a-sha.png", params=_params()).status_code == 400
    assert client.get("/api/media/../../etc/passwd", params=_params()).status_code in (400, 404)
    assert client.get(f"/api/media/{'0' * 16}.png", params=_params()).status_code == 404


def test_get_media_is_per_account(client):
    saved = image_media.save_bytes(BOT, PNG)

    other = client.get(f"/api/media/{saved['sha']}.png", params={"bot_id": 999})

    assert other.status_code == 404  # 别的账号看不到这个账号的图


# ---------- 清单 / 清理 / 删除 ----------


def test_media_list_reports_usage(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)

    body = client.get("/api/media", params=_params()).json()

    assert body["ok"] is True
    assert body["count"] >= 1
    assert body["referenced"] >= 1


def test_media_purge_keeps_referenced_files(client):
    kept = image_media.save_bytes(BOT, PNG)
    orphan = image_media.save_bytes(BOT, PNG2)
    _archive("abc12345", kept)

    body = client.post("/api/media/purge", params=_params()).json()

    assert body["removed"] == 1
    assert image_media.path_of(BOT, kept["sha"])
    assert image_media.path_of(BOT, orphan["sha"]) is None


def test_delete_media_is_refused_while_referenced(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)

    resp = client.delete(f"/api/media/{saved['sha']}", params=_params())

    assert resp.status_code == 409
    body = resp.json()
    assert body["references"][0]["task_id"] == "abc12345"
    assert image_media.path_of(BOT, saved["sha"])  # 文件还在


# ---------- 会话里的图片清单 ----------


def test_conversation_exposes_image_url_and_status(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)

    body = client.get(
        f"/api/sessions/{SESSION}/conversations/abc12345", params=_params()
    ).json()

    message = body["conversation"]["messages"][0]
    image = message["images"][0]
    assert image["sha"] == saved["sha"]
    assert image["status"] == "cached"
    assert image["url"] == f"/api/media/{saved['sha']}.png"
    assert image["seg"] == 0


def test_conversation_marks_missing_file(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)
    import os

    os.remove(image_media.path_of(BOT, saved["sha"]))

    body = client.get(
        f"/api/sessions/{SESSION}/conversations/abc12345", params=_params()
    ).json()

    image = body["conversation"]["messages"][0]["images"][0]
    assert image["status"] == "missing"
    assert image["url"] == ""


def test_conversation_marks_uncached_segment(client):
    _archive("abc12345", None)  # 还没落盘的图（只有占位段的情况由前端处理）

    # 没有图片段时 images 为空列表（不是 null），前端不必判空
    body = client.get(
        f"/api/sessions/{SESSION}/conversations/abc12345", params=_params()
    ).json()
    assert body["conversation"]["messages"][0]["images"] == []


# ---------- 换图 / 移除图 ----------


def test_replace_message_media_points_to_new_bytes(client):
    old = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", old)

    resp = client.put(
        f"/api/sessions/{SESSION}/conversations/abc12345/messages/0/media/0",
        params=_params(),
        content=PNG2,
    )

    assert resp.status_code == 200, resp.text
    image = resp.json()["conversation"]["messages"][0]["images"][0]
    assert image["sha"] == image_media.sha_of(PNG2)
    assert image_media.path_of(BOT, image["sha"])
    # 旧图变成"无人引用"，可清理但不会立刻被删（可能有别的消息还在用）
    assert image_media.path_of(BOT, old["sha"])


def test_replace_message_media_rejects_empty_and_bad_index(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)
    url = f"/api/sessions/{SESSION}/conversations/abc12345/messages/0/media/0"

    assert client.put(url, params=_params(), content=b"").status_code == 400
    assert client.put(
        f"/api/sessions/{SESSION}/conversations/abc12345/messages/9/media/0",
        params=_params(), content=PNG2,
    ).status_code == 400
    assert client.put(
        f"/api/sessions/{SESSION}/conversations/abc12345/messages/0/media/7",
        params=_params(), content=PNG2,
    ).status_code == 400


def test_remove_message_media_clears_reference_and_placeholder(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved, text="")

    resp = client.delete(
        f"/api/sessions/{SESSION}/conversations/abc12345/messages/0/media/0",
        params=_params(),
    )

    assert resp.status_code == 200, resp.text
    conversation = resp.json()["conversation"]
    message = conversation["messages"][0]
    assert message["images"] == []
    assert message["content"] == "[图片已移除]"  # 不能留一条空壳消息


def test_remove_then_delete_media_succeeds(client):
    """顺序：先删引用 → 再删本地图（这就是"删除必须先删引用"的完整闭环）。"""
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)

    client.delete(
        f"/api/sessions/{SESSION}/conversations/abc12345/messages/0/media/0",
        params=_params(),
    )
    resp = client.delete(f"/api/media/{saved['sha']}", params=_params())

    assert resp.status_code == 200, resp.text
    assert image_media.path_of(BOT, saved["sha"]) is None


# ---------- 导出 / 恢复（含图片） ----------


def test_export_session_bundles_media_files(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)

    resp = client.get(f"/api/sessions/{SESSION}/export", params=_params())

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as bundle:
        names = bundle.namelist()
        assert "session.json" in names
        media_names = [n for n in names if n.startswith("media/")]
        assert media_names == [f"media/{saved['sha']}.png"]
        assert bundle.read(media_names[0]) == PNG


def test_export_session_json_without_media(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)

    resp = client.get(
        f"/api/sessions/{SESSION}/export", params=_params(with_media="false")
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["conversations"][0]["task_id"] == "abc12345"


def test_restore_bundle_brings_back_images(client):
    saved = image_media.save_bytes(BOT, PNG)
    _archive("abc12345", saved)
    bundle = client.get(f"/api/sessions/{SESSION}/export", params=_params()).content

    # 模拟"换台机器"：删掉会话与本地图片，再从备份包恢复
    store = create_history_store(BOT)
    try:
        store.delete_history("abc12345")
    finally:
        store.close()
    import os

    os.remove(image_media.path_of(BOT, saved["sha"]))

    resp = client.post("/api/sessions/restore-bundle", params=_params(), content=bundle)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["restored"] == 1
    assert body["media"] == 1
    assert image_media.path_of(BOT, saved["sha"])  # 图片跟着回来了，指向自然可用


def test_restore_bundle_rejects_garbage(client):
    assert client.post(
        "/api/sessions/restore-bundle", params=_params(), content=b"not a zip"
    ).status_code == 400
    assert client.post("/api/sessions/restore-bundle", params=_params(), content=b"").status_code == 400


# ---------- 删对话：唯一对话不再报错 ----------


def _active_session_with_one_conversation(task_id: str, media: dict | None) -> str:
    """造一个"内存里活跃 + 归档只有一行"的会话（线上报错的那个形态）。"""
    mgr = SessionManager(str(BOT))
    session = mgr.create_session(SESSION, "group", 600)
    conv = session.conversations[session.active_id]  # 建会话时自带的那个（唯一一个）
    conv.task_id = task_id
    conv.title = "唯一对话"
    conv.data.history = [{
        "role": "user",
        "content": "[图片]",
        "message_id": "1003",
        "base": {"time": 1, "segments": [{"type": "image", "data": {"media": dict(media)}}] if media else []},
    }]
    mgr.history.save_session(session)
    return conv.id


def test_deleting_the_only_conversation_clears_instead_of_erroring(client):
    _active_session_with_one_conversation("abc12345", None)

    resp = client.delete(
        f"/api/sessions/{SESSION}/conversations/abc12345", params=_params()
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "cleared"
    assert "清空" in body["message"]


def test_session_with_no_conversations_is_not_a_404(client):
    """删掉唯一对话后会话列表归零：空列表 ≠ 会话不存在（前端不该弹"会话不存在"）。"""
    _active_session_with_one_conversation("abc12345", None)
    client.delete(f"/api/sessions/{SESSION}/conversations/abc12345", params=_params())

    resp = client.get(f"/api/sessions/{SESSION}", params=_params())

    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["conversations"] == []
    assert body["session"]["empty"] is True


def test_deleting_unknown_conversation_still_404(client):
    resp = client.delete(
        f"/api/sessions/{SESSION}/conversations/deadbeef", params=_params()
    )

    assert resp.status_code == 404
    assert "对话不存在" in resp.json()["message"]
