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

> **本轮状态**：#1–#6 已修复并端到端验证（见 §12、§13、§14）；**#7–#12 六项 P1 已修复**（见 §15），其中 #7 在 P0 轮已修一半（缓存机制），本轮补上生产触发点，#8–#12 本轮新做。仍未做：§9.2 的完整审批引擎（会签/超时结论/委派/邮件链接）、§13-B/C 的注册入口加固。

| # | 优先级 | 问题 | 证据 | 改法方向 |
|---|---|---|---|---|
| 1 | **P0** | 开放自助注册：任何人可注册 `employee` 账号，密码仅 6 位，注册/登录/聊天**零限流** | `JwtAuthFilter.java:30`、`AuthService.java:36,83` | 关闭自助注册改为邀请/管理员开户；密码策略；按 §9.7 的行业基线加限流与验证码 |
| 2 | **P0** | 内部服务令牌为文档占位符，且 agent 服务监听 `0.0.0.0:8000`，凭 `X-User-ID` 即可冒充任意用户 | `agent-service/.env:1`、`application.yml:26`、`config.py:13`、`core/security.py:45` | 强随机令牌 + 服务间 mTLS/私网 + 不信任裸 `X-User-ID`（改签名/JWT） |
| 3 | **P0** | 审批无归属：没有 `decided_by`/`decided_at`，`required_approver_roles` 从不写入也从不校验 → 申请人可自审批；且**完全没有超时、会签、委派** | `agent-service/schema.sql:7-35`、`ApprovalIngestService.java:99-118` | 见 §9.2：按 iTop 语义建审批引擎（法定人数/超时结论/委派/邮件链接/审计轨迹） |
| 3b | **P0** | **权限撤销不生效**：索引被 `lru_cache` 永久缓存且无失效机制 → 给角色撤权后，进程不重启仍可检索到该文档 | `ingest.py:31`、`knowledge_repository.py:222` | 见 §9.1：变更事件驱动失效 + 过滤下推索引 + 细到用户/组 |
| 4 | **P0** | 无任何审计日志表/接口，用户与管理动作不可追溯 | 全仓 grep `audit` = 0 命中 | 追加式审计表 + 关键动作埋点 + 导出 |
| 5 | **P0** | 前端**没有审批界面**：整个 checkpoint 审批链路只能靠 API 调 | `front-service` 全量 grep `approval` = 0 命中 | 审批中心页面 + 待办角标 + 审批详情 |
| 6 | **P0** | 检索到的文档/网页不做间接提示注入检测；Guardian 仅正则，且只查用户消息 | `guardian.py:6-12,39` | 检索内容进入上下文前过检测；输出侧接地校验 |
| 7 | **P1** | 知识索引 `lru_cache(maxsize=1)` 进程内缓存，**知识更新必须重启**；`load_index()` 全量载入内存 | ~~`ingest.py:31-32`~~ | ✅ 已修（§15.1）：TTL + 写入即失效 + 运维刷新端点；但**多副本失效仍需 Redis**，且真正的外部索引/向量库仍未做 |
| 8 | **P1** | 默认向量通道是 **Hash 伪向量**（无 embedding key 时），"混合检索"实际只有词法 | ~~`embedding.py:72-75`~~ | ✅ 已修（§15.2）：无真实 provider 就**如实降级为词法**并在 `/ready` 标注；哈希向量改为显式 opt-in |
| 9 | **P1** | 可观测性是空实现：`tracing.py` 两个函数直接 `return`，无 trace/无 token/成本统计 | ~~`observability/tracing.py:4-21`~~ | ✅ 已修（§15.3）：真实指标注册表 + Prometheus 端点 + token/成本记账；**未接 OTel SDK 与 Langfuse，已如实标注** |
| 10 | **P1** | 评测无门槛：`run_eval.py` 只打印 JSON，无阈值无退出码；`citation_accuracy`/`groundedness` 是"非空"伪指标 | ~~`eval/run_eval.py:10`~~ | ✅ 已修（§15.4）：阈值化 + 退出码 0/1/2；引用改为真实解析；groundedness 无裁判时报**不可用**而非 1.0 |
| 11 | **P1** | `RunEventStore` 进程内 `ConcurrentHashMap` 且**无淘汰**：多副本 SSE 回放/取消失效，且事件永久驻留堆内存 | ~~`RunEventStore.java:19,54`~~ | ⚠️ 部分修复（§15.5）：TTL + 运行数上限 + 每运行事件上限已加（内存有界）；**多副本仍需共享存储**——注意原文提到的 `RedisEventStream` 在本仓库**并不存在**，是错误引用 |
| 12 | **P1** | Java→Python 主链路 `AgentGatewayClient` **无超时/重试/熔断**；Java 无全局异常处理 | ~~`AgentGatewayClient.java:39-43`~~ | ✅ 已修（§15.6）：连接/空闲超时、仅对"连接未建立"重试、熔断器、统一 `@RestControllerAdvice` |

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

> **状态：S1 已落地**（见 §16）。最小闭环已实现：`ticket` / `ticket_comment` /
> `ticket_activity` / `ticket_link`、服务端强制的状态机、影响×紧急度推导优先级、
> 追加式时间线、不可变通信日志、关联/合并/拆分、权限模型、审计、REST API 与前端页面。
> **尚未做**：`queue_id`/`category`/`subcategory`/`channel`（属 L3 队列与分派）、
> `sla_policy_id` 与三个 due 时间（属 L2 SLA）、`reopened_count`、自定义字段（L4）、
> 看板视图、以及"一键转工单"（需要把 conversation_id/turn_id/证据/审批 ID 一起带过去——
> `conversation_id` 字段已预留并落库，转换入口待建）。

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
2. `JwtAuthFilter` 里 `Mono.fromCallable` 对已删除账号返回 `null` 会变成**空 Mono**，导致既不继续链路也不写响应（返回一个没有状态的空响应）。当时加的是 `switchIfEmpty`，但那个位置**本身就是错的**——它在 `chain.filter(...)` 之后，而 `Mono<Void>` 正常完成也是 empty，于是每次响应结束后都会去写 401；流式响应此时已提交，直接抛异常并撕掉连接。最终改为把查询包进 `Optional`，让"账号不存在"成为一个真实值而不是空完成（详见 §15.7）。
3. **"最后一个管理员"保护过弱**：`user_admin` 也被计入管理员数，所以只要存在一个 `user_admin`，最后一个**真正的 admin** 就能被降权——结果没人能再授予管理员角色，正是这个守卫要防的死锁。→ 区分"能管理员"与"能授予管理员角色"，降权/停用 `admin` 时按 `countFullAdministrators` 判断。
4. **角色变更不即时生效**（比第 3 点更严重）：JWT 里的角色是签发时写死的，所以被降权的管理员**仍然保有一个长达 7 天的管理员 token**；停用是即时生效的，降权却不是。→ 让过滤器用**数据库里的当前角色**而不是 token 里的角色（同一次查询，零额外成本），停用与降权都在下一个请求生效。

第 3、4 点单测发现不了——它们需要"两个管理员 + 一个 user_admin + 两次登录"这种真实状态组合，是我跑端到端脚本时暴露出来的。

**仍未做**：邮箱验证与验证码/反自动化（§13 的 B）、注册限流的 fail-closed 分层（§13 的 C）、成员管理之外的密码重置流程。

---

## 15. P1 修复记录（#7–#12，本轮已落地并验证）

统一原则：**宁可如实报告能力缺失，也不要让占位实现冒充能力**。六项里有四项的根因都是"看起来有、实际没有"。

### 15.1 #7 知识索引：从"重启才生效"到"写入即失效"

P0 轮已把 `lru_cache` 换成 TTL 缓存 + `invalidate_index()`，但**只有测试在调用它**，生产路径没有任何触发点。本轮补上：写入路径调用即失效（带 generation 守卫，避免与并发写竞争的旧索引被回填缓存）；新增内部鉴权的 `POST /v1/knowledge/index/refresh` 供运维强制重建；`/ready` 的 `index_cache` 扩展为 age / ttl / generation / 文档与分片计数 / 作用域角色。

**残留（需诚实标注）**：跨副本失效仍需 Redis pub/sub——刷新端点只让**处理该请求的那个进程**失效，其他副本要等 TTL。真正的稠密检索也需要向量库（pgvector/Qdrant）并把分片向量在摄取时落库；当前即使配置了 HTTP embedding，每次查询仍会重新 embed 全部候选，只适用于玩具语料。

### 15.2 #8 稠密通道：不再静默伪造

原来无 embedding key 时 `default_embedding_provider()` 会**静默**返回哈希伪向量，于是"混合检索"里混进了一路纯噪声。现在：解析器返回 `None`，检索**如实降级为词法**，并在结果、plan、`to_dict()` 与 `/ready` 四处标注 `lexical_only`；哈希向量移入 `ALLOW_HASH_EMBEDDINGS` 显式开关（默认关）且构造时大声告警；`hybrid_search` 把原来误标为 `vector` 的稀疏通道改标 `sparse`，稠密通道改为**增量**而非替换稀疏结果。

**同类缺陷一并修掉**（子代理因文件归属未动，本轮接手）：`app/intent/semantic.py` 此前默认构造哈希向量却把结果标为 `"semantic"`——现在无真实 provider 即不可用（回退到规则/LLM 路径），使用哈希时标注 `token-overlap`，状态接入 `/ready`；`app/tickets/similar_search.py` 把稀疏余弦改标 `sparse` 而非 `vector`。

### 15.3 #9 可观测性：从空实现到真实记账

原 `tracing.py` 两个函数体只有 `return`。现在：线程安全的进程内指标注册表（计数器/直方图）作为唯一事实来源；`GET /metrics` 以 Prometheus 文本暴露，**受内部身份保护**（它暴露模型、流量与花费）；从 provider 返回的 usage 记 token / 时延 / 成本，未上报的 usage 记为 `unreported` 而**不记为零**；OTLP/HTTP 尽力导出（2 秒超时、绝不抛进请求路径、导出前剥离提示词与用户 id）。

**如实标注的部分**：未接 OpenTelemetry SDK 与 Langfuse SDK（不新增依赖），因此 `/ready` 明写 `instrumentation: manual-best-effort`、`auto_instrumentation: false`、Langfuse `not-configured`。价格表只收**已核实**的型号（`deepseek-flash`、`deepseek-v4-pro`，取低谷价）；仓库默认的 `deepseek-chat` 无公开价，因此**成本报不可用而非估算**。

### 15.4 #10 评测：从打印 JSON 到可进 CI 的门槛

