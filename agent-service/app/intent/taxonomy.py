from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=1)
def load_intent_rules() -> dict:
    path = Path(__file__).with_name("rules.yaml")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def enabled_rules() -> list[dict]:
    payload = load_intent_rules()
    return [rule for rule in payload.get("rules", []) if rule.get("enabled", True)]
