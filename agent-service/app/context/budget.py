"""The budget planner: floors, weighted water-filling, and a hard ceiling.

Replaces "walk the blocks in priority order and subtract from what is left",
which had three defects the gap analysis names explicitly:

1. **No flow-back.** A high-priority block that used less than its share kept the
   difference unused, so the total prompt was smaller than the window allowed.
   Here the share is re-computed each round over the blocks that still want
   tokens, so an unspent share returns to the pool.
2. **The newest messages were outside the budget.** ``recent_messages`` carried
   the largest weight and was then sent verbatim, so a pasted document blew the
   window no matter what the other blocks did. Messages are now a participant in
   the same allocation, and the current turn is guaranteed whole or explicitly
   head/tail truncated.
3. **Safety blocks degraded with a warning nobody read.** A ``hard`` block below
   its floor is now a failure (``BudgetInfeasible``), not a log line: continuing
   with a half policy in the prompt is worse than not answering.

The guarantee this module owes its caller is the invariant the design document
calls I1:

    used_tokens + max_output + safety_margin <= context_window

so ``used_tokens <= usable_tokens`` is asserted, not hoped for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.context.policy import (
    MESSAGES_KIND,
    PROTECTION_DROPPABLE,
    PROTECTION_HARD,
    ContextPolicy,
    policy_for,
)
from app.context.report import (
    BlockReport,
    ContextReport,
    EvidenceReport,
    MessagesReport,
    SummaryRequest,
)
from app.context.tokenizer import count_message, count_messages, count_text
from app.context.truncate import truncate_text

logger = logging.getLogger(__name__)

#: The prompt always carries at least this much space for the conversation. Below
#: it there is no point calling a model at all, so infeasibility is explicit.
MIN_MESSAGES_RESERVE = 256

#: Below this many tokens an item-counted block cannot hold even a trimmed entry,
#: so the item is dropped instead of included as an unreadable fragment. It is the
#: floor that makes "trim to fit" honest rather than a way to always claim a block
#: was placed.
MIN_ITEM_TOKENS = 96

#: Tokens the renderer adds when it joins sections together. Each block is sized
#: individually and the section header is charged to that block, but a prompt is
#: also `\n\n`-joined, and those separators are real tokens. Ignoring them made the
#: rendered prompt exceed the ceiling by a few tokens per section -- exactly the
#: kind of "it fitted on paper" overrun the invariant exists to prevent.
SECTION_JOIN_TOKENS = 2

#: Defensive bound on water-filling rounds. Each round either fills at least one
#: block or ends the loop, so this can only be reached by a pathological policy
#: table -- and then it should stop rather than spin.
MAX_FILL_ROUNDS = 8

#: Guards against a nonsense configuration (a zero window, a window smaller than
#: the reserved output).
MIN_USABLE_TOKENS = 512


class BudgetInfeasible(RuntimeError):
    """The prompt cannot be built inside the window without breaking a guarantee.

    Carries the shortfall so a caller can size the request (or the model) rather
    than guess from a message string.
    """

    def __init__(self, missing_tokens: int, detail: str = "") -> None:
        self.missing_tokens = max(0, int(missing_tokens))
        self.detail = detail
        super().__init__(
            f"上下文预算不可行：还缺 {self.missing_tokens} tokens。{detail}".strip()
        )


@dataclass
class ContextBlockInput:
    """A candidate block, already parsed out of the envelope."""

    id: str
    kind: str
    content: str
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PackedEvidence:
    """Evidence the prompt actually cites, renumbered from 1."""

    items: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    tokens: int = 0
    citation_map: dict[str, str] = field(default_factory=dict)
    dropped_ids: list[str] = field(default_factory=list)


@dataclass
class Allocation:
    """The outcome of planning one prompt."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    evidence: PackedEvidence = field(default_factory=PackedEvidence)
    report: ContextReport = field(default_factory=ContextReport)
    #: Messages that left the prompt. The summary must cover exactly these.
    evicted_message_ids: list[str] = field(default_factory=list)
    #: Measured tokens of the rendered prompt, for the calibration ratio.
    estimated_input_tokens: int = 0
    #: Set when messages were evicted and a summary revision is owed.
    summary_request: SummaryRequest | None = None

    @property
    def tokens(self) -> int:
        return self.report.used_tokens