`run_eval.py` 现在支持阈值文件 / `--set` / `--require` / `--judge` / `--json`，逐指标 PASS/FAIL/UNAVAILABLE，退出码 0（通过）/ 1（越界或必需指标不可用）/ 2（调用错误）。`citation_accuracy` 改为**真实解析**：引用必须命中实际检索到的文档集合，`[n]` 标注越界计 0 分。`groundedness` 需要 LLM 裁判，无裁判时返回 `None` + 原因，**不再伪造 1.0**；有裁判时用 `temperature=0` 并记录 `PROMPT_VERSION`。

### 15.5 #11 运行事件缓冲：内存有界（多副本仍待做）

`RunEventStore` 原来把每个 run 的每个事件永久留在堆里。现在有 TTL（默认 30 分钟，仅淘汰已结束的 run）、运行数上限（默认 500，优先淘汰已结束的）、每运行事件上限（默认 2000）。事件被丢弃时不会静默跳过：`replay` 会先发一个 `event: truncated` 事件告知客户端游标之前的空洞。

**仍未做**：多副本 SSE 回放与取消需要共享存储（Redis Streams）。原文称"已有 `RedisEventStream` 可复用"——**经查该文件在本仓库并不存在**，属文档错误引用，此处更正。

### 15.6 #12 Java→Python 链路韧性 + 统一错误契约

- **超时**：连接超时 3 秒；空闲读超时 120 秒。刻意区分两者——连不上的请求**可以安全重试**（请求从未送达），而"连上之后卡住"的 POST 若重试会**重复执行并重复计费**，因此不重试。
- **熔断**：仅统计传输层失败（HTTP 4xx/5xx 是"服务活着但在拒绝我们"，计入会因调用方 bug 打开熔断）；打开后快速失败，冷却后半开探测。
- **错误契约**：新增 `@RestControllerAdvice`，全部错误统一为 `{"error": CODE, "message": ...}`；未预期异常返回 500 且**不回显内部信息**（日志留全量）。
- `/health` 现在暴露 `runEvents`（有界缓冲统计）与 `agentCircuit` 状态。

### 15.7 本轮由"实时验证"抓出的缺陷（单测抓不到）

| 缺陷 | 为什么单测抓不到 |
|---|---|
| **SSE 流结束后连接被撕掉**：`JwtAuthFilter` 的"账号不存在"兜底 `switchIfEmpty` 被放在 `chain.filter(...)` **之后**，而 `Mono<Void>` 正常完成就是 empty，于是每次响应结束后都去写 401；流式响应此时已提交，`ReadOnlyHttpHeaders.set` 抛异常、连接中断。JSON 路径侥幸没炸 | 单测里 `MockServerWebExchange` 的行为与真实 Netty 不同（其 `bufferFactory().wrap` 本身未实现）。只有真实服务 + 流式响应才暴露 |
| **新增 `@ConfigurationProperties` 未注册**，Spring 上下文启动即失败 | 单测直接构造这些类，从不启动应用上下文 |
| **`countAdministrators` 用 `LIKE '%admin%'`** 会把 `network_admin` 也算作管理员 | 需要 `network_admin` 账号的真实数据组合 |
| **"最后一个管理员"保护过弱** + **角色变更不即时生效**（被降权者仍握有最长 7 天的管理员 token） | 需要"两个管理员 + 一个 user_admin + 两次登录"的真实状态组合 |

这张表本身就是本轮最该记住的结论：**账号状态、权限、流式响应、Spring 上下文这四类问题，单元测试基本抓不到，必须跑真实服务。**

### 15.8 验证结果

| 层次 | 结果 |
|---|---|
| Java 单测 | **143 项全绿**（新增 23 项：有界缓冲淘汰与截断标记、熔断状态机与重试安全性分类、错误契约映射、过滤器回归） |
| Python 单测 | **222 项全绿**（新增 117 项：检索诚实性、索引失效、指标注册表、成本记账、`/metrics`、评测门槛、意图标注、工单标签） |
| 前端 | `tsc --noEmit` 0 错误 |
| 实时：Java 韧性 | **16/16** · agent 停机时 SSE 返回结构化 error 事件（非 500）、连续失败后熔断打开、被拒请求 <1 秒返回、`/health` 可观测 |
| 实时：Python 能力 | **15/15** · `/metrics` 需内部身份且无泄漏、索引刷新 generation 递增、**一次真实模型调用记录 `llm_calls` / `llm_tokens=1441` / `llm_cost_usd=$0.000529`**（真实测量而非桩） |
| 实时：整体回归 | **26/26** · 邀请制开户 → 发起审批 → 待办队列 → 自我审批 403 → 通过且 `resume=resumed` → 轨迹与归属落库 → 审计；同一次运行里 P1 的有界缓冲与熔断状态正常 |
| 数据库 | schema 漂移检查 PASS（16 张表与 DDL 声明一致） |

---

## 16. S1 交付：L1 工单核心

按 §4.1 与用户确认的阶段划分，先做 L1（它是 L2 SLA、L3 分派、L4 触发器、L5 渠道、L6 知识的共同前置）。

### 16.1 命名：与既有检索语料划清界线

仓库里**已经存在** `ticket_case` / `ticket_event`，但它们**不是工单系统**：那是 4 条已解决案例 + 9 条事件的**只读历史语料**，供相似工单检索使用（`schemas` 里 `ticket_event.case_id → ticket_case`）。因此实时工单使用不冲突的命名，避免两个"ticket"含义共用一张表：

| 表 | 用途 |
|---|---|
| `ticket` | 实时工单（23 列，含 3 个 SLA 时间戳） |
| `ticket_comment` | **不可变**通信日志（公开回复 / 内部备注），无 update/delete 路径 |
| `ticket_activity` | 追加式生命周期history（谁、从什么、改成什么、JSONB 详情） |
| `ticket_link` | 关系，**只存声明方向**，反向由读取时推导 |
| `ticket_case` / `ticket_event` | （既有）历史案例检索语料，与本功能无关 |

### 16.2 三处"服务端强制"，客户端绕不过去

1. **状态机**：`new/open/in_progress/pending/resolved/closed` 的合法迁移在服务端校验。同状态不算迁移；`closed` 只能重开。**重开会清空 `resolved_at`/`closed_at`**——否则重开的工单在 SLA 与报表里仍显示为"已解决"。
2. **优先级由影响×紧急度矩阵推导**（ITIL 3×3 → critical/high/medium/low），不接受客户端直接指定；管理员可显式覆盖，但覆盖会记入时间线（`prioritySource: manual_override`）以便看出没走矩阵。
3. **权限**：申请人只能看自己的工单、只能发公开回复、只能"确认解决/驳回重开"；处理人（staff 角色）可流转/分派/合并/拆分；`auditor` **可读全部、不能写**。不可见的工单返回 **404 而不是 403**（403 会确认 id 存在）。内部备注在 **SQL 层**按 `visibility` 过滤，不依赖调用方记得过滤。

### 16.3 S1 顺带修掉/发现的

| 项 | 说明 |
|---|---|
| 三个 SLA 时间戳**从第一天就落库** | `first_response_at`/`resolved_at`/`closed_at` 无法事后回填；若等 S2 再补，就只能度量 S2 上线之后创建的工单 |
| 首次响应语义 | 定义为**处理人的第一条公开回复**（内部备注不算"回应了申请人"，申请人自己的补充也不算） |
| 关系只存一个方向 | "A blocks B" 在 B 上推导为 `blocked_by`，两端不会各自持有可能失同步的行 |
| 合并即墓碑 | 源工单保留行并指向目标、置为 closed，**通信日志与历史一起迁移**；再次合并或改状态返回 409 |
| 合并环检测 | 服务端用递归 CTE 检测合并链；实测中发现"已合并工单不可作目标"这条规则已经让环结构上不可能，故该检测实为纵深防御（测试已按真实行为断言并注明） |
| **内存替身不忠实于 SQL（测试有效性问题）** | 单元测试的 mapper 替身原先返回 store 里的**活对象**，于是 `update` 比较"新值 vs 旧值"时旧值已被写入改掉，时间线记录被静默跳过——而生产（MyBatis 每次返回新对象）行为正常。已让替身返回**游离副本**，并让生产代码**显式快照旧值**，两边都不再依赖"读出来的对象是否游离"这一隐含契约 |

### 16.4 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **195 项全绿**（新增 52 项：状态机 9、优先级矩阵 6、权限 7、服务层 30——含可见性、内部备注过滤、流转合法性、时间戳清除、首次响应、关联反向标签、合并单赢与环、拆分、检索分页与计数） |
| 前端 | `tsc --noEmit` 0 错误（16 个新文件 + 2 处改动；**未做浏览器实测**） |
| 实时端到端（真实 PostgreSQL） | **57 项通过 / 0 失败**：矩阵推导、越权 403、不可见 404、内部备注过滤、流转与时间戳、重开清空、关联两端标签、重复/自关联 409、拆分、合并迁移与 409、审计只读、已合并默认不出现在列表、按编号检索、分页、编号唯一递增、JSONB 详情、审计动作、库侧改挂核对 |
| 数据库 | schema 漂移检查 PASS |

### 16.5 仍未做（诚实清单）

- **看板视图**、`reopened_count`、`queue_id`/`category`/`subcategory`（归 L3）、`sla_policy_id` 与三个 due 时间（归 L2）、自定义字段（归 L4）。
- **一键转工单**：字段已预留（`conversation_id` 落库），但把对话/证据/审批 ID 一起转成工单的入口还没建。
- **通知**：状态变更目前不通知申请人（需要 L5 的出站模板）。
- 前端**没有浏览器实测**，只有类型检查与样式对齐；`activityType` 的文案是尽力而为 + 原码兜底。

---

## 17. 工具层：从"能演"到"能用"（只做了工单）

被问到"工具是否能够使用，不要像个 demo"时，先做的是**核实而不是辩解**。结论：**管线是真的，工具实现基本是假的**。

### 17.1 核实结果（改动前）

| 部分 | 真实状态 |
|---|---|
| 适配器抽象 + 真实 HTTP 适配器 | ✅ 真的：`OpenApiToolAdapter` 会真发 POST，带身份头与 `Idempotency-Key` |
| 双层授权、工具护栏、幂等账本、审批门控、审计 | ✅ 真的 |
| **工具实现** | ❌ **20+ 个工具返回硬编码字符串**，还编造标识符：`IT-IDP-10086`、`SW-2001`、年假固定 5 天、金额 `1280.00`、预算 `85000`、`AUD-<时间戳>`（什么都没写） |
| **审批通过后的"执行"** | ❌ `finance_submit`/`vpn_api_provision` 返回一句"已提交/已开通"——审批链路真实，执行是字符串 |
| **模型是否参与工具选择** | ❌ 全仓无 `tools=`/`tool_choice`/`tool_calls`；`select_tools_for_request` 用**正则**决定调什么 |

