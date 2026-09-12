"""B 站视频解析业务逻辑。

流程：
  总开关 → 群范围检查 → 文本/JSON 段提取链接 → b23 短链归一 → BV 去重 → 限数
  → 获取视频信息（同步、快）
  → 【后台任务】取 720P 单文件流 → 体积预判 → 流式下载（本地缓存）
  → 发送合并转发（简介节点 + 视频节点）

设计要点：
- 取流 + 45MB 下载耗时数十秒，若在 handle 里 await 会**卡死该 bot 的串行事件队列**，
  因此交给 `services.task_manager` 后台执行（模块卸载自动取消）；
- 只下载**第一个**视频（其余只发简介节点），避免一条消息触发多份下载；
- 任何环节失败都降级为文本节点（原因 + 链接），简介节点始终送达。
"""

from __future__ import annotations

import os

from app.core.logger import module_logger
from app.modules import get_data_path
from app.modules.groups import check_group_enabled
from . import bilibili_api as bapi
from . import forward as fwd
from . import video as vlib

# 本地视频缓存子目录
CACHE_SUBDIR = "video_cache"


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

    # 1. 从文本段提取链接（同时收集 ?p=N 分 P 参数，参考实现 parse_video_id 的等价物）
    bv_list: list = []
    page_map: dict = {}
    if config.get("enable_link_video", True):
        texts = [seg["data"].get("text", "") for seg in segments if seg.get("type") == "text"]
        bv_list.extend(bapi.extract_from_text(texts))
        page_map = bapi.extract_page_map(texts)

    # 2. 从 JSON 段（小程序卡片）提取链接
    if config.get("enable_json_video", True):
        json_parts = [seg["data"].get("data", {}) for seg in segments if seg.get("type") == "json"]
        bv_list.extend(bapi.extract_from_json(json_parts))

    if not bv_list:
        return

    # 3-6. 网络请求（短链归一 + 视频信息）走 BilibiliAPI 封装（curl_cffi 指纹模拟）
    results: list[tuple[str, dict, int]] = []
    async with bapi.BilibiliAPI() as api:
        bv_ids = await api.extract_b23(bv_list)
        if not bv_ids:
            return

        # 4. BV 去重保序（按 bot_id 独立去重，空串一并过滤）
        if config.get("enable_bv_dedup", True):
            video_ids = bapi.filter_bv_dedup(
                bv_ids, int(config.get("bv_dedup_timeout", 60) or 60), bot_id=module.bot_id
            )
        else:
            video_ids = list(dict.fromkeys(bv_ids))
        if not video_ids:
            return

        # 5. 限制数量
        video_ids = video_ids[: int(config.get("max_parse_count", 3) or 3)]
        source = f"群{group_id}" if group_id else "私聊"
        logger.info(f"{source} 识别到 {len(video_ids)} 个视频: {video_ids}")

        # 6. 逐个查询视频信息（快；取流/下载放到后台）
        for vid in video_ids:
            try:
                info = await api.get_video_info(
                    vid,
                    timeout=int(config.get("timeout", 10) or 10),
                    cookie=config.get("cookie", "") or "",
                )
                if info:
                    results.append((vid, info, page_map.get(str(vid).upper(), 1)))
            except Exception as e:
                logger.error(f"解析 {vid} 失败: {e}")

    if not results:
        return

    # LLM 接管规则：解析回复已接管「链接」话题，默认跳过 LLM；
    # 唯一例外——群聊中用户 @ 了 bot（如「@bot 这视频讲了啥」），
    # 说明期望 LLM 参与对话，不跳过。
    if not event.is_at_me():
        event.llm.stop()

    if not config.get("use_forward_msg", True):
        await _send_legacy(module, event, results)
        return

    # 合并转发：取流 + 下载耗时，交后台任务（无 task_manager 时同步执行，便于单测）
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
            name=f"bili_forward_{target['group_id'] or target['user_id']}",
            owner=f"module:{module.module_name}:{module.bot_id}",
        )


async def _send_legacy(module, event, results: list) -> None:
    """旧版单条消息：引用回复 + 简介文案（不下载视频，用于回滚开关）。"""
    config = module.config
    chain: list = []
    if config.get("is_reply", True) and event.message_id:
        chain.append({"type": "reply", "data": {"id": event.message_id}})
    for i, (_vid, info, _page) in enumerate(results):
        if i > 0:
            chain.append({"type": "text", "data": {"text": "\n──────────────\n"}})
        chain.extend(bapi.build_video_message(info, config.get("show_cover", True)))

    await event.bot.send_msg(
        message_type=event.message_type,
        user_id=event.user_id,
        group_id=event.group.group_id,
        message=chain,
    )


