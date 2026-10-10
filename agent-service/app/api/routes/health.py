from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.context.model_specs import describe_model_specs
from app.context.policy import describe_policies
from app.context.tokenizer import describe_counter
from app.core.effect_ledger import effect_ledger
from app.knowledge.ingest import index_cache_state
from app.intent.semantic import semantic_classifier_status
from app.observability.calibration import describe_calibration
from app.observability.tool_metrics import tool_metrics_snapshot
from app.observability.tracing import observability_status
from app.tools.actions import action_status
from app.tools.adapters import catalog_status, routing_status, tool_status
from app.tools.catalog import TOOLS, WRITE_DIRECT, approval_gated_tools, confirm_tools
from app.tools.resilience import describe_resilience
from app.retrieval.embedding import describe_dense_channel

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    """Readiness that reflects the durability contract.

    A service that cannot durably store checkpoints and approvals is not ready:
    it would accept a high-risk request it can never resume.
    """
    backend = getattr(request.app.state, "checkpoint_backend", None)
    graph = getattr(request.app.state, "graph", None)
    durable = backend == "postgres"
    dense_channel = describe_dense_channel()
    checks = {
        "graph": graph is not None,
        "checkpoint_backend": backend,
        "durable_checkpoints": durable,
        "approval_store": "postgres" if settings.database_url else "memory",
        "environment": settings.environment,
        "caller_identity": (
            "signed-jwt" if settings.agent_internal_jwt_secret else "legacy-static"
        ),
        "rate_limiting": settings.rate_limit_enabled,
        "retrieval_rail": settings.retrieval_rail_enabled,
        # Which retrieval channels this deployment will really use, so "hybrid
        # retrieval" is a claim an operator can verify rather than assume.
        "retrieval_mode": dense_channel["mode"],
        "dense_embeddings": dense_channel,
        "index_cache": index_cache_state(),
        # What the context engine is actually doing: which tokenizer counts the
        # budget, which model window it believes in (and whether that is a verified
        # number), how well the estimate matched the provider, and what each block
        # kind is guaranteed. "The context is budgeted" is a claim an operator can
        # now check instead of trust.
        "context": {
            "engine_version": 2,
            "counter": describe_counter(str(settings.deepseek_model)),
            "models": describe_model_specs(),
            "policies": describe_policies(),
            "calibration": describe_calibration(),
            "summary_enabled": settings.context_summary_enabled,
            "shadow_mode": settings.context_shadow_mode,
            "safety_margin_tokens": settings.context_safety_margin,
            "window_override": settings.model_context_window or None,
            "output_override": settings.output_reserve or None,
        },
        # What observability is actually active, so "traced" is verifiable.
        "observability": observability_status(),
        # Tool resilience: what is enforced, and what is open right now. The numbers are read
        # from the registry the calls write to, so this endpoint and /metrics cannot disagree.
        "tool_resilience": {
            **describe_resilience(),
            "effectLedger": effect_ledger.status(),
            "heartbeatSeconds": settings.tool_heartbeat_seconds,
            "timeouts": {
                "toolSeconds": settings.tool_timeout_seconds,
                "nodeIdleSeconds": settings.node_idle_timeout_seconds,
                "streamIdleSeconds": settings.stream_idle_timeout_seconds,
                # T5 is a *relationship*, not three numbers: reporting it as a boolean means an
                # operator can see a misconfiguration instead of inferring it from a timeout.
                "ordered": (
                    settings.tool_timeout_seconds
                    < settings.node_idle_timeout_seconds
                    < settings.stream_idle_timeout_seconds
                ),
            },
            "writePolicy": {
                "confirmationEnabled": settings.write_confirmation_enabled,
                "confirmTools": confirm_tools(),
                "directWriteTools": [
                    name
                    for name, spec in TOOLS.items()
                    if spec.writes and spec.write_policy == WRITE_DIRECT
                ],
                "approvalTools": approval_gated_tools(),
            },
            "metrics": tool_metrics_snapshot(),
        },
        # Whether the intent classifier can really compare meaning. Without an embedding model
        # it is unavailable and the rule/LLM paths decide, which an operator should be able to
        # see rather than infer from a stale design document.
        "intent_semantic": semantic_classifier_status(),
        # Which tools are integrations and which are still placeholders, so nobody has to read
        # the source to find out. `routing` says whether the model or the rules chose the tools,
        # because claiming model-driven tool use when the fallback is running would be a lie.
        "tools": {**tool_status(), "routing": routing_status(), "catalog": catalog_status()},
        # Which approved high-risk actions can actually reach a target system. Today none can:
        # the approval is real, the execution is not, and an operator should see that here
        # rather than after a user reports that their VPN still does not work.
        "write_actions": action_status(),
    }
    healthy = graph is not None and (durable or not settings.is_production)
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "degraded", "checks": checks},
    )
