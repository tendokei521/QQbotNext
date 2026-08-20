"""P1 注入链路测试：build_messages 记忆块 / 召回评分 / manager 注入 / #chat memory 命令。"""

from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.commands import handle_memory_command
from app.llm.memory.manager import MemoryManager, scope_owners
from app.llm.memory.recall import rank, render_block
from app.llm.memory.store import MemoryStore, owner_group, owner_group_member, owner_private
from app.llm.prompt import build_messages


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
        self.memory = None


def _manager(runtime=None, bot_id="botmem", data=None):
    rt = runtime or FakeRuntime(data=data, bot_id=bot_id)
    rt.memory = MemoryManager(rt)
    return rt, rt.memory


# ---- build_messages ----
def test_build_messages_injects_memory_block_only_when_present():
    base = dict(system_prompt="sys", user_text="hi", with_schedule_instruction=False)
    no = build_messages(**base)
    assert not any("长期记忆" in m["content"] for m in no)
    with_block = build_messages(**base, memory_text="### 长期记忆\n- 我喜欢美式")
    contents = [m["content"] for m in with_block]
    assert "### 长期记忆\n- 我喜欢美式" in contents
    assert contents.index("### 长期记忆\n- 我喜欢美式") < contents.index("hi")


# ---- rank / render_block ----
def test_rank_cjk_gram_recall():
    st = MemoryStore("botmem")
    st.upsert_fact(
        "小明喜欢喝美式咖啡", owner_group_member("9", "2"),
        keywords="小明 咖啡 美式", importance=0.8,
    )
    st.upsert_fact("群公共约定：周五不开会", owner_group("9"))
    hits = rank(
        st,
        owners=scope_owners("group_9", "1"),
        query="小明喜欢什么",
        mention_owners=[owner_group_member("9", "2")],
        limit=5,
    )
    assert any("美式咖啡" in r["content"] for r in hits)
    block = render_block(hits)
    assert block.startswith("###")
    assert "美式咖啡" in block
    st.close()


def test_rank_private_keyword():
    st = MemoryStore("botmem")
    st.upsert_fact("我住在上海", owner_private(5), keywords="上海 住")
    hits = rank(st, owners=[owner_private(5)], query="上海", limit=5)
    assert len(hits) == 1 and hits[0]["content"] == "我住在上海"
    no = rank(st, owners=[owner_private(5)], query="火星", limit=5)
    assert len(no) == 0
    st.close()


# ---- scope_owners 隔离 ----
def test_scope_owners_isolation():
    assert scope_owners("private_5") == ["user_5"]
    assert scope_owners("group_9") == ["group_9"]
    assert scope_owners("group_9", user_id="1") == ["group_9", "user_1@group_9"]


# ---- manager 注入 ----
def test_recall_block_injects_and_toggles():
    rt, mgr = _manager()
    mgr.store.upsert_fact("我喜欢燕麦拿铁", owner_private(5))
    block = mgr.recall_block("private_5", user_id="5")
    assert "燕麦拿铁" in block

    rt2, mgr2 = _manager(data={"memory_group_enable": False})
    mgr2.store.upsert_fact("群记忆", owner_group("9"))
    assert mgr2.recall_block("group_9", user_id="1") == ""

    rt3, mgr3 = _manager(data={"memory_enable": False})
    mgr3.store.upsert_fact("私记", owner_private(5))
    assert mgr3.recall_block("private_5", user_id="5") == ""


# ---- #chat memory 命令 ----
async def _dispatch(rt, session_id, user_id, action, is_admin=True, is_private=False):
    sent = []

    async def send(text):
        sent.append(text)

    await handle_memory_command(rt, session_id, user_id, is_admin, is_private, action, send)
    return "\n".join(sent)


async def test_command_list_search_forget_clear():
    rt, mgr = _manager()
    mgr.store.upsert_fact("我喜欢燕麦拿铁", owner_private(5), source="manual")
    mgr.store.upsert_fact("我喜欢喝美式", owner_private(5), source="manual")
    mgr.store.upsert_fact("别人记忆", owner_private(7), source="manual")

    out = await _dispatch(rt, "private_5", "5", "memory list", is_private=True)
    assert "燕麦拿铁" in out and "美式" in out
    assert "别人记忆" not in out

    out2 = await _dispatch(rt, "private_5", "5", "memory search 美式", is_private=True)
    assert "美式" in out2 and "燕麦" not in out2

    out3 = await _dispatch(rt, "private_5", "5", "memory forget 美式", is_private=True)
    assert "删除" in out3
    out4 = await _dispatch(rt, "private_5", "5", "memory list", is_private=True)
    assert "美式" not in out4 and "燕麦" in out4

    out5 = await _dispatch(rt, "private_5", "5", "memory clear", is_private=True)
    assert "0 条" not in out5
    out6 = await _dispatch(rt, "private_5", "5", "memory list", is_private=True)
    assert "无" in out6


async def test_command_group_forget_only_own_layer():
    rt, mgr = _manager()
    mgr.store.upsert_fact("小明喜欢美式", owner_group_member("9", "2"), source="manual")
    mgr.store.upsert_fact("我讨厌香菜", owner_group_member("9", "1"), source="manual")
    mgr.store.upsert_fact("群公共：全体禁烟", owner_group("9"), source="manual")

    out = await _dispatch(rt, "group_9", "1", "memory clear")
    assert "1 条" in out
    assert mgr.store.count_by_owner(owner_group_member("9", "2")) == 1
    assert mgr.store.count_by_owner(owner_group("9")) == 1

    out2 = await _dispatch(rt, "group_9", "1", "memory list")
    assert "群公共：全体禁烟" in out2
    assert "小明喜欢美式" not in out2
    assert "我讨厌香菜" not in out2


async def test_command_when_disabled():
    rt, mgr = _manager(data={"memory_enable": False})
    out = await _dispatch(rt, "private_5", "5", "memory list", is_private=True)
    assert "未启用" in out
