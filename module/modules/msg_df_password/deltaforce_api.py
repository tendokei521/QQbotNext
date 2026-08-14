"""三角洲行动密码获取器：双站点实现（从 astrbot napcat_deltaforce_password 移植）。

- kkrb:  POST 三步流程（Cookie → built_ver → 密码 JSON）
- tmini: GET 单步流程（直接请求纯文本，正则解析）
"""

from __future__ import annotations

import re
import ssl

import aiohttp

from app.core.logger import module_logger
from yarl import URL

MAP_MAPPING = {
    "db": "零号大坝",
    "cgxg": "长弓溪谷",
    "bks": "巴克什",
    "htjd": "航天基地",
    "cxjy": "潮汐监狱",
}

TINI_API_URL = "https://www.tmini.net/api/sjzmm?ckey=&type="


# ==================== 站点: kkrb ====================


class DeltaForceKkrbFetcher:
    """kkrb.net — POST 三步流程获取密码。"""

    def __init__(self):
        self.base_url = "https://www.kkrb.net"
        self.session: aiohttp.ClientSession | None = None
        self.version: str | None = None

        # 站点证书校验异常（历史遗留），关闭校验以保证可用
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        self.base_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
        }
        self.api_headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/?viewpage=view%2Foverview",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
            "X-Requested-With": "XMLHttpRequest",
        }

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _ensure_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context))

    async def get_initial_cookie(self) -> bool:
        await self._ensure_session()
        try:
            async with self.session.get(  # type: ignore[union-attr]
                self.base_url, headers=self.base_headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as _:
                cookies = self.session.cookie_jar.filter_cookies(URL(self.base_url))  # type: ignore[union-attr]
                return bool(cookies)
        except Exception as e:
            module_logger.error(f"[DeltaForce:kkrb] Cookie 获取失败: {e}")
            return False

    async def get_menu_data(self) -> dict | None:
        await self._ensure_session()
        try:
            async with self.session.post(  # type: ignore[union-attr]
                f"{self.base_url}/getMenu", headers=self.api_headers, data={},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.version = data.get("built_ver")
                    if self.version:
                        module_logger.info(f"[DeltaForce:kkrb] built_ver={self.version}")
                        return data
                return None
        except Exception as e:
            module_logger.error(f"[DeltaForce:kkrb] getMenu 异常: {e}")
            return None

    async def fetch_passwords(self) -> dict | None:
        if not self.version and not await self.get_menu_data():
            return None
        try:
            async with self.session.post(  # type: ignore[union-attr]
                f"{self.base_url}/getOVData", headers=self.api_headers,
                data={"version": self.version, "globalData": "false"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            module_logger.error(f"[DeltaForce:kkrb] getOVData 异常: {e}")
            return None

    @staticmethod
    def _parse_kkrb_date(updated_str: str) -> str:
        """将 kkrb 的 updated 字段（如 20260623000002）转为 MM月DD日。"""
        if len(updated_str) >= 8:
            return f"{int(updated_str[4:6])}月{int(updated_str[6:8])}日"
        return updated_str

    @staticmethod
    def parse_passwords(data: dict) -> dict:
        passwords: dict = {}
        bd_data = data.get("data", {}).get("bdData", {})
        date_str = ""
        for key, value in bd_data.items():
            if key in MAP_MAPPING:
                pwd = value.get("password")
                if pwd:
                    passwords[MAP_MAPPING[key]] = pwd
                # 从第一个有效条目提取日期
                if not date_str:
                    updated = value.get("updated", "")
                    date_str = DeltaForceKkrbFetcher._parse_kkrb_date(updated)
        if date_str:
            passwords["_date"] = date_str
        return passwords

    async def get_today_passwords(self) -> dict | None:
        if not await self.get_initial_cookie():
            return None
        if not await self.get_menu_data():
            return None
        data = await self.fetch_passwords()
        if not data:
            return None
        return self.parse_passwords(data)


# ==================== 站点: tmini ====================


class DeltaForceTminiFetcher:
    """tmini.net — GET 单步请求，正则解析明文密码。"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self):
        if not self._session:
            self._session = aiohttp.ClientSession()

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def get_today_passwords(self) -> dict | None:
        await self._ensure_session()
        try:
            async with self._session.get(  # type: ignore[union-attr]
                TINI_API_URL,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    module_logger.error(f"[DeltaForce:tmini] HTTP {resp.status}")
                    return None
                text = await resp.text()
        except Exception as e:
            module_logger.error(f"[DeltaForce:tmini] 请求异常: {e}")
            return None

        return self._parse(text)

    def _parse(self, text: str) -> dict | None:
        """正则解析 tmini 明文密码响应。"""
        passwords: dict = {}

        # 提取日期: "更新日期: 06月23日每日密码已更新"
        date_match = re.search(r"更新日期:\s*(\d+月\d+日)", text)
        if date_match:
            passwords["_date"] = date_match.group(1)

        blocks = re.split(r"-{20,}", text)

        for block in blocks:
            name_match = re.search(r"地图名称:\s*(.+)", block)
            pwd_match = re.search(r"密码:\s*(\d+)", block)
            if name_match and pwd_match:
                name = name_match.group(1).strip()
                pwd = pwd_match.group(1)
                passwords[name] = pwd

        if not passwords:
            module_logger.warning("[DeltaForce:tmini] 未解析到密码")
            return None

        module_logger.info(
            f"[DeltaForce:tmini] 解析到 {len(passwords) - (1 if '_date' in passwords else 0)} 个地图密码"
        )
        return passwords


# ==================== 统一入口 ====================

SITE_FETCHERS = {
    "kkrb": DeltaForceKkrbFetcher,
    "tmini": DeltaForceTminiFetcher,
}


async def fetch_passwords_from_site(site: str) -> dict | None:
    """从指定站点获取密码。失败返回 None。"""
    fetcher_cls = SITE_FETCHERS.get(site)
    if not fetcher_cls:
        module_logger.error(f"[DeltaForce] 未知站点: {site}")
        return None

    fetcher = fetcher_cls()
    try:
        result = await fetcher.get_today_passwords()
    finally:
        await fetcher.close()
    return result
