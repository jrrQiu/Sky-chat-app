from app.config import settings


def trace_event(name: str, attributes: dict[str, object] | None = None) -> None:
    if settings.otel_exporter_otlp_endpoint:
        # OpenTelemetry instrumentation hooks into FastAPI and LiteLLM here.
        return


def trace_model_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
) -> None:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        # Langfuse integration records model, retrieval, and tool traces here.
        return
