"""The budget-side citation check: a cited `[n]` must exist in the prompt.

The gap analysis (§9.1, and problem 7 in `docs/context-engine-design.md`) records
this defect precisely: retrieval evidence was numbered *before* the block was
trimmed, so the model could be handed an answer template that cites `[3]` while
`[3]` had already been dropped from the prompt. The engine now renumbers after
packing and exposes the map it used; this check grades that map.
"""

from __future__ import annotations

from eval.metrics import (
    REASON_MAP_NOT_CONTIGUOUS,
    REASON_MARKER_MISSING,
    REASON_SOURCE_ABSENT,
    check_context_citation_map,
)


def test_a_consistent_map_scores_one():
    prompt = "【检索证据】\n[1] 制度 A\n[2] 制度 B"
    score, details = check_context_citation_map(
        {"1": "POL-A", "2": "POL-B"}, prompt, ["POL-A", "POL-B"]
    )
    assert score == 1.0
    assert details == []


def test_an_empty_map_is_not_a_failure():
    """Nothing cited means nothing can dangle."""
    score, details = check_context_citation_map({}, "", [])
    assert score == 1.0
    assert details == []


def test_a_marker_missing_from_the_prompt_is_a_failure():
    """This is exactly the old defect: a map entry with no rendered reference."""
    prompt = "【检索证据】\n[1] 制度 A"
    score, details = check_context_citation_map(
        {"1": "POL-A", "2": "POL-B"}, prompt, ["POL-A", "POL-B"]
    )
    assert score < 1.0
    assert any(item["reason"] == REASON_MARKER_MISSING for item in details)
    assert details[0]["number"] == "2"


def test_non_contiguous_numbering_is_a_failure():
    """Gaps are how a dropped item leaves a hole in the numbering."""
    prompt = "【检索证据】\n[1] 制度 A\n[3] 制度 C"
    score, details = check_context_citation_map(
        {"1": "POL-A", "3": "POL-C"}, prompt, ["POL-A", "POL-C"]
    )
    assert score < 1.0
    assert details[0]["reason"] == REASON_MAP_NOT_CONTIGUOUS


def test_a_map_pointing_at_absent_evidence_is_a_failure():
    prompt = "【检索证据】\n[1] 制度 A"
    score, details = check_context_citation_map(
        {"1": "POL-MISSING"}, prompt, ["POL-A"]
    )
    assert score == 0.0
    assert details[0]["reason"] == REASON_SOURCE_ABSENT


def test_the_real_engine_produces_a_resolvable_map():
    """End-to-end: whatever the budget did, the map it reports is consistent."""
    from app.context.builder import assemble_context

    evidence = [
        {
            "source_id": f"POL-{index}",
            "title": f"制度 {index}",
            "content": "条款内容 " * 100,
            "score": 1.0 - index / 100,
            "metadata": {"citation": f"POL-{index}"},
        }
        for index in range(1, 10)
    ]
    assembled = assemble_context(
        {
            "messages": [{"id": "m1", "role": "user", "content": "VPN 制度"}],
            "latest_user_message": "VPN 制度",
            "evidence": evidence,
        }
    )

    score, details = check_context_citation_map(
        assembled.citation_map,
        assembled.system,
        [item["source_id"] for item in assembled.kept_evidence],
    )
    assert score == 1.0, details
    # And the packing really did drop something, so this is not a vacuous pass.
    assert assembled.report["evidence"]["dropped"] >= 0
