# Sky Chat Java Service

Java business backbone for `sky-chat-app`.

## Stack

- Spring Boot 3.4
- Spring WebFlux for SSE
- MyBatis for persistence
- PostgreSQL as the shared business/vector database
- Redis for optional hot state and event streams

## Build

Install Maven first, then run:

```powershell
cd D:\VueProject\sky-chat-app\java-service
mvn spring-boot:run
```

The service starts on `http://localhost:8080` by default.

## Endpoints

```text
GET  /health
POST /v1/auth/register
POST /v1/auth/login
GET  /v1/auth/me
GET  /v1/conversations
POST /v1/conversations
PATCH /v1/conversations/{id}
DELETE /v1/conversations/{id}
GET  /v1/conversations/{id}/messages
POST /v1/conversations/{id}/messages
POST /v1/messages/delete
GET  /v1/approvals
PATCH /v1/approvals/{id}/decision
POST /v1/agent/chat/stream
POST /v1/chat/stream
GET  /v1/chat/runs/{runId}
DELETE /v1/chat/runs/{runId}
```

The agent chat endpoint proxies SSE to the Python Agent Service.

## Configuration

Copy `application.yml` values into your environment or use environment variables:

```text
DB_URL=jdbc:postgresql://localhost:5433/skychat
DB_USERNAME=skychat
DB_PASSWORD=password123
REDIS_HOST=localhost
REDIS_PORT=6379
AGENT_SERVICE_URL=http://localhost:8000
AGENT_SERVICE_TOKEN=replace-with-internal-jwt
JWT_SECRET=replace-with-a-long-random-secret
```

`spring.sql.init.mode` is set to `always` so the service creates missing tables
from `schema.sql` on startup.
