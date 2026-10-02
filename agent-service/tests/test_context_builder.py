from app.context.builder import assemble_context, estimate_tokens


def test_context_builder_uses_recent_messages_as_model_messages():
    state = {
        "messages": [
            {"role": "user", "content": "VPN 申请需要什么"},
            {"role": "assistant", "content": "需要主管和系统管理员审批"},
        ],
        "knowledge_context": "VPN 制度 v2.0：双人审批",
        "tool_results": [],
        "slots": {},
    }

    assembled = assemble_context(
        state,
        {"name": "Knowledge Agent", "description": "检索制度"},
    )

    assert assembled.messages == state["messages"]
    assert "主管和系统管理员审批" not in assembled.system
    assert "检索证据" in assembled.system
    assert assembled.context_hash
    assert assembled.report["usable_tokens"] > 0


def test_context_builder_filters_expired_tool_results():
    state = {
        "messages": [{"role": "user", "content": "查询状态"}],
        "tool_results": [
            {
                "toolCallId": "tool_1",
                "toolName": "cmdb",
                "text": "旧结果",
                "eligible_for_context": True,
                "expires_at": "2000-01-01T00:00:00+00:00",
            },
            {
                "toolCallId": "tool_2",
                "toolName": "erp",
                "text": "当前结果",
                "eligible_for_context": True,
            },
        ],
    }

    assembled = assemble_context(state)
    assert "当前结果" in assembled.system
    assert "旧结果" not in assembled.system


def test_token_budget_does_not_drop_safe_policy_block():
    state = {
        "messages": [{"role": "user", "content": "申请 VPN"}],
        "knowledge_context": "制度内容 " * 300,
        "tool_results": [],
    }

    assembled = assemble_context(state)
    policy_block = next(
        block for block in assembled.blocks if block["kind"] == "system_policy"
    )
    assert estimate_tokens(policy_block["content"]) >= 1
    assert "系统策略" in assembled.system
