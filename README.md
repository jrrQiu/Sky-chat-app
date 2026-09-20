# Sky Chat

Sky Chat 是一个企业服务台 Agent 应用，当前仓库按职责拆成三个运行单元：

- `前端`：Vite + React + TypeScript，提供聊天、会话管理和登录界面。
- `java-service`：Spring Boot 3 + WebFlux + MyBatis，负责账号、会话、消息、审批和 SSE 业务代理。
- `agent-service`：FastAPI + LangGraph + LiteLLM，负责 Guardian、Orchestrator、工具调用和最终回答生成。

## 目录

```text
front-service/    React/Vite 前端
java-service/     Java 业务服务
agent-service/    Python Agent 服务
docs/             架构说明
```

## 本地启动

### 1. 基础设施

```powershell
docker compose up -d
```

启动 PostgreSQL 和 Redis。PostgreSQL 映射到 `localhost:5433`，默认账号为
`skychat/password123`。

### 2. Java 业务服务

```powershell
cd java-service
mvn spring-boot:run
```

默认地址：`http://localhost:8080`。启动时会执行
`src/main/resources/schema.sql` 初始化表结构。

### 3. Python Agent 服务

```powershell
cd agent-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

默认地址：`http://localhost:8000`。Java 服务通过
`AGENT_SERVICE_URL` 调用它。

### 4. React 前端

```powershell
cd front-service
npm install
npm run dev
```

默认地址：`http://localhost:5173`。前端默认访问
`http://localhost:8080`，如需修改请设置 `VITE_API_BASE_URL`。

## 环境变量

前端示例见 `front-service/.env.example`。

Java 服务主要变量：

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

Agent 服务主要变量：

```text
AGENT_SERVICE_TOKEN=replace-with-internal-jwt
DATABASE_URL=postgresql://skychat:password123@localhost:5433/skychat
REDIS_URL=redis://localhost:6379/0
DEEPSEEK_API_KEY=your-deepseek-key
```

## 架构边界

浏览器只与 Java 业务服务通信。Java 服务负责登录鉴权、会话/消息持久化和 SSE
代理；Python Agent 服务不直接暴露给浏览器，只接收 Java 服务转发的标准化
请求，并返回 SSE 事件。

```text
Browser (React/Vite)
  -> Java business service (Spring Boot/WebFlux)
       -> PostgreSQL/Redis
       -> Python agent service (FastAPI/LangGraph)
```
