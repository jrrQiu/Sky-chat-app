from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.intent.models import IntentResolution
from app.intent.semantic import SemanticIntentClassifier
from app.intent.taxonomy import enabled_rules


HIGH_CONFIDENCE = 0.85
CLARIFY_THRESHOLD = 0.60


def _extract_entities(text: str) -> dict[str, Any]:
    lower = text.lower()
    entities: dict[str, Any] = {
        "products": [],
        "systems": [],
        "environment": None,
        "error_codes": re.findall(r"\b[A-Z]{2,}[-_ ]?\d{3,}\b", text),
    }
    product_rules = [
        ("vpn", "VPN"),
        ("wi-?fi", "Wi-Fi"),
        ("firewall|防火墙", "Firewall"),
        ("财务系统", "FinanceSystem"),
        ("erp", "ERP"),
        ("idp|统一身份|密码", "IdP"),
        ("软件", "Software"),
        ("设备", "Device"),
        ("报销|发票", "Reimbursement"),
    ]
    for pattern, product in product_rules:
        if re.search(pattern, lower):
            entities["products"].append(product)

    if re.search(r"财务系统", lower):
        entities["systems"].append("finance-system")
    if re.search(r"vpn", lower):
        entities["systems"].append("vpn")
    if re.search(r"erp", lower):
        entities["systems"].append("erp")
    if re.search(r"home|居家|远程", lower):
        entities["environment"] = "home"
    elif re.search(r"office|办公|公司", lower):
        entities["environment"] = "office"

    entities["products"] = list(dict.fromkeys(entities["products"]))
    entities["systems"] = list(dict.fromkeys(entities["systems"]))
    return entities


def _rule_candidates(text: str, context_text: str) -> list[dict[str, Any]]:
    candidates = []
    for rule in enabled_rules():
        pattern = str(rule.get("pattern", ""))
        if not pattern:
            continue
        compiled = re.compile(pattern, re.I)
        matched_text = text if compiled.search(text) else None
        if matched_text is None and context_text and compiled.search(context_text):
            matched_text = context_text
        if matched_text is None:
            continue
        candidates.append(
            {
                "rule_id": rule["id"],
                "priority": int(rule.get("priority", 100)),
                "domain": rule["domain"],
                "operation": rule["operation"],
                "intent": rule["intent"],
                "risk": rule.get("risk", "low"),
                "confidence": float(rule.get("confidence", 0.95)),
                "source": "rule",
                "agent_id": rule["agent_id"],
                "action": rule["action"],
                "plan": list(rule.get("plan", [])),
                "matched_text": matched_text,
            }
        )
    candidates.sort(key=lambda item: item["priority"])
    return candidates


def _policy_risk(candidate: dict[str, Any]) -> str:
    operation = candidate.get("operation")
    domain = candidate.get("domain")
    if operation == "approve":
        return "high"
    if operation == "submit":
        if domain in {"network", "finance"}:
            return "high"
        if domain in {"it", "hr"}:
            return "medium"
        return candidate.get("risk", "high")
    return "low"


def _apply_policy(candidate: dict[str, Any]) -> dict[str, Any]:
    updated = dict(candidate)
    updated["risk"] = _policy_risk(candidate)
    return updated


def _llm_candidate(text: str, client: Any) -> dict[str, Any] | None:
    raw = client.complete_json(
        system=(
            "你是企业服务台意图分类器。只输出 JSON，字段为 domain、operation、"
            "intent、confidence、entities。operation 只能是 query/submit/approve/"
            "investigate。"
        ),
        user=text,
    )
    if not isinstance(raw, dict):
        return None
    domain = raw.get("domain")
    operation = raw.get("operation")
    if not isinstance(domain, str) or operation not in {"query", "submit", "approve", "investigate"}:
        return None
    return {
        "domain": domain,
        "operation": operation,
        "intent": str(raw.get("intent") or f"{domain}.{operation}"),
        "confidence": float(raw.get("confidence", 0.75)),
        "source": "llm",
        "agent_id": _agent_for_domain(domain, operation),
        "action": "query" if operation in {"query", "investigate"} else operation,
        "plan": [],
        "entities": raw.get("entities") if isinstance(raw.get("entities"), dict) else {},
        "risk": "low",
    }


def _agent_for_domain(domain: str, operation: str) -> str:
    if operation in {"query", "investigate"}:
        if domain in {"network", "finance", "hr", "it", "approval"}:
            return domain if domain != "approval" else "knowledge"
        return "knowledge"
    return domain


def _confidence_gate(
    candidate: dict[str, Any],
    text: str,
) -> tuple[dict[str, Any], bool, str]:
    confidence = float(candidate.get("confidence", 0.0))
    if confidence >= HIGH_CONFIDENCE:
        return candidate, False, ""
    if confidence >= CLARIFY_THRESHOLD:
        question = f"请补充关键信息：{text.strip()}"
        return candidate, True, question

    return {
        **candidate,
        "domain": "knowledge",
        "operation": "query",
        "intent": "knowledge.retrieve",
        "agent_id": "knowledge",
        "action": "query",
        "plan": ["answer_general_question"],
        "confidence": confidence,
    }, False, ""


def resolve_intent(
    text: str,
    context: dict[str, Any] | None = None,
    *,
    semantic_enabled: bool | None = None,
    llm_client: Any | None = None,
) -> IntentResolution:
    context_text = _context_text(context)
    rule_candidates = _rule_candidates(text, context_text)
    candidate = rule_candidates[0] if rule_candidates else None
    use_semantic = settings.intent_use_semantic if semantic_enabled is None else semantic_enabled

    if candidate is not None and float(candidate["confidence"]) >= HIGH_CONFIDENCE:
        selected = candidate
    else:
        semantic = SemanticIntentClassifier().classify(text) if use_semantic else None
        if semantic is not None and (
            candidate is None or float(semantic["confidence"]) > float(candidate["confidence"])
        ):
            selected = semantic
        elif candidate is not None:
            selected = candidate
        else:
            selected = semantic

        if selected is None or float(selected["confidence"]) < HIGH_CONFIDENCE:
            if llm_client is not None:
                llm_result = _llm_candidate(text, llm_client)
                if llm_result is not None:
                    selected = llm_result
            if selected is None:
                selected = {
                    "domain": "knowledge",
                    "operation": "query",
                    "intent": "knowledge.retrieve",
                    "confidence": 0.4,
                    "source": "fallback",
                    "agent_id": "knowledge",
                    "action": "query",
                    "plan": ["answer_general_question"],
                    "entities": {},
                    "risk": "low",
                }

    selected = _apply_policy(selected)
    entities = _extract_entities(text)
    if selected.get("entities"):
        entities.update(selected["entities"])
    selected["entities"] = entities
    gated, requires_clarification, question = _confidence_gate(selected, text)
    return IntentResolution(
        domain=gated["domain"],
        operation=gated["operation"],
        intent=gated["intent"],
        risk=gated["risk"],
        confidence=float(gated["confidence"]),
        source=gated["source"],
        agent_id=gated["agent_id"],
        action=gated["action"],
        plan=list(gated.get("plan", [])),
        entities=entities,
        slots={},
        missing_slots=[],
        requires_clarification=requires_clarification,
        clarifying_question=question,
    )


def _context_text(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    parts: list[str] = []
    messages = context.get("messages") or []
    for message in messages[-6:]:
        parts.append(str(message.get("content", "")))
    if context.get("summary"):
        parts.append(str(context["summary"]))
    if context.get("slots"):
        parts.append(str(context["slots"]))
    if context.get("approval"):
        parts.append(str(context["approval"]))
    return " ".join(parts)
