# 工具执行与异常兜底方案（Tool Execution & Fallback v1）

状态：**PR1–PR6 已全部落地（见 `platform-gap-analysis.md` §25）**。本文保留为方案原文；
落地时对第 4.2 节与第 4.6 节各做了一处取舍，并在第 0 节记录，别只读第 4 节。

范围：`agent-service/app/tools/**`、`app/graph/nodes/tools.py`、`app/core/effect_ledger.py`、
`app/config.py`，以及 Java 侧 `InternalToolController` / `ApprovalService` 与前端审批中心。
基线下沿：代码基线为 2026-10-05 16:00–18:06 的实现（`tools/calling.py`、`tools/catalog.py`、
`graph/nodes/tools.py`、`tools/adapters/*` 已重写；`context/*` 已落地）。
关联文档：[context-engine-design.md](context-engine-design.md)（token 计量与预算）、
[token-budget-and-cost-design.md](token-budget-and-cost-design.md)（成本准入与归因）。

## 0. 落地后的修正（先读这一节）

1. **§4.2 的节点级 `retry_policy` 只用在模型节点上。**`execute_tools` 只加
   `TimeoutPolicy`：LangGraph 的重试是**重跑整个节点**，对这个节点意味着再向模型要一次
   工具决策（真实花费）并重发一轮事件；写操作本身已由 effect ledger 与指纹去重兜住，
   但重复的模型调用与重复事件不值这个价。`generate_answer` 与 `retrieve_knowledge` 有
   run timeout。
2. **§4.6 的方案 1（图级确认节点）已实现，但不引入新的前端交互**：确认与审批共用
   interrupt/checkpoint 协议，因此 Java 侧把确认也落成 `approval_task` 一行，前端复用
   既有审批中心。为此 `required_approver_roles` 使用显式哨兵 `requester`：写确认的正确
   语义是"申请人本人点头"，而空值无法与"没人想过这件事"区分，服务端只对该值放开自批。
3. **§4.3 的租约由库的时钟判定**（`updated_at < NOW() - lease`），不是由调用方传入的
   时间戳：两副本有两个时钟，而行是另一个副本写的。第一版实现传了"读到的 updated_at"，
   于是判定变成 `updated_at < 自己`，**每一次崩溃重放都被判成仍在进行中**——写操作会
   永久卡住。这个缺陷是租约测试抓出来的。
4. **§1 的基线里"写操作不再经人工审批"这一句在本轮之后不再成立**：`ticket_create` /
   `ticket_comment` 现在是 `confirm` 档位，默认需要一次用户确认。
5. **§4.1 的重试分级已实现，但只读工具的重试发生在适配器层**：`TicketToolAdapter`
   按目录里的 `writes` 选择 `RetryPolicy`，因此"写只在连接未建立时重试"是数据驱动的，
   而不是在调用点靠人记得。
6. **§6 测试矩阵现状**：T1/T2/T3/T4/T6/T7/T8 已全部覆盖，并通过单测 + 真实 PostgreSQL；
   T3 与 T7 另有**真实服务在线**的端到端实测（同 key 两次只建一张单；真实对话暂停在
   `WRITE-CONFIRMATION-REQUIRED`、确认后建出唯一一张工单、再次 resume 不重复）。
   **T5 的"25s 工具不触发流判死"仍未实测**——需要一次真实慢工具的端到端运行，见 §25.5。

---

## 1. 现状基线（已经做到的，不在本方案范围内）

工具层上一个版本的问题——规则选工具、桩工具满天飞、权限/参数/工具清单四处漂移、
工具结果无人复核——已经解决。当前实现的事实：

| 能力 | 位置 |
| --- | --- |
| 工具目录单一真源（权限 / JSON Schema / 参数白名单 / 是否需审批 / 是否写） | `catalog.py:27-117` |
| 桩工具全部删除，未实现的工具**拒绝**而非假装成功 | `adapters/base.py:20-33`、`catalog.py:15-18` |
| function calling（模型选工具），流式决策可见 | `tools/calling.py:124-228` |
| 有界 agentic loop：轮次 / 调用数 / token 三个上限 | `graph/nodes/tools.py:288-345`、`config.py:92-99` |
| 每轮闸门：审批工具排除 → agent 白名单 → RBAC → 参数护栏（入/出） | `graph/nodes/tools.py:149-191, 244-248` |
| 精确重复调用指纹拒绝 | `graph/nodes/tools.py:76-78, 171-183` |
| 写操作走 effect ledger（key = turn + tool + 参数摘要） | `graph/nodes/tools.py:193-212` |
| 只读调用并发、写调用按模型给定顺序 | `graph/nodes/tools.py:265-281` |
| 工具抛异常 = 一次失败的调用，不是失败的回合 | `graph/nodes/tools.py:236-242` |
| 无模型时退化为规则选择，且 `/ready` 如实报告 `fellBack` | `tools/calling.py:70-101` |

