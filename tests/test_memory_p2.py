"""P2 工具与确定性兜底测试：detect / manager.autosave / build_memory_tools 工具。"""

from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.detect import autosave_clause, wants_autosave
from app.llm.memory.manager import MemoryManager
from app.llm.memory.store import owner_group, owner_private
from app.llm.memory.tool import build_memory_tools


class FakeConfig:
    def __init__(self, data=None):
        self.data = dict(DEFAULT_LLM_CONFIG)
        self.data.update({"memory_enable": True, "experimental_long_term_memory": True})
        if data:
            self.data.update(data)

    def get(self, key, default=None):
        if key in self.data and self.data[key] is not None:
            return self.data[key]
        return default


class FakeRuntime:
    def __init__(self, data=None, bot_id="botmem"):
        self.bot_id = bot_id
        self.config = FakeConfig(data)
        self.memory = MemoryManager(self)
        self.llm_tools = None
        self.skills = None


# ---- detect ----
def test_autosave_clause_cases():
    assert autosave_clause("记住我喜欢喝美式") == "我喜欢喝美式"
    assert autosave_clause("帮我记住我叫小明") == "我叫小明"
    assert autosave_clause("我是前端工程师") == "前端工程师"
    assert autosave_clause("还记得我吗？") == ""
    assert autosave_clause("你记住了吗") == ""
    assert autosave_clause("今天天气不错") == ""
    # 疑问句/问题误拦截：问“我喜欢什么”不得存成“什么”
    assert autosave_clause("我喜欢什么") == ""
    assert wants_autosave("记住我住在上海") is True
    assert wants_autosave("在吗") is False


# ---- manager.autosave ----
async def test_autosave_writes_dedup_and_shutdown():
    rt = FakeRuntime()
    mgr = rt.memory
    assert mgr.autosave("private_5", "5", "记住我喜欢喝美式") == "user_5"
    assert mgr.store.count_by_owner(owner_private(5)) == 1
    # 同内容再次触发 → 去重（仍 1 条）
    mgr.autosave("private_5", "5", "记住我喜欢喝美式")
    assert mgr.store.count_by_owner(owner_private(5)) == 1
    # 疑问句不入库
    assert mgr.autosave("private_5", "5", "还记得我吗？") is None
    # 场景关 → 不入库
    assert FakeRuntime(data={"memory_private_enable": False}).memory.autosave(
        "private_5", "5", "记住我喜欢喝美式"
    ) is None
    # 确定性兜底关 → 不入库
    rt2 = FakeRuntime(data={"memory_save_deterministic": False})
    assert rt2.memory.autosave("private_5", "5", "记住我喜欢喝美式") is None


# ---- build_memory_tools ----
def test_build_memory_tools_returns_three():
    rt = FakeRuntime()
    tools = build_memory_tools(rt, "private_5", "5", True)
    names = [t.name for t in tools]
    # 基础三个 + v2 的 correct/deny
    assert {"memory_save", "memory_recall", "memory_delete",
            "memory_correct", "memory_deny"} <= set(names)


# ---- 工具处理器 ----
def _spec(tools, name):
    return next(t for t in tools if t.name == name)


async def test_tool_save_session_and_group_public():
    rt = FakeRuntime()
    tools = build_memory_tools(rt, "private_5", "5", True)
    out = await _spec(tools, "memory_save").handler(None, {"content": "我喜欢燕麦"})
    assert "success" in out
    assert rt.memory.store.count_by_owner(owner_private(5)) == 1

    # 私聊不能写群公共
    out2 = await _spec(tools, "memory_save").handler(
        None, {"content": "群约", "scope": "group_public"}
    )
    assert "error" in out2

    # 群聊写群公共
    rt2 = FakeRuntime()
    gtools = build_memory_tools(rt2, "group_9", "1", False)
    out3 = await _spec(gtools, "memory_save").handler(
        None, {"content": "周五不开会", "scope": "group_public"}
    )
    assert "success" in out3
    assert rt2.memory.store.count_by_owner(owner_group("9")) == 1


async def test_tool_save_user_scope_requires_config():
    rt = FakeRuntime()  # cross_group=False 默认
    tools = build_memory_tools(rt, "private_5", "5", True)
    out = await _spec(tools, "memory_save").handler(
        None, {"content": "跨群画像", "scope": "user"}
    )
    assert "error" in out and "跨群" in out

    rt2 = FakeRuntime(data={"memory_user_cross_group": True})
    tools2 = build_memory_tools(rt2, "private_5", "5", True)
    out2 = await _spec(tools2, "memory_save").handler(
        None, {"content": "我喜欢喝茶", "scope": "user"}
    )
    assert "success" in out2
    assert rt2.memory.store.count_by_owner(owner_private(5)) == 1


async def test_tool_recall_and_delete_visible_scope():
    rt = FakeRuntime()
    tools = build_memory_tools(rt, "private_5", "5", True)
    rt.memory.store.upsert_fact("我喜欢燕麦拿铁", owner_private(5))

    out = await _spec(tools, "memory_recall").handler(None, {"query": "燕麦"})
    assert "燕麦拿铁" in out

    out2 = await _spec(tools, "memory_delete").handler(None, {"query": "燕麦"})
    assert "删除 1 条" in out2
    assert rt.memory.store.count_by_owner(owner_private(5)) == 0


async def test_tool_recall_group_public_but_not_others_profile():
    rt = FakeRuntime()
    tools = build_memory_tools(rt, "group_9", "1", False)
    rt.memory.store.upsert_fact("群公共：周五不开会", owner_group("9"))
    rt.memory.store.upsert_fact("小明喜欢美式", "user_2@group_9")
    out = await _spec(tools, "memory_recall").handler(None, {"query": "开会"})
    assert "周五不开会" in out
    # 他人画像在未开启提及扩展前不可见（P3 处理）
    out2 = await _spec(tools, "memory_recall").handler(None, {"query": "小明"})
    assert "未找到" in out2