### 17.2 本轮把工单工具做成真的

选工单的理由：**两端都是我们的**，不必等第三方提供契约。

| 改动 | 说明 |
|---|---|
| **反向内部令牌** | Python 回调 Java 时签发 `iss=sky-chat-agent-service` / `aud=sky-chat-java-service`，与 Java→Python 的 `iss`/`aud` **刻意相反**。若共用一个 pair，任何观察过出站令牌的一方都能把它回放给 Java 并以该用户身份行事（confused deputy）。两侧都校验两个 claim，令牌只会被它被寻址的那一方接受；测试直接断言"Java 签发的令牌回放到 Java 必须被拒" |
| **Java 内部工具端点** | `POST /v1/internal/tools/{toolName}`，**复用 `TicketService`**，行为人取自令牌 `sub`。不在 Python 里重写可见性/状态机/矩阵（那会造出第二个必然漂移的策略点），也不让 Python 直接写表（那是第二条写路径） |
| **失败即失败** | 连不上 Java 返回 `success=false` + `TICKET_SERVICE_UNAVAILABLE`（文案含"该操作未执行"），**绝不编造成功**；业务拒绝（无权限/不可见）是 HTTP 200 + `success=false`，让 agent 能对用户解释而不是报崩溃 |
| **真实适配器 + 路由** | `TicketToolAdapter` 走 HTTP；`RoutingToolAdapter` 把工单工具派给它、其余回落到 mock，**并在结果里标 `simulated: true`** |
| **工具与权限注册** | `agents.yaml` 声明哪些 agent 可用；`TOOL_PERMISSIONS` 声明哪些角色可用 |
| **`/ready` 逐工具如实标注** | `implemented` / `declared_real`（已接给 agent）/ `declared_mock`（声明了但仍是桩）/ `implemented_not_wired`（有适配器但未接出），并声明 `routing: rule-based` |

### 17.3 顺带修掉的四个缺陷（测试与实测抓出来的）

| 缺陷 | 说明 |
|---|---|
| **意图分类法没有"报障"这一类** | 最要紧的一条：`我的打印机坏了` 匹配不到任何规则，fallback 到 `knowledge.retrieve`（conf 0.4），**永远走不到专家 agent，也就永远碰不到 `execute_tools`**——我把工具接在了一条这类输入永不经过的路径上。已补 `it.incident.report`（priority 75，排在 `it.service.request` 之前，因为"坏了"是故障不是请求）与 `support.ticket.status`（route 到 **service** agent，因为 knowledge 子图只检索不执行工具）；同时给 `it.service.request` 补了 `显示器/键盘/鼠标/打印机` 等常见名词 |
| **agent 级权限被放宽** | `authorize_agent` 是"用户拥有该 agent 所需权限中的**任意一个**"。我往 agent 的 `permissions` 里加了 `ticket:read` 这类**广泛持有**的权限，于是 `hr_user` 突然能进 `network` agent。权限行已移除，工单授权只放 `TOOL_PERMISSIONS`，并在 `agents.yaml` 写明这个陷阱 |
| **`TOOLS_BY_AGENT` 导致无条件选中** | 我先把工单工具加进了那张表，而它是"该 agent 的基础工具集"——等于**无条件选中**，于是"今天天气怎么样"也会建工单。工单工具改为只由条件化选择产生 |
| **`"me"` 被当字面量传给 SQL** | `InternalToolController` 把 `requesterId="me"` 直接交给服务，而 `TicketService` 拿它做 SQL 相等过滤（`"me"` 的解析在控制器层）。这个工具会**永远返回 0 条**；已改为传 `actor.userId()` |

另有一条**测试自身的教训**：实时脚本跨次尝试复用了同一个 runId，而 `ChatStreamService` 对已存在的 runId **重放已存事件、不再执行**——第二次跑出来的"结果"其实是第一次（规则修好前）的事件。这是 SSE 恢复/幂等该有的行为；脚本已改用唯一 runId，并把这个重放语义**固化成断言**。

### 17.4 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **214 项全绿**（新增 19：反向令牌 9——含回放拒绝、错 iss/aud、过期、错密钥、无 subject、无密钥；内部工具端点 10——含行为人取自令牌、业务拒绝是工具结果、未知工具、信封兼容、按编号解析经可见列表） |
| Python 单测 | **246 项全绿**（新增 24：令牌 iss/aud 相反、请求构造、连不上不编造成功、401 归因配置、路由分流与 `simulated`、工具状态、参数推断、选择启发式、capabilities 不选工单） |
| 实时端到端 | **27 项通过 / 0 失败**：一句话故障描述 → `ticket_create` → **`ticket` 表真实落行 #14** → 申请人是令牌 `sub` → `source=chat` → 时间线 actor 是该员工 → 审计 `origin=agent, tool=ticket_create`；追问"我的工单" → `ticket_list` 返回 #14 且**未重复建单**；runId 复用走重放；`/ready` 如实标注 |
| 前端 / 数据库 | `tsc` 0 错误；schema 漂移 PASS |

一次真实对话里**两个方向的内部令牌都被压到了**：Java 签发 Java→Python 令牌 → Python 校验 → Python 签发 Python→Java 令牌 → Java 校验。

### 17.5 仍未做（诚实清单）

- **没有 function calling**：工具仍由正则/规则选择。要真正让模型自主选工具需要接 function calling 并把参数交给模型；届时我会把 `routing` 从 `rule-based` 改成 `model`，不会两边都声称。
- **`ticket_get` / `ticket_comment` 有真适配器但没接出去**：`ticket_get` 的能力目前通过 `ticket_list` 的编号检索 + 可见性过滤实现；`ticket_comment` 需要可靠地从自然语言解析"回复内容"，我没有硬猜。
- **其余 20+ 个工具仍是桩**，只是现在**会自我标注**（`simulated: true`）、`/ready` 列明、`agents.yaml` 顶部也写明。接真需要目标系统的地址与契约（`OpenApiToolAdapter` 期望 `POST {base}/{tool}` 返回 `{success,text}`），或像工单这样写第一方适配器。
- **高风险 `it`/`hr` 的 `pending_action` 仍是 mock**：`prepare_approval` 按 agent 硬编码映射，`it`/`hr` 会落到 `approval_decision` → mock 返回"审批流程已完成"。这条与工单无关但同属"假执行"，需要一并修。
- **前端还没把 `simulated` 显示出来**：事件里已带该标志，聊天界面尚无"模拟数据"角标。

---

## 18. 高风险路径的假执行（已修）

第 17 节末尾留下的那条"审批是真的、执行是假的"，本轮修掉了。

### 18.1 缺陷的确切形状

```python
pending_action = {"network": "vpn_api_provision", "finance": "finance_submit"}
                 .get(selected_agent, "approval_decision")     # 按 agent 猜动作名
...
"vpn_api_provision": lambda: {"success": True,
    "text": "VPN 审批已通过，账号和配置已开通。",                  # 硬编码成功
    "data": {"provisioned": True, "vpn_account": "vpn-user-001"}}
```

三点问题：动作名是**猜的**（`it`/`hr` 会拿到一个根本不对应的 `approval_decision`）；返回值是**写死的成功**；`verify_result` 还会无条件说"写操作已完成，正在核对执行结果"。因为审批**确实**发生了，这段文案读起来就像确认。

### 18.2 改法

| 改动 | 说明 |
|---|---|
| **写操作注册表** `app/tools/actions.py` | 把"这个动作有没有接入目标系统"变成可查询的事实。`external_implemented`（外部系统是否接入）与 `records_change_ticket`（是否在我们自己的系统留痕）是**两件独立的事**，此前被混成了一句文案 |
| **动作由意图推导** | `pending_action` 不再按 agent 猜，而是按 intent 映射（`network.vpn.request → vpn_api_provision`）。没有对应动作的意图（如 `approval.decision.request`）**就是** `None`——"没有外部动作"是一个真答案，编造一个才是缺陷来源 |
| **未接入就绝不调适配器** | 外部动作未接入时**不调用**适配器，直接返回 `success=false` + `ACTION_NOT_IMPLEMENTED`，文案明确指出"该动作未执行" |
| **可达成的那一半是真的** | 通过**同一套 Java 工单接口**创建一张真实的**变更单**（`type=change`），把审批单 id、意图、原始请求写进描述。这是登记，**不是**开通，文案把两者分得很清楚 |
| **账本记为 failed** | `success=false` 让账本落为 `failed`。只有 `succeeded` 才会短路，所以等目标系统接入后**仍可重试**，而不是这次审批被永久标记为已完成 |
| **审计如实** | `tool.write` 记录 `outcome=failure`、`external_executed=false`、`error=ACTION_NOT_IMPLEMENTED`、`change_ticket_number` |
| **`verify_result` 不再谎报** | 写失败时说"写操作未成功执行：…"，无动作时说"本次没有需要执行的外部动作" |
| **删除假成功处理器** | mock 里 `vpn_api_provision`/`finance_submit`/`approval_decision` 三个处理器替换为 `implemented_write_refusal()`，即使被直接调用也只能返回失败 |
| **护栏改为按工具声明 schema** | 建变更单需要 `type`/`title`/`description`，但共享白名单拒绝它们。没有放宽全局白名单（那会同时松动其它所有工具），而是让工具声明自己的 schema（`TOOL_ARG_KEYS_EXTRA`），其它工具仍然拒绝任何自造参数 |
| **`/ready` 增加 `write_actions`** | `externalExecutionAvailable: false` 与逐个动作的 `externalImplemented`，运维不必读源码就知道"审批会通过，执行不会发生" |

### 18.3 顺带发现

| 发现 | 说明 |
|---|---|
| **审批键可能算出两个** | `prepare_approval` 与 mock 的 `approval_result` 各自用 `approval_key_for(turn_id, action)`，而动作名来源不同；一旦不一致，**同一轮会生成两张审批单**。已统一到 `action_key(intent)`，两边不可能分歧 |
| **被拒的写动作没有审计** | 批准后的写动作若被 RBAC 拒绝，原先直接返回、**不写审计**——于是"批准了但什么都没发生"在审计里不留痕迹。现已记录 `outcome=denied` + `reason=RBAC_DENIED` |
| **执行用的是申请人权限，不是审批人权限** | 这是**既有策略**，我没有在无告知的情况下改动：审批是加一道签名，**不是**授予申请人原本没有的权限（否则审批就成了提权通道）。附带后果值得记录：`employee` 角色连 `network` agent 都进不去（`authorize_agent` 是 any-of 判定），所以纯员工**根本无法发起** VPN 申请，也就永远走不到审批。这是权限姿态问题，不是本轮缺陷 |
| **恢复结果未持久化** | 实测中 `approval_task.resume_state=succeeded` 而 `result` 为 `None`。审批动作到底做了什么，目前**只能从审计与账本行还原**；`result` 这一列不可依赖。已记录，未修 |

