# 平台差距分析与改造路线图

对标对象：GitHub 上成熟的开源企业服务台 / ITSM 项目（Zammad、GLPI、Znuny(OTRS)、
osTicket、FreeScout、Chatwoot、iTop），以及 AI 原生侧的实现（Onyx/Danswer、Dify、
RAGFlow）与 LLM 工程实践（LangGraph 持久化/HITL、MCP、OWASP LLM Top 10、
OTel GenAI 约定、RAG 评测）。

配套文档：

- [itsm-capability-benchmark.md](itsm-capability-benchmark.md) — 10 个成熟开源项目的能力基准，逐条附一手文档链接
- [ai-service-desk-sota-report.md](ai-service-desk-sota-report.md) — AI 原生服务台与 LLM 工程实践基准
- 第 9 节是两份调研落地后的**修订**，其中包含一条新发现的 P0 安全问题与若干修正判断

本文所有"现状"结论都来自当前仓库代码，并附 `文件:行` 证据，可直接核对。

---

## 0. 结论摘要

**当前系统的真实定位是"聊天式 AI 助手 + 高风险审批中枢"，不是企业服务台。**
对标项目全部以**工单（Ticket）**为核心领域对象，围绕它构建生命周期、SLA、队列分派、
自动化、渠道、门户、报表；本仓库没有工单实体，仅有会话（Conversation）与消息（Message）。

这不是"少几个功能"，而是**领域模型缺一层**：知识库检索、历史工单检索、审批流都挂在
聊天会话上，无法承载"谁在处理、什么时候到期、升级给谁、处理结果如何闭环"。

### Top 12 必改项（按优先级）

> **本轮状态**：#1 #2 #3 #3b #4 #6 已修复并端到端验证（见 §12）；**#5（审批 UI）未做**，需要单独的前端工程。其余为 P1，未动。

| # | 优先级 | 问题 | 证据 | 改法方向 |
|---|---|---|---|---|
| 1 | **P0** | 开放自助注册：任何人可注册 `employee` 账号，密码仅 6 位，注册/登录/聊天**零限流** | `JwtAuthFilter.java:30`、`AuthService.java:36,83` | 关闭自助注册改为邀请/管理员开户；密码策略；按 §9.7 的行业基线加限流与验证码 |
| 2 | **P0** | 内部服务令牌为文档占位符，且 agent 服务监听 `0.0.0.0:8000`，凭 `X-User-ID` 即可冒充任意用户 | `agent-service/.env:1`、`application.yml:26`、`config.py:13`、`core/security.py:45` | 强随机令牌 + 服务间 mTLS/私网 + 不信任裸 `X-User-ID`（改签名/JWT） |
| 3 | **P0** | 审批无归属：没有 `decided_by`/`decided_at`，`required_approver_roles` 从不写入也从不校验 → 申请人可自审批；且**完全没有超时、会签、委派** | `agent-service/schema.sql:7-35`、`ApprovalIngestService.java:99-118` | 见 §9.2：按 iTop 语义建审批引擎（法定人数/超时结论/委派/邮件链接/审计轨迹） |
| 3b | **P0** | **权限撤销不生效**：索引被 `lru_cache` 永久缓存且无失效机制 → 给角色撤权后，进程不重启仍可检索到该文档 | `ingest.py:31`、`knowledge_repository.py:222` | 见 §9.1：变更事件驱动失效 + 过滤下推索引 + 细到用户/组 |
| 4 | **P0** | 无任何审计日志表/接口，用户与管理动作不可追溯 | 全仓 grep `audit` = 0 命中 | 追加式审计表 + 关键动作埋点 + 导出 |
| 5 | **P0** | 前端**没有审批界面**：整个 checkpoint 审批链路只能靠 API 调 | `front-service` 全量 grep `approval` = 0 命中 | 审批中心页面 + 待办角标 + 审批详情 |
| 6 | **P0** | 检索到的文档/网页不做间接提示注入检测；Guardian 仅正则，且只查用户消息 | `guardian.py:6-12,39` | 检索内容进入上下文前过检测；输出侧接地校验 |
| 7 | **P1** | 知识索引 `lru_cache(maxsize=1)` 进程内缓存，**知识更新必须重启**；`load_index()` 全量载入内存 | `ingest.py:31-32`、`knowledge_repository.py:222` | 外部索引/向量库；热更新与版本化；分页/租户过滤下推 SQL |
| 8 | **P1** | 默认向量通道是 **Hash 伪向量**（无 embedding key 时），"混合检索"实际只有词法 | `embedding.py:72-75` | 接真实 embedding 并持久化向量；或明确降级为 BM25 并如实标注 |
| 9 | **P1** | 可观测性是空实现：`tracing.py` 两个函数直接 `return`，无 trace/无 token/成本统计 | `observability/tracing.py:4-21` | 接 OTel + Langfuse，落 token/成本/时延；补指标端点 |
| 10 | **P1** | 评测无门槛：`run_eval.py` 只打印 JSON，无阈值无退出码；`citation_accuracy`/`groundedness` 是"非空"伪指标 | `eval/run_eval.py:10`、`eval/metrics.py` | LLM-as-judge + 忠实度；阈值化并进 CI |
| 11 | **P1** | `RunEventStore` 进程内 `ConcurrentHashMap` 且**无淘汰**：多副本 SSE 回放/取消失效，且事件永久驻留堆内存 | `RunEventStore.java:19,54` | 迁 Redis Streams（已有 `RedisEventStream` 可复用）；加 TTL 与上限 |
| 12 | **P1** | Java→Python 主链路 `AgentGatewayClient` **无超时/重试/熔断**；Java 无全局异常处理 | `AgentGatewayClient.java:39-43`、无 `@RestControllerAdvice` | 加超时+重试+熔断+降级；统一错误响应 |

---

## 1. 对标基准：成熟项目定义了哪些能力

成熟开源服务台的能力结构高度一致，可归纳为 7 层。本仓库在各层的覆盖情况如下。

| 层 | 成熟项目的能力 | 本仓库 |
|---|---|---|
| L1 领域对象 | 工单/事件/请求/问题/变更，父子与关联工单，合并拆分 | ❌ 只有会话与消息 |
| L2 流程与时限 | SLA 策略、服务日历/工作日历/节假日、响应与解决时限、多级升级、暂停条件 | ❌ 完全缺失 |
| L3 组织与分派 | 队列/组、分派规则、轮询/负载均衡、技能路由、关注者/抄送 | ❌ 完全缺失 |
| L4 自动化 | 触发器、宏/快捷回复、定时规则、业务规则引擎、自定义字段与表单 | ❌ 完全缺失 |
| L5 渠道 | 入站邮件(IMAP)成单、出站模板与多语言、Web 组件/在线客服、Slack/Teams、Webhook | ❌ 只有浏览器页面 |
| L6 知识与资产 | 文章生命周期(评审/过期/版本/反馈)、KCS、CMDB/CI 与影响分析 | ⚠️ 只有只读检索语料，无文章管理、无 CMDB |
| L7 治理 | 细粒度 RBAC、审计、数据保留、SSO/SAML/LDAP、CSAT、报表、多租户/多品牌 | ⚠️ 有基础 RBAC 与单租户占位；其余缺失 |

