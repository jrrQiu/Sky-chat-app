from functools import lru_cache
from pathlib import Path

import yaml

@lru_cache
def load_agents() -> dict[str, dict]:
    path = Path(__file__).with_name("agents.yaml")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    return {agent["id"]: agent for agent in data["agents"]}


AGENTS = load_agents()


def get_agent(agent_id: str) -> dict:
    return AGENTS.get(agent_id) or AGENTS["knowledge"]


def can_agent_use_tool(agent_id: str, tool_name: str) -> bool:
    return tool_name in get_agent(agent_id).get("tools", [])


def list_agents() -> list[dict]:
    return list(AGENTS.values())
