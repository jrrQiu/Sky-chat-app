"""The context engine's guarantees, as tests rather than intentions.

The design document (`docs/context-engine-design.md` §2) lists six invariants. Each
one is asserted here, plus the two properties that made the old engine unsafe: it
under-counted Chinese, and it let `recent_messages` escape the budget entirely.
"""

from __future__ import annotations

import json
import random

import pytest

from app.context.budget import (
    MAX_FILL_ROUNDS,
    BudgetInfeasible,
    ContextBlockInput,
    allocate,
    pack_evidence,
)
from app.context.policy import (
    CONTEXT_POLICIES,
    PROTECTION_DROPPABLE,
    PROTECTION_HARD,
    policy_for,
)
from app.context.render import render_block_section
from app.context.report import ENGINE_VERSION, estimate_error_ratio
from app.context.tokenizer import (
    TIKTOKEN_ENCODING,
    WeightedHeuristicCounter,
    count_message,
    count_messages,
    count_text,
    describe_counter,
    get_counter,
    set_counter_for_testing,
)


def _clear_count_cache() -> None:
    """Drop the per-text count cache (only needed when swapping counters)."""
    from app.context.tokenizer import _count_cached

    _count_cached.cache_clear()
from app.context.truncate import truncate_text


def _render(kind: str, content: str) -> str:
    return render_block_section(kind, content)


def _allocate(blocks, messages, evidence=None, **overrides):
    kwargs = dict(
        window=8192,
        max_output=1024,
        safety_margin=256,
        scaffolding_tokens=120,
        model="deepseek-flash",
        counter_description={"name": "test"},
        render_block=_render,
    )
    kwargs.update(overrides)
    return allocate(blocks, messages, evidence or [], [], **kwargs)


# --------------------------------------------------------------------------
# I1: the hard ceiling
# --------------------------------------------------------------------------
def test_invariant_i1_used_never_exceeds_usable_for_random_inputs():
    """500 seeded rounds of random blocks and messages, none over the ceiling."""
    rng = random.Random(20260602)
    for round_index in range(500):
        kinds = rng.sample(list(CONTEXT_POLICIES), k=rng.randint(1, 6))
        blocks = [
            ContextBlockInput(
                id=f"{kind}-{round_index}",
                kind=kind,
                priority=policy_for(kind).priority,
                content="内容" * rng.randint(0, 400) + " text" * rng.randint(0, 200),
            )
            for kind in kinds
            if kind != "recent_messages"
        ]
        messages = [
            {
                "id": f"m{index}",
                "role": "user" if index % 2 == 0 else "assistant",
                "content": "消息内容" * rng.randint(1, 300),
            }
            for index in range(rng.randint(0, 12))
        ]
        evidence = [
            {
                "source_id": f"POL-{index}",
                "title": f"制度 {index}",
                "content": "条款" * rng.randint(1, 300),
                "score": rng.random(),
                "metadata": {},
            }
            for index in range(rng.randint(0, 6))
        ]
        window = rng.choice([2048, 4096, 8192, 32768])
        try:
            allocation = _allocate(
                blocks,
                messages,
                evidence,
                window=window,
                max_output=rng.choice([512, 1024, 2048]),
            )
        except BudgetInfeasible:
            # Refusing is a legal outcome; sending an over-budget prompt is not.
            continue

        report = allocation.report
        assert report.used_tokens <= report.usable_tokens, (
            f"round {round_index}: {report.used_tokens} > {report.usable_tokens}"
        )
        assert (
            report.used_tokens + report.max_output + report.safety_margin
            <= report.window
        )


def test_invariant_i1_counts_the_rendered_prompt_not_the_raw_content():
    """The section headers are part of what the provider charges for."""
    allocation = _allocate(
        [
            ContextBlockInput(
                id="system_policy",
                kind="system_policy",
                priority=100,
                content="策略内容" * 20,
            )
        ],
        [{"id": "m1", "role": "user", "content": "问题"}],
    )
    block = next(item for item in allocation.report.blocks if item.kind == "system_policy")
    # actual includes the 【系统策略】 header, so it is strictly larger than content.
    assert block.actual > count_text("策略内容" * 20)