**表列项（几乎每款成熟产品都有，缺失即为硬伤）**：工单生命周期、SLA + 服务日历、
队列与分派、宏/触发器、邮件渠道、知识文章全生命周期、审计日志、SSO、CSAT、报表、
REST API + Webhook、版本化数据库迁移、自动化测试与 CI。

**差异化项（强者才有）**：CMDB 影响分析、变更/CAB 审批、多品牌多租户、技能路由、
KCS 闭环、多语言模板、插件市场。

---

## 2. 现状盘点（已具备的能力，值得保留）

不是全盘不足，以下是真实优势，改造时应保留并复用：

- **Agent 编排**：Guardian → Orchestrator → knowledge/service/high-risk 三路分支，
  子图封装清晰（`app/graph/root.py`），LangGraph 1.2 上的中断与恢复已经做到
  **PostgreSQL 持久化 + CAS + 幂等重放**（前一轮工作已验证）。
- **检索流水线**：混合检索（BM25 + 向量 + 精确）、查询理解/改写、重排、证据门控
  （`app/retrieval/`），并有角色级 ACL 过滤（`policy_search.py:11-20`、
  `router.py:9-18`、`tickets/similar_search.py:12-18`）——**ACL 是真的在生效**。
- **上下文装配**：带权重的块预算、token 估算、`context_hash`、工具结果过期过滤
  （`app/context/builder.py`），并在系统提示中明确"检索内容与网页内容为不可信数据"。
- **前端流式层**：消息状态机、工具调用状态、自动重连、`Last-Event-ID` 续传、
  主动中止（`front-service/features/chat/services/chat.service.ts`，1000+ 行），
  这块质量高于多数早期项目。
- **审批可靠性的地基**：`resume_request_id` 唯一、`claim_resume` 原子抢占、
  `effect_ledger` 写工具至多一次、双侧 reconciler。

---

## 3. P0：上线前必须修的安全与合规问题

### 3.1 身份与访问

- **开放注册**：`/v1/auth/register` 在白名单内（`JwtAuthFilter.java:30`），
  任何人都能开户（`AuthService.java:36`），密码只要 6 位（`:83`），
  且注册/登录**没有速率限制**——可被撞库与批量注册。
- **服务间身份可伪造**：agent 服务用 `X-User-ID` 头当作用户身份
  （`core/security.py:45-47`），只要持有内部令牌即可冒充任意用户；而该令牌在
  运行配置里就是占位符 `replace-with-internal-jwt`（`agent-service/.env:1`、
  `application.yml:26`），同时 agent 服务监听 `0.0.0.0:8000`（`config.py:13`）。
  README 声称"Python 服务不直接暴露给浏览器"，但**没有任何机制保证**。
- **JWT 过弱**：默认 TTL 7 天（`application.yml:30`），无刷新、无吊销、无
  `iss`/`aud` 校验（`JwtService.java:73`）。
- **无 SSO**：成熟服务台一律支持 SAML/OIDC/LDAP/AD 同步；本仓库只有本地账号。

### 3.2 审批合规（最严重）

- `approval_task` 没有 `decided_by` / `decided_at`（`agent-service/schema.sql:7-35`）
  → **审计时无法回答"是谁批准了这次高风险操作"**。
- `required_approver_roles` 字段存在但**从不写入**（`ApprovalIngestService.java:99-118`
  未设置该字段），也**从不校验** → 无"审批人必须具备某角色"约束。
- 结果：申请人自己就能批准自己的高风险请求（`ApprovalController` 只校验 `user_id`
  归属，不校验"审批人 ≠ 申请人"）。
- 无审批历史表：状态被就地覆盖，看不到流转轨迹。
- 无审批超时升级/委派/会签，与成熟产品的 CAB 审批差距明显。

### 3.3 审计与数据治理

- 无审计日志（全仓 `audit` 0 命中），无管理动作留痕，无导出。
- 无数据保留策略与删除接口（`memory.expires_at` 有字段但无治理流程）。
- 用户可 `POST /v1/conversations/{id}/messages` 写入**任意 role 的消息**
  （`MessageController.java:38-56`），包括伪造 `assistant` 历史，而这段历史会进入
  模型上下文（`ConversationContextService.java:156-174`）→ 历史伪造 + 注入向量。
- 根 `.env` 残留前 Next.js 时代的**真实** GitHub/Google OAuth secret 与 Tavily key，
  且 `NEXTAUTH_URL=http://localhost:3000` 与当前 Vite 应用端口不符 → 应清理并轮换密钥。

### 3.4 提示注入与内容安全

Guardian 是纯正则（`guardian.py:6-12`），且只检查 `latest_user_message`
（`guardian.py:39`）：**检索到的制度文档、历史工单、网页内容完全不检测**——
这正是 RAG 间接注入的经典入口。同时输入侧遇到 15-19 位数字或 `password:`
直接整单拒绝（`:29-30`），误杀率高。输出侧只做正则脱敏（`:85`），无接地校验、
无弃答策略。

---

## 4. P1：服务台产品能力缺口（领域模型）

### 4.1 工单实体与生命周期（最大缺口）

需要一个 `ticket` 领域对象与状态机，建议最小字段集：

```
ticket: id, tenant_id, type(incident|request|problem|change), status(new|open|pending|
        resolved|closed), priority, impact, urgency, requester_id, assignee_id, queue_id,
        category, subcategory, channel, sla_policy_id, first_response_due_at,
        resolve_due_at, first_responded_at, resolved_at, closed_at, reopened_count,
        parent_ticket_id, source_conversation_id, source_message_id, custom_fields
ticket_event: 追加式流转/评论/字段变更/通知记录（审计与时间线共用）
ticket_link: 关联/重复/阻塞 关系
```

- 与聊天打通：高风险或未解决问题**一键转工单**，把 `conversation_id` +
  `turn_id` + 检索证据 + 审批 ID 一并带过去（当前审批链路已经具备这些标识，天然可挂接）。
- 需要工单列表/详情/看板 API 与前端页面——**当前前端只有聊天一个界面**。

### 4.2 SLA 与时限（缺失，且是服务台的立身之本）