async def _parse_and_send(module, bot, target: dict, results: list) -> None:
    """取流 → 下载 → 发送合并转发（一条视频一条转发）。"""
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config
    qn = vlib.HEIGHT_TO_QN.get(str(config.get("video_quality", "720") or "720"), vlib.DEFAULT_QN)
    enable_download = bool(config.get("enable_video_download", True))
    uin = str(target.get("self_id") or module.bot_id or fwd.FALLBACK_UIN)

    try:
        async with bapi.BilibiliAPI() as api:
            for index, (_vid, info, page) in enumerate(results):
                video_file = video_error = None
                # 只下载第一个视频（其余仅发简介节点）
                if enable_download and index == 0:
                    video_file, video_error = await _fetch_video(
                        module, api, info, page=page, qn=qn, logger=logger
                    )
                nodes = fwd.build_forward_nodes(
                    info,
                    uin=uin,
                    show_cover=config.get("show_cover", True),
                    video_file=video_file,
                    video_error=video_error,
                )
                await _send_forward(bot, target, nodes)
    except Exception as e:
        logger.error(f"合并转发发送失败: {e}")


async def _fetch_video(module, api, info: dict, *, page: int = 1, qn: int, logger) -> tuple[str | None, str | None]:
    """取 720P 单文件流并下载到本地缓存，返回 ``(文件路径, 失败原因)``。

    选 cid 与参考实现一致：先按分 P 序号 ``pick_page``，cid 缺失时用 ``pagelist`` 交叉校正。
    """
    config = module.config
    bvid = info.get("bvid") or ""
    page_info = vlib.pick_page(info, page)
    cid = page_info.get("cid")
    if not cid and bvid:
        pagelist = await api.get_pagelist(bvid, timeout=int(config.get("timeout", 10) or 10))
        page_info = pagelist[0] if pagelist else page_info
        cid = page_info.get("cid")
    if not bvid or not cid:
        return None, "无法确定 cid"

    page_no = int(page_info.get("page") or 1)
    cache_dir = os.path.join(get_data_path(module.module_name), CACHE_SUBDIR)
    dest = vlib.cache_path(cache_dir, bvid, page_no)
    ttl = int(config.get("video_cache_ttl_minutes", 720) or 720)
    if config.get("video_cache_enabled", True) and vlib.is_fresh(dest, ttl):
        logger.debug(f"{bvid} 命中本地视频缓存")
        return dest, None

    play = await api.get_playurl_single(
        bvid,
        cid,
        qn=qn,
        cookie=config.get("cookie", "") or "",
        timeout=int(config.get("timeout", 10) or 10),
    )
    single = vlib.pick_single(play)
    if not single:
        return None, "取流失败（B站风控或该视频受限）"

    max_mb = int(config.get("video_max_mb", 80) or 0)
    if max_mb > 0 and single["total_size"] > max_mb * 1024 * 1024:
        return None, f"体积 {vlib.human_size(single['total_size'])} 超过上限 {max_mb}MB"

    try:
        os.makedirs(cache_dir, exist_ok=True)
        await api.download_durl(
            single["segments"], dest, timeout=int(config.get("video_download_timeout", 60) or 60)
        )
    except Exception as e:
        logger.error(f"{bvid} 视频下载失败: {e}")
        return None, "视频下载失败"

    logger.info(f"{bvid} 视频就绪：{single['quality_name']} {vlib.human_size(single['total_size'])}")
    _sweep_cache(cache_dir, ttl, int(config.get("video_cache_max_mb", 1024) or 0), logger)
    return dest, None


def _sweep_cache(cache_dir: str, ttl_minutes: int, max_mb: int, logger) -> None:
    """按 TTL + 容量上限清理本地视频缓存（删最旧的）。"""
    try:
        entries = []
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            if os.path.isfile(path):
                entries.append((path, os.path.getmtime(path), os.path.getsize(path)))
        stale = vlib.pick_stale(entries, ttl_minutes, max_mb * 1024 * 1024)
        for path in stale:
            os.remove(path)
        if stale:
            logger.debug(f"清理视频缓存 {len(stale)} 个文件")
    except OSError as e:
        logger.warning(f"清理视频缓存失败: {e}")


async def _send_forward(bot, target: dict, nodes: list) -> None:
    """群聊走 send_group_forward_msg，私聊走 send_private_forward_msg。"""
    if target.get("message_type") == "private":
        await bot.send_forward_msg(user_id=target.get("user_id") or 0, msgdata=nodes)
    else:
        await bot.send_forward_msg(group_id=target.get("group_id") or 0, msgdata=nodes)
