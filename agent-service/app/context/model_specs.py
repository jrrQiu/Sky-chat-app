"""Per-model context window and output ceiling: one source for two budgets.

The context budget and the ``max_tokens`` sent to the provider must come from the
*same* number. Before this module they did not: the budget subtracted a
config-only ``output_reserve`` while the request sent no ``max_tokens`` at all, so
the input budget was computed against a ceiling the request did not enforce.

Facts here are transcribed from the vendor page rather than guessed, and the
page and retrieval date are recorded next to the numbers so a stale entry is
visible instead of silent:

* ``deepseek-flash`` / ``deepseek-v4-pro`` — context length **1M**, maximum
  output **384K**.
  Source: https://api-docs.deepseek.com/quick_start/pricing/ (retrieved 2026-06-02)
* ``deepseek-chat`` — the name still set as ``DEEPSEEK_MODEL`` in this
  repository — is **not** on that page (the legacy ``deepseek-v4-flash`` names are
  retired and served by Flash). It therefore has **no verified spec** and falls
  back to ``DEFAULT_MODEL_SPEC`` while reporting ``known: false``.

Two different numbers, deliberately kept apart:

``context_window``
    What the model accepts (input + output).
``max_output_limit``
    What the model *can* emit. 384K is a capability, not a per-call policy: making
    it the output reserve would leave almost nothing for the prompt.
``default_max_output``
    What this service actually asks for (``max_tokens``) and reserves in the
    budget. Sized for a chat answer, and overridable per model with
    ``CONTEXT_MAX_OUTPUT_<MODEL>``.

Overrides are read from the environment because the vendor can change these
numbers without a code change on our side:

* ``CONTEXT_WINDOW_<MODEL>`` / ``CONTEXT_MAX_OUTPUT_<MODEL>`` /
  ``CONTEXT_MAX_OUTPUT_LIMIT_<MODEL>`` where ``<MODEL>`` is the model name
  upper-cased with non-alphanumerics replaced by ``_``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

#: The page every network entry below was read from.
SPEC_SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
SPEC_RETRIEVED_ON = "2026-06-02"

#: Output a chat answer is allowed to take when a model has no explicit entry.
#: This is the value reserved in the budget *and* sent as `max_tokens`.
DEFAULT_MAX_OUTPUT = 4096


@dataclass(frozen=True)
class ModelSpec:
    """One model's measured limits plus this service's output policy."""

    model: str
    #: Input + output the model accepts.
    context_window: int
    #: Capability ceiling. Never used as the reserve.
    max_output_limit: int
    #: What we ask for via `max_tokens`, and what the budget reserves.
    default_max_output: int
    #: False when the numbers are a conservative default, not a verified entry.
    known: bool
    source: str = ""

    def describe(self) -> dict[str, object]:
        return {
            "model": self.model,
            "context_window": self.context_window,
            "max_output_limit": self.max_output_limit,
            "default_max_output": self.default_max_output,
            "known": self.known,
            "source": self.source,
        }


#: Verified entries only. A model missing here uses `DEFAULT_MODEL_SPEC` and is
#: reported as `known: false` rather than being silently treated as verified.
MODEL_SPECS: dict[str, ModelSpec] = {
    "deepseek-flash": ModelSpec(
        model="deepseek-flash",
        context_window=1_000_000,
        max_output_limit=384_000,
        default_max_output=DEFAULT_MAX_OUTPUT,
        known=True,
        source=f"{SPEC_SOURCE_URL} (retrieved {SPEC_RETRIEVED_ON})",
    ),
    "deepseek-v4-pro": ModelSpec(
        model="deepseek-v4-pro",
        context_window=1_000_000,
        max_output_limit=384_000,
        default_max_output=DEFAULT_MAX_OUTPUT,
        known=True,
        source=f"{SPEC_SOURCE_URL} (retrieved {SPEC_RETRIEVED_ON})",
    ),
}

#: Conservative window for a model with no verified entry. Small enough that an
#: unknown model is very unlikely to be sent more than it accepts, which is the
#: failure this default exists to prevent (a provider 400).
DEFAULT_CONTEXT_WINDOW = 65_536

DEFAULT_MODEL_SPEC = ModelSpec(
    model="*",
    context_window=DEFAULT_CONTEXT_WINDOW,
    max_output_limit=DEFAULT_MAX_OUTPUT,
    default_max_output=DEFAULT_MAX_OUTPUT,
    known=False,
    source="conservative default (no verified spec)",
)


def _env_key(model: str, suffix: str) -> str:
    normalized = "".join(
        char if char.isalnum() else "_" for char in (model or "").strip().upper()
    )
    return f"CONTEXT_{suffix}_{normalized}"


def _env_int(env: dict[str, str], key: str) -> int | None:
    raw = (env.get(key) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _with_overrides(spec: ModelSpec, env: dict[str, str]) -> ModelSpec:
    if not spec.model or spec.model == "*":
        return spec
    window = _env_int(env, _env_key(spec.model, "WINDOW"))
    max_output = _env_int(env, _env_key(spec.model, "MAX_OUTPUT"))
    limit = _env_int(env, _env_key(spec.model, "MAX_OUTPUT_LIMIT"))
    if window is None and max_output is None and limit is None:
        return spec
    resolved_window = window or spec.context_window
    resolved_limit = limit or spec.max_output_limit
    resolved_output = max_output or spec.default_max_output
    return ModelSpec(
        model=spec.model,
        context_window=resolved_window,
        max_output_limit=resolved_limit,
        # Never let the reserve exceed the window; an inverted pair would make the
        # usable budget negative and every request infeasible.
        default_max_output=max(1, min(resolved_output, resolved_window - 1)),
        known=spec.known,
        source=f"{spec.source} + environment override",
    )


def resolve_model_spec(
    model: str | None,
    env: dict[str, str] | None = None,
) -> ModelSpec:
    """Resolve a model name to its spec, tolerating release suffixes.

    ``deepseek-flash-0731`` resolves to the ``deepseek-flash`` entry, matching how
    the price table treats release names.
    """
    environ = os.environ if env is None else env
    name = (model or "").strip()
    if name in MODEL_SPECS:
        return _with_overrides(MODEL_SPECS[name], environ)
    if name:
        for known, spec in MODEL_SPECS.items():
            if name.startswith(f"{known}-"):
                return _with_overrides(spec, environ)
    if name:
        unknown = ModelSpec(
            model=name,
            context_window=DEFAULT_MODEL_SPEC.context_window,
            max_output_limit=DEFAULT_MODEL_SPEC.max_output_limit,
            default_max_output=DEFAULT_MODEL_SPEC.default_max_output,
            known=False,
            source=DEFAULT_MODEL_SPEC.source,
        )
        return _with_overrides(unknown, environ)
    return DEFAULT_MODEL_SPEC


@lru_cache(maxsize=64)
def _cached_spec(model: str) -> ModelSpec:
    return resolve_model_spec(model)


def model_spec(model: str | None) -> ModelSpec:
    """Cached spec lookup. Cache is safe because overrides are process-static."""
    return _cached_spec((model or "").strip())


def clear_spec_cache() -> None:
    """Drop the cache. For tests that mutate the environment."""
    _cached_spec.cache_clear()


def describe_model_specs() -> dict[str, object]:
    """Reporting view for ``/ready``: what we believe, and how well we know it."""
    return {
        "source": SPEC_SOURCE_URL,
        "retrieved_on": SPEC_RETRIEVED_ON,
        "known_models": {
            name: _with_overrides(spec, os.environ).describe()
            for name, spec in MODEL_SPECS.items()
        },
        "default": DEFAULT_MODEL_SPEC.describe(),
    }
