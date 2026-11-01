"""输出模块的配置 Schema（流式传输参数 + 发言通道的气泡约束）。

两个正交维度里，本模块只提供**实现参数**：
- 传输方式：`stream_output`（流式 / 一次拿全）——唯一开关，不再有 `mode` 三选一；
- 发言通道：由回复模式决定（不在这里），模块只留 `speech_max_bubbles` 等气泡约束。
"""

SCHEMA = {
    "stream_output": {
        "type": "boolean",
        "label": "流式输出",
        "description": "开启=边收边发；关闭=一次拿全再发。只影响请求方式，不影响是否发言。",
        "default": False,
    },
    "stream_send_by_sentence": {
        "type": "boolean",
        "label": "按句子发送",
        "description": "按句切分后逐条发送",
        "default": True,
    },
    "stream_sentence_max_length": {
        "type": "number",
        "label": "单句最大长度",
        "description": "流式输出时每条消息的最大字符数",
        "default": 200,
    },
    "stream_keep_order": {
        "type": "boolean",
        "label": "严格按顺序发送",
        "description": "保持生成顺序逐条发送（建议开启）",
        "default": True,
    },
    "stream_send_pool_enabled": {
        "type": "boolean",
        "label": "启用流式消息池",
        "description": "开启后流式句子进入有序消息池，按频率发送",
        "default": False,
    },
    "stream_send_interval_mode": {
        "type": "select",
        "label": "发送间隔模式",
        "description": "固定间隔 = 恒定节奏；按字数曲线 = 长句多等；不等待 = 连续发送。",
        "default": "none",
        "options": {"none": "不等待", "fixed": "固定间隔", "length_curve": "按字数曲线"},
    },
    "stream_send_interval_base_ms": {
        "type": "number",
        "label": "基础发送间隔(ms)",
        "description": "固定间隔或曲线的基础延迟",
        "default": 0,
    },
    "stream_send_interval_min_ms": {
        "type": "number",
        "label": "最短发送间隔(ms)",
        "description": "最终延迟的下限",
        "default": 0,
    },
    "stream_send_interval_max_ms": {
        "type": "number",
        "label": "最长发送间隔(ms)",
        "description": "最终延迟的上限",
        "default": 0,
    },
    "stream_send_curve": {
        "type": "select",
        "label": "发送曲线",
        "description": "按字数计算延迟的曲线",
        "default": "none",
        "options": {
            "none": "无",
            "sqrt": "平方根",
            "linear": "线性",
            "short_long": "按长短句",
        },
    },
    "stream_send_curve_k": {
        "type": "number",
        "label": "曲线强度",
        "description": "曲线系数 k，越大延迟随字数增长越明显",
        "default": 1.0,
    },
    "stream_short_message_length": {
        "type": "number",
        "label": "短句阈值(字)",
        "description": "按字数曲线模式下判定短句的阈值。",
        "default": 10,
    },
    "stream_short_message_delay_ms": {
        "type": "number",
        "label": "短句发送延迟(ms)",
        "description": "按字数曲线模式下短句的延迟。",
        "default": 300,
    },
    "stream_long_message_delay_ms": {
        "type": "number",
        "label": "长句发送延迟(ms)",
        "description": "按字数曲线模式下长句的延迟。",
        "default": 1200,
    },
    "stream_send_prefix": {
        "type": "string",
        "label": "发送前缀",
        "description": "每条流式消息发送前追加的文本",
        "default": "",
    },
    "stream_send_suffix": {
        "type": "string",
        "label": "发送后缀",
        "description": "每条流式消息发送后追加的文本",
        "default": "",
    },
    "stream_send_max_queue": {
        "type": "number",
        "label": "消息池最大排队数",
        "description": "队列满时的处理策略见下",
        "default": 20,
    },
    "stream_queue_full_policy": {
        "type": "select",
        "label": "队列满策略",
        "description": "消息池满时的行为",
        "default": "backpressure",
        "options": {
            "backpressure": "等待（背压）",
            "drop_newest": "丢弃最新的",
            "drop_oldest": "丢弃最旧的",
        },
    },
    "stream_flush_on_finish": {
        "type": "boolean",
        "label": "结束后立即清空",
        "description": "流结束后是否忽略剩余间隔立即发送剩余消息",
        "default": False,
    },
    # 发言通道由回复模式决定；模块只保留气泡约束（催发言/补救轮的键已删除：
    # 参与度模式下"沉默"是合法结局，不该再催它开口）。
    "speech_max_bubbles": {
        "type": "number",
        "label": "单轮最多几条",
        "description": "一轮对话里最多发几个气泡",
        "default": 5,
    },
    "speech_max_bubble_chars": {
        "type": "number",
        "label": "单条字数上限",
        "description": "单个气泡的最大字符数",
        "default": 200,
    },
}
