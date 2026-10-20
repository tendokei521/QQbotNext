"""本地图片仓（``app/llm/image_media.py``）测试。

这是"图片不再存直链"改造的地基：会话里存的是 ``{"sha","mime","bytes"}`` 指向，
字节落在 ``data/llm/<bot_id>/media/<sha16>.<ext>``。本文件钉住：

1. **内容寻址**：同内容重复落盘只留一份（幂等），不同账号各自一份；
2. **MIME 按字节嗅探**：声明错的 MIME 会让网关 400，所以一律以魔数为准；
3. **路径安全**：sha 必须是 16 位十六进制，取文件不能跳出媒体目录；
4. **引用扫描 / 删除**：仍被会话引用的图**拒绝删除**并列出引用位置，
   没人引用的才能删（这是"先删引用再删本地图"的实现）；
5. **内联预算**：超过单张上限先降采样，降不下来就这一张不发（绝不发必被拒的巨串）；
6. 落盘失败/文件丢失都只降级成占位，不抛异常。
"""

from __future__ import annotations

import base64
import io
import os
import time

import pytest

from app.agent.media import store as image_media

BOT = 778
OTHER_BOT = 779
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64
GIF_BYTES = b"GIF89a" + b"\x00" * 32


def _png(size: int = 300, *, noisy: bool = True) -> bytes:
    """造一张真 PNG（noisy=True 时压缩不动，用来验证降采样确实变小）。"""
    PILImage = pytest.importorskip("PIL.Image")
    import random

    rng = random.Random(7)
    image = PILImage.new("RGB", (size, size))
    if noisy:
        image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))] * (size * size))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------- 落盘 / 读取 ----------


def test_save_bytes_is_content_addressed_and_idempotent():
    first = image_media.save_bytes(BOT, PNG_BYTES)
    second = image_media.save_bytes(BOT, PNG_BYTES)

    assert first == second
    assert first["sha"] == image_media.sha_of(PNG_BYTES)
    assert image_media.SHA_RE.match(first["sha"])
    assert first["bytes"] == len(PNG_BYTES)
    assert first["mime"] == "image/png"

    path = image_media.path_of(BOT, first["sha"])
    assert path and os.path.isfile(path)
    assert os.listdir(image_media.media_dir(BOT)) == [os.path.basename(path)]  # 只留一份


def test_mime_is_sniffed_not_trusted():
    """``mime=image/jpeg`` 说错了也不算数：字节是 PNG 就按 PNG 存。"""
    saved = image_media.save_bytes(BOT, PNG_BYTES, mime="image/jpeg")

    assert saved["mime"] == "image/png"
    assert saved["sha"] and image_media.path_of(BOT, saved["sha"]).endswith(".png")


def test_each_account_gets_its_own_file():
    a = image_media.save_bytes(BOT, PNG_BYTES)
    b = image_media.save_bytes(OTHER_BOT, PNG_BYTES)

    assert a["sha"] == b["sha"]  # 同一份字节
    assert image_media.path_of(BOT, a["sha"]) != image_media.path_of(OTHER_BOT, b["sha"])
    assert image_media.media_dir(BOT) != image_media.media_dir(OTHER_BOT)


def test_save_bytes_rejects_empty_and_reports_failure(monkeypatch):
    assert image_media.save_bytes(BOT, b"") is None

    def _boom(*a, **kw):
        raise OSError("磁盘满了")

    monkeypatch.setattr("builtins.open", _boom)
    assert image_media.save_bytes(BOT, PNG_BYTES + b"x") is None  # 只降级，不抛


def test_read_bytes_round_trip_and_limits():
    saved = image_media.save_bytes(BOT, PNG_BYTES)

    assert image_media.read_bytes(BOT, saved["sha"]) == PNG_BYTES
    assert image_media.read_bytes(BOT, saved["sha"], max_bytes=1) is None
    assert image_media.read_bytes(BOT, "0" * 16) is None


def test_path_of_rejects_bad_shas_and_traversal():
    assert image_media.path_of(BOT, "") is None
    assert image_media.path_of(BOT, "not-a-sha") is None
    assert image_media.path_of(BOT, "../../etc/passwd") is None
    assert image_media.path_of(BOT, "0" * 15) is None