def _policy_index(blocks: list[ContextBlockInput]) -> dict[str, ContextPolicy]:
    return {block.kind: policy_for(block.kind) for block in blocks}


def _dedupe_blocks(blocks: list[ContextBlockInput]) -> list[ContextBlockInput]:
    """One block per kind. First occurrence wins; the rest are recorded."""
    seen: dict[str, ContextBlockInput] = {}
    for block in blocks:
        if not block.kind:
            continue
        if block.kind in seen:
            continue
        seen[block.kind] = block
    return list(seen.values())


def _is_empty(block: ContextBlockInput, policy: ContextPolicy) -> bool:
    """Placeholder blocks that carry no information must not consume budget.

    Applies to the *final* content (after the builder has folded in tool results
    and task slots), so an empty placeholder cannot survive simply because the
    envelope declared it.
    """
    if policy.is_hard:
        return False
    stripped = (block.content or "").strip()
    return stripped in {"", "{}", "[]", "null"}


def _demand(block: ContextBlockInput, policy: ContextPolicy) -> int:
    return min(count_text(block.content), policy.max_tokens)


def _message_overhead(messages: list[dict[str, Any]]) -> int:
    return sum(count_message({"role": item.get("role"), "content": ""}) for item in messages)


def _pack_messages(
    messages: list[dict[str, Any]],
    budget: int,
    *,
    protected_content: str = "",
) -> tuple[list[dict[str, Any]], int, list[str], list[str], int, bool]:
    """Fill messages newest-first inside ``budget``.

    Returns ``(kept, tokens, dropped_ids, truncated_ids, omitted_tokens, current_kept)``.

    Two rules, both of which the summary depends on:

    * **The current turn may be truncated; history may not.** A history message is
      either sent whole or not at all, because half a historical question reads as a
      different question. That also keeps this function honest about information
      loss: a cut history message would silently change what was asked.
    * **Eviction is contiguous.** Once a history message does not fit, every older
      one is dropped too. The evicted set is therefore always a *prefix* of the
      conversation, which is what lets the rolling summary cover exactly the dropped
      range with no gap and no overlap. Filling greedily through the middle of the
      history produced a non-contiguous evicted set, and the summary then refused to
      cover it at all -- a real defect this rule removes.
    """
    kept: list[dict[str, Any]] = []
    truncated_ids: list[str] = []
    omitted_tokens = 0
    used = 0
    protected = (protected_content or "").strip()
    current_kept = False
    # The current turn is identified by position, not by comparing text: once it is
    # truncated the text no longer equals `protected_content`, and a content match
    # would then report that the protected message never survived.
    current_index = -1
    if protected:
        for position, candidate in enumerate(messages):
            if (
                candidate.get("role") == "user"
                and str(candidate.get("content") or "").strip() == protected
            ):
                current_index = position

    # Newest first: the most recent turns are the ones the model needs.
    ordered: list[tuple[int, dict[str, Any]]] = [
        (index, item) for index, item in enumerate(messages)
    ]
    ordered.reverse()

    history_full = False
    for index, item in ordered:
        content = item.get("content")
        content = content if isinstance(content, str) else ""
        overhead = count_message({"role": item.get("role"), "content": ""})
        is_current = index == current_index
        room = budget - used - overhead

        if is_current:
            # Protected: an excerpt is better than losing the question.
            if room <= 0:
                break
            result = truncate_text(content, room, "head_tail")
            if not result.text.strip():
                break
            entry: dict[str, Any] = {"role": item.get("role"), "content": result.text}
            if item.get("id"):
                entry["id"] = item.get("id")
            if item.get("name"):
                entry["name"] = item.get("name")
            kept.append(entry)
            used += result.tokens + overhead
            current_kept = True
            if result.truncated:
                omitted_tokens += result.omitted_tokens
                if item.get("id"):
                    truncated_ids.append(str(item.get("id")))
            continue

        if history_full or room <= 0:
            history_full = True
            omitted_tokens += count_message({**item, "content": content})
            continue
        cost = count_text(content) + overhead
        if cost > room:
            # Does not fit whole: evict it and everything older.
            history_full = True
            omitted_tokens += count_message({**item, "content": content})
            continue

        entry = {"role": item.get("role"), "content": content}
        if item.get("id"):
            entry["id"] = item.get("id")
        if item.get("name"):
            entry["name"] = item.get("name")
        kept.append(entry)
        used += cost

    kept.reverse()

    # The evicted ids are reported in **conversation order**, not in the order they
    # were considered. The planner derives the summary range from the first and last
    # entry, so a reverse-ordered list produced a range of `from=m20, until=m1` that
    # no slice can satisfy -- and the summary then refused to cover anything.
    kept_ids = {str(entry.get("id") or "") for entry in kept}
    evicted: list[str] = []
    for index, item in enumerate(messages):
        identifier = str(item.get("id") or f"message_{index}")
        if identifier in kept_ids:
            continue
        evicted.append(identifier)

    return kept, used, evicted, truncated_ids, omitted_tokens, current_kept