# --------------------------------------------------------------------------
# I2: hard blocks are whole or the assembly fails
# --------------------------------------------------------------------------
def test_invariant_i2_raises_when_hard_blocks_cannot_fit():
    blocks = [
        ContextBlockInput(
            id="system_policy",
            kind="system_policy",
            priority=100,
            content="策略" * 5000,
        ),
        ContextBlockInput(
            id="approval_state",
            kind="approval_state",
            priority=60,
            content="审批状态" * 500,
        ),
    ]
    with pytest.raises(BudgetInfeasible) as error:
        _allocate(blocks, [{"id": "m1", "role": "user", "content": "问题"}])
    assert error.value.missing_tokens > 0


def test_invariant_i2_never_truncates_a_hard_block():
    """A hard block that fits is sent whole, header included."""
    content = "策略条款内容。" * 30
    allocation = _allocate(
        [
            ContextBlockInput(
                id="system_policy",
                kind="system_policy",
                priority=100,
                content=content,
            )
        ],
        [{"id": "m1", "role": "user", "content": "问题"}],
    )
    assert allocation.sections["system_policy"] == content
    block = next(item for item in allocation.report.blocks if item.kind == "system_policy")
    assert block.truncated is False


def test_invariant_i2_rejects_a_window_smaller_than_the_reserves():
    with pytest.raises(BudgetInfeasible):
        _allocate([], [], window=900, max_output=1024, safety_margin=256)


# --------------------------------------------------------------------------
# I3: the current turn survives, visibly truncated if necessary
# --------------------------------------------------------------------------
def test_invariant_i3_current_turn_is_truncated_with_a_visible_marker():
    long_question = "请问" + "细节" * 4000 + "最后一句结论"
    allocation = _allocate(
        [],
        [{"id": "m1", "role": "user", "content": long_question}],
        window=4096,
        max_output=1024,
        protected_content=long_question,
    )
    assert len(allocation.messages) == 1
    kept = allocation.messages[0]["content"]
    assert "已省略" in kept
    assert kept.startswith("请问")
    assert kept.rstrip().endswith("最后一句结论")
    assert "m1" in allocation.report.messages.truncated_ids


def test_invariant_i3_fails_loudly_when_even_the_marker_does_not_fit():
    with pytest.raises(BudgetInfeasible):
        _allocate(
            [],
            [],
            window=1200,
            max_output=1024,
            safety_margin=256,
            scaffolding_tokens=100,
            protected_content="非常长的问题" * 500,
        )


# --------------------------------------------------------------------------
# I4: nothing is lost silently
# --------------------------------------------------------------------------
def test_invariant_i4_dropped_ids_match_what_left_the_prompt():
    messages = [
        {"id": f"m{index}", "role": "user", "content": "很长的历史 " * 200}
        for index in range(1, 21)
    ]
    allocation = _allocate(
        [],
        messages,
        window=4096,
        max_output=1024,
        protected_content=messages[-1]["content"],
    )
    kept_ids = {item.get("id") for item in allocation.messages}
    dropped = set(allocation.report.messages.dropped_ids)
    truncated = set(allocation.report.messages.truncated_ids)
    evicted = set(allocation.report.messages.evicted_ids)

    assert evicted == dropped | truncated
    # Nothing may be both kept and dropped, and every dropped id must be real.
    assert not (kept_ids - truncated) & dropped
    all_ids = {item["id"] for item in messages}
    assert dropped <= all_ids
    assert report_covers_everything(allocation.report)


def report_covers_everything(report) -> bool:
    """Every block appears exactly once in the report."""
    kinds = [block.kind for block in report.blocks]
    return len(kinds) == len(set(kinds))


