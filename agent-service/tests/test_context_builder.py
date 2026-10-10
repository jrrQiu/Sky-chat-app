"""The previous public contract of `assemble_context`, plus the guarantees.

These tests keep asserting the *old* observable behaviour where it is still
correct (messages reach the model, evidence is rendered into the system prompt,
the policy block is never squeezed out) and add the new ones: the report accounts
for everything, and a prompt that cannot be built says so instead of degrading.
"""

import pytest

from app.context.builder import BudgetInfeasible, assemble_context, estimate_tokens
from app.context.tokenizer import count_text


def test_context_builder_uses_recent_messages_as_model_messages():
    state = {
        "messages": [
            {"role": "user", "content": "VPN 申请需要什么"},
            {"role": "assistant", "content": "需要主管和系统管理员审批"},
        ],
        "latest_user_message": "VPN 申请需要什么",
        "knowledge_context": "VPN 制度 v2.0：双人审批",
        "tool_results": [],
        "slots": {},
    }

    assembled = assemble_context(
        state,
        {"name": "Knowledge Agent", "description": "检索制度"},
    )

    # Content and order are preserved, and the current turn appears exactly once.
    assert [item["content"] for item in assembled.messages] == [
        "VPN 申请需要什么",
        "需要主管和系统管理员审批",
    ]
    assert [item["role"] for item in assembled.messages] == ["user", "assistant"]
    # Messages are sent as model messages, not repeated inside the system prompt.
    assert "主管和系统管理员审批" not in assembled.system
    assert "检索证据" in assembled.system
    assert assembled.context_hash
    assert assembled.report["usable_tokens"] > 0
    assert assembled.report["engine_version"] == 2


def test_context_builder_reports_what_it_kept():
    state = {
        "messages": [
            {"id": "m1", "role": "user", "content": "VPN 申请需要什么"},
            {"id": "m2", "role": "assistant", "content": "需要主管审批"},
        ],
        "latest_user_message": "VPN 申请需要什么",
        "knowledge_context": "VPN 制度 v2.0：双人审批",
    }

    assembled = assemble_context(state)

    report = assembled.report
    assert report["messages"]["kept"] == 2
    assert report["messages"]["dropped_ids"] == []
    assert report["used_tokens"] <= report["usable_tokens"]
    assert report["utilization"] <= 1.0
    # Every rendered block is accounted for by kind.
    kinds = {block["kind"] for block in report["blocks"]}
    assert "system_policy" in kinds
    assert "retrieval_evidence" in kinds


def test_context_builder_filters_expired_tool_results():
    state = {
        "messages": [{"role": "user", "content": "查询状态"}],
        "latest_user_message": "查询状态",
        "tool_results": [
            {
                "toolCallId": "tool_1",
                "toolName": "cmdb",
                "text": "旧结果",
                "eligible_for_context": True,
                "expires_at": "2000-01-01T00:00:00+00:00",
            },
            {
                "toolCallId": "tool_2",
                "toolName": "erp",
                "text": "当前结果",
                "eligible_for_context": True,
            },
        ],
    }

    assembled = assemble_context(state)
    assert "当前结果" in assembled.system
    assert "旧结果" not in assembled.system


def test_token_budget_does_not_drop_safe_policy_block():
    state = {
        "messages": [{"role": "user", "content": "申请 VPN"}],
        "latest_user_message": "申请 VPN",
        "knowledge_context": "制度内容 " * 300,
        "tool_results": [],
    }

    assembled = assemble_context(state)
    policy_block = next(
        block for block in assembled.blocks if block["kind"] == "system_policy"
    )
    assert estimate_tokens(policy_block["content"]) >= 1
    assert "系统策略" in assembled.system


