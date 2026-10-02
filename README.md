# Sky Chat

Sky Chat 是一个企业服务台 Agent 应用，当前仓库按职责拆成三个运行单元：

- `前端`：Vite + React + TypeScript，提供聊天、会话管理和登录界面。
- `java-service`：Spring Boot 3 + WebFlux + MyBatis，负责账号、会话、消息、审批和 SSE 业务代理。
- `agent-service`：FastAPI + LangGraph + direct model gateway，负责 Guardian、Orchestrator、工具调用和最终回答生成。

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
python -m app
```

默认地址：`http://localhost:8000`。Java 服务通过
`AGENT_SERVICE_URL` 调用它。

> Windows 上请用 `python -m app` 启动。uvicorn 在非 reload 模式下会硬编码
> `ProactorEventLoop`，而 psycopg 的异步驱动无法在其上运行（`CHECKPOINT_BACKEND=postgres`
> 时会直接失败）。`uvicorn app.main:app --reload` 也可用（reload 子进程走 Selector loop）。
>
> 服务间身份已改为**签名 JWT**：`AGENT_INTERNAL_JWT_SECRET` 必须与 Java 侧配置一致，
> 否则生产环境拒绝启动、非生产环境所有接口返回 503。旧的静态
> `AGENT_SERVICE_TOKEN` 仅作为非生产的逃生通道（`ALLOW_STATIC_INTERNAL_TOKEN=true`）。

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
INVITE_BASE_URL=http://localhost:5173/invite
BOOTSTRAP_ADMIN_EMAIL=
```

### 账号开通

自助注册默认**关闭**（`AUTH_SELF_REGISTRATION_ENABLED=false`）：账号只能由管理员开通，
或凭管理员发出的**一次性邀请链接**设置密码。

全新部署的第一个管理员这样创建：把 `BOOTSTRAP_ADMIN_EMAIL` 设成你的邮箱后启动 Java 服务。
当库里没有任何在用的管理员时，启动日志会打印一条管理员邀请链接（只打印一次），
用浏览器打开它设置密码即可。之后在「成员管理」页里继续邀请其他人。

```powershell
$env:BOOTSTRAP_ADMIN_EMAIL = "you@example.com"
cd java-service; mvn spring-boot:run   # 从日志里复制邀请链接
```

要点：

- 邀请令牌只以 SHA-256 形式入库，链接一旦生成就无法再取回；重新发送会给同一邮箱
  换发新链接并**作废旧链接**。
- 邀请默认 72 小时过期（`INVITE_TTL_HOURS`），且只能用一次。
- 被邀请人不能自选角色，角色来自邀请本身。
- 管理员可以停用账号，停用**立即生效**（每次请求都会校验账号状态），不必等 JWT 过期。
- 最后一个管理员既不能被降权也不能被停用。

Agent 服务主要变量：

```text
AGENT_SERVICE_TOKEN=replace-with-internal-jwt
AGENT_INTERNAL_JWT_SECRET=<至少 32 字节随机值，两侧必须一致>
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