所以本方案只处理**仍然缺失的工程韧性**，不再讨论工具抽象本身。

---

## 2. 剩余缺口清单

| # | 缺口 | 证据 | 后果 |
| --- | --- | --- | --- |
| 1 | **工具调用零重试、零退避、零熔断** | `adapters/ticket.py:156` 单次 `httpx.AsyncClient(timeout=30)`，无 retry（全文件 grep 无 retry/backoff） | Java 抖动一次，用户就看到"工单服务暂时不可用"；Java→Python 方向有熔断（`AgentGatewayClient`），反向不对称 |
| 2 | **幂等键没有跨服务传递** | `ticket.py` 不发送 `Idempotency-Key`；只有未启用的 `OpenApiToolAdapter` 发送（`adapters/base.py:55`）；Java `InternalToolController` 也不校验 | ledger 的 provider 幂等键止步于 Python 进程边界，跨服务重放没有去重依据 |
| 3 | **effect ledger 的 `started` 残留会重放** | `core/effect_ledger.py:101` 只在 `status == "succeeded"` 短路；`started` 在 `:110` 写入、`:144` 才转为终态 | 进程在 `begin_effect` 与 `finish_effect` 之间崩溃 → 重放再执行一次写。**现在写操作不再经人工审批**（`ticket_create`/`ticket_comment` 是 `writes` 且非 `approval_gated`，`catalog.py:44-100`），重复写 = 重复建单 / 重复公开评论 |
| 4 | **模型调用（工具选择回合）无重试** | `calling.py:290-294`、`:361-365`：遇 ≥400 直接抛 `FunctionCallingUnavailable` → 循环 `break`（`tools.py:293-295`） | 一次瞬时 429/5xx 就把本轮降级为规则式选工具，而不是重试一次 |
| 5 | **工具选择回合固定用 `settings.deepseek_model`** | `calling.py:135`、`:175` 写死 `payload["model"] = settings.deepseek_model` | 忽略前端选择的模型（`state["model"]` 在 `generate.py` 里是被使用的），模型选择在同一回合内分叉，成本与行为不可预期 |
| 6 | **token 预算在 provider 未上报 usage 时失效** | `tools.py:300-301` `tokens += turn.usage.get("totalTokens", 0)`；`calling.py:314-315` 缺字段时返回 `None` | 未上报 → `tokens` 恒为 0 → `agent_token_budget`（`config.py:96`）形同不存在，循环只受轮次约束 |
| 7 | **时间预算分层不一致，且工具执行期无事件** | `tool_timeout_seconds=30`（`config.py:37`）== `stream_idle_timeout_seconds=30`（`config.py:73`）；`tool_progress` 全仓无人发送（已 grep 确认，前端 `chat.service.ts` 却在解析它） | 一个耗时 25–30s 的合法工具会让 Python 侧"30s 无事件"判死并 error 收尾；Java 侧 idle 是 120s，真正的裁判是 Python 的 30s，余量为零 |
| 8 | **循环内的上下文不受 ContextEngine 约束** | `tools.py:283` 用 `calling.build_messages` 自建消息表；`tool_result_message`（`calling.py:322-341`）不过预算 | 工具结果原样进 messages 并跨轮重复发送；仅靠护栏 20k 字符上限间接收敛，`context/*` 的预算引擎管不到这里 |
| 9 | **工具结果无 schema 校验** | `ticket.py` 只校验 `isinstance(body, dict)` 并 `setdefault("success", False)` | `ticket_create` 返回 success 但缺 `data.number` 时，模型可能凭上下文编造工单号（项目一直坚持的"不编造"在这里有缺口） |
| 10 | **图节点没有任何韧性策略** | `root.py` / `tools.py` / `service_workflow.py` grep `retry_policy`/`timeout`/`error_handler`/`heartbeat` 全部无命中 | 框架已支持节点级重试、超时、错误处理器（见 §4.2），却一个都没用；唯一保护是循环上限 + 外层 idle |
| 11 | **写操作策略是隐式的** | `ticket_create` / `ticket_comment` 是 `writes=True` 且非 `approval_gated` | 模型可以自主建单、以用户名义发**公开**评论，没有任何确认环节；这是"当前允许"而非"明确决定"，需要写成策略 |
| 12 | **循环的停止原因与 token 消耗不可观测** | `tools.py:365-369` 返回 `tool_accounting`，只进 state | 运维看不到"为什么停"（轮次/调用数/token/写成功），也无法按工具统计耗时与失败率 |

