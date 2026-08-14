"""B 站视频解析业务逻辑。"""

import html
import json
import re
from typing import Dict, List, Optional, Set

import aiohttp

from app.core.logger import module_logger

REGEX_SHORT = re.compile(r"(?:https?://)?b23\.tv/[a-zA-Z0-9]+", re.IGNORECASE)
REGEX_VIDEO = re.compile(r"(BV[a-zA-Z0-9]{10}|av\d+)", re.IGNORECASE)
REGEX_DIRECT_LINK = re.compile(
    r"https?://(?:www\.)?bilibili\.com/video/(BV[a-zA-Z0-9]{10}|av\d+)/?.*",
    re.IGNORECASE,
)


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")

    message = event.message
    message_id = event.message_id
    group_id = event.group.group_id
    user_id = event.user_id
    message_type = event.message_type

    config = module.config
    enable_json_video = config.get("enable_json_video", True)
    enable_link_video = config.get("enable_link_video", True)
    show_cover = config.get("show_cover", True)
    max_parse_count = config.get("max_parse_count", 3)
    timeout = config.get("timeout", 10)

    target_text = ""
    for seg in message:
        seg_type, seg_data = seg.type, seg.data or {}
        if enable_json_video and seg_type == "json":
            raw_json = seg_data.get("data", "")
            extracted_url = extract_bilibili_card_url(raw_json)
            if extracted_url:
                target_text += f" {extracted_url} "
        if enable_link_video and seg_type == "text":
            text_content = seg_data.get("text", "")
            if "bilibili.com" in text_content or "b23.tv" in text_content:
                target_text += f" {text_content} "

    if not target_text.strip():
        return
    target_text = html.unescape(target_text)

    parse_tasks: list[str] = []
    seen_ids: Set[str] = set()

    if enable_link_video:
        for match in REGEX_DIRECT_LINK.finditer(target_text):
            vid = match.group(1).strip()
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                parse_tasks.append(vid)
                source = f"群{group_id}" if group_id else f"用户{user_id}"
                logger.info(f"{source} 识别到直链，任务: {parse_tasks}")

    if enable_json_video:
        for match in REGEX_SHORT.finditer(target_text):
            link = match.group()
            real_url = await BiliAPI.resolve_short_link(link, timeout)
            target_text += " " + real_url
        for match in REGEX_VIDEO.findall(target_text):
            vid = match if isinstance(match, str) else match[0]
            vid = vid.strip()
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                parse_tasks.append(vid)
                source = f"群{group_id}" if group_id else f"用户{user_id}"
                logger.info(f"{source} 识别到小程序/短链，任务: {parse_tasks}")

    if not parse_tasks:
        return

    message_chain = [{"type": "reply", "data": {"id": message_id}}]
    has_content = False
    for i, vid in enumerate(parse_tasks):
        if i >= max_parse_count:
            break
        try:
            info = await BiliAPI.get_video_info(vid, timeout)
            if info:
                segments = build_video_message(info, show_cover)
                if segments:
                    if has_content:
                        message_chain.append({"type": "text", "data": {"text": "\n──────────────\n"}})
                    message_chain.extend(segments)
                    has_content = True
        except Exception as e:
            logger.error(f"解析出错: {e}")

    if has_content:
        await event.bot.send_msg(
            message_type=message_type,
            user_id=user_id,
            group_id=group_id,
            message=message_chain,
        )


class BiliAPI:
    BASE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    }

    @staticmethod
    def format_number(num: int) -> str:
        if not isinstance(num, int):
            return "0"
        if num >= 100000000:
            return f"{round(num / 100000000, 1)}亿"
        if num >= 10000:
            return f"{round(num / 10000, 1)}万"
        return str(num)

    @classmethod
    async def resolve_short_link(cls, url: str, timeout: int = 10) -> str:
        if not url.startswith("http"):
            url = "https://" + url
        try:
            async with aiohttp.ClientSession(headers=cls.BASE_HEADERS) as session:
                async with session.head(url, allow_redirects=True, timeout=timeout) as resp:
                    return str(resp.url)
        except Exception:
            return url

    @classmethod
    async def get_video_info(cls, vid: str, timeout: int = 10) -> Optional[Dict]:
        params = {}
        vid = vid.strip()
        if vid.lower().startswith("bv"):
            params["bvid"] = vid
        elif vid.lower().startswith("av"):
            params["aid"] = vid[2:]
        else:
            return None
        url = "https://api.bilibili.com/x/web-interface/view"
        try:
            async with aiohttp.ClientSession(headers=cls.BASE_HEADERS) as session:
                async with session.get(url, params=params, timeout=timeout) as resp:
                    data = await resp.json()
                    return data["data"] if data.get("code") == 0 else None
        except Exception as e:
            module_logger.error(f"获取视频信息失败: {e}")
            return None


def extract_bilibili_card_url(json_data_str: str) -> str:
    try:
        clean_json_str = html.unescape(json_data_str)
        data = json.loads(clean_json_str)
        meta = data.get("meta", {})
        for key, detail in meta.items():
            if not isinstance(detail, dict):
                continue
            if "qqdocurl" in detail and detail["qqdocurl"]:
                return detail["qqdocurl"]
            if "jumpUrl" in detail and detail["jumpUrl"]:
                return detail["jumpUrl"]
    except (json.JSONDecodeError, Exception):
        pass
    return ""


def build_video_message(data: Dict, show_cover: bool = True) -> List[Dict]:
    bvid = data.get("bvid", "")
    title = data.get("title", "未知标题")
    pic = data.get("pic", "")
    owner_name = data.get("owner", {}).get("name", "未知UP")
    stat = data.get("stat", {})
    view = BiliAPI.format_number(stat.get("view", 0))
    danmaku = BiliAPI.format_number(stat.get("danmaku", 0))
    like = BiliAPI.format_number(stat.get("like", 0))
    coin = BiliAPI.format_number(stat.get("coin", 0))
    favorite = BiliAPI.format_number(stat.get("favorite", 0))
    share = BiliAPI.format_number(stat.get("share", 0))
    desc = data.get("desc", "") or "无简介"
    desc = desc.replace("\n", " ")
    if len(desc) > 100:
        desc = desc[:100] + "..."

    segments = [{"type": "text", "data": {"text": f"📺 {title}\n"}}]
    if show_cover and pic:
        segments.append({"type": "image", "data": {"file": pic}})
        segments.append({"type": "text", "data": {"text": "\n"}})
    info_text = (
        f"UP主：{owner_name}\n"
        f"点赞：{like}  |  收藏：{favorite}\n"
        f"投币：{coin}  |  转发：{share}\n"
        f"播放：{view}  |  弹幕：{danmaku}\n"
        f"简介：{desc}\n"
        f"https://www.bilibili.com/video/{bvid}"
    )
    segments.append({"type": "text", "data": {"text": info_text}})
    return segments
