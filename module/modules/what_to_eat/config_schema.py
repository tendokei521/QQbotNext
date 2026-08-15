"""今天吃什么配置表单。"""

SCHEMA = {
    "enable": {
        "type": "boolean",
        "label": "启用模块",
        "description": "是否响应“吃什么”相关消息",
        "default": True,
    },
    "scope": {
        "type": "select",
        "label": "生效范围",
        "description": "群聊 / 私聊 / 全部",
        "default": "all",
        "options": {
            "all": "全部",
            "group": "仅群聊",
            "private": "仅私聊",
        },
    },
    "foods": {
        "type": "string_list",
        "label": "食物列表",
        "description": "每行一个食物，随机推荐",
        "default": [
            "麻辣烫", "炸鸡", "汉堡", "披萨", "寿司", "螺蛳粉",
            "黄焖鸡", "盖浇饭", "烤肉饭", "煲仔饭", "卤肉饭", "猪脚饭",
            "鸡公煲", "冒菜", "米线", "酸辣粉", "炒饭", "炒面",
            "饺子", "小笼包", "生煎", "肉夹馍", "凉皮", "烤冷面",
            "手抓饼", "煎饼果子", "小龙虾", "烧烤", "火锅", "麻辣香锅",
            "咖喱饭", "蛋包饭", "牛肉面", "兰州拉面", "重庆小面", "热干面",
            "炸酱面", "拌面", "意面", "沙拉", "轻食", "关东煮",
            "炸串", "铁板烧", "石锅拌饭", "韩式炸鸡", "日式拉面", "泰式咖喱",
            "越南河粉", "奶茶", "甜品",
        ],
    },
    "echo_enabled": {
        "type": "boolean",
        "label": "允许回复“是啊，吃什么”",
        "description": "开启后有一定概率回旋镖式回复",
        "default": True,
    },
    "echo_probability": {
        "type": "number",
        "label": "回旋镖概率",
        "description": "0~1，回复“是啊，吃什么”的概率",
        "default": 0.2,
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    "reply_templates": {
        "type": "string_list",
        "label": "回复模板",
        "description": "每行一个模板，用 {food} 占位",
        "default": [
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
        ],
    },
}