```
sla_policy: id, tenant_id, name, scope(按 type/queue/priority), first_response_minutes,
            resolve_minutes, pause_on(status=pending_customer), escalation_chain
service_calendar: 工作日、上下班时间、时区、节假日
sla_clock: ticket_id, target, started_at, paused_seconds, breach_at, breached, level
```

要点：时限必须走**服务日历**而不是自然时间；必须有暂停/恢复；必须有
breach 预警与多级升级（成熟产品如 Zammad 的 SLA + 日历、GLPI 的 SLA/OLA 都是这个模型）。
配套需要一个定时扫描任务（本仓库已经有 `ApprovalResumeReconciler` 可以照抄成
`SlaEscalationReconciler`）。

### 4.3 队列、分派与所有权

`queue` / `group` + 分派策略（手工 / 轮询 / 负载最低 / 技能标签）+ 关注者与抄送。
审批链路里已经有"agent_id"概念，但那是**AI Agent 标识**，不是人的组织归属，
两者必须区分清楚。

### 4.4 自动化

触发器（条件 → 动作）、宏/快捷回复、定时规则。注意：成熟产品的规则引擎是
**配置驱动、可热更新、可版本化**的；本仓库目前 `rules.yaml` 是打包进镜像的静态文件
（`app/intent/rules.yaml`），改规则要重新部署。

### 4.5 渠道

- **入站邮件**（IMAP 轮询 → 建单、线程化归并、附件、自动回复）——服务台第一渠道，
  当前完全没有。
- 出站邮件模板多语言、通知订阅与偏好。
- Web 组件/在线客服、Slack/Teams 应用、Webhook 出站（`grep webhook` = 0 命中）。

### 4.6 知识管理（当前只有"检索"，没有"管理"）

现在语料是代码里的 `corpus.py` + 数据库表，没有：文章编辑/发布/评审/过期、
版本与差异、有用/无用反馈、草稿从工单沉淀（KCS）、多语言、按租户隔离。
`knowledge_document` 有 `expires_at`/`status`/`authority_level` 字段，
但**没有定时任务去处理过期**，也没有任何管理 API。

### 4.7 治理其余项

CSAT 调研（0 命中）、报表指标（首次响应/解决时长/积压/重开率，0 命中）、
多租户（`ConversationContextService.java:60-64` 硬编码 `tenant=default`，
且 `conversation`/`message`/`approval_task` 都没有 `tenant_id`）、国际化
（前后端硬编码中文，0 命中）。

---

## 5. P1：AI 工程能力缺口

| 项 | 现状 | 证据 | 目标 |
|---|---|---|---|
| 追踪 | `trace_event`/`trace_model_call` 是空函数 | `observability/tracing.py:4-21` | OTel GenAI 约定 + Langfuse，落 trace_id |
| 成本 | 无 token/成本统计与预算 | 全仓 0 命中 | 按会话/用户/租户记账，超额熔断 |
| 评测 | 只打印、无阈值；golden set ≈11 条 | `eval/run_eval.py:10`、`eval/golden_set.py` | LLM-as-judge + 忠实度/接地率，阈值进 CI |
| 伪指标 | `citation_accuracy`/`groundedness` 只判断字段非空 | `eval/metrics.py` | 换成真实引用与接地判定 |
| 向量 | 默认 Hash 伪向量；语料不存向量，每次查询现算 | `embedding.py:72-75`、`corpus.py` 无向量字段 | 真实 embedding + 持久化 + ANN 索引 |
| 索引刷新 | `@lru_cache(maxsize=1)`，更新需重启 | `ingest.py:31-32` | 版本化索引 + 热更新 + 失效通知 |
| 权限检索 | 全量载入内存后在 Python 过滤 | `knowledge_repository.py:222` | 过滤条件下推 SQL，行级安全 |
| 提示词 | 硬编码在 Python 字符串里，无版本/无 A-B | `generate.py`、`ConversationContextService.java:55` | 提示词注册表 + 版本钉扎 + 灰度 |
| 模型 | 单provider、无重试/回退/缓存 | `gateway/litellm.py` | 重试 + 备用模型 + 语义缓存 |
| 工具 | 手写 mock/OpenAPI 适配器，无 MCP | `tools/adapters/` | 工具注册表 + schema 校验 + MCP |
| 弃答 | 无"我不知道"与置信度门限对外暴露 | `generate.py` | 低置信直接弃答并转人工 |
| HITL | 有审批中断，但**无人工接管/改稿/转人工** | `graph/nodes/service_workflow.py` | 人工编辑后再发送、转人工带上下文 |
| 上下文块 | `task_slots`/`tool_results` 是硬编码 `{}`/`[]` 占位 | `ConversationContextService.java:68-79` | 要么填实，要么删掉避免误导 |
| 摘要 | 不是 LLM 摘要，只是"最后一条用户+助手消息"拼接，且**每轮插一行** | `ConversationSummaryService.java:44-53,56-77` | LLM 滚动摘要 + 触发条件 + 压缩比校验 |

---

## 6. P1：可靠性与运维缺口

| 项 | 现状 | 证据 | 影响 |
|---|---|---|---|
| 运行事件存储 | 进程内 `ConcurrentHashMap`，无 TTL/无上限 | `RunEventStore.java:19,54` | 多副本回放失效；**事件永久驻留堆内存（泄漏）** |
| 服务间调用 | 主链路无超时/重试/熔断 | `AgentGatewayClient.java:39-43` | agent 挂起会拖住 Java 连接 |
| 异常处理 | 无 `@RestControllerAdvice`，无统一错误体 | Java 全量 grep = 0 | 前端拿到裸 500 |
| 校验 | 引了 `spring-boot-starter-validation` 但无 `@Valid` 使用 | pom + grep | 参数校验靠手写，易漏 |
| 数据库迁移 | 无 Flyway/Liquibase；`spring.sql.init.mode=always` 每次启动跑 schema | `application.yml:12-14` | 生产不可控、无法回滚 |
| CI/CD | 无 `.github/workflows`、无 Dockerfile、无 Makefile | 仓库根目录 | 无自动化质量门 |
| 监控 | 无 actuator、无指标端点 | pom 无 actuator | 无法观测 |
| 分页 | 消息列表接口无分页，全量返回 | `MessageController.java:28` | 长会话拖垮前端与数据库 |
| 测试 | Java 44 个主类仅 1 个测试类（15 例）；前端 0 测试；无跨服务契约测试 | — | 回归风险高 |
| 文档 | `docs/` 仅 1 篇架构说明 | — | 无运维手册/API 文档/DR 预案 |
| 备份 | 无备份与恢复流程 | — | 无法承诺 RPO/RTO |

---

## 7. 前端与体验缺口

