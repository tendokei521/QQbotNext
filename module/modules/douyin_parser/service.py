"""抖音短视频解析业务逻辑。

流程：
  总开关 → 群范围检查 → 文本/JSON 段提取链接 → 短链归一成 aweme_id → 作品去重 → 限数
  → 取作品详情（快）
  → 【后台任务】取无水印地址 → 下载（本地缓存）→ 发送合并转发（文案节点 + 媒体节点）

设计要点：
- 下载耗时可达数十秒，若在 handle 里 await 会**卡死该 bot 的串行事件队列**，
  因此交给 ``services.task_manager`` 后台执行（模块卸载自动取消）；
- 只下载**第一个**作品（其余只发文案节点），避免一条消息触发多份下载；
- 任何环节失败都降级为文本节点（原因 + 链接），文案节点始终送达。
"""

from __future__ import annotations

import os

from app.core.logger import module_logger
from app.modules import get_data_path, media_cache
from app.modules.groups import check_group_enabled

from . import forward as fwd
from . import media
from .douyin_api import DouyinAPI, DownloadTooLarge, filter_aweme_dedup

# 本地媒体缓存子目录
CACHE_SUBDIR = "media_cache"


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config

    if not config.get("enable_auto_parse", True):
        return

    # 群范围检查
    group_id = event.group.group_id
    if group_id and not check_group_enabled(config, str(group_id)):
        return

    segments = [seg.to_dict() for seg in event.message]
    if not segments:
        return

    # 1. 从文本段提取链接
    links: list = []
    if config.get("enable_link_parse", True):
        texts = [seg["data"].get("text", "") for seg in segments if seg.get("type") == "text"]
        links.extend(media.extract_links(texts))

    # 2. 从 JSON 段（小程序卡片）提取链接
    if config.get("enable_json_parse", True):
        json_parts = [seg["data"].get("data", {}) for seg in segments if seg.get("type") == "json"]
        links.extend(media.extract_from_json(json_parts))

    if not links:
        return

    timeout = int(config.get("timeout", 10) or 10)
    cookie = config.get("cookie", "") or ""
    results: list[tuple[dict, dict]] = []

    async with DouyinAPI(user_agent=config.get("user_agent", "") or "") as api:
        # 3. 链接 → aweme_id（保序去重；短链要跟随跳转，失败的单条跳过）
        aweme_ids: list[str] = []
        for link in links:
            try:
                aweme_id = await api.resolve_aweme_id(link, timeout=timeout)
            except Exception as e:
                logger.error(f"解析链接 {link} 失败: {e}")
                continue
            if aweme_id and aweme_id not in aweme_ids:
                aweme_ids.append(aweme_id)
        if not aweme_ids:
            return

        # 4. 作品去重保序（按 bot_id 独立去重）
        if config.get("enable_aweme_dedup", True):
            aweme_ids = filter_aweme_dedup(
                aweme_ids, int(config.get("aweme_dedup_timeout", 60) or 60), bot_id=module.bot_id
            )
        else:
            aweme_ids = list(dict.fromkeys(aweme_ids))
        if not aweme_ids:
            return

        # 5. 限制数量
        aweme_ids = aweme_ids[: int(config.get("max_parse_count", 3) or 3)]
        source = f"群{group_id}" if group_id else "私聊"
        logger.info(f"{source} 识别到 {len(aweme_ids)} 个作品: {aweme_ids}")

        # 6. 逐个取作品详情（快；下载放到后台）
        for aweme_id in aweme_ids:
            try:
                data = await api.get_aweme_detail(aweme_id, timeout=timeout, cookie=cookie)
            except Exception as e:
                logger.error(f"解析作品 {aweme_id} 失败: {e}")
                continue
            if data:
                results.append((media.build_meta(data), data))

    if not results:
        return

    # LLM 接管规则：解析回复已接管「链接」话题，默认跳过 LLM；
    # 唯一例外——群聊中用户 @ 了 bot（如「@bot 这视频讲了啥」），说明期望 LLM 参与对话。
    if not event.is_at_me():
        event.llm.stop()

    if not config.get("use_forward_msg", True):
        await _send_legacy(module, event, results)
        return

    # 合并转发：下载耗时，交后台任务（无 task_manager 时同步执行，便于单测）
    target = {
        "message_type": event.message_type,
        "user_id": event.user_id,
        "group_id": group_id,
        "self_id": event.self_id,
    }
    coro = _parse_and_send(module, event.bot, target, results)
    task_manager = getattr(module.ctx.services, "task_manager", None)
    if task_manager is None:
        await coro
    else:
        task_manager.create_task(
            coro,
            name=f"douyin_forward_{target['group_id'] or target['user_id']}",
            owner=f"module:{module.module_name}:{module.bot_id}",
        )


async def _send_legacy(module, event, results: list) -> None:
    """旧版单条消息：引用回复 + 文案（不下载媒体，用于回滚开关）。"""
    config = module.config
    chain: list = []
    if config.get("is_reply", True) and event.message_id:
        chain.append({"type": "reply", "data": {"id": event.message_id}})
    for index, (meta, _data) in enumerate(results):
        if index > 0:
            chain.append({"type": "text", "data": {"text": "\n──────────────\n"}})
        chain.extend(fwd.build_intro_segments(meta, config.get("show_cover", True)))

    await event.bot.send_msg(
        message_type=event.message_type,
        user_id=event.user_id,
        group_id=event.group.group_id,
        message=chain,
    )


