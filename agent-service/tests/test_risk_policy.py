"""The risk policy is a floor, not a ceiling.

Risk is derived from (operation, domain) so that the *shape* of an action decides how much
scrutiny it gets, rather than the wording of a hand-written regex. That is the right default, but
it made every rule's `risk` field a silent no-op: a rule could declare `high` and still be
treated as `medium`.

A rule may now escalate above the floor -- some actions are dangerous regardless of the domain
bucket they land in, and merging two tickets destroys one of them -- and may not downgrade it,
because otherwise a careless pattern could exempt its own action from approval.
"""

from __future__ import annotations

from app.intent.resolver import _policy_risk, resolve_intent


def test_the_policy_floor_still_applies_when_the_rule_says_nothing():
    assert _policy_risk({"operation": "submit", "domain": "network"}) == "high"
    assert _policy_risk({"operation": "submit", "domain": "it"}) == "medium"
    assert _policy_risk({"operation": "query", "domain": "it"}) == "low"
    assert _policy_risk({"operation": "approve", "domain": "it"}) == "high"


def test_a_rule_may_escalate_above_the_floor():
    # `it` + submit is medium by policy; a destructive action in that bucket must be able to ask
    # for more scrutiny than the bucket implies.
    assert _policy_risk({"operation": "submit", "domain": "it", "risk": "high"}) == "high"


def test_a_rule_may_not_downgrade_the_floor():
    # Otherwise a pattern author could quietly exempt their own action from approval.
    assert _policy_risk({"operation": "submit", "domain": "network", "risk": "low"}) == "high"
    assert _policy_risk({"operation": "approve", "domain": "it", "risk": "low"}) == "high"


def test_merging_tickets_is_high_risk_so_it_reaches_the_approval_path():
    result = resolve_intent("帮我把 #12 合并到 #14", {"roles": ["it_staff"]})

    assert result.intent == "support.ticket.merge"
    assert result.agent_id == "it"
    # High risk is what routes the turn through the approval gate instead of executing directly.
    assert result.risk == "high"


def test_the_other_intents_keep_their_policy_risk():
    assert resolve_intent("申请一个显示器", {"roles": ["it_staff"]}).risk == "medium"
    assert resolve_intent("我要在外网访问财务系统，帮我申请 VPN").risk == "high"
    assert resolve_intent("报销制度是什么").risk == "low"
