"""密码获取与格式化：双站点 fallback + 每日缓存。"""

from datetime import date

from app.core.logger import module_logger

from ..deltaforce_api import fetch_passwords_from_site

# 每日结果缓存（同一天不重复请求）
_cached_date = None
_cached_passwords = None


async def _fetch_and_format(module) -> str | None:
    """获取密码并格式化为文本（主站失败 → 备用源）。"""
    config = module.config
    site = config.get("default_site", "kkrb")
    passwords = await _fetch_today_password(site)
    if passwords:
        return _format(passwords)

    if config.get("enable_fallback", False):
        fb_site = config.get("fallback_site", "tmini")
        if fb_site != site:
            module_logger.info(f"[DeltaForce] {site} 失败，尝试备用源 {fb_site}")
            passwords = await _fetch_today_password(fb_site)
            if passwords:
                return _format(passwords)

    return None


async def _fetch_today_password(site: str):
    """带每日缓存的站点获取。返回 {_date, 地图名: 密码} 或 None。"""
    global _cached_date, _cached_passwords

    today = date.today().strftime("%m月%d日")
    if _cached_date == today and _cached_passwords:
        return _cached_passwords

    result = await fetch_passwords_from_site(site)
    if result:
        _cached_date, _cached_passwords = today, result
    return result


def _format(passwords: dict) -> str:
    """格式化密码字典为可读文本。"""
    date_str = passwords.get("_date", "")
    header = "三角洲行动 今日密码"
    if date_str:
        header += f" ({date_str})"
    lines = [header]
    for name, pwd in passwords.items():
        if name == "_date":
            continue
        lines.append(f"{name}: {pwd}")
    return "\n".join(lines)