async def _parse_and_send(module, bot, target: dict, results: list) -> None:
    """取流 → 下载 → 发送合并转发（一条作品一条转发）。"""
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config
    uin = str(target.get("self_id") or module.bot_id or fwd.FALLBACK_UIN)
    cache_dir = os.path.join(get_data_path(module.module_name), CACHE_SUBDIR)

    try:
        async with DouyinAPI(user_agent=config.get("user_agent", "") or "") as api:
            for index, (meta, data) in enumerate(results):
                video_file = image_files = media_error = None
                # 只下载第一个作品（其余仅发文案节点）
                if index == 0:
                    video_file, image_files, media_error = await _fetch_media(
                        module, api, meta, data, cache_dir=cache_dir, logger=logger
                    )
                nodes = fwd.build_forward_nodes(
                    meta,
                    uin=uin,
                    show_cover=config.get("show_cover", True),
                    video_file=video_file,
                    image_files=image_files,
                    media_error=media_error,
                )
                await _send_forward(bot, target, nodes)
    except Exception as e:
        logger.error(f"合并转发发送失败: {e}")


async def _fetch_media(module, api, meta: dict, data: dict, *, cache_dir: str, logger):
    """下载作品媒体，返回 ``(视频路径, 图片路径列表, 失败原因)``（三者互斥）。"""
    if meta.get("content_type") == "image":
        if not module.config.get("enable_album_download", True):
            return None, None, None
        return await _fetch_album(module, api, meta, cache_dir=cache_dir, logger=logger)
    if not module.config.get("enable_video_download", True):
        return None, None, None
    return await _fetch_video(module, api, meta, data, cache_dir=cache_dir, logger=logger)


async def _fetch_video(module, api, meta: dict, data: dict, *, cache_dir: str, logger):
    """取最高清晰度无水印流并下载到本地缓存。"""
    config = module.config
    aweme_id = str(meta.get("aweme_id") or "")
    quality = media.pick_quality(
        media.extract_video_qualities(data), str(config.get("video_quality", "highest") or "highest")
    )
    if not quality:
        return None, None, "取流失败（作品受限或 Cookie 失效）"

    ttl = int(config.get("video_cache_ttl_minutes", 720) or 720)
    dest = media.video_cache_path(cache_dir, aweme_id)
    if config.get("video_cache_enabled", True) and media_cache.is_fresh(dest, ttl):
        logger.debug(f"{aweme_id} 命中本地视频缓存")
        return dest, None, None

    max_mb = int(config.get("video_max_mb", 80) or 0)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        await api.download(
            quality["url"],
            dest,
            timeout=int(config.get("video_download_timeout", 60) or 60),
            max_bytes=max_mb * 1024 * 1024 if max_mb > 0 else 0,
        )
    except DownloadTooLarge as e:
        logger.info(f"{aweme_id} 视频超限放弃: {e}")
        return None, None, f"体积超过上限 {max_mb}MB"
    except Exception as e:
        logger.error(f"{aweme_id} 视频下载失败: {e}")
        return None, None, "视频下载失败"

    logger.info(f"{aweme_id} 视频就绪：{quality['quality_label']} {media_cache.human_size(os.path.getsize(dest))}")
    _sweep_cache(module, cache_dir, ttl, logger)
    return dest, None, None


async def _fetch_album(module, api, meta: dict, *, cache_dir: str, logger):
    """逐张下载图集（Live 图存 mp4）；单张失败跳过，不影响其它张。"""
    config = module.config
    aweme_id = str(meta.get("aweme_id") or "")
    limit = int(config.get("album_max_images", 9) or 9)
    urls = (meta.get("image_urls") or [])[:limit]
    if not urls:
        return None, None, "图集地址缺失"

    ttl = int(config.get("video_cache_ttl_minutes", 720) or 720)
    timeout = int(config.get("video_download_timeout", 60) or 60)
    cache_enabled = bool(config.get("video_cache_enabled", True))

    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as e:
        logger.error(f"{aweme_id} 图集缓存目录创建失败: {e}")
        return None, None, "图集下载失败"

    paths: list[str] = []
    for index, url in enumerate(urls, start=1):
        dest = media.album_cache_path(cache_dir, aweme_id, index, url)
        if cache_enabled and media_cache.is_fresh(dest, ttl):
            paths.append(dest)
            continue
        try:
            await api.download(url, dest, timeout=timeout)
        except Exception as e:
            logger.warning(f"{aweme_id} 图集第 {index} 张下载失败: {e}")
            continue
        paths.append(dest)

    if not paths:
        return None, None, "图集下载失败"
    logger.info(f"{aweme_id} 图集就绪：{len(paths)}/{len(urls)} 张")
    _sweep_cache(module, cache_dir, ttl, logger)
    return None, paths, None


def _sweep_cache(module, cache_dir: str, ttl_minutes: int, logger) -> None:
    """按 TTL + 容量上限清理本地媒体缓存（通用策略见 ``app/modules/media_cache.py``）。"""
    media_cache.sweep(cache_dir, ttl_minutes, int(module.config.get("video_cache_max_mb", 1024) or 0), logger)


async def _send_forward(bot, target: dict, nodes: list) -> None:
    """群聊走 send_group_forward_msg，私聊走 send_private_forward_msg。"""
    if target.get("message_type") == "private":
        await bot.send_forward_msg(user_id=target.get("user_id") or 0, msgdata=nodes)
    else:
        await bot.send_forward_msg(group_id=target.get("group_id") or 0, msgdata=nodes)
