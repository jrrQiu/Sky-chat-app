"""Guardrail primitives, organised by trigger point.

Modelled on the rails taxonomy the mature guardrail stacks use: **input**,
**retrieval**, **dialog**, **execution**, **output**. The original implementation
only had input and output, which left the two highest-risk paths unguarded:

* **retrieval rail** — retrieved documents, historical tickets and web content
  are untrusted text that lands directly in the model prompt. That is the classic
  indirect prompt-injection entry point, and nothing screened it.
* **execution rails** — tool arguments and tool results crossed the boundary
  unvalidated, so a confused or compromised model could smuggle instructions into
  a write, or a tool response could carry instructions back into the loop.

Two rules are deliberately encoded here:

1. **Guardrails are a runtime layer, not an access layer.** They never replace the
   RBAC checks; authorisation stays in `registry/rbac.py`.
2. **Log before blocking.** Every rail reports what it acted on, and each can run
   in observation mode (`*_log_only`) so a rollout does not start by refusing
   benign traffic.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Direct instructions aimed at the assistant, in the languages this deployment
# serves plus the usual English forms. Kept intentionally narrow: a rail that
# matches everything also refuses legitimate questions *about* instructions.
PROMPT_INJECTION = re.compile(
    r"(ignore (all )?(previous|prior|above)|disregard (the )?(previous|prior|above)"
    r"|忽略(以上|之前|前面)|无视(以上|之前)|覆盖(系统|安全)策略|override (the )?system"
    r"|bypass (the )?(policy|rules)|reveal (your )?system prompt|输出(你的)?系统提示"
    r"|you are now|从现在开始你是|developer mode|jailbreak)",
    re.I,
)

# Patterns that are only suspicious in *retrieved* content or tool output, never
# in a user's own message (a user may legitimately write "system:").
INDIRECT_INJECTION = re.compile(
    r"(\bsystem\s*[:：]|\bassistant\s*[:：]|\binstructions?\s*[:：]\s*\w"
    r"|do not tell the user|不要告诉用户|忽略(以上|之前)(所有)?(指令|规则|要求)"
    r"|新的(指令|规则)|new instructions?)",
    re.I,
)

# Data-exfiltration shapes seen in markdown-rendering chat clients.
EXFILTRATION = re.compile(
    r"(!\[[^\]]*\]\(\s*https?://|<\s*img[^>]+src\s*=|https?://\S+\?\S*=)"
    r"|\bfetch\s*\(\s*['\"]https?://",
    re.I,
)

SENSITIVE_CONTENT = re.compile(
    r"(\b\d{15,19}\b|\b\d{3}-\d{2}-\d{4}\b|password\s*[:=]|token\s*[:=]|身份证号|银行卡号)"
)

# Tool-level guardrails. Arguments are allow-listed so a model cannot invent a
# parameter that widens a write, and tool text is both bounded and screened.
MAX_TOOL_TEXT_CHARS = 20_000
ALLOWED_TOOL_ARG_KEYS = frozenset(
    {
        "request_id",
        "user_id",
        "intent",
        "risk_level",
        "latest_user_message",
        "idempotency_key",
        "effect_key",
        "action",
        "selected_agent",
        "turn_id",
        "query",
    }
)


@dataclass
class RailVerdict:
    """Outcome of one rail check."""

    allowed: bool
    reason: str | None = None
    matches: list[str] = field(default_factory=list)


def redact_text(text: str) -> str:
    return SENSITIVE_CONTENT.sub("[REDACTED]", text)


def guard_input(text: str) -> dict[str, Any]:
    """Input rail.

    Injection is refused; sensitive data is **redacted rather than refused**.
    Blocking a whole request because it contains a 15-19 digit number produced a
    high false-positive rate (order numbers, ticket ids) without adding
    protection, because the model input is what actually matters.
    """
    if PROMPT_INJECTION.search(text):
        return {"allowed": False, "reason": "Detected prompt injection attempt"}

    sanitized = redact_text(text)
    return {"allowed": True, "sanitized": sanitized, "redacted": sanitized != text}


def screen_retrieved_content(text: str) -> RailVerdict:
    """Retrieval rail: is this chunk safe to place in the model prompt?"""
    matches: list[str] = []
    for name, pattern in (
        ("prompt_injection", PROMPT_INJECTION),
        ("indirect_injection", INDIRECT_INJECTION),
        ("exfiltration", EXFILTRATION),
    ):
        if pattern.search(text):
            matches.append(name)
    if matches:
        return RailVerdict(
            allowed=False, reason="RETRIEVAL_CONTENT_FLAGGED", matches=matches
        )
    return RailVerdict(allowed=True)


def apply_retrieval_rail(
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition evidence into (kept, quarantined).

    Quarantined chunks are dropped rather than forwarded: losing a little recall
    is a far better failure mode than letting retrieved text steer the agent.
    """
    if not settings.retrieval_rail_enabled:
        return evidence, []

    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in evidence:
        text = f"{item.get('title', '')}\n{item.get('content', '')}"
        verdict = screen_retrieved_content(text)
        if verdict.allowed or settings.retrieval_rail_log_only:
            kept.append(item)
            if not verdict.allowed:
                blocked.append(
                    {
                        "source_id": item.get("source_id"),
                        "reason": verdict.reason,
                        "matches": verdict.matches,
                        "log_only": True,
                    }
                )
            continue
        blocked.append(
            {
                "source_id": item.get("source_id"),
                "reason": verdict.reason,
                "matches": verdict.matches,
                "log_only": False,
            }
        )

    if blocked:
        logger.warning(
            "Retrieval rail quarantined %d/%d evidence item(s): %s",
            len(blocked),
            len(evidence),
            blocked[:5],
        )
    return kept, blocked