### 18.4 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **214 项全绿** |
| Python 单测 | **262 项全绿**（新增 16：注册表解析、无动作即 None、文案区分"登记/执行"、旧动作名只能失败、护栏未被全局放宽、未接入不调适配器、变更单参数与关联、审计失败与未执行、无动作分支、开关关闭、RBAC 仍拦截、`verify_result` 如实） |
| 实时端到端 | **34 项通过 / 0 失败**，两个场景 |
| 前端 / 数据库 | `tsc` 0 错误；schema 漂移 PASS |

实时结果（真实 PostgreSQL 与两个服务）：

- **场景 A（申请人有写权限）**：审批单 `pending_action=vpn_api_provision`（取自意图）→ 批准 → 恢复无错 → 事件与恢复结果里**没有任何**"已开通/账号和配置"字样 → **真实变更单 #15**（关联审批单 id、标注"尚未接入"）→ 账本 `vpn_api_provision = failed` → 审计 `failure / external_executed=false / ACTION_NOT_IMPLEMENTED / change_ticket_number=15`。
- **场景 B（申请人无写权限）**：批准成功但写动作被 RBAC 拒 → 审计 `denied / RBAC_DENIED / external_executed=false` → 不建变更单 → 也不谎称已开通。

另有一条**测试自身的问题**被修掉：`test_approval_checkpoint_protocol` 等三个测试断言"适配器被调用过 1 次"以验证恰好执行一次，而默认注册表里所有动作都未接入、按设计不调适配器，断言会变得**空洞**。现在这些测试显式把动作标记为已接入并让替身像真目标系统那样返回成功，断言重新具备意义。

### 18.5 仍未做（诚实清单）

- **外部系统仍然没有接入**：这是 `external_implemented=False` 的字面含义。接入 VPN/ERP 需要各自的地址与契约，在那之前"审批通过 → 未执行"就是正确行为。
- **变更单只是登记**，不等于开通；描述里已写明，但产品话术上仍需注意别把它说成"已开通"。
- **`approval_task.result` 不可依赖**（见 18.3），恢复结果的持久化待查。
- **高风险只覆盖 `network.vpn.request` 与 `finance.service.submit`** 两类意图；`it`/`hr` 目前没有高风险意图，因此没有对应写动作。

---

## 19. 执行授权模型：员工申请、管理员批准、系统开通（已实现）

第 18.3 节留下的那个策略问题由产品意图定案：**VPN 开通是提权路径**——员工本来就不该持有 `network:vpn:create`，审批就是授权它的那个控制项。因此授权模型从"申请人权限"改为**按动作显式声明**。

### 19.1 两种模型，按动作声明

| 动作 | 授权模型 | 含义 |
|---|---|---|
| `vpn_api_provision` | `system` | 员工申请 → 管理员批准 → **系统**执行。申请人**不需要**持有写权限，审批即授权 |
| `finance_submit` | `requester` | 员工提交自己的报销，**必须**持有该权限，审批是加在其上的第二道签名 |

关键在于这是**每个动作显式声明**的事实，而不是全局策略；`/ready` 的 `write_actions.actions[].authority` 与 `systemExecutedActions` 把它暴露出来，因为系统执行的那一类才是高风险的。

### 19.2 提权通道必须窄：系统执行的七道校验

把"系统账号能开通 VPN"变成现实，等于造了一个**提权能力**。所以 `evaluate_system_authority()` 是一个纯函数，逐条挡住每一种滥用方式，且**每条都有对应测试**：

| 校验 | 挡住什么 | 拒绝码 |
|---|---|---|
| 必须有审批 id | 无审批即执行 | `APPROVAL_MISSING` |
| 审批必须能在库中读到 | 引用失效/丢失的审批 | `APPROVAL_NOT_FOUND` |
| **以库中的状态为准**，而非图状态 | 重放一个被撤回或过期的决定 | `APPROVAL_NOT_APPROVED` |
| 必须记录决定人 | 无人授权的"系统自动通过" | `APPROVAL_NOT_AUTHORIZED` |
| 审批的动作必须与本次动作一致 | **批准"重置密码"却用来开通 VPN** | `APPROVAL_ACTION_MISMATCH` |
| 未被消费过 | 一次审批买多次效果 | `APPROVAL_ALREADY_CONSUMED` |
| 未过期（默认 24h，可配） | 旧审批成为长期通行证 | `APPROVAL_EXPIRED` |
| 决定人 ≠ 申请人 | **自批**使整套控制形同虚设 | `APPROVAL_SELF_DECIDED` |
| 审批属于当前申请人 | 把别人的审批借到自己的会话里用 | `APPROVAL_REQUESTER_MISMATCH` |

两层事实来源的分工值得写清楚：**授权有效性以审批库为准**（"现在的决定"），**效果一次性由幂等账本保证**（`turn_id + action`，DB 行 + 进程内锁），`consumed_at` 是纵深防御。图状态只用来取审批 id——它是"当时的决定"，对一条提权路径而言不够。

### 19.3 审计回答"是谁授权的"

系统执行时申请人只是**受益人**，调查真正需要的是授权人。所以 `tool.write` 的 detail 增加三个字段：

```
executed_as: "system" | "requester"
authorizer_id: <审批人 id>        # 系统执行时必有
on_behalf_of: <申请人 id>
```

### 19.4 顺带确认：提权通道没有从普通路径泄出

系统执行的动作**只能**由 `execute_approved_action` 触发。已加测试断言 `vpn_api_provision` 不在任何 agent 的 `TOOLS_BY_AGENT` 里，也不会被 `select_tools_for_request` 选中（对 7 个 agent × 3 种说法逐一断言）——否则"员工申请"就变成了"员工直接开通"。

### 19.5 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **214 项全绿** |
| Python 单测 | **289 项全绿**（新增 27：两种模型声明、九种拒绝路径逐一覆盖、拒绝文案、有效审批下无写权限者也可执行、审计三字段、双人复核不误标为系统执行、库不可读不等于授权、提权动作不可从普通路径选中） |
| 实时端到端 | **36 项通过 / 0 失败**，三个场景 |
| 前端 / 数据库 | `tsc` 0 错误；schema 漂移 PASS |

实时结果（真实 PostgreSQL 与两个服务，`sa.requester` 只有 `network_user`，**本身没有写权限**）：

- **场景 A（提权路径）**：员工发起申请 → 审批单 `required_approver_roles=network_admin,approver` → 管理员批准 → **没有出现 RBAC 拒绝**（模型一的阻塞已消除）→ 真实变更单 #16（关联审批单 id）→ 账本 `vpn_api_provision = failed, attempts=1` → 审计 `failure / executed_as=system / authorizer_id=<审批人> / on_behalf_of=<申请人> / external_executed=false / ACTION_NOT_IMPLEMENTED`。
- **场景 B（拒绝）**：管理员拒绝 → 审批 `rejected` → **什么都不执行**（无变更单、无账本行、无假文案）。
- **场景 C（一次性）**：重复决策 → 仍只有一张变更单、只有一次执行尝试。

### 19.6 仍未做（诚实清单）

- **VPN 系统仍未接入**：所以"系统开通"目前表现为"授权成立、外部未执行 + 真实变更单登记"。接入后要做的只有替换 `external_implemented` 并把系统凭据接进适配器——授权链路已经就位。
- **系统凭据尚未存在**：真正的 VPN 适配器应当使用**服务账号**凭据，而不是申请人的令牌。当前唯一真实的效果（变更单）刻意以申请人身份创建，因为那**就是申请人的工单**，需要出现在他的列表里；这个区分在审计里已写明（`executed_as` 与 `on_behalf_of`）。
- **`finance_submit` 的写权限用的是 `finance:invoice:read`**（读权限当写门禁），是既有命名遗留，未改。
- **审批过期只在系统执行路径校验**，双人复核路径不校验（那边由申请人自己的权限兜底）。
- **没有"撤销已批准"的流程**：批准后到执行前无法撤回。

---

## 20. 审批挂到真实效果：合并工单（虚构写动作已删除）

第 19 节留下一个尴尬结论：`authority=system` 的能力建好了，但**没有任何生产动作使用它**，因为 VPN 系统不存在。当时的"下一步"里我写了"接 VPN 适配器"——那是念计划而不是想清楚：接谁？没有可接的对象，写适配器就等于对着我自己编的 API 写 HTTP 客户端。

所以这一轮做的是：**把审批挂到我们真正拥有的系统上，并删掉虚构动作。**

### 20.1 唯一的动作是真的

| | |
|---|---|
| 动作 | `ticket_merge`——合并两张工单 |
| 效果 | 源工单被关闭并指向目标（墓碑），通信记录与历史**迁移**到目标 |
| 为什么需要二次签名 | **近似不可逆**：源单不再作为独立实体存在 |
| 授权模型 | `requester`（双人复核）。`TicketService.merge` 有 `requireAgent`，所以申请人本身就是处理人，审批是加在其上的第二道签名 |
| 已接入 | ✅ `external_implemented: true`——它调用的是我们自己的 Java 工单服务 |

`vpn_api_provision` 与 `finance_submit` **已删除**。它们的意图现在解析为"没有需要执行的动作"，此时已批准的请求会被**登记为一张变更单**交由人工跟进，而不是凭空消失。

### 20.2 顺带修掉的四个真问题（都是测试/实测抓出来的）

| 问题 | 说明 |
|---|---|
| **护栏会拦掉真实动作** | `ticket_merge` 的参数（`sourceNumber`/`targetNumber` 等）不在按工具声明的白名单里，真跑起来会被工具输入护栏拒绝。已加入 `TOOL_ARG_KEYS_EXTRA`——注意**没有**放宽共享白名单，其它工具仍然拒绝自造参数 |
| **规则里的 `risk: high` 是死字段** | `_policy_risk` 按 (operation, domain) 覆盖规则声明，`it`+`submit` 一律 medium，所以规则写的 high 被静默忽略——下一条规则也会踩。改为**策略是下限、规则可升不可降**：可以上调（合并在 it 桶里也必须更严），但**不能下调**，否则一个随手写的模式就能把自己的动作豁免出审批 |
| **恢复时执行的动作用的是图状态而非审批记录** | 图状态里的 `pending_action` 可能是空的或是别的值。现在**以审批记录里的动作为准**（那才是"被批准的到底是什么"），两边不一致就拒绝并审计双方动作名。这同时挡住了最阴的一种重放：批准"合并工单"，却让系统去执行别的动作 |
| **协议测试的断言会变得空洞** | 三个"恰好执行一次"的测试原先依赖一个已删除的动作名，且它们的请求（"申请VPN权限"）现在解析为无动作，于是 `adapter 调用次数 == 1` 永远不成立也不会失败得有意义。已把请求改成真实的合并请求，并让审批人使用通用 `approver` 角色 |

