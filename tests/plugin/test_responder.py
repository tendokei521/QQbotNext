"""响应权仲裁：候选集合 + 策略 → 决策（纯函数）。"""

from __future__ import annotations

from app.plugin.responder import (
    FALLBACK_CLOSED,
    FALLBACK_OPEN,
    POLICY_ALL,
    POLICY_DEDUPE,
    POLICY_DELEGATE,
    POLICY_LEADER,
    ResponderPolicy,
    decision_as_dict,
    elect,
    normalize_candidates,
    normalize_fallback,
    normalize_policy,
    policy_from_legacy,
)


def _policy(policy=POLICY_ALL, primary=None, fallback=FALLBACK_OPEN):
    return ResponderPolicy(
        capability_id="demo", channel_id="g1", policy=policy,
        primary_account_id=primary, fallback=fallback,
    )


# ==================== 候选规整 ====================

def test_candidates_dedupe_and_keep_order():
    assert normalize_candidates(["b", "a", "b", "", None, "a"]) == ("b", "a")


# ==================== all / dedupe ====================

def test_all_lets_every_candidate_respond():
    decision = elect(_policy(POLICY_ALL), ["a", "b"])

    assert decision.responders == ("a", "b")
    assert decision.reason == "all"
    assert decision.degraded is False


def test_all_with_no_candidates_is_not_an_error():
    decision = elect(_policy(POLICY_ALL), [])

    assert decision.responders == ()
    assert decision.reason == "all"


def test_dedupe_picks_first_candidate():
    decision = elect(_policy(POLICY_DEDUPE), ["b", "a"])

    assert decision.responders == ("b",)
    assert decision.reason == "dedupe"
    assert decision.single == "b"


def test_dedupe_with_single_candidate_still_responds():
    decision = elect(_policy(POLICY_DEDUPE), ["a"])

    assert decision.responders == ("a",)


def test_no_candidates_never_degrades():
    decision = elect(_policy(POLICY_DELEGATE, primary="a"), [])

    assert decision.responders == ()
    assert decision.reason == "no-candidates"
    assert decision.degraded is False


# ==================== delegate ====================

def test_delegate_primary_in_candidates():
    decision = elect(_policy(POLICY_DELEGATE, primary="b"), ["a", "b"])

    assert decision.responders == ("b",)
    assert decision.reason == "primary"
    assert decision.degraded is False


def test_delegate_primary_unavailable_falls_back_open_by_default():
    decision = elect(_policy(POLICY_DELEGATE, primary="ghost"), ["a", "b"])

    assert decision.responders == ("a",)
    assert decision.reason == "primary-unavailable:fallback-open"
    assert decision.degraded is True


def test_delegate_primary_unavailable_can_be_closed():
    decision = elect(_policy(POLICY_DELEGATE, primary="ghost", fallback=FALLBACK_CLOSED), ["a", "b"])

    assert decision.responders == ()
    assert decision.reason == "primary-unavailable:closed"
    assert decision.degraded is True


def test_delegate_without_primary_falls_back_open():
    decision = elect(_policy(POLICY_DELEGATE), ["a", "b"])

    assert decision.responders == ("a",)
    assert decision.reason == "missing-primary:fallback-open"
    assert decision.degraded is True


def test_delegate_without_primary_closed():
    decision = elect(_policy(POLICY_DELEGATE, fallback=FALLBACK_CLOSED), ["a"])

    assert decision.responders == ()
    assert decision.reason == "missing-primary:closed"


# ==================== leader ====================

def test_leader_prefers_primary():
    decision = elect(_policy(POLICY_LEADER, primary="b"), ["a", "b", "c"])

    assert decision.responders == ("b",)
    assert decision.reason == "primary"


def test_leader_takes_over_when_primary_offline():
    """主备语义：primary 不在候选时由次选接管，并标记降级。"""
    decision = elect(_policy(POLICY_LEADER, primary="b"), ["a", "c"])

    assert decision.responders == ("a",)
    assert decision.degraded is True
    assert "fallback-open" in decision.reason


# ==================== 兼容 ====================

def test_policy_from_legacy_preserves_old_behavior():
    assert policy_from_legacy(False, "acc2").policy == POLICY_ALL
    assert policy_from_legacy(True, None).policy == POLICY_ALL

    delegated = policy_from_legacy(True, "acc2")
    assert delegated.policy == POLICY_DELEGATE
    assert delegated.primary_account_id == "acc2"
    assert delegated.fallback == FALLBACK_OPEN


def test_normalizers_are_tolerant():
    assert normalize_policy("dedupe") == POLICY_DEDUPE
    assert normalize_policy("bogus") == POLICY_ALL
    assert normalize_policy(None) == POLICY_ALL
    assert normalize_fallback("closed") == FALLBACK_CLOSED
    assert normalize_fallback("bogus") == FALLBACK_OPEN


def test_allows_compares_account_ids_as_strings():
    decision = elect(_policy(POLICY_DELEGATE, primary="123"), ["123"])

    assert decision.allows(123) is True
    assert decision.allows("456") is False


def test_decision_serialization_is_stable():
    decision = elect(_policy(POLICY_DEDUPE), ["a", "b"])

    assert decision_as_dict(decision) == {
        "capability_id": "demo",
        "channel_id": "g1",
        "policy": POLICY_DEDUPE,
        "candidates": ["a", "b"],
        "responders": ["a"],
        "reason": "dedupe",
        "degraded": False,
    }