def test_invariant_i4_dropped_evidence_is_reported_by_id():
    evidence = [
        {
            "source_id": f"POL-{index}",
            "title": f"制度 {index}",
            "content": "条款内容 " * 200,
            "score": 1.0 - index / 100,
            "metadata": {},
        }
        for index in range(1, 9)
    ]
    allocation = _allocate(
        [
            ContextBlockInput(
                id="retrieval_evidence",
                kind="retrieval_evidence",
                priority=50,
                content=f"[{len(evidence)} items]",
            )
        ],
        [{"id": "m1", "role": "user", "content": "VPN 制度"}],
        evidence,
        window=4096,
        max_output=1024,
    )
    dropped = allocation.report.evidence.dropped_ids
    assert dropped, "with this little room some evidence must be dropped"
    assert allocation.report.evidence.kept + allocation.report.evidence.dropped == len(
        {item["source_id"] for item in evidence}
    )
    # The citation map only ever points at evidence that is actually present.
    assert set(allocation.report.evidence.citation_map.values()) == {
        item["source_id"] for item in allocation.evidence.items
    }


# --------------------------------------------------------------------------
# I5: reproducibility
# --------------------------------------------------------------------------
def test_invariant_i5_same_input_produces_the_same_allocation():
    blocks = [
        ContextBlockInput(
            id="system_policy", kind="system_policy", priority=100, content="策略内容"
        ),
        ContextBlockInput(
            id="user_profile", kind="user_profile", priority=80, content="用户画像"
        ),
    ]
    messages = [
        {"id": "m1", "role": "user", "content": "问题一"},
        {"id": "m2", "role": "assistant", "content": "回答一"},
    ]
    first = _allocate(blocks, messages)
    second = _allocate(blocks, messages)
    assert first.report.to_dict()["blocks"] == second.report.to_dict()["blocks"]
    assert first.report.used_tokens == second.report.used_tokens
    assert first.messages == second.messages


def test_invariant_i5_evidence_order_is_stable_for_equal_scores():
    evidence = [
        {"source_id": "B", "title": "b", "content": "内容", "score": 0.5, "metadata": {}},
        {"source_id": "A", "title": "a", "content": "内容", "score": 0.5, "metadata": {}},
    ]
    first = pack_evidence(evidence, 500)
    second = pack_evidence(list(reversed(evidence)), 500)
    assert [item["source_id"] for item in first.items] == [
        item["source_id"] for item in second.items
    ] == ["A", "B"]


# --------------------------------------------------------------------------
# I6: metering is the tokenizer the budget used, and calibration is recorded
# --------------------------------------------------------------------------
def test_invariant_i6_reported_counter_is_the_one_used():
    description = describe_counter()
    assert description["name"] == get_counter().name
    assert "kind" in description
    # The report states which counter produced the numbers.
    allocation = _allocate([], [{"id": "m1", "role": "user", "content": "问题"}])
    assert allocation.report.counter == {"name": "test"}
    assert allocation.report.engine_version == ENGINE_VERSION


def test_calibration_ratio_uses_the_provider_count():
    assert estimate_error_ratio(1000, 1250) == pytest.approx(0.2)
    assert estimate_error_ratio(1000, 800) == pytest.approx(-0.25)
    # A missing measurement is not a perfect one.
    assert estimate_error_ratio(1000, None) is None
    assert estimate_error_ratio(None, 1000) is None
    assert estimate_error_ratio(1000, 0) is None