---

## 3. 设计不变量

1. **T1 只在确定无副作用时重试**：连接层失败（连接未建立、连接被拒）可重试；
   已送出请求后的读超时、5xx、响应不可解析，**对写操作一律不重试**，交由 effect ledger 判定。
2. **T2 重试有总预算**：任何一次工具/模型调用的重试次数、总时长都有上限，且计入同一轮的
   wall-clock 预算，不允许"每个调用各自重试 3 次"叠加成不可控耗时。
3. **T3 幂等键必须端到端**：写操作从 ledger 生成 `idempotency_key` → 透传到 Java →
   Java 侧校验并去重；任一侧缺失则该能力在 `/ready` 中显式标注为未生效。
4. **T4 崩溃不重复写**：`started` 状态必须有租约语义（见 §4.3）；重放时要么短路，
   要么明确接管，不允许"默默再执行一次"。
5. **T5 时间预算分层单调**：`单次工具超时 < 节点空闲窗口 < 流空闲窗口`，且每个超时都有事件
   或心跳可以证明"还在推进"。
6. **T6 循环内的上下文与成本同样受约束**：工具结果的体积、轮次累积 token 都走与
   ContextEngine 相同的计量器；usage 未上报时用本地估算推进预算，绝不计 0。
7. **T7 写操作必须有明确的授权档位**：每个写工具显式声明 `direct` / `confirm` / `approval`，
   不允许"因为没被标成 approval_gated 所以就自动直通"。
8. **T8 失败必须可归因**：每次工具调用的结果码、耗时、重试次数、重试原因都进事件与指标。

---

## 4. 机制方案

### 4.1 重试分级（T1/T2）

| 调用 | 幂等性 | 可重试错误 | 策略 |
| --- | --- | --- | --- |
| 模型（工具选择回合） | 只读语义，幂等 | 429 / 5xx / 连接错误 / 超时 | 退避重试 2 次（200ms→600ms+抖动），仍失败才降级规则式 |
| 模型（最终生成） | 幂等 | 同上 | 流开始前可重试 1 次；**流已开始则绝不重试**（避免重复输出） |
| 工具 · 只读（`ticket_get`/`ticket_list`） | 幂等 | 429 / 5xx / 超时 / 连接 | 重试 2 次，退避 |
| 工具 · 写（`ticket_create`/`ticket_comment`） | 需要幂等键 | **仅**连接未建立的错误 | 重试 1 次；其余不重试，return 失败结果交由模型决定下一步 |
| 节点级（LangGraph） | — | 白名单异常 | 见 §4.2 |

实现落点：

- 新增 `app/tools/resilience.py`：`retry_async(call, policy, on_retry)`，统一退避、抖动、
  总时长上限、以及 `is_safe_to_retry(exc, writes: bool)` 判定（与 Java
  `AgentCircuitBreaker.isSafeToRetry` 保持同一套语义并写进注释互相引用）。
- 轻量熔断：按 `tool_name` 维护连续失败计数，超阈值进入冷却（复用
  `redis_streams.py:64-79` 的降级模式：短超时 + 失败计数 + 冷却窗口 + 只记一条告警），
  冷却期内直接返回 `TOOL_PROVIDER_UNAVAILABLE`，不再打网络。
- 重试次数与耗时写入结果（`attempts`、`retryReasons`），进入 §4.7 的事件与指标。

### 4.2 时间预算与心跳（T5）

现状是四个各自为政的超时（Java connect 3s / Java idle 120s / Python 模型 60s /
Python 工具 30s / Python 流空闲 30s），且工具执行期没有任何事件。目标关系：

