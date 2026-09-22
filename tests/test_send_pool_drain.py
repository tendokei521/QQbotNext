"""StreamSendPool 发送完成语义回归测试。

线上表现：模型明明回复了，用户却收不到最后一条——`LlmPipeline` 在
``wait_drained()`` 返回后立刻 ``shutdown()`` 取消发送协程，而 ``wait_drained()``
原先只等 ``_drained`` 事件。该事件由 ``finish()`` 在「队列为空」时立即置位，
于是还在发送节奏 ``sleep`` 里的最后一条消息被取消掉、静默丢失。
"""

from __future__ import annotations

import asyncio

from app.domain.message import Message
from app.llm.send_pool import StreamSendPool


def _slow_policy_config() -> dict:
    return {
        "stream_send_interval_mode": "fixed",
        "stream_send_interval_base_ms": 80,
        "stream_send_max_queue": 10,
        "stream_flush_on_finish": False,
    }


async def test_wait_drained_waits_for_inflight_send():
    """finish() 时队列为空，但已取出的那条还在节奏 sleep 中——必须等它发完。"""
    sent: list[str] = []

    async def send_message(msg):
        sent.append(msg.text)

    pool = StreamSendPool(_slow_policy_config(), send_message=send_message)
    try:
        await pool.put(Message.from_text("唯一一条"))
        await pool.finish()

        # 队列此刻已被 sender 取走 → _drained 会被立即置位；
        # 若 wait_drained 不等发送协程，这里返回时 sent 仍是空的。
        await asyncio.wait_for(pool.wait_drained(), timeout=3)
        assert sent == ["唯一一条"]
    finally:
        await pool.shutdown()


async def test_send_failure_is_logged_and_does_not_hang_finish(caplog):
    """单条发送失败：留痕、不中断后续发送、收尾不会被挂死。"""
    import logging

    sent: list[str] = []

    async def flaky(msg):
        if msg.text == "炸":
            raise RuntimeError("发送失败")
        sent.append(msg.text)

    pool = StreamSendPool({"stream_send_interval_mode": "none"}, send_message=flaky)
    try:
        with caplog.at_level(logging.ERROR):
            await pool.put(Message.from_text("炸"))
            await pool.put(Message.from_text("后续一条"))
            await pool.finish()
            # 失败那条不能把收尾挂死
            await asyncio.wait_for(pool.wait_drained(), timeout=3)
        assert sent == ["后续一条"], "一条失败不应中断后续发送"
        assert any("发送失败" in r.message for r in caplog.records), "发送失败必须留痕"
    finally:
        await pool.shutdown()