def test_hard_block_that_cannot_fit_fails_instead_of_degrading(monkeypatch):
    """Invariant I2: a hard block below its floor is an error, not a warning.

    The previous engine wrote `report["error"] = ...` and carried on, which meant a
    request could be answered with a truncated policy statement and nobody read the
    field. Here the policy statement is larger than the whole usable budget.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "model_context_window", 4000, raising=False)
    monkeypatch.setattr(settings, "output_reserve", 1024, raising=False)
    state = {
        "messages": [{"role": "user", "content": "申请 VPN"}],
        "latest_user_message": "申请 VPN",
        "context_blocks": [
            {
                "id": "system_policy",
                "kind": "system_policy",
                "priority": 100,
                "content": "策略条款内容。" * 4000,
            }
        ],
    }

    with pytest.raises(BudgetInfeasible) as error:
        assemble_context(state)
    assert error.value.missing_tokens > 0


def test_current_user_message_is_never_silently_dropped(monkeypatch):
    """Invariant I3: the current turn is kept whole, or truncated *visibly*."""
    from app.config import settings

    # A small window forces the truncation path; the point is the behaviour, not
    # the default window.
    monkeypatch.setattr(settings, "model_context_window", 4000, raising=False)
    monkeypatch.setattr(settings, "output_reserve", 1024, raising=False)
    state = {
        "messages": [],
        "latest_user_message": "长文" * 4000,
        "knowledge_context": "制度内容 " * 500,
    }

    assembled = assemble_context(state)
    current = [item for item in assembled.messages if item["role"] == "user"]
    assert current, "the current turn must survive"
    assert "已省略" in current[-1]["content"]
    assert assembled.report["messages"]["truncated_ids"]


def test_evidence_citations_are_renumbered_after_packing():
    """A cited [n] must exist *after* trimming, which the old code did not ensure."""
    evidence = [
        {
            "source_id": f"POL-{index}",
            "title": f"制度 {index}",
            "content": "条款内容 " * 40,
            "score": 1.0 - index / 100,
            "metadata": {"citation": f"POL-{index}"},
        }
        for index in range(1, 8)
    ]
    state = {
        "messages": [{"role": "user", "content": "VPN 制度"}],
        "latest_user_message": "VPN 制度",
        "evidence": evidence,
    }

    assembled = assemble_context(state)
    citation_map = assembled.report["evidence"]["citation_map"]
    numbers = sorted(int(key) for key in citation_map)
    # Numbering is contiguous from 1, so no rendered reference can dangle.
    assert numbers == list(range(1, len(numbers) + 1))
    for number, source_id in citation_map.items():
        assert f"[{number}]" in assembled.system
        assert source_id in {
            item["source_id"] for item in assembled.kept_evidence
        }


def test_summary_is_requested_when_messages_are_evicted():
    """The summary range must come from what was actually evicted."""
    state = {
        "messages": [
            {"id": f"m{index}", "role": "user", "content": "很长的历史消息 " * 200}
            for index in range(1, 40)
        ],
        "latest_user_message": "很长的历史消息 " * 200,
    }

    assembled = assemble_context(state)
    report = assembled.report
    if report["messages"]["dropped_ids"] or report["messages"]["truncated_ids"]:
        assert assembled.needs_summary
        assert report["summary_request"]["covered_from_message_id"]
        assert report["summary_request"]["message_count"] == len(
            report["messages"]["evicted_ids"]
        )
    # Whatever happened, the accounting is complete and inside the ceiling.
    assert report["used_tokens"] <= report["usable_tokens"]


def test_eviction_is_a_contiguous_prefix_so_the_summary_can_cover_it():
    """Regression: a non-contiguous evicted set made the summary refuse to cover it.

    Filling greedily through the middle of the history evicted, say, m1..m20 plus
    m24, so the reported range could not be sliced and `summarise_request` returned
    `evicted_range_not_contiguous` with no summary at all. Eviction now stops at the
    first history message that does not fit, so the dropped set is a prefix.
    """
    from app.config import settings

    original_window = settings.model_context_window
    original_reserve = settings.output_reserve
    settings.model_context_window = 24000
    settings.output_reserve = 2048
    try:
        state = {
            "messages": [
                {
                    "id": f"m{index}",
                    "role": "user" if index % 2 else "assistant",
                    "content": "很长的历史内容 " * 120,
                }
                for index in range(1, 41)
            ],
            "latest_user_message": "很长的历史内容 " * 120,
        }

        assembled = assemble_context(state)
        evicted = assembled.evicted_message_ids
        assert evicted, "this conversation cannot fit in a 24k window"

        # Contiguous prefix: the evicted ids are the first N messages in order.
        expected = [f"m{index}" for index in range(1, len(evicted) + 1)]
        assert evicted == expected

        request = assembled.report["summary_request"]
        assert request is not None
        assert request["covered_from_message_id"] == evicted[0]
        assert request["covered_until_message_id"] == evicted[-1]
        assert request["message_count"] == len(evicted)
    finally:
        settings.model_context_window = original_window
        settings.output_reserve = original_reserve


def test_a_summary_computed_after_allocation_is_folded_into_the_prompt():
    """Regression: the running summary was produced but never rendered.

    The summary needs an await, so it is applied after the prompt is assembled. The
    skeleton has to declare the `conversation_summary` section *before* that, or the
    renderer has nowhere to put it and the summary is silently dropped.
    """
    state = {
        "messages": [{"id": "m1", "role": "user", "content": "VPN 申请需要什么"}],
        "latest_user_message": "VPN 申请需要什么",
    }
    assembled = assemble_context(state)
    assert "会话摘要" not in assembled.system

    assembled.apply_summary_delta(
        {
            "covered_from_message_id": "m1",
            "covered_until_message_id": "m1",
            "fields": {"facts": ["用户所在部门是网络运维"]},
            "text": "事实：用户所在部门是网络运维",
            "quality": "model",
        }
    )

    assert "会话摘要" in assembled.system
    assert "用户所在部门是网络运维" in assembled.system
    assert assembled.summary_delta["quality"] == "model"
    assert assembled.report["needs_summary"] is False
    # The hash follows the prompt: a different prompt is a different hash.
    assert assembled.context_hash != assemble_context(state).context_hash


def test_estimated_input_tokens_match_the_rendered_prompt():
    state = {
        "messages": [
            {"id": "m1", "role": "user", "content": "VPN 申请需要什么"},
        ],
        "latest_user_message": "VPN 申请需要什么",
        "knowledge_context": "VPN 制度",
    }

    assembled = assemble_context(state)
    assert assembled.report["estimated_input_tokens"] == assembled.report["used_tokens"]
    # The measurement is of the rendered prompt plus the messages actually sent.
    from app.context.tokenizer import count_messages

    expected = count_text(assembled.system) + count_messages(assembled.messages)
    assert assembled.report["used_tokens"] == expected