```text
tool_timeout(30s)  <  node_idle_timeout(60s)  <  stream_idle_timeout(120s)
                                   ↑
                          心跳事件按 10s 节奏重置所有空闲计时
```

落地：

1. **心跳**：工具执行前后与执行中每 10s emit 一个事件。前端已经能解析 `tool_progress`
   （`front-service/features/chat/services/chat.service.ts` 的 `tool_progress` 分支）
   而服务端从未发送——直接复用，前端零改动。长耗时且无细粒度进度的工具用
   `{"type":"tool_progress","toolCallId":...,"progress":null,"note":"still running"}`。
2. **`stream_idle_timeout_seconds` 提到 120s** 与 Java 对齐；判死前先在流里发一条
   带 `reason` 的 `error`，让用户看到的是"超时"而不是"连接断了"。
3. **节点级策略**（框架已支持，零自研）：`StateGraph.add_node(..., retry_policy=RetryPolicy(...), timeout=TimeoutPolicy(run_timeout=..., idle_timeout=...))`，
   `TimeoutPolicy.idle_timeout` 配合 `runtime.heartbeat()` 刷新。落点：
   - `execute_tools`：`run_timeout = max_rounds × (tool_timeout + llm_timeout)` + 余量，
     `idle_timeout = 60s`，并在每轮开始时 `runtime.heartbeat()`；
   - `generate_answer`：`run_timeout = llm_timeout + 10s`；
   - `retrieve_knowledge`：`run_timeout` 覆盖检索预算（同时是修"同步检索阻塞事件循环"的护栏）。
   > 注意框架文档的提醒：超时依赖 asyncio 取消，**同步阻塞代码不会按时触发**——这与
   > `retrieval/*` 里同步 httpx 调用的问题叠加，`context`/检索的同步化要一并处理。
4. **取消传播**：确认 `asyncio.CancelledError` 不被 `except Exception` 吞掉。
   现有 `tools.py:236-242` 用的是 `except Exception`（正确，`CancelledError` 继承
   `BaseException`），但 `run_round` 的 `asyncio.gather` 需要保持"任一取消即整体取消"的
   默认语义——补一条测试断言，避免将来有人加 `return_exceptions=True` 破坏它。

### 4.3 effect ledger 的租约语义（T4）

现状状态机只有 `started → succeeded | failed`，且只有 `succeeded` 短路。补全为：

```text
run_once(effect_key):
  row = lookup(effect_key)
  ├─ 无            → insert(started, attempt=1, lease_until=now+lease) → 执行
  ├─ succeeded     → 短路返回 {deduplicated: true}（现状，保留）
  ├─ failed        → insert/update(started, attempt+1, 新 lease) → 执行（失败可重试）
  └─ started
       ├─ lease 未过期 → 返回 {success:false, error:"EFFECT_IN_PROGRESS"}（冲突，不重放）
       └─ lease 已过期 → CAS 抢占（attempt+1、新 lease 成功者才执行）→ 执行
```

- 表已有 `attempts` / `updated_at`（`schema.sql` 的 `effect_ledger`），**租约可直接由
  `updated_at + lease_seconds` 推导，无需迁移**；若要显式租约再加 `lease_until` 列。
- 抢占用 `UPDATE ... WHERE effect_key=%s AND status='started' AND updated_at < %s`
  的 CAS，返回行数决定谁赢——与项目已有的 compare-and-swap 风格一致
  （`approval_store.decide`）。
- 租约时长取 `max(单次工具超时 × 2, 60s)`，避免正常的长调用被误判为悬挂。
- `EFFECT_IN_PROGRESS` 对模型是一个**可理解的失败**（"上一次相同操作仍在进行中"），
  不是异常。

### 4.4 循环内的上下文与成本（T6）

- **计量复用**：`run_tool_loop` 用 `context/tokenizer` 对每轮 `messages` 估算 token，
  与 provider `usage` 交叉校验；`usage` 缺失时用估算值推进 `agent_token_budget`
  （修缺口 6），并在事件里标注 `usageSource: "estimated"`。
- **单条工具结果上限**：在 `calling.tool_result_message` 处按 token 截断 `text`
  （建议 1500 token，超出保留首尾 + `…[truncated]`），`data` 只保留白名单字段。
  这样"3 轮 × 20k 字符"不会悄悄吃满窗口。