# --------------------------------------------------------------------------
# Token counting: the defect that started this work
# --------------------------------------------------------------------------
def test_counter_does_not_under_count_chinese_like_the_old_heuristic():
    text = "企业服务台的风险审批流程需要双人复核" * 20
    old_heuristic = max(1, int(len(text) / 3.2) + text.count(" ") // 8)
    actual = count_text(text)
    # The old estimate under-counted Chinese by roughly 2x; the new counter must be
    # materially closer to reality than that.
    assert actual > old_heuristic * 1.5


@pytest.mark.parametrize(
    "text",
    [
        "hello world, this is a plain English sentence.",
        "混合 mixed 内容 with 1234 digits and punctuation!!!",
        "def f(x):\n    return x ** 2  # comment",
        "",
        "   ",
    ],
)
def test_counter_is_deterministic_and_non_negative(text):
    assert count_text(text) == count_text(text)
    assert count_text(text) >= 0


def test_heuristic_fallback_agrees_with_tiktoken_within_tolerance():
    """The fallback exists for offline images; it must not be wildly wrong."""
    counter = WeightedHeuristicCounter()
    samples = [
        "企业服务台的审批流程" * 10,
        "The quick brown fox jumps over the lazy dog. " * 10,
        "混合 mixed 内容 with numbers 1234567890 and symbols !!!???",
    ]
    baseline = get_counter()
    if baseline.name != f"tiktoken/{TIKTOKEN_ENCODING}":
        pytest.skip("tiktoken is not available in this environment")
    for sample in samples:
        expected = baseline.count(sample)
        got = counter.count(sample)
        # Generous, because it is a fallback: the point is that it is the right
        # order of magnitude, not that it matches to the token.
        assert got == pytest.approx(expected, rel=0.6), sample


def test_message_overhead_is_counted():
    empty = count_message({"role": "user", "content": ""})
    assert empty > 0
    assert count_message({"role": "user", "content": "内容"}) > empty
    assert count_messages([]) == 0


def test_injected_counter_is_used_for_counting(monkeypatch):
    class FakeCounter:
        name = "fake/exact"

        def count(self, text: str) -> int:
            return len(text)

        def describe(self) -> dict:
            return {"name": self.name, "kind": "fake", "exact": True}

    set_counter_for_testing(FakeCounter())
    try:
        # The counting cache is keyed by text, so it has to be cleared when the
        # counter changes -- otherwise a cached tiktoken count is returned instead
        # of the injected one.
        _clear_count_cache()
        assert count_text("abcd") == 4
    finally:
        set_counter_for_testing(None)
        _clear_count_cache()
    assert count_text("abcd") == get_counter().count("abcd")


# --------------------------------------------------------------------------
# Policy table: the single source of truth
# --------------------------------------------------------------------------
def test_policy_table_is_internally_consistent():
    for kind, policy in CONTEXT_POLICIES.items():
        assert policy.kind == kind
        assert policy.weight > 0
        assert policy.min_tokens >= 0
        assert policy.max_tokens > 0
        if policy.protection == PROTECTION_HARD:
            # A hard block is never cut, so a cutting strategy would be dead config.
            assert policy.truncation == "none"
            assert policy.min_tokens > 0
        if policy.count_by == "items":
            assert policy.truncation == "items"


def test_unknown_kind_gets_a_conservative_policy():
    policy = policy_for("something_new")
    assert policy.protection == PROTECTION_DROPPABLE
    assert policy.weight < max(item.weight for item in CONTEXT_POLICIES.values())


def test_water_filling_returns_unused_share_to_the_pool():
    """A block that cannot use its share must not waste it (the old defect)."""
    # Comfortably below the `user_profile` ceiling, so this exercises the
    # allocation and not the ceiling.
    profile_text = "用户画像内容" * 40
    allocation = _allocate(
        [
            ContextBlockInput(
                id="approval_state",
                kind="approval_state",
                priority=60,
                content="审批状态正常",
            ),
            ContextBlockInput(
                id="user_profile",
                kind="user_profile",
                priority=80,
                content=profile_text,
            ),
        ],
        [{"id": "m1", "role": "user", "content": "问题"}],
        window=32768,
    )
    profile = next(
        item for item in allocation.report.blocks if item.kind == "user_profile"
    )
    # The tiny hard block releases its share, so the elastic block is served in full
    # rather than being limited to a share that nothing else could use.
    assert allocation.sections["user_profile"] == profile_text
    assert profile.truncated is False


def test_droppable_blocks_give_up_budget_before_hard_ones():
    """When room runs out the advice is dropped, not the approval conclusion."""
    allocation = _allocate(
        [
            ContextBlockInput(
                id="system_policy",
                kind="system_policy",
                priority=100,
                content="策略内容",
            ),
            ContextBlockInput(
                id="approval_state",
                kind="approval_state",
                priority=60,
                content="pending approval id=42",
            ),
            ContextBlockInput(
                id="retrieval_evidence",
                kind="retrieval_evidence",
                priority=50,
                content="[3 items]",
            ),
        ],
        [{"id": "m1", "role": "user", "content": "问题"}],
        [
            {
                "source_id": f"POL-{index}",
                "title": "制度",
                "content": "条款" * 200,
                "score": 1.0,
                "metadata": {},
            }
            for index in range(3)
        ],
        window=4096,
        max_output=1024,
    )
    kinds = {block.kind: block for block in allocation.report.blocks}
    assert kinds["approval_state"].dropped is False
    assert kinds["approval_state"].truncated is False
    assert "pending approval id=42" in allocation.sections["approval_state"]


def test_fill_rounds_are_bounded():
    """A pathological policy table must not spin the allocator."""
    assert 1 <= MAX_FILL_ROUNDS <= 32


def test_empty_placeholder_blocks_do_not_consume_budget():
    allocation = _allocate(
        [
            ContextBlockInput(
                id="task_slots", kind="task_slots", priority=55, content="{}"
            ),
            ContextBlockInput(
                id="tool_results", kind="tool_results", priority=45, content="[]"
            ),
        ],
        [{"id": "m1", "role": "user", "content": "问题"}],
    )
    for kind in ("task_slots", "tool_results"):
        block = next(item for item in allocation.report.blocks if item.kind == kind)
        assert block.dropped is True
        assert block.budget == 0
        assert block.notes == ["empty_block"]


# --------------------------------------------------------------------------
# Truncation
# --------------------------------------------------------------------------
def test_head_tail_truncation_keeps_both_ends_and_stays_in_budget():
    text = "开头部分" + "中间内容" * 400 + "结尾结论"
    result = truncate_text(text, 100, "head_tail")
    assert result.truncated
    assert result.tokens <= 100
    assert result.text.startswith("开头部分")
    assert result.text.endswith("结尾结论")
    assert "已省略" in result.text


def test_tail_truncation_keeps_the_head():
    text = "第一段" * 200
    result = truncate_text(text, 50, "tail")
    assert result.truncated
    assert result.tokens <= 50
    assert result.text.startswith("第一段")
    assert result.omitted_tokens > 0


def test_truncation_of_none_never_cuts():
    text = "内容" * 500
    result = truncate_text(text, 10, "none")
    assert result.truncated is False
    assert result.text == text
    assert "strategy_none" in result.notes


def test_truncation_that_cannot_fit_the_marker_reports_a_full_omission():
    text = "内容" * 500
    result = truncate_text(text, 3, "tail")
    assert result.text == ""
    assert result.truncated
    assert result.omitted_tokens > 0


def test_truncation_leaves_short_text_alone():
    result = truncate_text("短文本", 100, "head_tail")
    assert result.text == "短文本"
    assert result.truncated is False
    assert result.tokens == count_text("短文本")


def test_truncation_omitted_counts_are_consistent():
    text = json.dumps({"payload": "内容" * 300}, ensure_ascii=False)
    result = truncate_text(text, 80, "head_tail")
    kept_tokens = count_text(result.text)
    assert result.tokens == kept_tokens
    assert result.omitted_chars <= len(text)
    assert result.omitted_tokens > 0


# --------------------------------------------------------------------------
# Evidence packing
# --------------------------------------------------------------------------
def test_evidence_budget_reflects_the_real_content_not_a_placeholder():
    """Regression: the evidence block was sized from the caller's placeholder.

    The builder declares the rendered candidate text as the block content, because
    the demand the planner sees comes from that string. When it was a placeholder
    such as ``"[12 items]"``, the engine reserved room for a header and then dropped
    every item for lack of space -- a silent loss of all grounding on a wide window.
    """
    evidence = [
        {
            "source_id": f"POL-{index}",
            "title": f"制度 {index}",
            "content": "条款内容 " * 60,
            "score": 1.0 - index / 100,
            "metadata": {"citation": f"POL-{index}"},
        }
        for index in range(1, 13)
    ]
    state = {"messages": [{"id": "m1", "role": "user", "content": "VPN 制度"}]}
    state["evidence"] = evidence
    state["latest_user_message"] = "VPN 制度"

    from app.context.builder import assemble_context

    assembled = assemble_context(state, {"name": "K", "description": "d"})
    report = assembled.report["evidence"]
    assert report["kept"] == len(evidence), "every item fits a default-window prompt"
    assert report["dropped"] == 0
    assert report["tokens"] > 500


def test_evidence_is_trimmed_rather_than_all_dropped_when_room_is_tight():
    """A trimmed excerpt attributed to its source beats no grounding at all."""
    from app.context.budget import MIN_ITEM_TOKENS, pack_evidence

    evidence = [
        {
            "source_id": "POL-1",
            "title": "很长的制度",
            "content": "条款" * 500,
            "score": 1.0,
            "metadata": {"citation": "POL-1"},
        }
    ]
    packed = pack_evidence(evidence, MIN_ITEM_TOKENS * 2)
    assert packed.items, "the only evidence item was dropped instead of trimmed"
    assert packed.tokens <= MIN_ITEM_TOKENS * 2
    assert "POL-1" in packed.text


def test_section_join_overhead_is_reserved():
    """Regression: the rendered prompt exceeded the ceiling by the join separators.

    Each block was sized correctly and the section header charged to it, but the
    renderer joins sections with a blank line, which is a real token per section.
    The live check caught a prompt a few tokens over budget for this reason.
    """
    from app.context.budget import SECTION_JOIN_TOKENS

    kinds = ("system_policy", "tenant_profile", "user_profile", "approval_state")
    blocks = [
        ContextBlockInput(
            id=kind, kind=kind, priority=policy_for(kind).priority, content="内容" * 50
        )
        for kind in kinds
    ]
    allocation = _allocate(blocks, [{"id": "m1", "role": "user", "content": "问题"}])
    report = allocation.report
    raw_usable = report.window - report.max_output - report.safety_margin
    # The joins are actually deducted, not merely documented.
    assert report.usable_tokens == raw_usable - len(kinds) * SECTION_JOIN_TOKENS
    assert report.used_tokens <= report.usable_tokens


def test_evidence_is_packed_high_score_first_and_renumbered():
    evidence = [
        {
            "source_id": f"POL-{index}",
            "title": f"制度 {index}",
            "content": "内容" * 30,
            "score": index / 10,
            "metadata": {"citation": f"POL-{index}"},
        }
        for index in range(1, 5)
    ]
    packed = pack_evidence(evidence, 200)
    assert packed.items, "at least one item must survive"
    assert packed.items[0]["source_id"] == "POL-4"
    assert list(packed.citation_map) == [
        str(index) for index in range(1, len(packed.items) + 1)
    ]
    for number, source_id in packed.citation_map.items():
        assert f"[{number}]" in packed.text
        assert source_id in packed.text


def test_evidence_drops_duplicates_and_empty_items():
    evidence = [
        {"source_id": "POL-1", "title": "a", "content": "内容", "score": 1.0},
        {"source_id": "POL-1", "title": "a", "content": "内容", "score": 1.0},
        {"source_id": "POL-2", "title": "b", "content": "   ", "score": 1.0},
        {"source_id": "", "title": "c", "content": "内容", "score": 1.0},
    ]
    packed = pack_evidence(evidence, 500)
    assert [item["source_id"] for item in packed.items] == ["POL-1"]


def test_evidence_keeps_at_least_one_item_under_pressure():
    evidence = [
        {
            "source_id": "POL-1",
            "title": "很长的制度",
            "content": "条款" * 400,
            "score": 1.0,
            "metadata": {},
        }
    ]
    packed = pack_evidence(evidence, 128)
    assert packed.items, "the answer needs something to ground on"
    assert packed.tokens <= 128
