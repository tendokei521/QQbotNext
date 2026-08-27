"""MCP（Model Context Protocol）标准输入/输出客户端。

通过子进程 stdio JSON-RPC 2.0 连接 MCP server，把远端工具桥接成框架 ToolSpec。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from app.core.logger import logger
from app.llm.tool import ToolSpec

_MCP_ID_RE = re.compile(r"[^0-9a-zA-Z_\-]+")


def _safe(name: str) -> str:
    return _MCP_ID_RE.sub("_", str(name or "")) or "mcp"


class MCPClient:
    """单个 MCP stdio server 客户端。"""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict | None = None,
        cwd: str | None = None,
        *,
        timeout: int = 30,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(os.environ)
        if env:
            self.env.update({str(k): str(v) for k, v in env.items()})
        self.cwd = cwd
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            cwd=self.cwd,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qqbot-next", "version": "1.0.0"},
            },
        )
        self._notify("notifications/initialized", {})

    async def _read_stdout(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode("utf-8", errors="ignore").strip())
                except json.JSONDecodeError:
                    continue
                msg_id = data.get("id")
                if msg_id is not None and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        future.set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.add_info("MCP").debug(f"MCP {self.name} stdout reader stopped: {e}")

    def _notify(self, method: str, params: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))

    async def _request(self, method: str, params: dict, timeout: int | None = None) -> dict:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError(f"MCP server {self.name} 未连接")
        self._id += 1
        req_id = self._id
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[req_id] = future
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=timeout or self.timeout)
        finally:
            self._pending.pop(req_id, None)

    async def list_tools(self) -> list[dict]:
        result = await self._request("tools/list", {})
        return (result.get("result") or {}).get("tools") or []

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        result_data = result.get("result") or {}
        content = result_data.get("content") or []
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text", "")))
        if texts:
            return "\n".join(texts)
        return json.dumps(result_data, ensure_ascii=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self._reader_task is not None:
            self._reader_task.cancel()


class MCPManager:
    """按 AgentRuntime 管理多个 MCP server，并把远端工具转成 ToolSpec。"""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._clients: dict[str, MCPClient] = {}
        self._specs: list[ToolSpec] = []
        self._ready = False

    def _servers(self) -> list[dict]:
        try:
            raw = self.runtime.config.get("mcp_servers", []) or []
        except Exception:
            raw = []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = []
        return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []

    def enabled(self) -> bool:
        return bool(self._servers())

    async def ensure_ready(self) -> bool:
        if self._ready:
            return True
        servers = self._servers()
        if not servers:
            return False
        for cfg in servers:
            if not isinstance(cfg, dict):
                continue
            name = str(cfg.get("name") or "").strip()
            command = str(cfg.get("command") or "").strip()
            if not name or not command:
                continue
            client = MCPClient(
                name=name,
                command=command,
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
                cwd=cfg.get("cwd"),
                timeout=int(cfg.get("timeout", 30) or 30),
            )
            try:
                await client.connect()
                tools = await client.list_tools()
                self._clients[name] = client
                for tool in tools:
                    tool_name = str(tool.get("name") or "")
                    desc = str(tool.get("description") or "")
                    params = (tool.get("inputSchema") or {}).get("properties")
                    parameters = (
                        {"type": "object", "properties": params or {}}
                        if isinstance(params, dict)
                        else {"type": "object", "properties": {}}
                    )
                    spec_name = f"mcp_{_safe(name)}_{_safe(tool_name)}"

                    async def handler(_ctx, args, client=client, tool_name=tool_name):
                        return await client.call_tool(tool_name, args)

                    self._specs.append(ToolSpec(
                        name=spec_name,
                        description=f"[MCP:{name}] {desc or tool_name}",
                        parameters=parameters,
                        handler=handler,
                    ))
                logger.add_info("MCP").info(
                    f"MCP {name} 连接成功，暴露 {len(tools)} 个工具"
                )
            except Exception as e:
                logger.add_info("MCP").warning(
                    f"MCP {name} 连接/加载失败: {e}"
                )
                try:
                    client.close()
                except Exception:
                    pass
        self._ready = True
        return bool(self._clients)

    def build_tools(self) -> list[ToolSpec]:
        return list(self._specs)

    def close(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()
        self._specs.clear()
        self._ready = False