- **成本归因**：循环的每次模型调用按 `purpose="tool_loop"` 记入
  [token-budget-and-cost-design.md](token-budget-and-cost-design.md) 的 `model_usage`；
  循环的 token 消耗同时计入该轮的预算预留（否则成本准入会漏掉循环这部分）。
- **模型一致性**（修缺口 5）：`calling.converse*` 接受 `model` 参数，由
  `run_tool_loop` 传 `state["model"]`，缺省时回落 `settings.deepseek_model`；
  与 `generate_answer` 用同一个解析函数，只保留一处真源。

### 4.5 工具结果契约（T8）

- `ToolSpec` 增加 `result_required: tuple[str, ...]`（点路径，如 `("number",)` 或
  `("data.number",)`）；
- 适配器返回后由 `invoke` 统一校验：缺失 → 结果改写为
  `{success: false, error: "RESULT_SCHEMA_VIOLATION", text: "..."}`，
  并在事件中标注，避免模型拿一个"成功但无编号"的结果去编造；
- `data` 走白名单投影 + 尺寸上限，防止第三方系统把任意结构灌进模型上下文。

### 4.6 写操作的授权档位（T7）

给 `ToolSpec` 增加 `write_policy: Literal["direct","confirm","approval"]`：

| 工具 | 建议档位 | 理由 |
| --- | --- | --- |
| `ticket_list` / `ticket_get` | —（只读） | |
| `ticket_create` | `confirm` | 建单会通知他人、占用队列，值得一次显式确认 |
| `ticket_comment` | `confirm` | 以用户名义对外发言，不可撤回 |
| `ticket_merge` | `approval`（现状） | 不可逆 |

**`confirm` 的实现方式很关键**：不要在 `execute_tools` 循环里直接 `interrupt()`——
LangGraph 恢复时会**重跑整个节点**，循环里的模型调用会重复（成本）且消息序列需要重建。
正确做法是两者之一：

1. **图级确认节点**：`execute_tools` 只收集"待确认的写调用"放进 state，新增
   `confirm_writes` 节点 → `interrupt()` → 用户确认 → 再执行（与现有
   `prepare_approval`/`approval_gate` 同构，复用同一套 checkpoint 引用与恢复协议）；
2. **拒绝即回灌**：不引入交互，改为"首次写调用先返回一个需要用户确认的失败结果，
   由模型在下一轮转述"，实现简单但没有真正的确认语义。

推荐方案 1：它复用项目已经打磨过的中断/恢复/checkpoint 协议，且**副作用仍在 interrupt 之前
完成**（写 pending 到 state，而不是写外部系统）。

### 4.7 可观测（T8）

| 指标 | 类型 | 标签 |
| --- | --- | --- |
| `tool.calls` | counter | `tool`, `outcome`（ok/refused/failed/denied） |
| `tool.latency_ms` | timer | `tool`, `outcome` |
| `tool.retries` | counter | `tool`, `reason` |
| `tool.circuit_open` | counter | `tool` |
| `agent_loop.rounds` | timer | `stop_reason` |
| `agent_loop.tokens` | counter | `usage_source`（reported/estimated） |
| `agent_loop.stop_reason` | counter | `reason`（ROUNDS/TOOL_CALL_BUDGET/TOKEN_BUDGET/WRITE_SUCCEEDED/NO_MODEL） |
| `effect_ledger.lease_conflicts` | counter | — |

事件侧：`tool_call` / `tool_result` 已有 `round` 字段，补充 `attempts`、`latencyMs`、
`errorCode`；停止原因额外 emit 一条 `thinking`（现状只对 token/调用数发了文案，轮次耗尽与
写成功停止没有对外说明）。

---

## 5. 分期落地

