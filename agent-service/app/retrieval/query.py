from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.retrieval.llm import LLMStructuredClient


DOMAIN_KB_MAP: dict[str, list[str]] = {
    "it": ["IT_SOP"],
    "security": ["SECURITY_POLICY"],
    "hr": ["HR_POLICY"],
    "finance": ["FINANCE_POLICY"],
}

DOMAIN_TERMS: dict[str, list[str]] = {
    "it": ["密码", "重置", "解锁", "账号", "软件", "设备", "安装", "it", "idp", "故障", "vpn", "网络"],
    "security": ["vpn", "外网", "防火墙", "内网", "权限", "网络安全", "敏感系统"],
    "hr": ["请假", "年假", "病假", "入职", "证明", "员工", "hrbp", "hr"],
    "finance": ["报销", "发票", "差旅", "付款", "预算", "采购", "财务", "erp"],
}

TICKET_RE = re.compile(
    r"(工单|案例|历史|排查|报错|错误码|故障|被拒|无法|超时|失败|异常|问题|ticket)"
)
POLICY_RE = re.compile(
    r"(制度|sop|流程|规范|政策|要求|条件|规则|怎么申请|如何申请|需要.*审批|多久|天数)"
)
ERROR_CODE_RE = re.compile(r"\b[A-Z]{2,}[-_ ]?\d{3,}\b")
TIME_RANGE_RE = re.compile(r"(20\d{2})年")


@dataclass
class SubQuery:
    kb: str
    query: str
    filters: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class QueryPlan:
    task_type: str
    entities: dict[str, Any]
    time_range: dict[str, Any] | None
    kb_scopes: list[str]
    subqueries: list[SubQuery]
    evidence_requirements: list[str]
    max_steps: int
    missing: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "entities": self.entities,
            "time_range": self.time_range,
            "kb_scopes": self.kb_scopes,
            "subqueries": [asdict(subquery) for subquery in self.subqueries],
            "evidence_requirements": self.evidence_requirements,
            "max_steps": self.max_steps,
            "missing": self.missing,
        }


def _detect_domains(text: str) -> list[str]:
    return [
        domain
        for domain, terms in DOMAIN_TERMS.items()
        if any(term in text for term in terms)
    ]


def _extract_entities(text: str) -> dict[str, Any]:
    products = []
    components = []
    environment = None
    systems = []

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
        if re.search(pattern, text, re.I):
            products.append(product)

    component_rules = [
        ("security|安全", "security"),
        ("identity|身份|账号|密码", "identity"),
        ("network|网络|vpn", "network"),
        ("finance|财务", "finance"),
    ]
    for pattern, component in component_rules:
        if re.search(pattern, text, re.I):
            components.append(component)

    if re.search(r"home|居家|远程", text, re.I):
        environment = "home"
    elif re.search(r"office|办公|公司", text, re.I):
        environment = "office"

    if re.search(r"财务系统", text):
        systems.append("finance-system")
    if re.search(r"vpn", text, re.I):
        systems.append("vpn")
    if re.search(r"erp", text, re.I):
        systems.append("erp")

    error_codes = ERROR_CODE_RE.findall(text)
    actions = []
    if re.search(r"申请|提交", text):
        actions.append("apply")
    if re.search(r"排查|定位|分析", text):
        actions.append("investigate")
    if re.search(r"查询|查看|检索", text):
        actions.append("query")

    return {
        "products": list(dict.fromkeys(products)),
        "components": list(dict.fromkeys(components)),
        "environment": environment,
        "systems": list(dict.fromkeys(systems)),
        "error_codes": error_codes,
        "actions": actions,
    }


def _extract_time_range(text: str) -> dict[str, Any] | None:
    year_match = TIME_RANGE_RE.search(text)
    if year_match:
        return {"year": int(year_match.group(1))}

    relative_match = re.search(r"最近(\d+)\s*(天|周|月)", text)
    if relative_match:
        return {"last": int(relative_match.group(1)), "unit": relative_match.group(2)}
    return None


def _missing_fields(plan_type: str, entities: dict[str, Any]) -> list[str]:
    if plan_type not in {"ticket_investigation", "mixed"}:
        return []
    missing = []
    if not entities.get("products"):
        missing.append("product")
    if not entities.get("environment"):
        missing.append("environment")
    if not entities.get("error_codes"):
        missing.append("error_code")
    return missing


def _subqueries_for(
    query: str,
    kb_ids: list[str],
    kb_keywords: dict[str, list[str]],
) -> list[SubQuery]:
    subqueries = []
    for kb_id in kb_ids:
        focus = " ".join(kb_keywords.get(kb_id, [])[:5])
        subqueries.append(
            SubQuery(
                kb=kb_id,
                query=f"{query} {focus}".strip(),
                filters={"status": "active"},
                rationale=f"命中知识库 {kb_id}",
            )
        )
    return subqueries


