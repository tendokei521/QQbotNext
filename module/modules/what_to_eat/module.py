"""模块声明：今天吃什么。

精确匹配：
- 吃什么
- 是啊，吃什么

命中后随机推荐一个外卖/食物，或随机回一句“是啊，吃什么”。
"""

import random

from app.modules import BaseModule, module_hook
from .config_schema import SCHEMA

DEFAULT_FOODS = [
    "麻辣烫", "炸鸡", "汉堡", "披萨", "寿司", "螺蛳粉",
    "黄焖鸡", "盖浇饭", "烤肉饭", "煲仔饭", "卤肉饭", "猪脚饭",
    "鸡公煲", "冒菜", "米线", "酸辣粉", "炒饭", "炒面",
    "饺子", "小笼包", "生煎", "肉夹馍", "凉皮", "烤冷面",
    "手抓饼", "煎饼果子", "小龙虾", "烧烤", "火锅", "麻辣香锅",
    "咖喱饭", "蛋包饭", "牛肉面", "兰州拉面", "重庆小面", "热干面",
    "炸酱面", "拌面", "意面", "沙拉", "轻食", "关东煮",
    "炸串", "铁板烧", "石锅拌饭", "韩式炸鸡", "日式拉面", "泰式咖喱",
    "越南河粉", "奶茶", "甜品",
]

DEFAULT_TEMPLATES = [
    "要不吃{food}？",
    "今天吃{food}吧！",
    "{food}也不错",
    "我投{food}一票！",
    "点一份{food}吧，绝对不会错",
    "要不试试{food}？换换口味",
    "今天适合吃{food}！",
    "就吃{food}吧，别纠结了",
    "{food}安排上！",
    "我推荐{food}，外卖刚好",
]

TRIGGER_WORDS = ("吃什么", "是啊，吃什么")
ECHO_REPLY = "是啊，吃什么"


class Module(BaseModule):
    name = "今天吃什么"
    sign = "WhatToEat"
    description = "遇到“吃什么”时随机推荐食物"
    permission = "everyone"
    subscribe = ("message_group", "message_private")
    default_config = {
        "enable": True,
        "scope": "all",
        "foods": DEFAULT_FOODS,
        "echo_enabled": True,
        "echo_probability": 0.2,
        "reply_templates": DEFAULT_TEMPLATES,
    }
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    @module_hook("message_private", order=10)
    async def handle(self, event):
        if not self.config.get("enable", True):
            return

        scope = self.config.get("scope", "all")
        if scope == "group" and event.event_type != "message_group":
            return
        if scope == "private" and event.event_type != "message_private":
            return

        text = event.text.strip()
        if text not in TRIGGER_WORDS:
            return

        await self._reply(event)
        event.llm.stop()

    async def _reply(self, event):
        foods = list(self.config.get("foods", []) or DEFAULT_FOODS)
        if not foods:
            foods = DEFAULT_FOODS

        templates = list(self.config.get("reply_templates", []) or DEFAULT_TEMPLATES)
        if not templates:
            templates = DEFAULT_TEMPLATES

        echo_enabled = self.config.get("echo_enabled", True)
        echo_probability = float(self.config.get("echo_probability", 0.2) or 0)

        if echo_enabled and random.random() < echo_probability:
            await event.reply(ECHO_REPLY)
            return

        food = random.choice(foods)
        template = random.choice(templates)
        await event.reply(template.replace("{food}", food))