- **没有审批中心**（P0）、没有工单列表/详情/看板、没有客服工作台、没有管理后台。
  目前前端是一个纯聊天应用（`src/pages/ChatConversationPage.tsx` + `features/chat|conversation|auth`）。
- 无国际化（0 命中）；无 ESLint 配置文件（devDeps 里有 eslint 但 `lint` 脚本只是
  `tsc --noEmit`）；无测试框架（无 vitest/jest）。
- 有 `ChartBlock`/`CodeBlock`/`ThinkingPanel` 等不错的渲染组件，可复用到工单详情。

---

## 8. 建议的落地顺序

### 阶段 0（1-2 周）安全与合规闸门 — 不完成不上线
1. 关闭自助注册，改为邀请/管理员开户；密码策略 ≥12 位 + 复杂度；登录失败锁定 + 限流。
2. 内部令牌换强随机值并纳入密钥管理；agent 服务只监听私网/回环；
   `X-User-ID` 改为短期内网 JWT（含 `sub`/`aud`/`exp`），不再信任裸头。
3. `approval_task` 增加 `decided_by`、`decided_at`、`decision_comment`、
   `required_approver_roles` 落库与校验；强制"申请人 ≠ 审批人"。
4. 新增 `audit_log` 追加式表 + 关键动作埋点（登录、审批、写工具、权限变更、导出）。
5. 前端补审批中心页面（待办列表 + 详情 + 通过与驳回 + 审批意见）。

**验收**：无法自助注册；伪造 `X-User-ID` 被拒；任一高风险审批可查到审批人/时间/理由；
自审批被服务端拒绝；审计表能还原一次完整审批链路。

### 阶段 1（3-6 周）工单最小闭环 + SLA 骨架
6. `ticket` / `ticket_event` / `ticket_link` 表 + 状态机 + Java API + 前端列表/详情。
7. 聊天 → 工单一键转单（携带会话、检索证据、审批 ID）。
8. `sla_policy` + `service_calendar` + `sla_clock`，加 `SlaEscalationReconciler` 定时任务。
9. 队列与分派（先手工 + 轮询），工单事件时间线。

**验收**：一条请求可从聊天转成工单并分派；SLA 到期前预警、到期后升级；
工单时间线完整可审计。

### 阶段 2（4-6 周）渠道、知识与门户
10. IMAP 入站成单 + 出站模板 + 线程化归并。
11. 知识文章生命周期（草稿/评审/发布/过期/版本/反馈）+ 管理 API + 热更新索引。
12. 自助门户（我的工单、知识搜索、提交请求）。
13. CSAT 调研 + Webhook 出站。

### 阶段 3（3-4 周）AI 质量闭环
14. OTel + Langfuse 实装，落 token/成本/时延；按租户预算与熔断。
15. 真实 embedding + 向量持久化 + ANN；检索过滤下推 SQL。
16. 评测升级：LLM-as-judge、忠实度、弃答率；阈值化并接入 CI 门禁。
17. 提示词注册表 + 版本钉扎；模型重试与回退；工具 schema 校验 + MCP。
18. 间接注入防护：检索内容入库前检测/打标，输出侧接地校验。

### 阶段 4（3-4 周）工程化与规模化
19. `RunEventStore` 迁 Redis Streams（复用已有 `RedisEventStream`）+ TTL。
20. Flyway 迁移替换 `sql.init.mode=always`；加 actuator + 指标 + 告警。
21. 补 CI：Java/Python/前端三套测试 + 契约测试 + 镜像构建；前端补 vitest。
22. 多租户（核心表加 `tenant_id` + 行级隔离）、国际化、报表指标。

---

## 9. 对标细化（两份调研落地后的修订）

> 引用来源：[itsm-capability-benchmark.md](itsm-capability-benchmark.md)（成熟开源服务台一手文档）与
> [ai-service-desk-sota-report.md](ai-service-desk-sota-report.md)（AI 原生侧一手文档）。
> 本节把上面的定性判断收敛成可验收的目标模型，并补一条调研中发现的**新安全问题**。

### 9.1 【新增·P0】权限撤销不生效：ACL 被永久缓存

先澄清一个事实：**我们的检索期 ACL 是真实生效的**——`policy_search.py:11-20`
（文档级角色交集，既用于候选集也用于 `document_map` 二次过滤）、`router.py:9-18`（知识库级）、
`similar_search.py:12-18`（历史工单级）。调研报告里"没有检索期文档级 ACL"的说法与代码不符。

但对照 Azure 的文档级访问控制文档，存在一个**具体的安全缺陷**：权限变更
"只有在权限元数据同步到索引之后才会反映在检索结果中"，继承型 ACL 还"需要显式刷新"。
我们用 `@lru_cache(maxsize=1)`（`ingest.py:31`）把索引永久缓存在进程内，且**没有任何失效机制**——
因此**给某角色撤销某文档的访问权后，只要 agent 进程不重启，该角色仍能检索到该文档**。
这是一条可以写进风险清单的越权路径，不是理论问题。

| 差距 | 现状 | 目标（对标） |
|---|---|---|
| 粒度 | 仅扁平角色 | 用户/组 + **嵌套组解析**（Azure `x-ms-query-source-authorization`、Glean 权限镜像） |
| 权限来源 | 手工写入 `allowed_roles` | 从源系统**同步**权限模型 |
| 生效点 | 全量载入内存后在 Python 过滤（`knowledge_repository.py:222`） | 过滤条件下推到索引/SQL（索引级安全过滤） |
| 变更传播 | 需重启进程 | 变更事件驱动失效 + 显式刷新 |
| 分块 ACL | 未存（靠父文档过滤，当前正确） | 若引入分块级 ACL，**必须投影到每个 chunk 行**，否则分块引用不会被过滤 |
| 引用点击 | 无复核 | 点击时**再次**校验权限（Glean） |
| 租户 | `tenant=default` 硬编码（`ConversationContextService.java:60-64`） | 索引与检索按租户隔离 |

### 9.2 审批引擎的目标模型（P0，差距远大于原判断）

iTop 的审批引擎是本轮调研里最完整的参考实现，我们目前连它的最小子集都没有：