def understand_query(
    query: str,
    kb_keywords: dict[str, list[str]] | None = None,
) -> QueryPlan:
    text = (query or "").lower()
    has_ticket = bool(TICKET_RE.search(text))
    has_policy = bool(POLICY_RE.search(text))

    if has_ticket and has_policy:
        task_type = "mixed"
    elif has_ticket:
        task_type = "ticket_investigation"
    elif has_policy:
        task_type = "policy_query"
    else:
        task_type = "policy_query"

    domains = _detect_domains(text)
    if not domains:
        domains = ["it", "security"]

    kb_ids: list[str] = []
    for domain in domains:
        for kb_id in DOMAIN_KB_MAP.get(domain, []):
            if kb_id not in kb_ids:
                kb_ids.append(kb_id)

    if len(kb_ids) > 1 and task_type == "policy_query":
        task_type = "cross_kb_query"

    if task_type == "ticket_investigation" and not kb_ids:
        kb_ids = ["IT_SOP", "SECURITY_POLICY"]

    entities = _extract_entities(text)
    time_range = _extract_time_range(text)
    keywords = kb_keywords or {}
    subqueries = _subqueries_for(query, kb_ids, keywords)
    max_steps = {
        "policy_query": 0,
        "cross_kb_query": 0,
        "ticket_investigation": 4,
        "mixed": 5,
    }[task_type]

    requirements: list[str] = []
    if task_type in {"policy_query", "cross_kb_query", "mixed"}:
        requirements.append("至少一条 active 制度，且版本与时效明确")
    if task_type == "cross_kb_query":
        requirements.append("覆盖请求涉及的每个候选知识库")
    if task_type in {"ticket_investigation", "mixed"}:
        requirements.append("至少一条已解决相似工单，或明确说明无匹配")
        requirements.append("包含根因、时间线和解决动作")

    return QueryPlan(
        task_type=task_type,
        entities=entities,
        time_range=time_range,
        kb_scopes=kb_ids,
        subqueries=subqueries,
        evidence_requirements=requirements,
        max_steps=max_steps,
        missing=_missing_fields(task_type, entities),
    )


def _normalize_model_plan(
    query: str,
    raw: dict[str, Any],
    kb_keywords: dict[str, list[str]] | None,
) -> QueryPlan:
    allowed_tasks = {
        "policy_query",
        "cross_kb_query",
        "ticket_investigation",
        "mixed",
    }
    task_type = raw.get("task_type")
    if task_type not in allowed_tasks:
        return understand_query(query, kb_keywords)

    kb_scopes = raw.get("kb_scopes")
    if not isinstance(kb_scopes, list) or not all(isinstance(item, str) for item in kb_scopes):
        kb_scopes = []

    entities = raw.get("entities")
    if not isinstance(entities, dict):
        entities = _extract_entities(query.lower())

    subqueries = []
    raw_subqueries = raw.get("subqueries")
    if isinstance(raw_subqueries, list):
        for item in raw_subqueries:
            if isinstance(item, dict) and item.get("query") and item.get("kb"):
                subqueries.append(
                    SubQuery(
                        kb=str(item["kb"]),
                        query=str(item["query"]),
                        filters=item.get("filters", {"status": "active"}),
                        rationale=str(item.get("rationale", "")),
                    )
                )

    if not kb_scopes:
        kb_scopes = [subquery.kb for subquery in subqueries if subquery.kb]
    if not subqueries:
        subqueries = _subqueries_for(query, kb_scopes, kb_keywords or {})

    requirements = raw.get("evidence_requirements")
    if not isinstance(requirements, list):
        requirements = []
        if task_type in {"policy_query", "cross_kb_query", "mixed"}:
            requirements.append("至少一条 active 制度，且版本与时效明确")
        if task_type in {"ticket_investigation", "mixed"}:
            requirements.append("至少一条已解决相似工单，或明确说明无匹配")

    max_steps = raw.get("max_steps")
    if not isinstance(max_steps, int):
        max_steps = {
            "policy_query": 0,
            "cross_kb_query": 0,
            "ticket_investigation": 4,
            "mixed": 5,
        }[task_type]

    return QueryPlan(
        task_type=task_type,
        entities=entities,
        time_range=raw.get("time_range") if isinstance(raw.get("time_range"), dict) else _extract_time_range(query),
        kb_scopes=kb_scopes,
        subqueries=subqueries,
        evidence_requirements=requirements,
        max_steps=max_steps,
        missing=_missing_fields(task_type, entities),
    )


def understand_query_with_model(
    query: str,
    kb_keywords: dict[str, list[str]] | None = None,
    client: LLMStructuredClient | Any = None,
) -> QueryPlan:
    model_client = client or LLMStructuredClient()
    system = (
        "你是企业知识检索的查询理解器。"
        "只输出 JSON，字段为 task_type、entities、time_range、kb_scopes、subqueries、"
        "evidence_requirements、max_steps。task_type 只能是 policy_query、"
        "cross_kb_query、ticket_investigation、mixed。"
    )
    raw = model_client.complete_json(
        system=system,
        user=query,
    )
    if raw is None:
        return understand_query(query, kb_keywords)
    return _normalize_model_plan(query, raw, kb_keywords)