### 20.3 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **217 项全绿**（新增 3：合并端点解析两个编号并审计、缺一张就拒绝且不调服务、业务拒绝保持错误码而非 500） |
| Python 单测 | **299 项全绿**（新增：真实合并的执行/审计/一次性、审批记录决定执行哪个动作、动作不一致拒绝、合成动作承载 system 与未接入两种能力、风险策略可升不可降、合并编号解析与本地拒绝、`/ready` 如实报告） |
| 实时端到端 | **35 项通过 / 0 失败**，三个场景 |
| 前端 / 数据库 | `tsc` 0 错误；schema 漂移 PASS |

实时结果（真实 PostgreSQL 与两个服务）：

- **场景 A（真实效果）**：处理人 `it_staff` 说"帮我把 #21 合并到 #22" → 高风险 → 审批单 `pending_action=ticket_merge`、`required_approver_roles=it_staff,approver` → 审批人批准 → **源工单真的变成 closed 并指向目标**，目标单保持打开，合并条目记在**目标单**时间线上、源单历史已迁移 → 账本 `ticket_merge = succeeded, attempts=1` → 审计 `success / executed_as=requester / external_executed=true / approval_id=…` → Java 侧 `ticket.merge` 审计标明 `origin=agent, tool=ticket_merge, source=21, target=22`。
- **场景 C（一次性）**：重复批准 → 仍然只有一次合并、账本只有一条。效果不可逆，所以这条尤其重要。
- **场景 B（有审批但无权限）**：`it_user` 能发起合并申请并被批准，但执行时 RBAC 拒绝 → 源工单**没有被合并** → 审计 `denied / RBAC_DENIED`。

### 20.4 仍未做（诚实清单）

- **`authority=system` 没有生产动作**：能力保留并有测试覆盖（用一个**测试专用**动作），`/ready` 的 `systemExecutedActions` 如实为空。要启用它需要真实第三方系统 + 服务账号凭据 + Java 侧校验审批，这些都没有。
- **`ticket_get` / `ticket_comment` 仍未接出**：有真适配器但不从对话选择（`implemented_not_wired`）。
- **其余 20+ 个工具仍是桩**（引用 IdP/MDM/HRIS/ERP），只是会自我标注 `simulated`、`/ready` 列明。**它们是下一批该删或该接的东西**——按这两轮的标准，没有目标系统的工具不应该继续以"可用"的样子存在。
- **没有"撤销已批准"**；`approval_task.result` 仍不可依赖。
- **合并的编号解析是正则**（"把 #12 合并到 #14" 的顺序即源→目标），仍无 function calling。

---

## 21. 清掉桩工具，接上 function calling

### 21.1 桩工具已全部删除

删掉的：`idp`、`mdm`、`software_center`、`vpn_api`、`cmdb`、`permission_system`、`hris`、
`organization_service`、`form_service`、`erp`、`ocr`、`budget_service`、`pgvector`、
`elasticsearch`、`reranker`、`rule_engine`、`temporal`、`notification_system`、`rbac`、`dlp`、
`audit_service`、`agent_registry`、`policy_api`——以及 `mock.py`（整个 mock 适配器）、
`graph/nodes/approval.py`（从未接线的死代码，却持有**第二份**动作映射并引用了已删除的动作）。

| 后果 | 说明 |
|---|---|
| **没有 mock 提供方了** | 未实现的工具由 `RefusingToolAdapter` 明确拒绝（`TOOL_NOT_IMPLEMENTED`），而不是返回编造的成功。工具没实现就**不能被调用** |
| **结果里的 `simulated` 标志消失** | 因为已经没有"模拟"这回事了 |
| **新权限模型** | 旧的域权限（`it:password_reset`、`network:access:read`…）存在的唯一目的就是给已删除的桩当门禁。现在 agent 准入是 `service:request`（所有已认证角色都有），**凡改变状态的操作一律逐工具授权**（`ticket:merge` 只有 staff 有） |
| **agent 不再有两份工具清单** | `agents.yaml` 与 catalog 的一致性由测试守住：声明了 catalog 里没有的工具会直接失败 |

**一个真正重要的一致性修正**：同一个事实原先写在四个地方（mock 处理器表、per-agent 工具表、
`TOOL_PERMISSIONS`、护栏参数白名单），它们已经漂移——`ticket_merge` 就带着被自己安全护栏拒绝的
参数上线了。现在**一切都从 `app/tools/catalog.py` 的一个条目派生**：权限、给模型的 JSON Schema、
护栏的允许参数清单。测试逐工具断言这三者一致。

### 21.2 function calling 已接入

| 设计点 | 理由 |
|---|---|
| 模型只看得到 **`direct_tools()`**（4 个）：`ticket_create`/`ticket_list`/`ticket_get`/`ticket_comment` | **审批门控的 `ticket_merge` 永不交给模型**。它仍由意图分类法 + 审批门驱动，所以模型永远不可能成为破坏性动作执行的原因 |
| 候选先经 `authorize_tool` + agent 允许清单过滤 | 不给模型看它会（也应该）被拒绝的工具 |
| 模型的调用是**请求，不是授权** | 三道门在事后照样执行：agent 允许清单、用户权限、参数 schema。测试断言模型塞入 `priority` 会被护栏整体拒绝 |
| `tool_choice: "auto"` | "直接回答"必须仍是选项，否则每一轮都会建单 |
| 模型不可用时**回落到规则**，并在 `/ready` 说明 | `tools.routing` 同时报 `configured` 与 `lastObserved`，以及 `fellBack`。只报"配置为模型选择"而实际在跑回落，就是在撒谎 |
| 模型选择不改变执行语义 | `tool_call` 事件新增 `chosenBy: model|rule-based`，审计与护栏路径完全相同 |

### 21.3 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **217 项全绿** |
| Python 单测 | **330 项全绿**（新增 31：catalog 单源一致性、护栏按 catalog 拒绝自造参数、门控工具不交给模型、仅 staff 可合并、未实现工具拒绝、请求构造与 `tool_calls` 解析（含畸形条目丢弃）、无模型时拒绝而非假装、`configured` 与 `lastObserved` 的区分、模型选择落库为真工单、模型塞参数被拒、审计者只能看到 `ticket_list`、模型失败回落、门控工具无法从该节点抵达、模型不选就不建单） |
| 实时端到端 | **22 项通过 / 0 失败** |
| 前端 / 数据库 | `tsc` 0 错误；schema 漂移 PASS |

实时结果（真实 PostgreSQL、两个服务、真实模型）：

- **桩的编造文案彻底消失**：`我的账号被锁了，帮我重置密码` 过去返回硬编码的"已生成密码重置链接"
  与编造的 `IT-IDP-10086`；现在**建出真实工单 #25**（`type=request`），且全部 10 个旧文案串
  一个都没出现。
- **模型在选工具**：`tool_call: ticket_create, chosenBy=['model']` → **真实工单 #26**
  （`source=chat`、申请人是本人）；追问"我的工单现在什么状态？"→ 只选 `ticket_list`，
  不重复建单。
- **`/ready` 如实**：`implemented` 只有 5 个工单工具、`declared_unimplemented` 为空、
  `modelCallable` 不含 `ticket_merge`；`routing = {configured: model, lastObserved: model, fellBack: false}`。

### 21.4 仍未做（诚实清单）

- **只有工单一个系统**：catalog 里 5 个工具全部指向 Java 工单服务。`authority=system` 仍无生产动作。
- **`ticket_get` / `ticket_comment` 仍未接给任何 agent**（`implemented_not_wired`）：模型看得到
  它们的 schema（它们不是门控工具），但没有任何 agent 声明，所以候选清单里不会出现。要启用只需在
  `agents.yaml` 加一行——不过我倾向先想清楚"agent 主动给工单写备注"的产品语义。
- **function calling 只有单轮**：模型选一次工具、执行、然后由 `generate_answer` 回复；
  **没有把工具结果回灌给模型再让它续选**的多轮循环。对当前 5 个工具够用，但不是完整的 agentic loop。
- **合并编号仍是正则解析**（在适配器里），且合并仍由意图分类法触发，不走模型。
- **知识的 `pgvector`/`elasticsearch`/`reranker` 不是工具**：知识检索一直由 `retrieve_knowledge`
  节点与检索管线完成，删掉的只是三个从未被任何路径选中的同名桩。
- `corpus.py` 里的知识文档仍提到 IdP/ERP——那是**文档内容**（某公司的历史工单语料），不是工具，保留。

---

## 22. 多轮 agentic loop（工具结果驱动下一步）

### 22.1 之前做不到什么

单轮：模型选一次 → 执行 → 由 `generate_answer` 写答复。**"先查单、再对这张单做点什么"无法表达**，因为第二步依赖第一步返回的东西。本轮把它改成有界循环。

### 22.2 循环引入的新失效模式，以及对应的防线

一次就能做完的事，循环会带来**新的**危险——所有防线都在测试里逐条钉住：

| 新失效模式 | 防线 |
|---|---|
| **模型重复调用同一工具** → 真实写操作就是**第二张工单** | 按 `(工具名, 参数)` 精确指纹**拒绝重复调用**（测试断言：相同参数第二次被执行拒绝，参数不同则照常执行） |
| **模型停不下来** → 账单无上限 | `agent_max_rounds`（默认 3，管的是**实际执行了工具的轮数**）+ `agent_max_tool_calls`（默认 6），达到上限时发一条 `thinking` 事件说明 |
| **越权在第二轮被绕过** | 三道门（agent 允许清单、用户权限、参数 schema）**每一轮、每次调用**都重新执行，不是只在第一次 |
| **协议回灌出错** | 助手消息**原样回显**（`tool_calls` 不被重建），工具结果按 `role: "tool"` + `tool_call_id` 逐条回灌；参数解析失败的调用**不回显也不执行** |
| **轮次不可见** | `tool_call`/`tool_result` 事件带 `round`，`/ready` 报 `maxRounds`/`maxToolCalls` |

