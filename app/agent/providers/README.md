> 本文只负责：说明 `app/agent/providers/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/providers/` —— 各 LLM 供应商适配：一类能力一个文件，全部可替换。

## 包含

| 文件 | 职责 |
|---|---|
| `base.py` | 供应商抽象基类 |
| `openai_compat.py` | OpenAI 兼容协议（含 DeepSeek / 各家兼容端点） |
| `anthropic.py` | Anthropic Messages API |
| `gemini.py` | Google Gemini |
| `policy.py` | 请求策略：重试 / 回退链 / 超时 / 超参 |
| `invoker.py` | 统一调用入口（非流式 + 流式 + 回退） |
| `runtime_manager.py` | 运行时实例管理 |
| `modalities.py` | 能力查询：这个模型支持图片/语音吗 |
| `embedding.py` | 向量化模型 |
| `rerank.py` | 重排模型 |
| `stt.py` | 语音转文字 |
| `tts.py` | 文字转语音 |

## 注意

**这是全库最规范的部分，是其它包的目标范本，不是改造对象。**
新代码照它的样子写：一个能力一个文件、有抽象基类、策略与实现分离。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