def _render_evidence_item(index: int, item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    citation = metadata.get("citation") or item.get("source_id") or ""
    return (
        f"[{index}] {item.get('title') or '未命名证据'}（{citation}）\n"
        f"权威等级：{metadata.get('authority_level', '-')}；"
        f"生效时间：{metadata.get('effective_at', '-')}；"
        f"状态：{metadata.get('status', '-')}\n"
        f"{item.get('content') or ''}"
    )


def _evidence_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    try:
        score = float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    # Descending score, then a stable tie-break so identical inputs produce an
    # identical prompt (the reproducibility invariant).
    return (-score, str(item.get("source_id") or ""))


def pack_evidence(items: list[dict[str, Any]], budget: int) -> PackedEvidence:
    """Pack evidence item-by-item: whole items where possible, trimmed where not.

    Citation numbers are assigned **after** packing (the old code numbered before
    trimming, so the model could cite a `[3]` that no longer existed). The map
    ``{new number: source_id}`` is returned so the evaluation harness can check a
    citation against what was actually sent.

    An earlier version of this function dropped every item once the budget was
    below one item's size. On a wide window that is a silent loss of all grounding,
    so a single item is now trimmed to fit the remaining room instead: an excerpt
    attributed to its source is worth more to the answer than nothing at all.
    """
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        if not source_id or source_id in seen:
            continue
        content = item.get("content")
        if not (content or "").strip():
            continue
        seen.add(source_id)
        ordered.append(item)
    ordered.sort(key=_evidence_sort_key)

    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    citation_map: dict[str, str] = {}
    rendered: list[str] = []
    used = 0

    for item in ordered:
        source_id = str(item.get("source_id"))
        index = len(kept) + 1
        block = _render_evidence_item(index, item)
        cost = count_text(block)
        if used + cost > budget:
            room = budget - used
            if room < MIN_ITEM_TOKENS:
                dropped.append(source_id)
                continue
            # Trim the body (keeping the title and citation intact, since the
            # citation is the part the answer has to reference).
            overhead = max(0, count_text(_render_evidence_item(index, {**item, "content": ""})))
            shortened = dict(item)
            shortened["content"] = truncate_text(
                str(item.get("content") or ""), max(8, room - overhead), "tail"
            ).text
            block = _render_evidence_item(index, shortened)
            cost = count_text(block)
            if not shortened["content"].strip() or used + cost > budget:
                dropped.append(source_id)
                continue
        kept.append(item)
        citation_map[str(index)] = source_id
        rendered.append(block)
        used += cost

    return PackedEvidence(
        items=kept,
        text="\n\n".join(rendered),
        tokens=used,
        citation_map=citation_map,
        dropped_ids=dropped,
    )


def _render_tool_result(item: dict[str, Any]) -> str:
    name = item.get("toolName") or item.get("name") or "tool"
    success = item.get("success")
    status = "成功" if success else "失败" if success is False else "未知"
    text = item.get("text") or item.get("message") or ""
    line = f"[{name}] ({status})"
    error = item.get("error")
    if error:
        # The code and its recovery advice are part of what the answer must respect:
        # rendering only "失败" left the model unable to tell "nothing happened"
        # from "the effect may have landed", which is the distinction that decides
        # whether it may claim anything at all.
        line += f" error={error}"
        recovery = (item.get("advice") or {}).get("recovery")
        if recovery:
            line += f" recovery={recovery}"
    return f"{line}\n{text}"


def render_tool_results(items: list[dict[str, Any]]) -> str:
    """The tool-results block as it will be rendered, for demand measurement."""
    return "\n\n".join(
        _render_tool_result(item) for item in items if isinstance(item, dict)
    )


def render_evidence_items(items: list[dict[str, Any]]) -> str:
    """The evidence block as it *would* be rendered if nothing were dropped.

    Used to size the demand. Measuring the caller's placeholder instead is how the
    evidence block ended up sized for a header and then thrown away whole.
    """
    return "\n\n".join(
        _render_evidence_item(index, item)
        for index, item in enumerate(items, start=1)
        if isinstance(item, dict)
    )


def pack_tool_results(
    items: list[dict[str, Any]], budget: int
) -> tuple[list[dict[str, Any]], str, int, list[str]]:
    """Pack tool results, newest first, whole item at a time where possible."""
    ordered = list(reversed([item for item in items if isinstance(item, dict)]))
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    rendered: list[str] = []
    used = 0
    for index, item in enumerate(ordered):
        identifier = str(
            item.get("toolCallId") or item.get("toolName") or item.get("name") or index
        )
        block = _render_tool_result(item)
        cost = count_text(block)
        if used + cost > budget:
            room = budget - used
            if room < MIN_ITEM_TOKENS:
                dropped.append(identifier)
                continue
            trimmed = truncate_text(
                str(item.get("text") or item.get("message") or ""),
                max(8, room // 2),
                "tail",
            )
            if not trimmed.text.strip():
                dropped.append(identifier)
                continue
            shortened = dict(item)
            shortened["text"] = trimmed.text
            block = _render_tool_result(shortened)
            cost = count_text(block)
            if used + cost > budget:
                dropped.append(identifier)
                continue
        kept.append(item)
        rendered.append(block)
        used += cost
    return kept, "\n\n".join(rendered), used, dropped


def _reclaim_sources(
    allocations: dict[str, int],
    floors: dict[str, int],
    policies: dict[str, ContextPolicy],
) -> list[tuple[str, int]]:
    """Blocks that can give tokens back, most expendable first."""
    candidates = [
        (kind, allocations[kind] - floors[kind])
        for kind in allocations
        if kind != MESSAGES_KIND
        and allocations[kind] > floors[kind]
        and not policies[kind].is_hard
    ]
    # Droppable blocks give first, then elastic ones; within a class, the smallest
    # share goes first so the prompt loses the least information per token.
    candidates.sort(
        key=lambda item: (
            0 if policies[item[0]].protection == PROTECTION_DROPPABLE else 1,
            allocations[item[0]],
        )
    )
    return candidates


def allocate(
    blocks: list[ContextBlockInput],
    messages: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    *,
    window: int,
    max_output: int,
    safety_margin: int,
    scaffolding_tokens: int,
    model: str = "",
    counter_description: dict[str, Any] | None = None,
    protected_content: str = "",
    render_block: Callable[[str, str], str] | None = None,
    summary_version: int = 0,
    summary_watermark: float = 0.85,
) -> Allocation:
    """Plan the prompt. Raises ``BudgetInfeasible`` when a guarantee cannot hold.

    ``render_block(kind, content)`` lets the caller measure the *rendered* form
    (which adds section headers) while this module decides the sizes. Measuring
    the raw content instead is how a prompt ends up slightly over the window and
    gets a provider 400.
    """
    usable_tokens = int(window) - int(max_output) - int(safety_margin)
    if usable_tokens < MIN_USABLE_TOKENS:
        raise BudgetInfeasible(
            MIN_USABLE_TOKENS - usable_tokens,
            f"窗口 {window} 减去输出预留 {max_output} 与安全余量 {safety_margin} 后不足",
        )
    # Reserve the join separators up front rather than discovering them after every
    # block has been sized: the ceiling has to hold for the *rendered* prompt.
    block_count = len({block.kind for block in blocks if block.kind})
    usable_tokens = max(0, usable_tokens - block_count * SECTION_JOIN_TOKENS)

    report = ContextReport(
        model=model,
        counter=dict(counter_description or {}),
        window=int(window),
        max_output=int(max_output),
        safety_margin=int(safety_margin),
        usable_tokens=usable_tokens,
        scaffolding_tokens=max(0, int(scaffolding_tokens)),
    )

    # --- candidates ------------------------------------------------------
    unique = _dedupe_blocks(blocks)
    policies = _policy_index(unique)
    notes: list[str] = []

    def _render(kind: str, content: str) -> str:
        if not content:
            return ""
        return render_block(kind, content) if render_block else content

    hard_blocks: list[ContextBlockInput] = []
    pool: list[ContextBlockInput] = []
    empty_blocks: list[ContextBlockInput] = []
    for block in unique:
        policy = policies[block.kind]
        if _is_empty(block, policy):
            empty_blocks.append(block)
            continue
        if policy.is_hard:
            hard_blocks.append(block)
        else:
            pool.append(block)

    floors: dict[str, int] = {}
    demands: dict[str, int] = {}
    #: Cost of a block's section header. A block's budget is *content* tokens, so
    #: the header has to be added wherever the content is fitted, and it has to be
    #: counted in the demand because the provider charges for it.
    header_costs: dict[str, int] = {
        block.kind: max(0, count_text(_render(block.kind, "x")) - count_text("x"))
        for block in unique
    }

    for block in hard_blocks:
        # A hard block is never cut, so its footprint *is* the rendered content.
        # The floor is that footprint rather than a policy constant: reserving more
        # than the block actually needs locks budget away from the blocks that do
        # want it, which is the under-utilisation this algorithm exists to fix.
        #
        # There is deliberately no "content must be at least min_tokens" rule:
        # `min_tokens` is a *budget floor* (what a block is guaranteed), not a
        # writing requirement. Demanding a minimum length would make every request
        # fail the day someone shortens a policy sentence.
        floor = count_text(block.content) + header_costs[block.kind]
        floors[block.kind] = floor
        demands[block.kind] = floor
    for block in pool:
        policy = policies[block.kind]
        # An allocation covers the *rendered* block, header included, so the
        # declared ceiling bounds what is placed and not only the raw text.
        demand = _demand(block, policy) + header_costs[block.kind]
        floors[block.kind] = min(policy.min_tokens, demand)
        demands[block.kind] = demand

    message_overhead = _message_overhead(messages)
    message_demand = message_overhead + min(
        count_messages(messages), max(0, usable_tokens)
    )
    floors[MESSAGES_KIND] = min(
        message_overhead + MIN_MESSAGES_RESERVE, max(0, usable_tokens)
    )
    demands[MESSAGES_KIND] = min(message_demand, max(0, usable_tokens))
    policies[MESSAGES_KIND] = policy_for(MESSAGES_KIND)
    header_costs[MESSAGES_KIND] = 0
    if message_demand > usable_tokens:
        notes.append("messages_demand_exceeds_window")

    # --- floors must be satisfiable (invariant I2) ------------------------
    reserved_floors = scaffolding_tokens + sum(floors.values())
    if reserved_floors > usable_tokens:
        raise BudgetInfeasible(
            reserved_floors - usable_tokens,
            "硬性块与消息下限之和超过可用预算（不静默降级）",
        )

    allocations: dict[str, int] = dict(floors)
    remaining = usable_tokens - reserved_floors

    # --- weighted water-filling with flow-back (invariant I1) -------------
    for _ in range(MAX_FILL_ROUNDS):
        if remaining <= 0:
            break
        active = [
            kind
            for kind, demand in demands.items()
            if allocations.get(kind, 0) < demand
        ]
        if not active:
            break
        weight_total = sum(max(0.0, policies[kind].weight) for kind in active)
        if weight_total <= 0:
            break
        taken = 0
        for kind in active:
            share = remaining * max(0.0, policies[kind].weight) / weight_total
            grant = min(demands[kind] - allocations.get(kind, 0), int(share))
            if grant > 0:
                allocations[kind] = allocations.get(kind, 0) + grant
                taken += grant
        if taken <= 0:
            break
        remaining -= taken

    # --- pack messages, reclaiming from expendable blocks if needed -------
    packed = _pack_messages(
        messages, allocations.get(MESSAGES_KIND, 0), protected_content=protected_content
    )
    attempts = 0
    while not packed[5] and protected_content.strip() and attempts < len(pool) + 1:
        # The current turn did not survive. Take the tokens back from the blocks
        # that can give them, most expendable first, and try again.
        reclaimed = False
        for kind, available in _reclaim_sources(allocations, floors, policies):
            if available <= 0:
                continue
            step = max(24, available)
            allocations[kind] -= step
            allocations[MESSAGES_KIND] = allocations.get(MESSAGES_KIND, 0) + step
            reclaimed = True
            break
        if not reclaimed:
            break
        attempts += 1
        packed = _pack_messages(
            messages,
            allocations.get(MESSAGES_KIND, 0),
            protected_content=protected_content,
        )

    if protected_content.strip() and not packed[5]:
        raise BudgetInfeasible(
            count_text(protected_content) + MIN_MESSAGES_RESERVE,
            "当前用户消息无法在预算内保留（不静默丢弃）",
        )
    kept_messages, message_tokens, dropped_ids, truncated_ids, omitted, _ = packed

    # --- pack the item-counted blocks -------------------------------------
    # The budget is *content* tokens; the section header is added when the block is
    # placed, so it is charged separately here.
    evidence_budget = max(
        0, allocations.get("retrieval_evidence", 0) - header_costs.get("retrieval_evidence", 0)
    )
    evidence_packed = pack_evidence(evidence, evidence_budget)
    tool_budget = max(
        0, allocations.get("tool_results", 0) - header_costs.get("tool_results", 0)
    )
    retained_tools, tool_text, tool_tokens, tool_dropped = pack_tool_results(
        tool_results, tool_budget
    )

    # --- place each block inside its allocation ---------------------------
    sections: dict[str, str] = {}
    used = scaffolding_tokens
    for block in sorted(unique, key=lambda item: item.priority, reverse=True):
        policy = policies[block.kind]
        budget = allocations.get(block.kind, 0)
        if block.kind == MESSAGES_KIND:
            continue
        if any(item is block for item in empty_blocks):
            report.blocks.append(
                BlockReport(
                    kind=block.kind,
                    priority=block.priority,
                    protection=policy.protection,
                    budget=0,
                    actual=0,
                    dropped=True,
                    count_by=policy.count_by,
                    notes=["empty_block"],
                )
            )
            continue
        if block.kind == "retrieval_evidence":
            # Measure it as it will appear: the section header is part of the
            # prompt the provider charges for.
            actual = (
                count_text(_render(block.kind, evidence_packed.text))
                if evidence_packed.items
                else 0
            )
            report.blocks.append(
                BlockReport(
                    kind=block.kind,
                    priority=block.priority,
                    protection=policy.protection,
                    budget=budget,
                    actual=actual,
                    truncated=bool(evidence_packed.dropped_ids),
                    dropped=not evidence_packed.items,
                    dropped_ids=list(evidence_packed.dropped_ids),
                    citation_map=dict(evidence_packed.citation_map),
                    count_by="items",
                )
            )
            if evidence_packed.items:
                sections[block.kind] = evidence_packed.text
            used += actual
            continue
        if block.kind == "tool_results":
            actual = count_text(_render(block.kind, tool_text)) if retained_tools else 0
            report.blocks.append(
                BlockReport(
                    kind=block.kind,
                    priority=block.priority,
                    protection=policy.protection,
                    budget=budget,
                    actual=actual,
                    truncated=bool(tool_dropped),
                    dropped=not retained_tools,
                    dropped_ids=list(tool_dropped),
                    count_by="items",
                )
            )
            if retained_tools and tool_text.strip():
                sections[block.kind] = tool_text
            used += actual
            continue

        # A block's content budget: the section header is charged by the allocator
        # separately, so it is subtracted before the content is fitted.
        allocation = allocations.get(block.kind, 0)
        content_budget = max(0, allocation - header_costs.get(block.kind, 0))
        if policy.is_hard:
            # A hard block is whole or it is a failure (invariant I2). Cutting a
            # policy statement to "as much as fits" would leave the model with a
            # rule that reads complete and is not.
            rendered = _render(block.kind, block.content)
            actual = count_text(rendered)
            if actual > allocation:
                logger.warning(
                    "hard block over its allocation: kind=%s actual=%s budget=%s",
                    block.kind,
                    actual,
                    allocation,
                )
                raise BudgetInfeasible(
                    actual - allocation,
                    f"安全块 {block.kind} 需要 {actual} tokens，仅分配 {allocation}",
                )
            report.blocks.append(
                BlockReport(
                    kind=block.kind,
                    priority=block.priority,
                    protection=policy.protection,
                    budget=allocations.get(block.kind, 0),
                    actual=actual,
                )
            )
            sections[block.kind] = block.content
            used += actual
            continue

        result = truncate_text(block.content, content_budget, policy.truncation)
        # Reported tokens are the rendered ones (header included); `budget` is the
        # same unit, so the two are comparable at a glance.
        actual = (result.tokens + header_costs.get(block.kind, 0)) if result.text else 0
        report.blocks.append(
            BlockReport(
                kind=block.kind,
                priority=block.priority,
                protection=policy.protection,
                budget=allocation,
                actual=actual,
                truncated=result.truncated,
                dropped=not result.text.strip(),
                omitted_tokens=result.omitted_tokens,
                notes=list(result.notes),
            )
        )
        if result.text.strip():
            sections[block.kind] = result.text
        used += actual

    # --- messages report ---------------------------------------------------
    messages_report = MessagesReport(
        budget=allocations.get(MESSAGES_KIND, 0),
        kept=len(kept_messages),
        dropped=len(dropped_ids),
        tokens=message_tokens,
        dropped_ids=list(dropped_ids),
        truncated_ids=list(truncated_ids),
        omitted_tokens=omitted,
        evicted_ids=list(dict.fromkeys([*dropped_ids, *truncated_ids])),
    )
    report.messages = messages_report
    report.evidence = EvidenceReport(
        budget=allocations.get("retrieval_evidence", 0),
        kept=len(evidence_packed.items),
        dropped=len(evidence_packed.dropped_ids),
        tokens=evidence_packed.tokens,
        dropped_ids=list(evidence_packed.dropped_ids),
        citation_map=dict(evidence_packed.citation_map),
    )
    used += message_tokens
    report.used_tokens = used
    report.notes = notes

    if used > usable_tokens:
        # The planner and the packer disagree. Refuse rather than send a prompt
        # that will be rejected by the provider; this is a bug, not a condition.
        raise BudgetInfeasible(
            used - usable_tokens,
            "内部错误：装配后的 prompt 超过可用预算（分配与打包不一致）",
        )

    report.needs_summary = bool(
        messages_report.evicted_ids
        or (usable_tokens and used / usable_tokens >= summary_watermark)
    )
    summary_request: SummaryRequest | None = None
    if report.needs_summary and messages_report.evicted_ids:
        summary_request = SummaryRequest(
            covered_from_message_id=messages_report.evicted_ids[0],
            covered_until_message_id=messages_report.evicted_ids[-1],
            previous_version=int(summary_version or 0),
            message_count=len(messages_report.evicted_ids),
            reason=(
                "messages_evicted"
                if messages_report.dropped_ids
                else "messages_truncated"
            ),
        )
        report.summary_request = summary_request

    return Allocation(
        messages=kept_messages,
        sections=sections,
        evidence=evidence_packed,
        report=report,
        evicted_message_ids=list(messages_report.evicted_ids),
        estimated_input_tokens=used,
        summary_request=summary_request,
    )