| 能力 | iTop / GLPI | 我们的现状 |
|---|---|---|
| 法定人数语义 | `Approval ending` ∈ {首个通过 / 首个否决 / 首个回复} 三选一 | 单人一票，无会签 |
| 通过阈值 | GLPI 每步**最低通过百分比**，审批人可为用户/组/组成员 | 无 |
| 超时结论 | Delay 只在**覆盖时间窗**内计时，且必须有 `Approved if no answer` | **完全没有超时** |
| 审批人身份 | 校验角色，`bypass_profiles` 特权绕过单独记录 | `required_approver_roles` 字段从不写入也从不校验 |
| 委派/代理 | Substitute 用 OQL 解析，按 delay 百分比提醒代理人 | 无 |
| 无账号审批 | **一次性邮件链接**（CAB/主管场景关键） | 无 |
| 审计轨迹 | 按审批人列出答复/时间/意见，重入待审批会重置，催办也被追踪 | **无**（无 `decided_by`/`decided_at`） |
| 变更/CAB | Znuny 单独 ITSM 包，含 change builder/manager/**CAB** 角色 | 无 |

**这修正了原报告的一个判断**：不是"补三个字段"，而是需要一个真正的审批引擎
（多人/多级/法定人数/超时结论/委派/邮件链接/审计轨迹）。建议直接以 iTop 的字段语义为蓝本建表。

### 9.3 审批 UI 的功能规格（P0）

Dify 的 Human Input 节点给出了一份可直接照抄的交互与语义规格：
**超时默认 3 天**、必须接**超时分支**（"若未连接超时分支，工作流直接结束"）、
**"首次响应后请求即关闭"**、可通过**邮件链接投递且无需账号**、
决策按钮映射为动作 ID、必填字段未填完则按钮禁用。
对照我们：**没有超时、没有超时分支、没有界面、没有邮件投递**。
Glean 的默认行为也值得抄：**写操作在执行前默认暂停等待用户确认**，
"Run without user confirmation" 是显式的管理员 opt-in 而非常态。

### 9.4 SLA / 升级的实现要点（P1）

- **SLA 是日历运算**：SLA 必须引用"工作日历"对象（多时段/时区/节假日，Zammad 支持 iCalendar 订阅），
  只在工作时段计时。**失败模式**：Znuny 明确记录"未配置日历则静默退化为 24×7×365"——
  我们会直接踩这个坑，所以日历必须是 SLA 的必填外键。
- **暂停用状态标志**：GLPI 的 sleep 顺延到期时间、Zammad 的状态自带 "SLA ignored" 标志。
- **升级是对持久化计时字段的定时扫描**：Znuny 每 5 分钟跑 `EscalationCheck`，
  只在定义好的事件集（`TicketSLAUpdate`/`TicketQueueUpdate`/`ArticleCreate`/`TicketMerge`…）上重算，
  且只在工作时段触发。我们的 `ApprovalResumeReconciler` 结构可直接复用。
- SLA 计时器是**三个**：首次响应 / 更新 / 解决，不是两个。

### 9.5 两类日志必须分开（P0 的审计设计）

调研给出的范式很明确，直接决定我们的表设计：

- **不可变通信日志**（Znuny）：通信与附件对用户不可更改，生命周期变更进历史，回答"谁在何时做了什么"。
- **行政审计日志**（Zammad）：记录管理权限动作（操作者/动作/对象/IP/时间），**只读**，
  **刻意不记录日常工单更新**，并在固定 12 个月后自动清理且**不可配置**。

混为一谈会得到两个坏结果之一：无限增长的审计表，或不可审计的历史。
我们目前**两者都没有**；建议一次建对：`ticket_event`（业务时间线，追加式）+ `audit_log`
（行政动作，独立保留策略）。另外 Zammad 的审计覆盖面值得抄：
trigger/scheduler/macro/calendar/SLA/webhook/**core workflow** 的配置变更全部入审计。

### 9.6 邮件渠道：先把数据清洗做对（P1）

成熟产品的经验是"线程化是数据清洗问题，不是解析问题"：

- 导入时**剥离引用内容**（GLPI 删除 `up`/`bottom` 标签之间的内容，并要求答复写在原文之外）。
- 路由是**头部规则引擎**（`in_reply_to`、`auto_submitted`、`X-Auto-Response-Suppress`、
  `X-priority`…），**命中第一条即停**，且**"拒收"是合法终态**（可通知发件人）。
- **租户在入库时由规则判定**（known-email-domain / user-group / single-profile），
  目的是避免单画像用户越权跨实体——这直接对应我们 `tenant=default` 的硬编码。
- 从头部采集 CC/观察者、用 Reply-To 当请求人（GLPI）。
- **慢 I/O 移出请求路径**：iTop 用 `email_asynchronous` + 后台 cron，因为 SMTP 延迟会主导保存耗时。

### 9.7 入口滥用防护的行业基线（P0 限流的具体数值）

Zammad 是唯一有一手文档化入口限流的项目，可直接作为我们的起点：
蜜罐 + ALTCHA/Turnstile/hCaptcha/Friendly Captcha/reCAPTCHA，
并按 `form_ticket_create_by_ip_per_hour=20`、`by_ip_per_day=240`、`per_day=5000` 限流。
我们的 `/v1/auth/register`、`/v1/auth/login`、`/v1/chat/stream` 目前**零限流**。

### 9.8 失控自动化的上限（P1）

- Zammad 给一次 scheduler 运行硬编码 **2000 对象上限**作为误配保险。
- FreeScout 在发现 workflow 会互相触发成死循环后才加 `Max Executions`。
- 我们有 `resume_max_attempts` 与 `APPROVAL_RESUME_RETRY_MS`，方向正确；
  需要把同一模式套到 SLA 扫描、通知投递与工具调用重试上（每次运行的对象上限 + 总尝试上限）。

### 9.9 AI 护栏要按"触发点"分层（P1，直接可落地的架构改动）

NeMo Guardrails 的分层是本轮最有价值的架构骨架，**它把"检索到的分块"和"工具 I/O"提升为一等策略对象**：

| 层 | 触发点 | 我们的现状 |
|---|---|---|
| Input rails | 用户输入 | ✅ `guardian_input`（但仅正则） |
| **Retrieval rails** | **RAG 检索完成后，处理检索到的分块** | ❌ **完全缺失**（间接注入入口） |
| Dialog rails | 对话过程 | ⚠️ 由提示词承担 |
| **Execution rails** | **动作执行前后（`check tool input` / `check tool output`）** | ❌ 缺失（写工具只在 RBAC 与 ledger 层面） |
| Output rails | 模型输出 | ⚠️ 只有正则脱敏（`guardian.py:85`） |

配套三条工程纪律：
1. **护栏是运行时层，不是访问层**（ServiceNow 的原话）——不得用护栏替代 ACL；
   我们把 RBAC 与 Guardian 分开的做法是对的，需要写进架构约束避免后人混淆。
2. **先观察再拦截**（ServiceNow："Configure Guardian to log before enabling blocking"）；
   Llama Guard 也自陈会"增加对良性提示的拒答"。我们当前的输入护栏是**直接硬拒**
   （数字 15-19 位或 `password:` 一律拒单），误杀率高且没有观察期。
3. **不要假设分类器能防注入**：Llama Guard 自陈"可能被对抗攻击或提示注入绕过"。

### 9.10 OWASP LLM Top 10 (2025) 映射

| 编号 | 条目 | 我们的暴露点 |
|---|---|---|
| LLM01 | Prompt Injection | Guardian 仅正则；**检索内容不检测** |
| LLM02 | Sensitive Information Disclosure | 输出仅正则脱敏；输入命中即整单拒绝 |
| LLM06 | Excessive Agency | 已有审批 + effect ledger（**领先**），但写工具无工具级 I/O 校验 |
| LLM08 | Vector and Embedding Weaknesses | **默认 Hash 伪向量**；无向量索引持久化 |
| LLM10 | Unbounded Consumption | **无 token/成本统计、无配额、无限流** |

### 9.11 LangGraph 陷阱核对（已对我们的代码逐条验证）

调研列出的 HITL 陷阱，逐条对照本仓库的结果：

| 陷阱 | 我们的状态 |
|---|---|
| resume 会**从头重跑整个节点** | ✅ 已规避：副作用在 `prepare_approval`（中断之前的节点），`approval_gate` 只调 `interrupt()` |
| 中断前的副作用必须幂等 | ✅ 已规避：`approval_key` 唯一约束 |
| 中断索引**按位置匹配** | ✅ 单节点单 `interrupt()`（`service_workflow.py:176`），未条件化 |
| `while True` + `interrupt()` 导致**指数级重放** | ✅ 无此写法 |
| 用 `try/except` 包 `interrupt()` 会吞掉暂停 | ✅ 未包裹 |
| 子图**不继承**父图的 `set_node_defaults` | ⚠️ **命中**：`high_risk_service` 是子图，将来加策略必须单独设置 |
| 同步节点的 timeout 会在编译期被拒 | ✅ 所有图节点均为 async（已逐文件核对） |
| 重试会掩盖非瞬态错误 | ⚠️ **命中**：当前**完全没有** `RetryPolicy`/`TimeoutPolicy`/`error_handler` |
| checkpoint 表会持续增长、无自动清理 | ⚠️ **命中**：无任何保留策略，文档明确警告"会增加延迟与存储成本" |
| 子图状态对父图不可见（各自命名空间） | ℹ️ 与我们的实测一致（根 `''` + `high_risk_service:<task_id>`），恢复用根 `checkpoint_id` 即可 |

### 9.12 评测、提示词与成本的治理目标（P1）

- **评测**：LangSmith 的离线/在线划分——离线对 `datasets` 的样例（有参考答案），在线对 `runs`/`threads`（无参考答案）；
  **数据集版本化并打 tag**，"在 CI 中锁定版本，避免数据集更新打断流水线"；
  CI 门禁语义是"新版本必须在相关指标上**优于**基线版本"。
  RAG 指标应改用 RAGAS 的命名：Context Precision / Context Recall / Noise Sensitivity /
  Faithfulness / Response Relevancy。我们现在的 `citation_accuracy`/`groundedness` 是"字段非空"代理指标。
- **提示词**：版本 + 环境标签 + 生产指针，且 Langfuse 的设计选择值得抄——
  "若请求的标签没有对应版本，请求以 404 失败，**绝不静默回退**到 production 或 latest"。
- **成本**：Langfuse 区分 **ingested** 与 **inferred** 成本（"两者都有时以 ingested 为准"），
  并自陈推断值可能不准；Onyx 提供**全局/按用户/按用户组**的 token 限额 + 统一 LLM 网关 + 审计日志 SIEM 导出。
  我们要做的是配额与告警，而不是只做统计。
- **工具层**：MCP 规范明确"工具描述（annotations）必须视为不可信，除非来自可信服务端"，
  且"Host 必须在调用任何工具前取得显式用户同意"；Onyx 的 `Validate Tool`（注册前校验定义）值得抄。

### 9.13 补充的表列项（原报告遗漏）

内部备注 vs 公开回复的**类型化区分**；**保存视图/个人队列/批量操作**；
自定义字段 + 条件可见表单（Znuny Dynamic Fields + Screens / GLPI FormCreator / Zammad Core Workflows）；
**容器化交付**；**备份恢复 + 有文档的升级路径**；**API 显式版本化并保持旧版存活**
（GLPI `/v1`+`/v2`+只读 GraphQL；iTop 参数版本 + 变更历史表）；AI 输出作为**草稿**并由人类署名
（Zammad：AI 生成的回答打 `ai-generated` 标签、人类是作者）。

---

## 10. 一句话建议

先把**工单 + SLA + 审批归属**这三件事做出来——它们决定了这个系统能不能被称为"服务台"；
再把**审计、SSO、渠道、可观测性**补齐——它们决定它能不能进企业生产环境。
AI 部分（编排、检索、审批恢复）已经是相对领先的一块，不要推倒重来，而是把
**评测、可观测性、权限检索、注入防护**这四个"AI 工程化"短板补上。
最后补一条本轮调研新发现、优先级等同 P0 的项：**权限撤销必须在秒级生效**（§9.1）。

---

## 11. 参考资料
### 成熟开源服务台（一手文档）

- Zammad：[SLAs](https://admin-docs.zammad.org/en/latest/manage/slas.html)、[Calendars](https://admin-docs.zammad.org/en/latest/manage/calendars.html)、[Trigger](https://admin-docs.zammad.org/en/latest/manage/trigger.html)、[Scheduler](https://admin-docs.zammad.org/en/latest/manage/scheduler.html)、[Core Workflows](https://admin-docs.zammad.org/en/latest/system/core-workflows.html)、[Audit Logs](https://admin-docs.zammad.org/en/latest/system/audit-logs.html)、[Data privacy](https://admin-docs.zammad.org/en/latest/system/data-privacy.html)、[KB](https://user-docs.zammad.org/en/latest/extras/knowledge-base.html)、[Form channel](https://admin-docs.zammad.org/en/latest/channels/form.html)、[AI guide](https://next.zammad.org/en/documentation/use/guides/ai.html)
- GLPI：[Service levels](https://help.glpi-project.org/documentation/modules/configuration/service_levels.md)、[Approvals](https://help.glpi-project.org/documentation/modules/assistance/tabs/approvals.md)、[Receivers](https://help.glpi-project.org/documentation/modules/configuration/collectors.md)、[Knowledge base](https://help.glpi-project.org/documentation/modules/tools/knowledgebase.md)、[Profiles](https://help.glpi-project.org/documentation/modules/administration/profiles.md)、[Entities](https://help.glpi-project.org/documentation/modules/administration/entities.md)、[RESTful API V2](https://help.glpi-project.org/documentation/modules/configuration/general/api/restful-api-v2.md)
- Znuny：[Ticket Escalation](https://doc.znuny.org/znuny_lts/generalinformation/ticketescalation/index.html)、[SLA config](https://doc.znuny.org/znuny_lts/admin/servicemanagement/servicelevels/index.html)、[Generic Agent](https://doc.znuny.org/znuny_lts/admin/automation/generic_agent/index.html)、[ACL reference](https://doc.znuny.org/znuny_lts/annexes/acl_reference/acl_properties.html)、[About](https://doc.znuny.org/znuny_lts/about/)、[ITSM features](https://doc.znuny.org/znuny_lts/itsmfeatures/index.html)
- iTop：[Approval extended](https://www.itophub.io/wiki/page?id=extensions:approval_extended)、[Notifications](https://www.itophub.io/wiki/page?id=3_2_0:admin:notifications)、[REST/JSON](https://www.itophub.io/wiki/page?id=3_2_0:advancedtopics:rest_json)、[Data model](https://www.itophub.io/wiki/page?id=3_2_0:datamodel:start)
- osTicket：[SLA Plans](https://docs.osticket.com/en/latest/Admin/Manage/SLA%20Plans.html)、[Email Piping](https://docs.osticket.com/en/latest/Getting%20Started/Email%20Piping.html)、[Visibility Permissions](https://docs.osticket.com/en/latest/Features/Visibility%20Permissions.html)
- Chatwoot：[SLA](https://www.chatwoot.com/hc/user-guide/articles/1713167310-service-level-agreemtns)、[assignment v2](https://www.chatwoot.com/hc/user-guide/articles/1763978164-chatwoot-assignment-v2)、[agent capacity](https://www.chatwoot.com/hc/user-guide/articles/1741998212-agent-capacity)、[Captain](https://www.chatwoot.com/hc/user-guide/articles/1788726513-captain)、[EE](https://developers.chatwoot.com/self-hosted/enterprise-edition)
- FreeScout：[Workflows](https://freescout.net/module/workflows/)、[Knowledge base](https://freescout.net/module/knowledge-base/)

### AI 原生与 LLM 工程（一手文档）

- 权限感知检索：[Azure 文档级访问控制](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview)、[Onyx permissions](https://docs.onyx.app/admins/permissions/understanding_permissions.md)、[Glean security](https://docs.glean.com/security/security-principles)
- 护栏与注入：[OWASP LLM Top 10](https://genai.owasp.org/llm-top-10/)、[NeMo Guardrails 配置](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md)、[promptfoo red team](https://www.promptfoo.dev/docs/red-team/)、[Llama Guard 3 model card](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/Llama-Guard3/8B/MODEL_CARD.md)、[ServiceNow AI Guardian](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/australia/markdown/platform-security/naai-threat-protection.md)
- 接地与弃答：[Azure Groundedness](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/groundedness)
- 中断与容错：[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)
- 评测：[LangSmith evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)、[RAGAS metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)
- 可观测与成本：[Langfuse token & cost](https://langfuse.com/docs/observability/features/token-and-cost-tracking)、[Langfuse prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control)、[Onyx spending limits](https://docs.onyx.app/admins/advanced_configs/spending_limits.md)
- 工具与 HITL：[MCP specification](https://modelcontextprotocol.io/specification/2025-06-18)、[Dify Human Input](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input)

---

## 12. P0 修复记录（本轮已落地并验证）

| # | 问题 | 修复 | 验证方式 |
|---|---|---|---|
| 2 | `X-User-ID` 可冒充任意用户；内部令牌是占位符 | Java 每次调用签发 60s HS256 内部 JWT（`iss`/`aud`/`sub`/`roles`/`jti`，用 `javax.crypto.Mac` 自实现，未加依赖）；Python 校验签名 + `exp` + `iss` + `aud`，身份只取 `sub`；`X-User-ID` 降级为日志提示；生产环境缺 `AGENT_INTERNAL_JWT_SECRET` 时**拒绝启动**；新增常量时间比较的静态令牌逃生通道（非生产） | 无签名请求 401、错密钥 401、错 audience 401、错 secret 401（HTTP 层 + 单测） |
| 3 | 审批无归属、可自审批、角色不校验 | `approval_task` 增 `decided_by`/`decided_at`/`decision_comment`；新增追加式 `approval_decision` 表；决策前校验**申请人≠审批人**与 `required_approver_roles`（`admin` 恒通过），CAS 与归属字段**同语句**写入；`prepare_approval` 按 agent 写入审批角色 | 自审批 403、错角色 403、正确角色 200、轨迹 1 条且含审批人与意见（实时端到端） |
| 3b | 权限撤销不生效（索引永久缓存） | 去掉 `lru_cache`，改为 TTL 缓存 + `invalidate_index()`；`load_index(roles=...)` 把 ACL 过滤**下推到 SQL**（含 chunk/ticket_event 的父级过滤）；`/ready` 暴露缓存状态 | 缓存复用 / 失效重建 / TTL 过期 / 角色作用域 4 项单测 |
| 4 | 无审计日志 | 新增 `audit_log` 表 + `AuditLogger`（写库失败不影响请求，镜像到进程日志）；埋点 `chat.stream`、`approval.decide`（含**被拒**）、`approval.resume`、`tool.write`、`guardrail.*`；只记管理与安全动作，不记日常读取；12 个月保留策略 | 被拒的自审批也写入审计；实时验证通过 |
| 6 | 检索内容不检测间接注入；护栏只有输入/输出两层 | 新增 `core/guardrails.py`，按触发点补齐**检索层**与**执行层**：检索分块与工具入参/出参统一筛查（注入 / 间接注入 / 外泄标记 / 参数白名单 / 输出大小上限），命中即隔离；支持 `*_log_only` 观察期；输入层由"整单拒绝"改为**脱敏**（修掉高误杀） | 检索隔离、外泄标记、越权参数、超大输出、工具回注共 12 项单测 |
| 1（Python 侧） | 无速率限制 | Redis 固定窗口限流（`chat.stream` 60/min、`approvals.decide` 30/min、`approvals.resume` 30/min），Redis 不可用时按进程降级并记录一次告警；`Retry-After` 头 | 超限 429、不同 key 互不影响、可关闭、可按配置 fail-closed |
| — | **修复过程中发现的新矛盾** | 原 CAS 按 `approval_task.user_id`（申请人）过滤，与新增的职责分离冲突 → **审批将永远无法完成**。已移除该过滤，授权前移；并同步修正 Java 侧 | 被拒后审批仍可被合法审批人完成（专项单测） |

测试：Python **105 passed**（本轮新增 35 项），Java 见下表。实时端到端 8 步全部通过（真实 PostgreSQL + Redis）。

**未修复**：#5 审批 UI（已在本轮完成，见 §13）、#7–#12 的 P1 项、以及 §9.2 的完整审批引擎（会签/超时结论/委派/邮件链接）——本轮只做了归属、职责分离与角色校验，法定人数与超时升级仍待建。

---

## 13. 做审批界面时又挖出三个现网级缺陷

| # | 问题 | 影响 | 修复 |
|---|---|---|---|
| A | **`user_account` 缺 `roles` 列**：该列只写在 `CREATE TABLE IF NOT EXISTS` 里，而既有库不会因此补列 | **登录接口直接 500**——本机数据库上整个系统无法登录；任何早于该列创建的部署都一样 | 补 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 守卫；并写了一个漂移检查脚本，逐表比对 DDL 声明与 `information_schema`，确认 16 张表全部一致 |
| B | 登录锁定**阈值不一致**：`peek` 用 `limit-1`、`check` 用 `>= limit` | 文档写"5 次失败锁定"，实际 **4 次就锁**，正确密码也被拒；这是被单测抓出来的 | 两个判断统一到同一阈值，并在计数后重新 peek 决定"本次是否触发锁定" |
| C | `assistant_message_id` 可能是 `null` 却被原样发给 agent 服务 | 前端漏传 `aiMessageId` 时，Python 返回 **422**，Java 把它降级成 "Agent 服务调用失败"，完全无法定位 | Java 侧对关联用的 id 兜底生成 |

**根因是同一条**：这个项目没有版本化迁移（§6），所有 DDL 都靠 `CREATE TABLE IF NOT EXISTS`，于是"给 CREATE 语句加一列"在既有库上**静默不生效**，直到运行时才炸。建议尽快引入 Flyway，并把"每一列都要有 ADD COLUMN 守卫"写成约定——A 类问题不可能靠测试发现，只能靠迁移纪律。

### 审批界面（本轮交付）

前端新增 `features/approvals/`（types / service / store / 5 个组件）+ `ApprovalsPage`，路由 `/approvals`，侧边栏加导航与待办角标：

- 三视图（与我相关 / 待我审批 / 我发起的）+ 状态过滤与计数，待办角标 60 秒校准
- 详情弹窗展示完整决策上下文：申请人、审批角色、规则、风险、意图、checkpoint 与恢复状态
- **权限不足时不隐藏操作按钮，而是说明原因**（"不能审批自己发起的申请"／"需要以下角色之一：…"）——隐藏按钮会让用户以为系统坏了
- 驳回强制填写理由；提交后有服务端结论反馈与 toast
- 追加式审批轨迹时间线（谁 / 何时 / 什么角色 / 意见 / 来源 IP）
- 深色模式、键盘可达（radix Dialog 焦点陷阱）、空/加载/错误态齐备

后端为支撑该界面补了三处：`GET /v1/approvals?scope=mine|to_approve|all`（待办队列的角色规则与决策接口**完全一致**，所以界面不会出现"点了却被拒"的按钮）、`GET /v1/approvals/{id}/decisions`（轨迹）、登录响应返回 `roles`。

---

## 14. 方案 A：管理员开户 + 邀请制（消除"关掉注册后无法开户"的运营断点）

§12 的 #1 把自助注册关掉之后留下了一个断点：**新员工无法开户，分配角色只能改数据库**。本节补上替代路径。

| 能力 | 实现 | 关键性质 |
|---|---|---|
| 首个管理员 | `BOOTSTRAP_ADMIN_EMAIL` + 启动时无管理员则签发一条 admin 邀请，链接只在日志打印一次 | **不在配置里放密码**；复用邀请机制，凭据始终不经过环境变量或日志 |
| 邀请开关 | `AUTH_SELF_REGISTRATION_ENABLED=false`（默认） | 403 `SELF_REGISTRATION_DISABLED` |
| 邀请签发 | `POST /v1/auth/invitations`（管理员） | 令牌只存 SHA-256；明文只在响应里出现一次 |
| 邀请接受 | `GET /v1/auth/invitations/preview`、`POST /v1/auth/invitations/accept`（公开） | 一次性（CAS 消费）、72 小时过期（可调）、可撤销；**角色来自邀请，被邀请人不能自选** |
| 成员管理 | `GET/POST /v1/admin/users`、`PATCH .../roles`、`POST .../disable\|enable`、`GET /v1/admin/roles` | 分页检索；角色白名单校验 |
| 权限边界 | `admin` 与 `user_admin` 分离 | `user_admin` 可开户但**不能授予管理员角色**（否则委派形同虚设） |
| 自我保护 | 最后一个管理员不可降权/停用；不可停用自己 | 防止把自己锁在门外 |
| 停用即时生效 | 每次请求校验账号状态 | JWT 无法撤销，否则"停用"按钮等于装饰 |
| 审计 | `user.create` / `user.roles_changed` / `user.disable` / `user.enable` / `invitation.send` / `invitation.revoke` / `invitation.accept` / `user.bootstrap_admin` | 被拒的操作也记 `denied` |

**做这件事时又发现并修掉四个缺陷**（前两个由单元测试、后两个由实时端到端验证发现）：

1. `countAdministrators` 原本用 `roles LIKE '%admin%'`，会把 `network_admin` 也算成管理员 → 改为 CSV 精确 token 匹配。
2. `JwtAuthFilter` 里 `Mono.fromCallable` 对已删除账号返回 `null` 会变成**空 Mono**，导致既不继续链路也不写响应（返回一个没有状态的空响应）→ 加 `switchIfEmpty`。
3. **"最后一个管理员"保护过弱**：`user_admin` 也被计入管理员数，所以只要存在一个 `user_admin`，最后一个**真正的 admin** 就能被降权——结果没人能再授予管理员角色，正是这个守卫要防的死锁。→ 区分"能管理员"与"能授予管理员角色"，降权/停用 `admin` 时按 `countFullAdministrators` 判断。
4. **角色变更不即时生效**（比第 3 点更严重）：JWT 里的角色是签发时写死的，所以被降权的管理员**仍然保有一个长达 7 天的管理员 token**；停用是即时生效的，降权却不是。→ 让过滤器用**数据库里的当前角色**而不是 token 里的角色（同一次查询，零额外成本），停用与降权都在下一个请求生效。

第 3、4 点单测发现不了——它们需要"两个管理员 + 一个 user_admin + 两次登录"这种真实状态组合，是我跑端到端脚本时暴露出来的。

**仍未做**：邮箱验证与验证码/反自动化（§13 的 B）、注册限流的 fail-closed 分层（§13 的 C）、成员管理之外的密码重置流程。




