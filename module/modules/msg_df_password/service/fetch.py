"""密码获取与格式化：双站点 fallback + 每日缓存（按 bot+site 分键，fallback 不污染主站缓存）。"""

from datetime import date

from app.core.logger import module_logger

from ..deltaforce_api import fetch_passwords_from_site

# 每日结果缓存：{(bot_id, site): (日期串, 密码 dict)}，同 bot 同站点当天不重复请求
_cached: dict[tuple, tuple] = {}


async def _fetch_and_format(module) -> str | None:
    """获取密码并格式化为文本（主站失败 → 备用源）。"""
    config = module.config
    site = config.get("default_site", "kkrb")
    passwords = await _fetch_today_password(site, module.bot_id)
    if passwords:
        return _format(passwords)

    if config.get("enable_fallback", False):
        fb_site = config.get("fallback_site", "tmini")
        if fb_site != site:
            module_logger.info(f"[DeltaForce] {site} 失败，尝试备用源 {fb_site}")
            passwords = await _fetch_today_password(fb_site, module.bot_id)
            if passwords:
                return _format(passwords)

    return None


async def _fetch_today_password(site: str, bot_id=None):
    """带每日缓存的站点获取。返回 {_date, 地图名: 密码} 或 None。"""
    key = (bot_id, site)
    today = date.today().strftime("%m月%d日")
    cached = _cached.get(key)
    if cached and cached[0] == today and cached[1]:
        return cached[1]

    result = await fetch_passwords_from_site(site)
    if result:
        _cached[key] = (today, result)
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