`rounds` 的语义特意定义为"**执行了工具的轮数**"：模型说"我不需要工具了"的那个终止轮不计入，因为上限管的就是执行轮；但它确实是一次真实模型调用，所以它决定了 `tool_routing = model`（即**没有**回落）。

### 22.3 顺带把 `ticket_get` 接出去

循环要有意义，就得有第二步可做。`ticket_get`（只读，需 `ticket:read`）此前是 `implemented_not_wired`，现在接给 it/network/hr/finance/knowledge。"先列出我的工单，再把某一张的详情读出来"正好是两步依赖——**实测中模型真的这么走了**。

### 22.4 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **217 项全绿** |
| Python 单测 | **334 项全绿**（新增 4：第二轮能看到第一轮结果（断言回灌的 `role: tool` 消息内容）、重复调用被拒、参数不同仍可执行、轮数上限、调用总数上限、无候选时不问模型） |
| 实时端到端 | **18 项通过 / 0 失败** |
| 前端 / 数据库 | `tsc` 0 错误；schema 漂移 PASS |

实时结果（真实模型 + 真实 PostgreSQL）：

- **一次故障只建一张单**：`tool_call: ticket_create rounds=[1]` → 工单 #27，**总数恰好 1**（循环没有把它变成两张）。
- **两步依赖真的发生了**：提示"我的工单里哪一张是关于打印机的？把那一条的详情读出来给我" →
  `tool_call: ['ticket_list', 'ticket_get'] 涉及轮次=[1, 2]`。**第二次调用用的是第一次返回的编号**，这就是"工具结果驱动下一步"。
- 单步查询仍然单步（`ticket_get rounds=[1]`），且不产生新工单。
- `/ready`：`routing = {configured: model, lastObserved: model, fellBack: false, maxRounds: 3, maxToolCalls: 6}`。

### 22.5 仍未做（诚实清单）

- **没有并行工具调用**：一轮里的多个调用是**顺序**执行的。对写操作这是对的（顺序可预测），但读操作本可并发。
- **没有流式工具决策**：循环里的模型调用是非流式的，只有最后 `generate_answer` 走 SSE。用户看到的是"思考中"而不是逐字的推理。
- **终止条件只有"模型不再调用"或撞上限**：没有"结果已足够回答"的判定，也没有成本预算（只按次数，不按 token）。
- **`ticket_comment` 仍未接出**：写备注的产品语义（agent 代表用户写、还是只建议用户自己写）没定，我没有单方面决定。
- **循环状态不进 checkpoint**：中断/恢复发生在审批节点，循环本身是单次节点执行。
- 合并仍由意图分类法触发（不走模型），编号解析仍是正则。

---

## 23. 循环的五项改进

### 23.1 只读并发、写操作串行

一轮里的只读调用用 `asyncio.gather` **并发**执行；**改变状态的调用按模型给出的顺序串行**——写的顺序本身携带语义。重复指纹**在任何 `await` 之前登记**，所以同一批里的两个相同调用不可能都通过检查（有专门测试守这条竞态）。测试用"同时在场计数"断言并发真的发生（顺序 await 永远不会超过 1）。

### 23.2 决策流式

工具**选择**过去是不可见的：用户看到一句通用"思考中"，然后是一个成型的答复。现在决策本身走流式，模型的话按 `thinking` 事件转发。要点：

- 工具调用以**片段**形式到达（名字一次、参数分片、按 `index` 归并），所以由 `_turn_from_fragments` 装配；参数解析失败的调用**不回显也不执行**；
- `stream_options.include_usage` 才会带上用量；**没有用量时报 `None` 而不是 0**（否则"未报告"会伪装成"零成本"）；
- 模型调用收敛到**单一接缝** `model_turn`，所以测试不必关心某轮走的是哪种 HTTP 形态；流式另有 `agent_stream_decisions` 开关。

### 23.3 预算与终止

按 **token**（`agent_token_budget`，默认 20000，来自 `usage.totalTokens`）与**写后终止**（`agent_stop_after_write`）收口，并记录 `stopReason`（`TOKEN_BUDGET` / `TOOL_CALL_BUDGET` / `WRITE_SUCCEEDED`）。"写成功即终止"的理由：写**就是**结果，再跑一轮只可能重复写、或读一些答复已经能写的东西。

> 这带来一个**语义变化**并改掉了我自己的三条测试：写成功即终止，所以"第二轮再写一次"不会再发生。重复抑制因此单独用 `agent_stop_after_write=False` 验证——否则那条测试是空过的。

### 23.4 `ticket_comment`：只允许公开回复

产品语义定案：**记录用户口述的内容**，而不是代替用户说话；`visibility` 从该工具的 schema 里**彻底移除**，因此模型自造它会被护栏拒绝（而不是被静默降级成 public）。**内部备注是人工在工单界面里的动作**，agent 没有这个能力——"对申请人隐藏"不该是一个助手能替别人做的事。

### 23.5 循环与重放安全（未按"拆成图节点"实现）

第 22.5 节说"循环状态不进 checkpoint"。把循环拆成图节点能获得**每轮** checkpoint，但那会重构我已经验证过的审批/checkpoint 路径。实际采用的办法是让**效果幂等**：

**每一个改变状态的直接调用都过幂等账本**，键为 `turn_id + 工具 + 参数摘要`。于是节点重放（循环中途崩溃、turn 重投）**无法把同一笔写做两次**，即使恢复粒度仍是每节点一次。只读调用不进账本（廉价且幂等，抑制它们反而会掩盖新结果）。

这条接线的第一次实现有 bug：我把参数嵌进账本 `action`，超出 `varchar(64)` 导致插入失败，账本**拒绝执行该写操作**——方向是对的（宁可拒绝也不无记录地写入），并且现场日志直接指出了它。已改为短摘要，并加了一条长度断言把这个上限留在测试里。

### 23.6 顺带发现并修复：限流后返回 200 空响应（真缺陷）

实时验证时登录反复失败，定性后发现是**客户端可见的认证缺陷**：

```
修复前：限流触发后连续 39 次登录 → HTTP 200，响应体为空（客户端会当成"登录成功但没有 token"）
修复后：38× 429（带 {"error":"RATE_LIMITED","retryAfterSeconds":N}）+ 2× 200（真实成功）+ 0 次空响应
```

原因在 `RateLimitFilter.proceedOrLimit`：限流时返回 `Mono.empty()`，**从不写响应**，Netty 于是把未写入的响应报告为 200 空体。已改为限流时写 429（带 `isCommitted()` 守卫，避免与其它分支重复写把连接撕掉）。

### 23.7 验证

| 层次 | 结果 |
|---|---|
| Java 单测 | **217 项全绿** |
| Python 单测 | **349 项全绿**（新增 14：并发真的发生、写等读且保序、同批相同调用不可能都跑、片段装配、畸形片段丢弃、无用量报 None、决策转发到客户端、token 预算终止、无用量时 accounting 为 None、重放不重复写、重放不抑制读、comment 无 `visibility` 且自造被拒、账本动作名不超列宽） |
| 实时端到端 | **14 项通过 / 1 项未完成**（见下） |
| 前端 / 数据库 | `tsc` 0 错误；schema 漂移 PASS |

实时确认的：`/ready` 报告 `tokenBudget`/`stopAfterWrite`/`maxRounds`/`maxToolCalls`；决策确实流式（一轮里有 167 个模型自己的片段）；**工单仍然只建一张**（连续两次 `ticket_create` 的重放在账本处被挡住后，故障报告正常建出 #28）；`ticket_comment` 已声明为写操作且无未接出工具；限流缺陷修复前后对照如上。

**未完成的一项**：`ticket_comment` 的**实时落库**没有确认。实测中模型对"帮我在工单 #28 上写一句…"先选了 `ticket_list` 查单、然后直接作答而没有写入；改用更明确的指令复测时，我的探针把登录限流额度用尽（限流本身已正确工作），未能完成。所以：评论路径**已接线、有单测**（schema 拒绝 `visibility`、写操作标记、RBAC），但**端到端未经实测确认**。我不把它算作已验证。

### 23.8 仍未做（诚实清单）

- **没有并行工具调用的上限**：一轮里只读调用的并发数由模型一次返回多少决定，没有独立的并发上限。
- **流式只覆盖决策**，最终答复仍由 `generate_answer` 单独流式；两段之间没有共享的 token 账。
- **预算只按 token**，没有货币成本；也没有"结果已足够回答"的语义判定（只有"写成功即终止"这个代理）。
- **每轮 checkpoint 仍未做**（用效果幂等替代，见 23.5）；恢复粒度仍是每节点一次。
- **`ticket_comment` 端到端未实测**（见 23.7）。
- 循环里超过 `agent_max_tool_calls` 的调用被**截断丢弃**而不是排队执行——模型不会知道有调用被丢掉了。

---

## 24. 上下文管理：从"启发式估算"到"可验证的预算引擎"（已实现）

第 2 节把"上下文装配"列为真实优势，第 5 节又列出四条短板（`task_slots` 占位、
拼接式伪摘要、硬编码提示词、`context_report` 不落地）。本轮按
`docs/context-engine-design.md` 的 PR1–PR4 全部落地，并且**该文档的问题清单本身
就有一半是错的或过时的**——下面按"改了什么、哪些说法被证伪、实测抓到什么"记录。

### 24.1 交付内容（PR1–PR4）

| 模块 | 改动 |
|---|---|
| `context/tokenizer.py` | 真实 tokenizer（`tiktoken/cl100k_base`）替代 `len/3.2` 启发式；tiktoken 不可用时降级到按文字系统加权的估算器，并在 `/ready` 标注 |
| `context/model_specs.py` | 模型窗口/输出上限的**单一事实来源**，数值取自厂商页面并记录来源与抓取日期 |
| `context/policy.py` | 块策略单源（权重/下限/上限/保护级/截断方式）；删掉 Java 与 Python 各一份的优先级副本 |
| `context/budget.py` | 先保下限、再加权注水、余额回流；**messages 作为参与者进入预算**；硬上限不变量由断言守住 |
| `context/render.py` | 系统提示的形态集中一处；**脚手架先计量再分配** |
| `context/report.py` | report v2：每个块的预算/实际/截断/丢弃 id、消息逐出 id、证据 citation_map、provider usage 与误差比 |
| `context/summary.py` | 增量滚动摘要：覆盖区间来自"实际被逐出的消息"，结构化字段，模型失败回退到确定性抽取且标注 `quality=fallback` |
| `context/builder.py` | 收敛为编排层；`context_report` 随 SSE 回传 |
| Java `ConversationContextService` | envelope 升到 `schema_version=2`：**删掉 `task_slots`/`tool_results` 占位块**、结构化摘要、候选窗口 20→40、`limits` 如实体现在 envelope 里 |
| Java `ConversationSummaryService` | 新增 `persistRolling`（写入引擎报告的覆盖区间与结构化字段）；旧的**每轮覆盖式 `roll` 默认关闭** |
| Java `StreamEventAccumulator` | 新增：一个可测试的 SSE 事件读取器，**未知事件类型一律忽略**（这是新增事件能向后兼容的原因） |
| Java `schema.sql` | `conversation_summary` 增 `covered_from_message_id`/`covered_until_message_id`/`field_values`，全部走 `ADD COLUMN IF NOT EXISTS` 守卫 |
| `eval/metrics.py` | 新增 `check_context_citation_map`：校验"被引用的 `[n]` 在提示里真的存在、编号从 1 连续、且指向实际保留的证据" |
| 前端 | `context_report` 进 `SSEEventType` 并在 chat service 显式忽略（不落到"未知事件"），dev 模式留一条可读日志 |