def test_sniff_and_name_helpers():
    assert image_media.sniff_mime(PNG_BYTES) == "image/png"
    assert image_media.sniff_mime(GIF_BYTES) == "image/gif"
    assert image_media.sniff_mime(b"not an image") == ""  # 认不出就空串，不瞎猜
    assert image_media.mime_from_name("a.PNG") == "image/png"
    assert image_media.mime_from_name("a.unknown") == ""


def test_segment_helpers_strip_remote_addresses():
    data = {"url": "https://cdn.example/a.png", "file": "a.image", "base64": "base64://AA", "summary": "x"}
    image_media.strip_remote_addresses(data)

    # url / base64 是会过期或能换到直链的东西 → 全抹掉；`file` 只是 QQ 内部文件名，留着有用
    assert set(data) == {"file", "summary"}

    # 但 `file` 里塞的是地址时也要抹掉（有些实现把直链放在 file 里）
    data2 = {"file": "https://cdn.example/a.png"}
    image_media.strip_remote_addresses(data2)
    assert data2 == {}
    seg = {"type": "image", "data": {"media": {"sha": "a" * 16, "mime": "image/png"}}}
    assert image_media.seg_type(seg) == "image"
    assert image_media.segment_media(seg)["sha"] == "a" * 16
    assert image_media.segment_media({"type": "text", "data": {}}) is None


def test_entry_segments_reads_both_shapes():
    assert image_media.entry_segments({"base": {"segments": [{"type": "text"}]}})[0]["type"] == "text"
    assert image_media.entry_segments({"segments": [{"type": "image"}]})[0]["type"] == "image"
    assert image_media.entry_segments({"content": "hi"}) == []


# ---------- 内联（唯一允许进请求体的形态） ----------


def test_data_url_inlines_local_bytes():
    saved = image_media.save_bytes(BOT, PNG_BYTES)

    url = image_media.data_url(BOT, saved)

    assert url == f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode()}"
    assert image_media.data_url(BOT, {"sha": "0" * 16}) is None
    assert image_media.data_url(BOT, None) is None


def test_inline_bytes_downscales_oversized_image():
    raw = _png(600)
    saved = image_media.save_bytes(BOT, raw)
    limit_mb = 0.002  # 2KB：原图必然超

    inline = image_media.inline_bytes(BOT, saved, max_mb=limit_mb, max_edge=64)

    assert inline is not None
    data, mime = inline
    assert len(data) < len(raw)
    assert mime in ("image/jpeg", "image/png")


def test_inline_bytes_gives_up_when_undecodable(monkeypatch):
    """既超限又缩不了 → 这张不发（而不是发一个必然被网关拒的巨串）。"""
    saved = image_media.save_bytes(BOT, b"\x89PNG\r\n\x1a\n" + b"z" * 4096)

    assert image_media.inline_bytes(BOT, saved, max_mb=0.001) is None


def test_downscale_keeps_transparency_as_png():
    PILImage = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    PILImage.new("RGBA", (400, 400), (255, 0, 0, 128)).save(buffer, format="PNG")

    shrunk = image_media.downscale(buffer.getvalue(), max_edge=64)

    assert shrunk is not None
    data, mime = shrunk
    assert mime == "image/png"
    assert len(data) > 0


def test_downscale_returns_none_for_garbage():
    assert image_media.downscale(b"not an image") is None


# ---------- 引用扫描 ----------


def _archive(payload: dict) -> None:
    """把一条对话写进归档（走存储层公开接口，SQLite / JSON 后端都支持）。"""
    from app.agent.storage import create_history_store

    store = create_history_store(BOT)
    try:
        store.import_conversation(payload)
    finally:
        store.close()


def _conversation_with_ref(task_id: str, media: dict, *, extra_message: bool = False) -> dict:
    messages = [
        {
            "role": "user",
            "content": "[图片]",
            "message_id": "1003",
            "base": {"time": 1, "segments": [{"type": "image", "data": {"media": dict(media)}}]},
        }
    ]
    if extra_message:
        messages.append({"role": "assistant", "content": "好图", "base": {"segments": []}})
    return {
        "task_id": task_id,
        "session_id": "group_1",
        "type": "group",
        "bot_id": BOT,
        "title": "测试",
        "messages": messages,
    }


def test_scan_references_locates_the_message():
    saved = image_media.save_bytes(BOT, PNG_BYTES)
    _archive(_conversation_with_ref("abc12345", saved))

    refs = image_media.scan_references(BOT)

    assert saved["sha"] in refs
    assert refs[saved["sha"]][0]["task_id"] == "abc12345"
    assert refs[saved["sha"]][0]["seg"] == 0
    assert image_media.references_of(OTHER_BOT, saved["sha"]) == []


