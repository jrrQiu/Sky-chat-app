"""Token-to-cost conversion from a configurable price table.

Why a table instead of a constant: model prices change, and a cost report that
silently uses a stale number is worse than no cost report. Every entry therefore
records where the number came from, and a model that has no verified entry
produces **no** cost — the absence is reported (`llm.cost_unavailable`) rather
than filled in with a guess.

Defaults come from the official DeepSeek pricing page
(https://api-docs.deepseek.com/quick_start/pricing, retrieved 2026-05-23), which
lists `deepseek-flash` and `deepseek-v4-pro` in USD per 1M tokens:

* `deepseek-flash`   — cache hit  0.003 off-peak / 0.006 peak,
                       cache miss 0.15  off-peak / 0.30  peak, output 0.60 / 1.20
* `deepseek-v4-pro`  — cache hit  0.022 off-peak / 0.044 peak,
                       cache miss 0.66  off-peak / 1.32  peak, output 1.98 / 3.96

`deepseek-chat` (the value of `DEEPSEEK_MODEL` in this repository) is **not** on
that page — it is a retired name — so it is deliberately absent from the table
and its cost is reported as unavailable.

The default rate is the **off-peak** one. DeepSeek's peak multiplier is a
wall-clock function of UTC, and a cost number that changes with the time of day
cannot be asserted in CI; off-peak is also the conservative choice, because a
report built from it under-states rather than over-states spend.

Overrides are read from the environment rather than from `app.config.Settings`
on purpose: that module is shared with concurrent work, and pricing is
observability-local configuration. Two variables are honoured:

* ``OBSERVABILITY_PRICE_TABLE_JSON`` — a JSON object ``{model: {...}}`` replacing
  the built-in table. Each entry accepts ``input_per_mtok``,
  ``output_per_mtok``, ``cached_input_per_mtok`` and ``source``; a partial entry
  merges over the built-in entry for the same model.
* ``OBSERVABILITY_PRICE_RATE`` — ``off_peak`` (default) or ``peak``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_PER_MTOK = 1_000_000.0

OFF_PEAK = "off_peak"
PEAK = "peak"
RATE_CHOICES = (OFF_PEAK, PEAK)

PRICE_TABLE_ENV = "OBSERVABILITY_PRICE_TABLE_JSON"
PRICE_RATE_ENV = "OBSERVABILITY_PRICE_RATE"

DEEPSEEK_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing"

_REASON_NO_ENTRY = "no verified price for this model"
_REASON_CACHED_UNKNOWN = "provider did not report cache-hit tokens"


@dataclass(frozen=True)
class ModelPrice:
    """Verified per-1M-token prices for one model, in USD."""

    model: str
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None
    source: str = ""
    #: Coarse label for metrics (`off-peak` / `peak`); kept separate from
    #: `source` so a URL never becomes a Prometheus label value.
    rate_basis: str = "off-peak"

    def per_token(self, *, cached: bool) -> float | None:
        if not cached:
            return self.input_per_mtok / _PER_MTOK
        if self.cached_input_per_mtok is None:
            return None
        return self.cached_input_per_mtok / _PER_MTOK


# Verified entries only. A model missing here reports cost as unavailable.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "deepseek-flash": ModelPrice(
        model="deepseek-flash",
        input_per_mtok=0.15,
        output_per_mtok=0.60,
        cached_input_per_mtok=0.003,
        source=f"{DEEPSEEK_PRICING_URL} (off-peak)",
        rate_basis="off-peak",
    ),
    "deepseek-v4-pro": ModelPrice(
        model="deepseek-v4-pro",
        input_per_mtok=0.66,
        output_per_mtok=1.98,
        cached_input_per_mtok=0.022,
        source=f"{DEEPSEEK_PRICING_URL} (off-peak)",
        rate_basis="off-peak",
    ),
}


@dataclass(frozen=True)
class CostEstimate:
    """Outcome of pricing one model call."""

    #: USD, or ``None`` when the price is not verifiable.
    cost_usd: float | None
    #: The rate the price came from, or ``None`` when there is no cost.
    price: ModelPrice | None
    #: Why the cost is unavailable; empty when it is available.
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.cost_usd is not None


class PriceTable:
    """Model name -> verified price, with explicit unavailability."""

    def __init__(
        self,
        prices: dict[str, ModelPrice] | None = None,
        *,
        rate: str = OFF_PEAK,
    ) -> None:
        self._prices = dict(DEFAULT_PRICES if prices is None else prices)
        self._rate = rate if rate in RATE_CHOICES else OFF_PEAK

    @property
    def rate(self) -> str:
        return self._rate

    @property
    def models(self) -> list[str]:
        return sorted(self._prices)

    def price_for(self, model: str) -> ModelPrice | None:
        """Look up a model, tolerating the provider's occasional suffixes."""
        if not model:
            return None
        key = model.strip()
        if key in self._prices:
            return self._prices[key]
        # `deepseek-flash-0731` style release suffixes price as the base model.
        for known, price in self._prices.items():
            if key.startswith(f"{known}-"):
                return price
        return None

    def estimate(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int | None = None,
    ) -> CostEstimate:
        """Price one call. Never invents a number for an unknown model."""
        price = self.price_for(model)
        if price is None:
            return CostEstimate(
                cost_usd=None,
                price=None,
                unavailable_reason=f"{_REASON_NO_ENTRY} ({model or 'unknown'})",
            )

        cache_hits = max(0, min(cached_tokens or 0, max(0, input_tokens)))
        cache_misses = max(0, input_tokens) - cache_hits
        cached_rate = price.per_token(cached=True)
        if cache_hits and cached_rate is None:
            return CostEstimate(
                cost_usd=None,
                price=price,
                unavailable_reason=_REASON_CACHED_UNKNOWN,
            )

        miss_cost = cache_misses * (price.per_token(cached=False) or 0.0)
        hit_cost = cache_hits * (cached_rate or 0.0)
        output_cost = max(0, output_tokens) * (price.output_per_mtok / _PER_MTOK)
        return CostEstimate(
            cost_usd=miss_cost + hit_cost + output_cost,
            price=price,
        )

    def describe(self, model: str) -> dict[str, object]:
        """Reporting view used by the observability status endpoint."""
        price = self.price_for(model)
        if price is None:
            return {
                "model": model,
                "priced": False,
                "reason": f"{_REASON_NO_ENTRY} ({model or 'unknown'})",
            }
        return {
            "model": model,
            "priced": True,
            "rate": self._rate,
            "rate_basis": price.rate_basis,
            "input_per_mtok": price.input_per_mtok,
            "output_per_mtok": price.output_per_mtok,
            "cached_input_per_mtok": price.cached_input_per_mtok,
            "source": price.source,
        }


