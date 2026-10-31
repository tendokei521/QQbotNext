# Prompt 装配分析：三选一方案为何从未生效

> 2026-10-31 · 分析对象：`logs/user.log` 里 13:56:09 那条 `[Prompt] group_466052056` 的完整装配结果
> （新代码已能打出整份 messages，见 `app/agent/loop/turn_metrics.py::log_debug_prompt_if_enabled`）。
> 结论：**用户问题里的两个症状由同一个代码缺陷引起**，外加一处数据层面的双份人格。

## 1. 这次实际装进去的 prompt（证据）

从 `logs/user.log` 抽出的 messages（保存为 `logs/review/prompt-dump.json`）：

| # | role | 内容 |
|---|---|---|
| 0 | system | 人格「kei」（1665 字，与 `role_presets.7f951b8bcbd4` **逐字相同**） |
| 1 | system | `### 定时任务`（旧常量 `SCHEDULE_INSTRUCTION`） |
| 2 | system | `### 主动性`（旧常量 `PROACTIVE_LINES`） |
| 3 | system | `（更早的对话没有全部列出…）` 历史提示 |
| 4…16 | user/assistant | 会话历史 + 本轮用户消息 |

**缺席的块**：行为规则段（安全规则 / 工作方式 / 表达 / 汇报禁令）、可用表情提示、消息格式说明。

对照实验（`logs/review/prompt-block-compare.json`，同一份配置、只换传入的 config 对象）：

| config 形态 | 装配出的 system 块（role:字符数） |
|---|---|
| 普通 dict | persona:7 · **behavior:3540** · **emoji:211** · schedule:396 · proactive:514 · **meta:227** |
| 运行时的 `ChainedConfig`（实际路径） | persona:7 · schedule:396 · proactive:**1156** · meta:**251** |

即：**3540 字的行为规则段整段消失**，proactive/meta 换成了旧常量版本。

## 2. 根因：`presets.resolve()` 用 `in config` 判断"有没有这个键"

`68e9b1c9`（2026-10-29 19:29，「提示词方案改为必选三选一」）把

```python
value = str(config.get("prompt_preset", "") or "")
```

改成了

```python
has_key = "prompt_preset" in config      # ← 问题在这里
value = str(config.get("prompt_preset", "") or "") if has_key else ""
```

而运行时传给装配器的 `config` 不是 dict：

- 聊天路径：`app/agent/loop/prompt_prep.py:248` → `policy_cfg = policy_view(runtime) or config` → `ChainedConfig`
- 主动消息 / 定时任务：`config=getattr(runtime, "config", None)` → `AgentConfig`

这两个对象**只实现 `get()`**，没有 `__contains__`。于是：

```
TypeError: argument of type 'ChainedConfig' is not iterable
```

被 `except Exception: return None` 吞掉 → 当成"配置里没有这个键" → `resolve()` 返回 `None`。

实测（`venv\Scripts\python.exe` 直接跑）：

```
'prompt_preset' in ChainedConfig(...)  -> TypeError
presets.resolve({'prompt_preset': 'chat'})   -> PromptPreset(key='chat', ...)
presets.resolve(AgentConfig({'prompt_preset': 'chat'}))   -> None
presets.resolve(ChainedConfig(...))                        -> None
```

`None` 方案的连锁反应（`app/agent/assembly/builtin_sections.py` 五处都吃它）：

| 段 | 后果 |
|---|---|
| `build_behavior` | `preset is None` 且 `assembly_profile=legacy` → **返回 []，行为段整段没有** |
| `build_emoji_hint` | 需要方案或 `qq_agent` 档 → 缺失 |
| `message_meta_instruction` | 走 legacy 分支；本账号 `meta_instruction_mode=legacy` 且 `llm_message_meta` 模块被禁用 → 连旧说明也没有 |
| `build_schedule_instruction` | 用常量 `SCHEDULE_INSTRUCTION`，不是方案文案 |
| `build_proactive` | 用常量 `PROACTIVE_LINES`，不是方案文案 |

同一个 `None` 还让 `prompt_preset_service.get_override(preset.key)`（「设置 → 编辑系统提示词」保存的三套文案）**永远读不到**——因为 override 只在 `preset is not None` 分支里取。

**所以"三选一"（聊天 / 角色扮演 / 助手）在当前运行时完全无效：三个选项装配出同一份 prompt。**

## 3. 为什么测试没抓到