def test_message_media_refs_lets_expand_image_reuse_local_bytes():
    saved = image_media.save_bytes(BOT, PNG_BYTES)
    entries = [
        {"message_id": "1003", "base": {"segments": [{"type": "image", "data": {"media": dict(saved)}}]}},
        {"message_id": "1004", "base": {"segments": []}},
    ]

    assert image_media.message_media_refs(entries, "1003") == [saved]
    assert image_media.message_media_refs(entries, 1003) == [saved]  # 数字 id 也认
    assert image_media.message_media_refs(entries, "9999") == []
    assert image_media.message_media_refs(entries, "") == []


# ---------- 删除：先删引用，再删文件 ----------


def test_delete_media_refuses_while_referenced():
    saved = image_media.save_bytes(BOT, PNG_BYTES)
    _archive(_conversation_with_ref("abc12345", saved))

    outcome = image_media.delete_media(BOT, saved["sha"])

    assert outcome["ok"] is False
    assert outcome["references"][0]["task_id"] == "abc12345"
    assert "仍有 1 处会话引用" in outcome["reason"]
    assert os.path.isfile(image_media.path_of(BOT, saved["sha"]))


def test_delete_media_succeeds_after_reference_removed():
    from app.agent.storage import create_history_store

    saved = image_media.save_bytes(BOT, PNG_BYTES)
    _archive(_conversation_with_ref("abc12345", saved))
    store = create_history_store(BOT)
    try:
        store.delete_history("abc12345")
    finally:
        store.close()

    outcome = image_media.delete_media(BOT, saved["sha"])

    assert outcome["ok"] is True
    assert image_media.path_of(BOT, saved["sha"]) is None


def test_delete_media_reports_missing_and_bad_sha():
    assert image_media.delete_media(BOT, "0" * 16)["ok"] is False
    assert "非法" in image_media.delete_media(BOT, "../x")["reason"]


def test_purge_unreferenced_keeps_referenced_files():
    kept = image_media.save_bytes(BOT, PNG_BYTES)
    garbage = image_media.save_bytes(BOT, PNG_BYTES + b"orphan")
    _archive(_conversation_with_ref("abc12345", kept))

    result = image_media.purge_unreferenced(BOT)

    assert result["removed"] == 1
    assert result["kept_referenced"] == 1
    assert image_media.path_of(BOT, kept["sha"])
    assert image_media.path_of(BOT, garbage["sha"]) is None


def test_gc_respects_ttl_but_never_touches_referenced_files():
    old = image_media.save_bytes(BOT, PNG_BYTES)
    kept = image_media.save_bytes(BOT, PNG_BYTES + b"referenced")
    _archive(_conversation_with_ref("abc12345", kept))
    stale = time.time() - 40 * 86400
    for saved in (old, kept):
        os.utime(image_media.path_of(BOT, saved["sha"]), (stale, stale))

    result = image_media.gc(BOT, ttl_days=14, max_mb=512)

    assert result["removed"] == 1
    assert image_media.path_of(BOT, old["sha"]) is None
    assert image_media.path_of(BOT, kept["sha"])  # 被引用的一律保留


def test_stats_counts_files_and_references():
    saved = image_media.save_bytes(BOT, PNG_BYTES)
    _archive(_conversation_with_ref("abc12345", saved))
    image_media.save_bytes(BOT, PNG_BYTES + b"orphan")

    data = image_media.stats(BOT)

    assert data["count"] >= 2
    assert data["bytes"] >= len(PNG_BYTES)
    assert data["referenced"] >= 1
    assert data["unreferenced"] >= 1


# ---------- prompt dump 脱敏 ----------


def test_prompt_dump_redacts_base64_payloads():
    from app.agent.assembly.dump import _redact_media

    payload = "data:image/png;base64," + "A" * 5000
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": payload}},
        ],
    }]

    redacted = _redact_media(messages)

    text = str(redacted)
    assert "A" * 100 not in text
    # 占位符形状与日志出口一致（见 app/core/redact.py）
    assert "data:image/png;base64,[base64 4.9KB]" in text
    assert redacted[0]["content"][1]["image_url"]["url"].endswith("[base64 4.9KB]")
