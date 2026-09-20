# Sky Chat Agent Service

FastAPI + LangGraph agent service for `sky-chat-app`.

## Install

```powershell
cd D:\VueProject\sky-chat-app\agent-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Optional integrations:

```powershell
python -m pip install -r requirements-optional.txt
```

## Run

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then configure the Java business service:

```env
AGENT_SERVICE_URL=http://localhost:8000
AGENT_SERVICE_TOKEN=replace-with-internal-jwt
```

## Main Endpoints

```text
GET  /health
POST /v1/chat/stream
GET  /v1/approvals
PATCH /v1/approvals/{id}/decision
```

The chat stream endpoint returns normalized SSE events compatible with the
React frontend through the Java service proxy.

## Tool Adapters

Set `TOOL_PROVIDER=mock` for the current deterministic adapters. When real
enterprise APIs are available, set:

```env
TOOL_PROVIDER=openapi
TOOL_BASE_URL=https://internal-tools.example.com
TOOL_API_TOKEN=replace-with-tool-token
```

The service will call `POST {TOOL_BASE_URL}/{tool_name}` for each tool.