def screen_tool_input(tool_name: str, args: dict[str, Any]) -> RailVerdict:
    """Execution rail (before): reject arguments that do not belong."""
    if not settings.tool_rail_enabled:
        return RailVerdict(allowed=True)

    unexpected = sorted(set(args) - ALLOWED_TOOL_ARG_KEYS)
    if unexpected:
        return RailVerdict(
            allowed=False, reason="TOOL_ARGUMENT_NOT_ALLOWED", matches=unexpected
        )

    for key, value in args.items():
        if isinstance(value, str) and PROMPT_INJECTION.search(value):
            return RailVerdict(
                allowed=False, reason="TOOL_ARGUMENT_INJECTION", matches=[key]
            )
    return RailVerdict(allowed=True)


def screen_tool_output(tool_name: str, result: dict[str, Any]) -> RailVerdict:
    """Execution rail (after): bound the size and screen returned text."""
    if not settings.tool_rail_enabled:
        return RailVerdict(allowed=True)

    text = str(result.get("text") or "")
    if len(text) > MAX_TOOL_TEXT_CHARS:
        return RailVerdict(
            allowed=False, reason="TOOL_OUTPUT_TOO_LARGE", matches=[str(len(text))]
        )

    verdict = screen_retrieved_content(text)
    if not verdict.allowed:
        return RailVerdict(
            allowed=False, reason="TOOL_OUTPUT_FLAGGED", matches=verdict.matches
        )
    return RailVerdict(allowed=True)


def enforce_tool_rails(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply both execution rails. Returns a refusal payload, or None to proceed."""
    verdict = screen_tool_input(tool_name, args)
    if not verdict.allowed and not settings.tool_rail_log_only:
        logger.warning("Tool input rail refused %s: %s", tool_name, verdict)
        return {
            "success": False,
            "text": "工具调用被安全护栏拒绝。",
            "rail_blocked": {"stage": "input", "reason": verdict.reason,
                             "matches": verdict.matches},
        }

    verdict = screen_tool_output(tool_name, result)
    if not verdict.allowed and not settings.tool_rail_log_only:
        logger.warning("Tool output rail refused %s: %s", tool_name, verdict)
        return {
            "success": False,
            "text": "工具返回内容被安全护栏拒绝。",
            "rail_blocked": {"stage": "output", "reason": verdict.reason,
                             "matches": verdict.matches},
        }
    return None