### 24.2 被证伪/修正的原判断（重要）

1. **问题 1 说"中文低估约 2×、8192 预算可能送 15k"——数值对，但结论过时。**
   仓库默认模型是 `deepseek-flash`，实测窗口是 **1M tokens**（厂商文档口径），
   默认输出预留 4096。也就是说 1M 窗口下"输入超窗"根本不是当前的失效模式；
   真正的失效模式是**另一种**（见 24.3 第 1 条）。
2. **问题 2 说 `recent_messages` 完全不受预算约束——已确认，且修掉后暴露出更严重的一层**：
   它不只是不受约束，而是**根本没有对应的块**（权重表里有 0.24，代码里却只从
   `context_blocks` 建块）。本轮把它变成真正的参与者。
3. **"默认 Hash 伪向量"（§15.2）与"tokenizer 不准"（问题 1）不是同一个问题**，
   不能互相替代：前者影响召回，后者影响窗口是否被撑破。
4. **`context_report` 的 SSE 事件对 Java 与前端都是向后兼容的**——已逐文件核对：
   Java 的 `accumulateEvent` 与前端 `chat.service` 都是 if/else 链，未知类型天然被忽略。
   这一点在落地前是"推测"，现在是核对过的结论（并已把 `context_report` 显式列进
   前端类型，避免将来把"已知但 UI 不展示"和"未知"混为一谈）。
5. **模型规格表里的 `deepseek-chat` 没有任何可核实的窗口数值**（厂商页面已无该名称，
   属退役名）。因此它走保守默认（65536/4096）并在 `/ready` 报 `known: false`，
   而不是抄一个看起来合理的数字。

### 24.3 实测（真实 PostgreSQL + 真实 tokenizer + 真实模型规格）抓到的四个缺陷

单测全绿之后跑实时校验，抓到四个**单测抓不到**的真缺陷——它们全都属于
"纸面上算得通、渲染出来就超"或"报告自相矛盾"这一类：

| # | 缺陷 | 为什么单测抓不到 | 修法 |
|---|---|---|---|
| 1 | **证据块预算只有 11 tokens，12 条证据全被丢弃** | 单测里证据块的内容是调用方给的字符串，而 `builder` 给的是占位符 `"[12 items]"`；planner 按占位符算 demand，于是只留了小标题的位置 | 块内容改为**渲染后的候选文本**（`render_evidence_items`/`render_tool_results`），demand 按真实内容计 |
| 2 | **渲染后的提示比预算多几个 token** | 每个块都算对了，小标题也算进了块预算，但渲染器还会用空行连接各节，**连接符本身是 token** | 分配前按块数预留 `SECTION_JOIN_TOKENS` |
| 3 | **滚动摘要永远生成不出来** | 逐出集合 `{m1..m20, m24}` 不是**连续前缀**（填充会跳过装不下的中间那条），于是 `covered_from=m20, covered_until=m1`，区间切片为空 | 逐出改为**连续前缀**：历史消息装不下就停，更旧的一律逐出（历史上"半句话"本来也不该发） |
| 4 | **摘要算出来了却不出现在提示里** | 摘要需要 await，在装配之后才折入；而 skeleton 的节清单来自"存在的块"，`conversation_summary` 只有在已有摘要时才存在 → 渲染器无处安放 | skeleton **恒定声明** `conversation_summary` 节，摘要折入时直接渲染 |

这四条的共同根因值得单独记下：**"分配"与"渲染"是两个阶段，任何只在一侧计的
开销都会变成超窗**；而**"报告里的区间"必须是可切片的连续区间**，否则下游
（摘要）无法消费它。

### 24.4 验证结果

| 层次 | 结果 |
|---|---|
| Python 单测 | **340 项全绿**（本轮新增 54：不变量矩阵、tokenizer 精度与降级、truncate 五种策略、证据装填与重编号、报告校准、摘要区间、引用映射校验、四个回归） |
| Java 单测 | **237 项全绿**（新增 20：envelope 不再有占位块、结构化摘要与覆盖区间、重放不产生重复版本、`ON CONFLICT` 不覆盖胜者、未知事件类型被忽略、畸形帧不毁这一轮、事件→落库的接线） |
| 前端 | `tsc --noEmit` 0 错误 |
| 数据库 | schema 漂移检查 **PASS**（14 张 DDL 声明的表逐列比对 `information_schema`，含三列新加的守卫 ALTER） |
| 实时校验（真实 PG + 真实 tokenizer） | **21 项通过 / 0 失败**：短会话不逐出、长会话连续逐出、摘要区间可切片且被折入提示、引用编号从 1 连续且都在提示里出现、calibration 有/无 usage 的区分、`ON CONFLICT` 重放只留一版、不可行预算显式拒绝 |

不可行预算的实测尤其值得记：**该拒绝的时候必须拒绝**。第一次实时校验里它
"装配成功"了——因为测试用的策略上限（4000）比内容小，而当时的检查只看内容不看
上限；现在内容超过声明上限会走"块足迹超过可用预算"这条聚合校验，短缺点数如实上报。

### 24.5 仍未做（诚实清单）

- **影子模式只是开关，不是实现**：`context_shadow_mode` 已进配置与 `/ready`，但
  没有"同时算新旧两套并 diff"的代码路径——本轮的取舍是**不保留旧实现**（旧实现的
  缺陷正是本轮要消除的），因此没有可对比的第二套结果。要真正的影子对比，需要先
  冻结一份旧引擎。
- **校准是进程内 EMA**：多副本各自漂移，没有跨副本聚合；`estimate_error_ratio`
  在单进程内有意义，在集群里只是"某一副本"的观测。
- **`cl100k_base` 只是近似**：DeepSeek 未公开 tokenizer，因此误差比是必需的护栏；
  目前只在 `/metrics` 与 `/ready` 暴露，**没有**基于它自动收紧 `safety_margin`。
- **摘要用的模型调用没有记入 token/成本账本**：它走的是独立 HTTP 调用，
  `llm.calls` 等指标只覆盖主链路。
- **`conversation_summary` 没有保留策略**：版本只增不减（每次真实逐出加一版），
  长期会话会累积；治理策略（保留最近 N 版）未做。
- **Java 侧没有把 `context_report` 落成结构化日志表**：只写了 debug 日志与滚动摘要，
  "按 turn 复盘"仍要靠日志检索。
- **`task_slots` 仍然基本为空**：引擎会正确跳过空占位，但槽位填充本身不在本轮范围。
- **`recent_messages` 的候选窗口 40 是拍的值**：在 1M 窗口下它远小于可用预算，
  也就是说当前**窗口没被用满**（实测利用率约 1%）。提高它需要先有"更长的历史
  是否真的提升回答质量"的评测结论，而不是直接把数字调大。

---

## 25. 工具执行与异常兜底：从"一次调用定生死"到有界韧性（已实现）

按 `docs/tool-execution-and-fallback-design.md` 的 **PR1–PR6 全部落地**。该文档的
12 条缺口我逐条对着代码核对过，**全部成立**；下面记录交付内容、落地时发现的缺陷，
以及四处需要用户拍板的决定。

### 25.1 交付内容（PR1–PR6）

| 模块 | 改动 |
|---|---|
| `tools/resilience.py`（新） | `RetryPolicy` + `call_with_retry` + `is_safe_to_retry` + 按工具熔断；退避带抖动、**总时长预算**、重试原因入指标 |
| `tools/calling.py` | 工具选择回合按策略重试；`function_calling_model` 解析器统一模型（T5/缺口 5）；`total_tokens` 改为"输入+输出"（缺口 6 的一部分）；新增 `estimated_tokens`；`tool_result_message` 按 token 截断 + `data` 白名单投影；`_open_stream` 拆出"可重试的连接阶段" |
| `tools/catalog.py` | 每条工具新增 `write_policy`（direct/confirm/approval）、`result_required`、`result_data_fields` |
| `graph/nodes/tools.py` | 心跳（`tool_progress` + `runtime.heartbeat()`）；per-tool 熔断；结果契约校验；token 预算用估算推进并标注 `usageSource`；停因事件与指标；`confirm` 档位改为收集待确认写并停止循环 |
| `confirm_writes`（新节点） | 图级确认：`interrupt()` 在**任何副作用之前**，待确认调用来自 state（重放时 id 稳定），确认后走与循环相同的 RBAC/契约/账本 |
| `core/effect_ledger.py` | `started` 租约语义：活租约 → `EFFECT_IN_PROGRESS`；过期 → CAS 抢占；失败可重试；取消**不写 failed**（效果未知） |
| `persistence/postgres_repository.py` | `claim_stale_effect`：`UPDATE ... WHERE status='started' AND updated_at < NOW() - make_interval(secs => …)`，用**库的时钟**判定 |
| Java `AgentToolIdempotencyMapper` / `ToolIdempotencyService`（新） | 跨服务去重：`Idempotency-Key` → 表 → 同 key 同工具返回首次结果；`ON CONFLICT DO NOTHING` 单赢家；只记成功（不把瞬时失败变永久）；定时清理 |
| Java `InternalToolController` | 在 switch **之前**读 idempotency，所以没有哪个工具有机会忘记它；重放写审计 `deduplicated` |
| Java `ApprovalService` | 唯一的职责分离例外：`required_approver_roles="requester"` 时允许申请人自决（写确认）。空值仍然禁止自批 |
| 前端 `ApprovalDetailDialog` | 同上放开按钮并说明原因；其余审批路径行为不变 |
| `observability/tool_metrics.py`（新） | `tool.calls`/`tool.latency_ms`/`agent_loop.*`；`/ready` 的 `tool_resilience` 块 |
| 配置 | 重试次数/退避/总预算、熔断阈值与冷却、心跳节奏、节点空闲窗口、租约开关与时长、`write_confirmation_enabled`、`tool_result_max_tokens`、`function_calling_model` |

