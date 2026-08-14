"""三角洲行动密码获取器：双站点实现（curl_cffi 浏览器指纹模拟，自 fabric_api 移植）。

- kkrb:  POST 三步流程（Cookie → built_ver → 密码 JSON）
- tmini: GET 单步流程（直接请求纯文本，正则解析）
"""

from __future__ import annotations

import re

from app.core.logger import module_logger
from app.infrastructure.curl_cffi import CurlCffiClient

MAP_MAPPING = {
    "db": "零号大坝",
    "cgxg": "长弓溪谷",
    "bks": "巴克什",
    "htjd": "航天基地",
    "cxjy": "潮汐监狱",
}

TINI_API_URL = "https://www.tmini.net/api/sjzmm?ckey=&type="

KKRB_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
}

KKRB_API_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.kkrb.net",
    "Referer": "https://www.kkrb.net/?viewpage=view%2Foverview",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


# ==================== 站点: kkrb ====================


class DeltaForceKkrbFetcher(CurlCffiClient):
    """kkrb.net — curl_cffi POST 三步流程获取密码（Cookie 自动管理）。"""

    def __init__(self, impersonate="chrome", proxy: str = "") -> None:
        super().__init__(impersonate=impersonate, proxy=proxy)
        self.base_url = "https://www.kkrb.net"
        self.version: str | None = None

    async def get_initial_cookie(self) -> bool:
        """步骤1: 获取 Cookie（curl_cffi 自动管理）。"""
        try:
            await self.GET(self.base_url, headers=KKRB_BASE_HEADERS, timeout=30)
            return bool(self.session is not None and self.session.cookies)
        except Exception as e:
            module_logger.error(f"[DeltaForce:kkrb] Cookie 获取失败: {e}")
            return False

    async def get_menu_data(self) -> bool:
        """步骤2: 获取菜单并提取 built_ver（仅作记录；getOVData 不依赖它）。

        调试确认（1/2/deltaforce_kkrb_api.py）：getMenu 为 POST 请求，参数 globalData=false。
        """
        try:
            response = await self.POST(
                f"{self.base_url}/getMenu",
                data={"globalData": "false"},
                headers=KKRB_API_HEADERS,
                timeout=30,
            )
            if response.status_code != 200:
                module_logger.warning(f"[DeltaForce:kkrb] getMenu 状态码异常: {response.status_code}")
                return False
            data = response.json()
            # 兼容两种结构：{"built_ver": ...} 或 {"data": {"built_ver": ...}}
            self.version = data.get("built_ver") or (data.get("data") or {}).get("built_ver")
            if self.version:
                module_logger.info(f"[DeltaForce:kkrb] built_ver={self.version}")
            return True
        except Exception as e:
            module_logger.error(f"[DeltaForce:kkrb] getMenu 异常: {e}")
            return False

    async def fetch_passwords(self) -> dict | None:
        """步骤3: 获取密码 JSON。

        调试确认：getOVData 仅需 globalData=false，无需 version 参数。
        """
        try:
            response = await self.POST(
                f"{self.base_url}/getOVData",
                data={"globalData": "false"},
                headers=KKRB_API_HEADERS,
                timeout=30,
            )
            if response.status_code == 200:
                return response.json()
            module_logger.warning(f"[DeltaForce:kkrb] getOVData 状态码异常: {response.status_code}")
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

    def parse_passwords(self, data: dict) -> dict:
        """解析密码 JSON 数据。"""
        passwords: dict = {}
        bd_data = data.get("data", {}).get("bdData", {})
        date_str = ""
        for key, value in bd_data.items():
            if key in MAP_MAPPING:
                pwd = value.get("password")
                if pwd:
                    passwords[MAP_MAPPING[key]] = pwd
                if not date_str:
                    updated = value.get("updated", "")
                    date_str = self._parse_kkrb_date(updated)
        if date_str:
            passwords["_date"] = date_str
        return passwords

    async def get_today_passwords(self) -> dict | None:
        """执行完整三步流程，获取今日密码。

        getMenu 失败不阻断（getOVData 不依赖 built_ver），仅记录日志。
        """
        if not await self.get_initial_cookie():
            return None
        await self.get_menu_data()
        data = await self.fetch_passwords()
        if not data:
            return None
        return self.parse_passwords(data)


# ==================== 站点: tmini ====================


class DeltaForceTminiFetcher(CurlCffiClient):
    """tmini.net — GET 单步请求，正则解析明文密码。"""

    async def get_today_passwords(self) -> dict | None:
        try:
            resp = await self.GET(TINI_API_URL, timeout=10)
            if resp.status_code != 200:
                module_logger.error(f"[DeltaForce:tmini] HTTP {resp.status_code}")
                return None
            text = resp.text
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

    async with fetcher_cls() as fetcher:
        return await fetcher.get_today_passwords()
