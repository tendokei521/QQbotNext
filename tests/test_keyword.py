"""共享关键词匹配库测试（覆盖三模块原语义：and/or/text/at/selfid/userid）。"""

from app.modules import match_keywords


def test_text_match():
    assert match_keywords({"text": "签到"}, ["今天签到吗"]) == ["签到"]
    assert match_keywords({"text": "签到"}, ["今天吃什么"]) == []


def test_and_all_required():
    cfg = {"and": [{"text": "A"}, {"text": "B"}]}
    assert match_keywords(cfg, ["xxAxx", "yyByy"])  # 两个都命中
    assert match_keywords(cfg, ["xxAxx", "其他"]) == []


def test_or_any_with_min_value():
    cfg = {"or": [{"text": "A"}, {"text": "B"}], "value": 2}
    assert match_keywords(cfg, ["A", "B"])  # 命中 2 个 ≥ value
    assert match_keywords(cfg, ["A", "其他"]) == []  # 命中 1 < value


def test_at_all_and_self():
    cfg_all = {"at": "all"}
    assert match_keywords(cfg_all, [], atlist=["100", "200"])  # 纯 @ 也能匹配
    cfg_self = {"at": "self"}
    assert match_keywords(cfg_self, [], atlist=["123"], self_id=123)
    assert match_keywords(cfg_self, [], atlist=["456"], self_id=123) == []


def test_selfid_userid_substitution():
    cfg = {"text": "selfid"}
    assert match_keywords(cfg, ["10001"], self_id=10001) == ["10001"]
    cfg2 = {"text": "userid"}
    assert match_keywords(cfg2, ["20002"], user_id=20002) == ["20002"]
