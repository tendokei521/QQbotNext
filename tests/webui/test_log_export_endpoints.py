"""给 webui/api 的日志导出端点补 HTTP 层用例。

起因：把 `container.get(LogService)` 改成 `Depends(get_log_service)` 时，`_log_dir()`
的参数从 `request` 变成 `log_service`，但三个调用点漏改——**没有测试覆盖这些端点，
所以全量测试仍然是绿的**。这条用例就是补上这个洞。
"""

from __future__ import annotations

from pathlib import Path


async def test_log_export_endpoints_resolve_log_dir(container, tmp_path):
    """三个导出端点都能拿到日志目录（缺依赖会在调用时抛 TypeError）。"""
    from starlette.testclient import TestClient

    from app.application.log_service import LogService
    from app.webui.app import create_app

    log_service = container.get(LogService)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "debug.log").write_text("hello\n", encoding="utf-8")
    original = log_service.log_dir
    log_service.log_dir = Path(log_dir)
    try:
        client = TestClient(create_app(container))

        r = client.get("/api/logs/export/list")
        assert r.status_code == 200, r.text
        assert any(f["name"] == "debug.log" for f in r.json()["current"])

        r = client.get("/api/logs/export/download", params={"file": "debug.log"})
        assert r.status_code == 200, r.text
        assert "hello" in r.text

        r = client.post("/api/logs/export/zip", json=[{"folder": "", "file": "debug.log"}])
        assert r.status_code == 200, r.text
        assert r.content[:2] == b"PK", "应返回 zip"
    finally:
        log_service.log_dir = original