def _merge_price(model: str, entry: dict[str, object]) -> ModelPrice:
    base = DEFAULT_PRICES.get(model)
    return ModelPrice(
        model=model,
        input_per_mtok=float(
            entry.get("input_per_mtok", base.input_per_mtok if base else 0.0)
        ),
        output_per_mtok=float(
            entry.get("output_per_mtok", base.output_per_mtok if base else 0.0)
        ),
        cached_input_per_mtok=(
            float(entry["cached_input_per_mtok"])
            if entry.get("cached_input_per_mtok") is not None
            else (base.cached_input_per_mtok if base else None)
        ),
        source=str(entry.get("source") or (base.source if base else "configured")),
        rate_basis=str(
            entry.get("rate_basis") or (base.rate_basis if base else "configured")
        ),
    )


def load_price_table(environ: dict[str, str] | None = None) -> PriceTable:
    """Build the price table from the environment, falling back to defaults."""
    env = os.environ if environ is None else environ
    rate = env.get(PRICE_RATE_ENV, OFF_PEAK)
    raw = env.get(PRICE_TABLE_ENV, "").strip()
    if not raw:
        return PriceTable(rate=rate)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("%s is not valid JSON; using the built-in price table", PRICE_TABLE_ENV)
        return PriceTable(rate=rate)

    if not isinstance(parsed, dict) or not parsed:
        logger.warning("%s must be a non-empty JSON object; using defaults", PRICE_TABLE_ENV)
        return PriceTable(rate=rate)

    prices: dict[str, ModelPrice] = {}
    for model, entry in parsed.items():
        if not isinstance(entry, dict):
            logger.warning("Ignoring non-object price entry for %r", model)
            continue
        try:
            prices[str(model)] = _merge_price(str(model), entry)
        except (TypeError, ValueError):
            logger.warning("Ignoring unparseable price entry for %r", model)
    if not prices:
        return PriceTable(rate=rate)
    return PriceTable(prices, rate=rate)


#: Module-level table, loaded once. Tests build their own `PriceTable`.
price_table = load_price_table()


def estimate_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int | None = None,
) -> CostEstimate:
    return price_table.estimate(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )
