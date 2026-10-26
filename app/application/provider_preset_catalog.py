"""内置 Provider 预设目录（模板，不落库）。

预设规则抄自 DSH（DeepSeek Harness）随包内置的 pi-ai provider catalog：
每个条目只描述「连哪里 / 说哪种协议 / 用哪个环境变量 / 常见模型」，本身不是
数据库记录——用户点「内置预设 → 添加」时才由
``ProviderPresetService.import_builtin_preset`` 落成一条真实的连接预设。

收录口径：

- 只收 QQbotNext6 适配器（``openai`` / ``anthropic`` / ``gemini``）能直接对话的
  端点；DSH 里需要专用协议的服务（openai-responses、bedrock、mistral、pi-messages
  等）不收，收进来也发不出去；
- ``api_base`` 按各家「真正拼出 /chat/completions、/v1/messages、:generateContent
  的基地址」写，别写完整端点（适配器会自己补路径）；
- ``env`` 是 DSH / pi-ai 约定的环境变量名，方便用户照着自己设 key；
- ``models`` 只是建预设时展示的常用模型名，**不建模型实例**——真实可用模型交给
  预设页的「拉取模型」（``/models``）按账号返回。
"""

from __future__ import annotations

import copy

#: QQbotNext6 已注册的适配器类型（见 ``app/agent/providers/__init__.py``）
OPENAI = "openai"
ANTHROPIC = "anthropic"
GEMINI = "gemini"

#: OpenCode 的 OpenAI 兼容端点要求带会话头；本项目没有请求级 session id，
#: 用固定值让上游把请求归到同一个「会话」。
_OPENCODE_HEADERS = {"x-opencode-session": "qqbotnext6"}

