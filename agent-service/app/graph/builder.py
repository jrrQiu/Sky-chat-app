from app.graph.root import build_root_graph


def build_agent_graph():
    return build_root_graph()


__all__ = ["build_agent_graph", "build_root_graph"]