`tests/agent/assembly/test_prompt_presets.py` 全部用**普通 dict** 构造 config：

```python
PromptRequest(config={**base, "prompt_preset": "chat"}, user_text="hi")
```

dict 支持 `in`，所以永远是绿的。真实路径的配置对象（`AgentConfig` / `ChainedConfig`）没有任何用例覆盖。

## 4. 第二处问题：人格有两份，内容不一致

账号 `123` 的现状（`bindings` 表）：

| 字段 | 值 |
|---|---|
| `persona_preset_id` | `7f951b8bcbd4`（「kei」，**1665 字**，10-29 20:09 更新） |
| `system_prompt`（账号内副本） | **1829 字**，等于旧预设 `fef0de8dbd1f`（「123角色设定」，10-28 17:46 更新） |

两份人格差 13 行（`logs/review/persona-diff.txt`），旧副本**多出**这些约束：

```
- 禁止使用任何 Markdown 符号…
【特别注意】
- 整段回复禁止使用任何 Markdown 符号…
- 回复字数必需在50字以内
- 不要使用括号描述动作
- 对话中默认称呼对方用户为『老师』
```

装配规则是 `build_persona`：**`persona_preset_id` 命中预设时用预设内容，只有预设缺失才回退 `config.system_prompt`**。于是：

- 现在装的是「kei」（**不含**上面 50 字/Markdown/称呼「老师」这些约束）；
- 用户在"账号级 system_prompt"里加的限制**不会生效**（预设优先，它只是兜底副本）；
- 反过来，一旦预设被删/绑定被清空，这份**旧文本会立刻顶上**——这就是"过时提示词还在被组装"的机制性来源；
- WebUI 若有地方直接渲染 `config.system_prompt`，显示的就是旧内容，与真实装配结果对不上。

## 5. 修复建议

### 5.1 必改（一行逻辑，能立刻让三选一生效）

`app/agent/assembly/presets.py::resolve`：用哨兵代替 `in`，兼容任何只有 `get()` 的配置对象。

```python
_MISSING = object()

def resolve(config: Any) -> PromptPreset | None:
    if config is None or not hasattr(config, "get"):
        return None
    try:
        value = config.get("prompt_preset", _MISSING)
    except Exception:
        return None
    if value is _MISSING:
        return None
    return _BY_KEY.get(str(value or "").strip().lower()) or CHAT
```

> 注意：`AgentConfig.get` 对 `DEFAULT_LLM_CONFIG` 里的键永远有值（`prompt_preset='chat'`），所以
> "键不存在"分支在真实路径不可达——这符合产品口径（产品路径永远带该键），不需要额外处理。

### 5.2 补契约测试（这次能挡住回归的那条）

用"只有 get() 的配置对象"跑一遍装配，断言存在行为段：

```python
class _GetOnlyConfig:
    def __init__(self, data): self._d = dict(data)
    def get(self, key, default=None): return self._d.get(key, default)

req = PromptRequest(config=_GetOnlyConfig({"prompt_preset": "chat"}), user_text="hi")
assert any("【安全规则" in str(m["content"]) for m in PromptAssembler().build(req))
```

### 5.3 人格双份的收敛（可选，需产品拍板）

三种口径选一：

1. **单一来源**：只认 `persona_preset_id`，保存时不再往 `config.system_prompt` 写副本（旧副本仅作迁移源，用后删除）；
2. **保持双份但同步**：应用人格时同时写两份，并在编辑预设时同步所有绑定了该预设的账号副本；
3. **保持现状但在 UI 明示**：账号级 `system_prompt` 标注"仅在未选择人格预设时生效"。

推荐 1（与 `docs/identity-and-capability-console.md` 的"身份单一来源"一致），迁移时把现存副本与绑定预设做一次对账，不一致的以预设为准并记日志。

## 6. 影响面

- 所有账号、所有会话：**行为规则段（含"不越权 / 不泄露 / 怎么发言"）长期缺失**；若哪天开启 `speech_via_tool`（发言通道），模型不会被要求"文本只是思考、要说话必须调用工具"，会表现为整轮静默——这正是该段存在的理由（见 `presets.py` 顶部"三条纪律"第 2 条）。
- 设置页「编辑系统提示词」保存的三套文案：保存成功但**永不生效**。
- `assembly_profile=qq_agent` 的用户不受影响（那条分支不看方案），所以问题只在默认 legacy 档暴露——也解释了为什么没人在更早发现。