### 25.2 落地时发现并修掉的缺陷

| # | 缺陷 | 为什么危险 | 修法 |
|---|---|---|---|
| 1 | **`CircuitBreaker.describe()` 死锁**：持有非重入锁后再调 `is_open()`（同一把锁） | 任何一次 `/ready` 或熔断查询都会挂住一个请求线程；表现是"测试卡住不报错" | 状态判断拆成 `_is_open_locked()`，锁改重入 |
| 2 | **重试指标标签名撞参**：`metrics.counter(name=...)` 与 `counter(name, help, **labels)` 的第一个参数同名 | 触发重试的那一刻抛 `TypeError`，把一次可重试的失败变成工具调用失败 | 标签改 `call=`，并在注册表文档里写明 `name` 是保留名 |
| 3 | **租约抢占的 CAS 谓词恒不成立**：`_take_over` 传 `stale_before=record.updated_at`，SQL 里是 `updated_at < to_timestamp(stale_before)` → 自己不小于自己 | 崩溃后的每一次重放都被判成"仍在进行中"，写操作**永久卡住** | 改由库的时钟判定：`updated_at < NOW() - lease`；新增 fake 仓库的 CAS 契约测试 |
| 4 | **规则兜底在预算停之后还会执行**：`mode=="rule-based"` → 调 `select_tools_for_request` | 预算刚拒绝过一次调用，紧接着又执行一个工具（还可能绕过确认档位） | 只有"模型自己说不需要工具"或"从未问过模型"才走兜底；任何停因都不再兜底 |
| 5 | **`total_tokens` 跨轮累加会重复计 prompt** | 第 3 轮的 `total_tokens` 已含前两轮输入，累加会提前触发 token 预算，把正常循环截断 | 只累加 `promptTokens + completionTokens`（两轮之间不重叠） |
| 6 | **关闭确认档位后写操作被静默拒绝**：`write_confirmation_enabled=false` 时不进中断分支，于是没有任何 decision，`_execute_confirmed` 全判 rejected | 开关一关，建单/评论全部"未执行"，而 `/ready` 说 confirm 档位已停用——语义相反 | 关闭时把待确认调用直接标为 approved（"不问"而不是"问了然后拒绝"） |
| 7 | **`confirm` 档位的写全部落在 loop 内**：`ticket_create` 标成 confirm 后，`execute_tools` 仍会执行它 | 确认形同虚设 | 循环只收集待确认写并停止，执行权只给 `confirm_writes` |
| 8 | **两个节点的 `config` 取用会抛 `AttributeError`**：`config.get(...)` 在非 dict 时不成立 | resume / 测试路径下节点直接崩 | 统一 `_emit(config)` 容错取值 |
| 9 | **`verdict` 泄漏到函数作用域外** | 关闭确认档位后 `write_confirmations` 写入错误的 `decision` 值 | 改为从 `decisions` 逐条生成 |

### 25.3 四处决定的取舍与理由

1. **图级确认节点，而不是"拒绝即回灌"**（用户选择）：需要一次真实确认，所以放弃
   了"首次调用返回失败让模型转述"的简化实现。落地后发现这个选择还带来一个额外好处：
   确认与审批**共用一套 interrupt/checkpoint 协议**，因此 Java 侧与前端复用了既有
   审批中心，零新增交互形态。
2. **`requester` 哨兵而不是空角色**：写确认的正确语义是"申请人本人点头"。用空值也能
   通过角色校验，但那与"没人想过这件事"无法区分。显式写 `requester` + 服务端只对
   该值放开自决，让例外在数据里可见、在 UI 里可解释。
3. **`execute_tools` 只加超时、不加 `retry_policy`**（用户选择）：LangGraph 的重试是
   **重跑整个节点**，对这个节点意味着再问一次模型（真实花费）并重发一轮事件。效果
   安全由账本兜住，但重复的模型调用与重复事件不值这个价。`generate_answer` /
   `retrieve_knowledge` 仍有 run timeout。
4. **Java 真去重**（用户选择）：原本 Python 只在"连接未建立"时重写，也就是不会重发
   已送出的请求，所以 Java 去重看起来"用不上"。但 T3 要求键端到端，而且这条表让
   **将来放宽写重试是安全的**——先把地基铺好，比等到需要时再改协议便宜。

### 25.4 验证结果

| 层次 | 结果 |
|---|---|
| Python 单测 | **392 项全绿**（本轮新增 55：重试边界 T1/T2、退避与抖动、取消不重试、熔断开合、租约决策表与两进程抢占、心跳、结果契约、token 估算推进、停因指标、`confirm` 档位的图级暂停/通过/驳回/幂等/RBAC/契约） |
| Java 单测 | **261 项全绿**（新增 24：控制器 idempotency 五例、`ToolIdempotencyService` 十一例、`requester` 自确认五例、保留清理、表不可达时如实标 inactive） |
| 前端 | `tsc --noEmit` 0 错误 |
| 实时校验（真实 PostgreSQL + 两个服务在线） | **19/19 通过 / 0 失败**，详见下表 |
| 数据库 | schema 漂移检查 **PASS**（15 张表逐列比对，含新增的 `agent_tool_idempotency`） |

实时校验逐项结果（`.buildcheck/live_full_chain_check.py`，临时 admin 账号跑完自动停用）：

| 检查 | 实测结果 |
|---|---|
| Java `/health` 上报幂等层 | `toolIdempotency.active=true`，去重名单 = `ticket_comment, ticket_create, ticket_merge` |
| 未签名的内部工具调用 | **401**（新增去重层没有开洞） |
| 同 `Idempotency-Key` 两次到达 Java | 只建 **1 张工单**；第二次 `deduplicated=true` 且**编号与文本与第一次相同**；`agent_tool_idempotency` 只有 **1 行** |
| 真实对话的事件序列 | `thinking → tool_call(ticket_create, chosenBy=model) → tool_result(CONFIRMATION_REQUIRED) → approval_required` |
| 暂停来源 | `rule_id=WRITE-CONFIRMATION-REQUIRED`、`kind=write_confirmation`、`required_approver_roles=requester`、带 `checkpoint_id`+`interrupt_id` |
| 暂停期间写入 | **0 张**（会话没建单） |
| `resume` 带 `approve` | `status=resumed`；执行结果 `success=true`，**真的建出工单 #32**（`source=chat`、`type=incident`、`priority=medium` 由矩阵推导） |
| 效果账本 | `ticket_create#78d28908… status=succeeded attempts=1` |
| 再次 `resume` | **没有第二张工单**（账本按 turn+action 去重） |
| 审计 | `ticket.create success {tool, number=32, origin=agent, priority=medium}`；被去重那次记 `ticket.ticket_create deduplicated {idempotencyKey}` |

**这次实测顺带纠正了两条我自己的说法**（都不影响代码，但影响你对该系统的认知）：

1. `/ready` **不要求内部身份**（未鉴权返回 200）。它只暴露能力清单、不含数据，可以接受，
   但与 `/metrics`（未鉴权 401）的严格程度不一致，值得知道。
2. 你当前运行的 agent-service 是 `checkpoint_backend: memory`（`durable_checkpoints: false`），
   虽然 `approval_store: postgres`。与本次改动无关，但意味着**重启后中断的审批无法 resume**。

### 25.5 仍未做（诚实清单）

- **`heartbeat()` 对 `TimeoutPolicy` 的实际效果未在真实图上验证**：单测确认事件按 10s
  发出、`tick()` 在每轮开始与每轮工具完成后被调用，但"心跳是否真的刷新了节点空闲计时"
  需要一次真实慢工具跑一遍才敢说成立。框架文档也提醒：**同步阻塞代码不会被超时按时
  取消**，而 `retrieval/*` 里仍有同步 httpx 调用——这是设计文档自己点出的叠加问题，
  本轮未解决。
- **确认档位只在 agent-service 侧闭环**：Java 侧把它当普通审批记录（`rule_id` =
  `WRITE-CONFIRMATION-REQUIRED`、`required_approver_roles=requester`），前端按钮已放开，
  但**没有在界面上把"这是写操作确认"与"这是高风险审批"显式区分**（只有文案差异）。
- **一次确认只覆盖一轮的写**：模型在确认后又提出新的写调用时会再次暂停（每次都要点头）。
  没有"本次会话内记住"的选项，也没有批量确认。
- **并发重复投递仍不是严格一次**：idempotency 是"事后记录"而非预留，两个同时到达的
  相同 key 都会执行，第二个的插入失败（`ON CONFLICT DO NOTHING` 返回 0）。这个缺口与
  写重试策略绑定（只在连接未建立时重写），因此有界，但没有消除；严格一次需要预留式
  begin/commit。
- **熔断状态是进程内的**：多副本各自计数、各自冷却；`/ready` 显示的是"这个副本"。
- **租约判定用库的时钟**，因此要求 agent-service 与 PostgreSQL 的时间可信且相对一致
  （同机或 NTP 同步）。没有做时钟漂移检测。
- **循环内的 token 估算粒度是"整段 messages"**：每轮重新估算而不是增量，轮数多时估算
  偏大 → 更早停。方向安全（宁早停不超支），但不精确。
- **`tool_result_max_tokens` 的默认 1500 是拍的**：没有基于"模型实际需要多少工具结果"
  的评测。
- **未做故障注入**：租约与重试的证据来自单测 +（真实 SQL 的）租约 CAS 与两进程并发抢占，
  但**没有真的杀进程或断网**。"取消时不写 failed"这条只有单测覆盖。
- **跨服务去重只验证了"事后重放"**：同 key 串行两次到达只建一张单已验证；**并发**两个相同
  key 仍会都执行（第二个插入失败）。这与写重试策略绑定（只在连接未建立时重写），因此有界，
  未消除。
- **心跳对 `TimeoutPolicy` 的实际效果未验证**：事件按节奏发出、`tick()` 被调用都有单测，
  但"心跳真的刷新了节点空闲计时"需要一次真实慢工具（≥ 25s）跑一遍。本轮所有工具都秒级返回。
- **验证过程在开发库里留下了测试数据**：工单 #29–#32（标题含「DSH 幂等验证」的是幂等验证，
  「打印机坏了」的是真实会话），以及若干 `effect_ledger` / `agent_tool_idempotency` 已清理后
  新增的行。临时账号都已停用（`status=disabled`）。要清掉可以按标题删除或留着当样例。