| PR | 内容 | 验收 |
| --- | --- | --- |
| **PR1 可观测与心跳** | 心跳/`tool_progress` 事件；超时分层对齐（idle→120s）；指标与停止原因 | 慢工具（25s）不再把流判死；`/metrics` 能看到工具失败率与停止原因分布 |
| **PR2 重试与熔断** | `tools/resilience.py`；模型回合与只读工具重试；按工具熔断 | 一次 429 不再降级为规则式；写操作只在连接未建立时重试（T1 测试） |
| **PR3 幂等打通** | ledger `provider_key` 透传到 Java；Java 侧校验与去重；`/ready` 标注 | 跨服务重放同一写操作只产生一个工单 |
| **PR4 租约与崩溃安全** | 状态机补 `started` 租约 + CAS 抢占 + 对账 | 模拟 `begin` 后崩溃，重放不产生第二次写（T4 测试） |
| **PR5 循环上下文与成本** | tokenizer 估算推进预算；工具结果按 token 截断；`purpose=tool_loop` 归因；模型选择统一 | usage 未上报时预算仍生效；循环成本出现在成本报表 |
| **PR6 写操作档位** | `write_policy` + 图级确认节点（复用 interrupt 协议） | `ticket_create` 需一次确认；`ticket_merge` 仍走审批；两条路径都不在循环内 interrupt |

PR1/PR2 无协议变更，可先合；PR3/PR4 影响跨服务契约，PR6 影响交互，需要单独灰度。

---

## 6. 测试矩阵（含故障注入）

| 类别 | 用例 |
| --- | --- |
| T1 重试边界 | 写操作遇到 `ReadTimeout` **不重试**；遇到 `ConnectError` 重试一次；只读两种都重试 |
| T2 重试预算 | 连续 429 时总耗时不超过预算，且重试次数有上限 |
| T3 幂等 | 同一 `idempotency_key` 两次到达 Java，只产生一个工单；Java 侧表有记录 |
| T4 崩溃重放 | 注入 `begin_effect` 后崩溃：租约未过期 → `EFFECT_IN_PROGRESS`；过期 → 抢占成功且只执行一次 |
| T4 并发抢占 | 两个协程同时抢占过期租约，只有一个执行 |
| T5 时间分层 | 25s 工具不触发流判死；心跳按 10s 到达；节点 `run_timeout` 按预期触发 |
| T5 取消传播 | 取消流 → 工具调用被取消 → 无残留任务（不 `return_exceptions`） |
| T6 预算兜底 | provider 不返回 usage 时，估算值推进预算并在事件里标 `estimated` |
| T6 结果截断 | 20k 字符工具结果被截断进模型，且原文仍在事件里可查 |
| T7 写档位 | `confirm` 工具在确认前不产生外部调用；拒绝后不留 pending 副作用 |
| T8 结果契约 | `ticket_create` 返回 success 但缺编号 → `RESULT_SCHEMA_VIOLATION`，模型不能编造 |
| 回归 | `test_root_workflow.py`、`test_ticket_tools.py`、`test_function_calling.py`、`test_loop_improvements.py`、`test_write_actions.py` 全绿 |

---

## 7. 明确不做的部分

- **不引入 Temporal 做循环持久化**：`persistence/temporal.py` 目前是 20 行的占位；
  本方案用"节点级 checkpoint + effect ledger 幂等"已经能给出一致性保证，
  引入工作流引擎的复杂度与运维成本不成比例。等出现跨小时的人工等待链路再评估。
- **不做 MCP**：工具目录现在是单一真源且与 RBAC/Schema/护栏绑定，接入 MCP 需要先解决
  "外部工具声明的权限如何映射到本地 RBAC"，这本身值得独立设计。
- **不做多 provider 路由**：属于成本方案 PR5 的降级阶梯范畴，本方案只保证单 provider 下的
  重试与熔断正确。
- **不做循环内的细粒度 checkpoint**：把每一轮做成 checkpoint 边界能减少崩溃重放的模型调用，
  但会放大 checkpoint 写入量；当 `agent_max_rounds` 默认从 3 提到 6 以上时再评估。
- **不改前端**：心跳复用前端已解析的 `tool_progress`，`confirm` 复用既有 `approval_required`
  交互形态。

---

## 8. 与另两份文档的接口

| 交叉点 | 方向 |
| --- | --- |
| tokenizer 与 token 预算 | 本方案复用 `context/tokenizer`；`usage` 缺失时的估算策略与
[context-engine-design.md](context-engine-design.md) 的 I6 一致 |
| 成本归因与准入 | 循环的模型调用以 `purpose="tool_loop"` 进入
[token-budget-and-cost-design.md](token-budget-and-cost-design.md) 的计量与预留 |
| `usage` 帧 | 本方案依赖成本方案 PR1 的计量链路；循环侧不另建一套 |
| 写操作确认与审批 | `confirm`（新增）与 `approval`（既有）共用同一套 interrupt/checkpoint 协议 |