BUILTIN_PROVIDER_PRESETS: tuple[dict, ...] = (
    # ── OpenCode（Zen / Go 订阅）─────────────────────────────────
    {
        "key": "opencode-go",
        "name": "OpenCode Go",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "OpenCode",
        "api_base": "https://opencode.ai/zen/go/v1",
        "env": "OPENCODE_API_KEY",
        "headers": dict(_OPENCODE_HEADERS),
        "models": ["deepseek-v4.1-flash", "glm-5.3", "kimi-k3", "qwen3.8-max"],
        "note": "OpenCode Go 订阅套餐；DSH 里写 inference/go/v1，本项目用 Zen 控制台的同名端点。",
        "docs": "https://opencode.ai/docs/zen/",
    },
    {
        "key": "opencode",
        "name": "OpenCode Zen",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "OpenCode",
        "api_base": "https://opencode.ai/zen/v1",
        "env": "OPENCODE_API_KEY",
        "headers": dict(_OPENCODE_HEADERS),
        "models": ["deepseek-v4.1-flash-free", "glm-5.3-flash", "kimi-k2.7-code"],
        "note": "OpenCode Zen 按量计费端点；免费模型带 -free 后缀。",
        "docs": "https://opencode.ai/docs/zen/",
    },
    # ── 国内 Coding Plan / 官方 API ──────────────────────────────
    {
        "key": "deepseek",
        "name": "DeepSeek",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://api.deepseek.com",
        "env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "note": "DeepSeek 官方 API；适配器会自动补 /v1。",
        "docs": "https://api-docs.deepseek.com/",
    },
    {
        "key": "zai",
        "name": "Z.AI Coding Plan",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://api.z.ai/api/coding/paas/v4",
        "env": "ZAI_API_KEY",
        "models": ["glm-5.3", "glm-5.2", "glm-4.7"],
        "note": "智谱 Z.AI 国际站 Coding Plan；路径带 /v4，适配器不会再加 /v1。",
        "docs": "https://docs.z.ai/",
    },
    {
        "key": "zai-coding-cn",
        "name": "Z.AI Coding Plan（国内）",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://open.bigmodel.cn/api/coding/paas/v4",
        "env": "ZAI_CODING_CN_API_KEY",
        "models": ["glm-5.3", "glm-4.6v"],
        "note": "智谱开放平台（bigmodel.cn）Coding Plan。",
        "docs": "https://open.bigmodel.cn/dev/api",
    },
    {
        "key": "kimi-coding",
        "name": "Kimi For Coding",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://api.kimi.com/coding/v1",
        "env": "KIMI_API_KEY",
        "models": ["kimi-for-coding", "k3", "k3-256k"],
        "note": "Kimi 会员 Coding 套餐端点。",
        "docs": "https://platform.moonshot.cn/docs/",
    },
    {
        "key": "moonshotai-cn",
        "name": "Moonshot AI（国内）",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://api.moonshot.cn/v1",
        "env": "MOONSHOT_API_KEY",
        "models": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.6"],
        "note": "月之暗面（moonshot.cn）按量计费。",
        "docs": "https://platform.moonshot.cn/docs/",
    },
    {
        "key": "qwen-token-plan-cn",
        "name": "Qwen Token Plan（国内）",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "env": "QWEN_TOKEN_PLAN_CN_API_KEY",
        "models": ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "deepseek-v4.1-flash"],
        "note": "阿里云百炼 Token Plan（北京）。",
        "docs": "https://help.aliyun.com/zh/model-studio/",
    },
    {
        "key": "qwen-token-plan",
        "name": "Qwen Token Plan（国际）",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "env": "QWEN_TOKEN_PLAN_API_KEY",
        "models": ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-max"],
        "note": "阿里云百炼 Token Plan（新加坡）。",
        "docs": "https://help.aliyun.com/zh/model-studio/",
    },
    {
        "key": "xiaomi-token-plan-cn",
        "name": "Xiaomi MiMo Token Plan（国内）",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国内",
        "api_base": "https://token-plan-cn.xiaomimimo.com/v1",
        "env": "XIAOMI_TOKEN_PLAN_CN_API_KEY",
        "models": ["mimo-v2.6-pro", "mimo-v2.6-flash"],
        "note": "小米 MiMo Token Plan。",
        "docs": "https://platform.xiaomimimo.com/",
    },
    {
        "key": "minimax-cn",
        "name": "MiniMax（国内）",
        "provider": ANTHROPIC,
        "protocol": "anthropic-messages",
        "category": "国内",
        "api_base": "https://api.minimaxi.com/anthropic",
        "env": "MINIMAX_CN_API_KEY",
        "models": ["MiniMax-M3", "MiniMax-M3.1-Flash"],
        "note": "MiniMax 的 Anthropic 兼容端点，走 anthropic 适配器。",
        "docs": "https://platform.minimaxi.com/document/",
    },
    # ── 国际官方 API ────────────────────────────────────────────
    {
        "key": "anthropic",
        "name": "Anthropic",
        "provider": ANTHROPIC,
        "protocol": "anthropic-messages",
        "category": "国际",
        "api_base": "https://api.anthropic.com",
        "env": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-5", "claude-haiku-5-5", "claude-opus-5"],
        "note": "Claude 官方 Messages API。",
        "docs": "https://docs.anthropic.com/",
    },
    {
        "key": "google",
        "name": "Google Gemini",
        "provider": GEMINI,
        "protocol": "google-generative-ai",
        "category": "国际",
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "env": "GEMINI_API_KEY",
        "models": ["gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-2.5-flash"],
        "note": "Gemini 官方 Generative Language API。",
        "docs": "https://ai.google.dev/gemini-api/docs",
    },
    {
        "key": "openai",
        "name": "OpenAI",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://api.openai.com/v1",
        "env": "OPENAI_API_KEY",
        "models": ["gpt-5.2", "gpt-5.4-mini", "gpt-4.1"],
        "note": "OpenAI 官方 Chat Completions；DSH 对 OpenAI 用 Responses 协议，这里走兼容的 chat 端点。",
        "docs": "https://platform.openai.com/docs",
    },
    {
        "key": "openrouter",
        "name": "OpenRouter",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
        "models": ["anthropic/claude-sonnet-4.5", "deepseek/deepseek-v4.1-flash", "z-ai/glm-5.3"],
        "note": "一个 key 转接多家模型；模型名要带厂牌前缀。",
        "docs": "https://openrouter.ai/docs",
    },
    {
        "key": "minimax",
        "name": "MiniMax（国际）",
        "provider": ANTHROPIC,
        "protocol": "anthropic-messages",
        "category": "国际",
        "api_base": "https://api.minimax.io/anthropic",
        "env": "MINIMAX_API_KEY",
        "models": ["MiniMax-M3", "MiniMax-M3.1-Flash"],
        "note": "MiniMax 国际站 Anthropic 兼容端点。",
        "docs": "https://platform.minimax.io/docs",
    },
    # ── 聚合 / 云推理 ───────────────────────────────────────────
    {
        "key": "groq",
        "name": "Groq",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://api.groq.com/openai/v1",
        "env": "GROQ_API_KEY",
        "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile"],
        "note": "Groq 低延迟推理。",
        "docs": "https://console.groq.com/docs",
    },
    {
        "key": "cerebras",
        "name": "Cerebras",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://api.cerebras.ai/v1",
        "env": "CEREBRAS_API_KEY",
        "models": ["gpt-oss-120b", "qwen-3.8-27b"],
        "note": "Cerebras 高速推理。",
        "docs": "https://inference-docs.cerebras.ai/",
    },
    {
        "key": "nvidia",
        "name": "NVIDIA NIM",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://integrate.api.nvidia.com/v1",
        "env": "NVIDIA_API_KEY",
        "models": ["deepseek-ai/deepseek-v4.1-flash", "moonshotai/kimi-k2.6"],
        "note": "NVIDIA build.nvidia.com 托管模型。",
        "docs": "https://build.nvidia.com/",
    },
    {
        "key": "together",
        "name": "Together AI",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://api.together.ai/v1",
        "env": "TOGETHER_API_KEY",
        "models": ["deepseek-ai/DeepSeek-V4.1-Flash", "moonshotai/Kimi-K3"],
        "note": "Together 托管开源模型。",
        "docs": "https://docs.together.ai/",
    },
    {
        "key": "huggingface",
        "name": "Hugging Face Router",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://router.huggingface.co/v1",
        "env": "HF_TOKEN",
        "models": ["deepseek-ai/DeepSeek-V4.1-Flash", "moonshotai/Kimi-K3"],
        "note": "Hugging Face Inference Providers 路由。",
        "docs": "https://huggingface.co/docs/inference-providers",
    },
    {
        "key": "fireworks",
        "name": "Fireworks AI",
        "provider": OPENAI,
        "protocol": "openai-completions",
        "category": "国际",
        "api_base": "https://api.fireworks.ai/inference/v1",
        "env": "FIREWORKS_API_KEY",
        "models": ["accounts/fireworks/models/glm-5p3", "accounts/fireworks/models/kimi-k3"],
        "note": "Fireworks 托管模型；模型名带 accounts/fireworks/ 前缀。",
        "docs": "https://docs.fireworks.ai/",
    },
)


def list_builtin_presets() -> list[dict]:
    """返回内置预设模板列表（深拷贝，调用方可随意改）。"""
    return copy.deepcopy(list(BUILTIN_PROVIDER_PRESETS))


def get_builtin_preset(key: str) -> dict | None:
    """按稳定的 ``key`` 取一条内置预设模板；不存在返回 ``None``。"""
    target = str(key or "").strip()
    if not target:
        return None
    for preset in BUILTIN_PROVIDER_PRESETS:
        if preset["key"] == target:
            return copy.deepcopy(preset)
    return None


__all__ = ["BUILTIN_PROVIDER_PRESETS", "list_builtin_presets", "get_builtin_preset"]
