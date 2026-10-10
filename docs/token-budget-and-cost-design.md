# Token 预算与成本闭环方案（Cost Governance v1）

状态：待评审
上游依赖：[context-engine-design.md](context-engine-design.md)（提供 `est_input_tokens`、`max_output`、
模型规格表、`usage` 帧）。本文档不重复那部分内容。
范围：`agent-service/app/observability/**`、`app/gateway/litellm.py`、`app/retrieval/llm.py`、
`app/core/budget.py`（新增）、`app/api/routes/**`、`agent-service/schema.sql`，
以及 Java 侧 `ChatStreamService`（补齐归因字段）与前端提示。

---

## 1. 现状：闭环缺在哪一段

| # | 断点 | 证据 | 后果 |
| --- | --- | --- | --- |
| 1 | **实际生效的模型没有单价** | 前端固定发 `deepseek-chat` / `deepseek-reasoner`（`models.ts:9-22`），Python 取 `model or settings.deepseek_model`（`litellm.py:65`），而价目表只有 `deepseek-flash` / `deepseek-v4-pro`（`cost.py:84-101`） | 每一次聊天调用的成本都是 `cost_unavailable`，`llm.cost_usd` 恒为空。**报表为零不是因为没花钱，而是因为没价** |
| 2 | **`DEEPSEEK_MODEL` 对主链路失效** | 前端总会传 `model`，`or` 右侧永不生效 | 运维改 `DEEPSEEK_MODEL` 以为切了模型，实际没切；只有标题生成用的是它（`litellm.py:168`） |
| 3 | **辅助模型调用完全不计费** | `retrieval/llm.py` 的 `complete_json/complete_text` 不引用 `app.observability` | 开了 `retrieval_use_llm` 后，每次检索的 query 理解 + rerank 调用连 token 都看不见 |
| 4 | **没有准入**：先花钱，后记账 | 计量发生在 `finally`（`litellm.py:146-147`），调用前无任何检查 | 配额、限速、熔断都只能是事后统计，拦不住任何一次超支 |
| 5 | **限流按次数不按成本** | chat 60 次/分钟/用户（`RateLimitFilter.java:35-40`） | 一次 60k token 的长上下文和一次 200 token 的寒暄权重相同 |
| 6 | **输出侧名义上有预留，实际上没有** | `output_reserve=1024` 只用于减法，请求体无 `max_tokens`（`litellm.py:87-98`） | 成本与延迟都不可预测，无法给出保守上界（见第 4.1 节，这是准入的前提） |
| 7 | **未上报的 usage 被当成 0 成本** | `usage=None` 只记 `reported="false"`（`tokens.py:136-149`） | 用量漏计；成本与用量对不上账 |
| 8 | **无归因维度** | `model_usage` 之类的表不存在；`UserContext.department` 有字段但 Java 只填 `userId`/`roles`（`ChatStreamService.java:186-189`） | 无法按人/部门/会话出账，也无法定位"谁在烧钱" |
| 9 | **无降级** | 单 provider、单模型、无 fallback | 配额耗尽或 provider 抖动时只有失败一条路 |
| 10 | **计费基期与报表基期冲突** | 价目表只按 off-peak 计价（`cost.py:22-25`），DeepSeek 峰值是 2× | 用 off-peak 做准入会**低估**峰值时段支出，配额会被击穿 |

> 诚实原则是这个模块的既有优点（`cost.py` 明确拒绝为未知模型编造价格）。
> 本方案不削弱它，而是把"报表要诚实"与"预算要有数"拆成两个数字（见第 3 节 D1）。

---

## 2. 设计不变量

1. **C1 先准入后花钱**：任何一次模型调用之前，其**保守上界成本**必须已被成功预留；预留失败则调用不得发起。
2. **C2 预留必结算**：无论成功、异常、超时、任务取消、客户端断开（`abandoned`），预留都必须在
   `finally` 路径结算；进程崩溃造成的悬挂预留由 TTL + 对账任务兜底。
3. **C3 不超发**：同一配额键上的并发预留必须原子化；允许的误差上界只有"并发数 × 单次上界"，
   且该上界必须可配置、可断言。
4. **C4 双数分离**：`reported_cost`（真实，可为 `NULL`）用于报表；`charged_cost`（保守上界，恒有值）
   用于预算。**任何报表都不得把 `charged_cost` 当作实际支出展示。**
5. **C5 无静默漏计**：用量缺失时按 `estimated` / `ceiling` 记账并打标，绝不计 0 且不留痕。
6. **C6 降级必须可见**：任何因预算或成本触发的模型降级，必须通过事件告知用户与调用方，
   且**不得跳过护栏、RBAC 或审批**。
7. **C7 成本准入失败不改变安全语义**：预算耗尽可以拒绝请求或降级，但绝不能因此少做一次
   Guardian 检查、少做一次审批。

---

## 3. 架构分层

```text
L4 治理   budget_policy（配额/周期/动作） · 告警 · 成本熔断 · 对账任务
L3 归因   model_usage（逐调用） ─► model_usage_daily（rollup）─► 报表 / 账单 / 部门视图
L2 执行   max_tokens · 模型档位与降级阶梯 · 前缀缓存友好排序 · 辅助调用开关
L1 准入   BudgetGuard.reserve() ──(拒绝)──► 429/402
L0 计量   UsageRecord：reported / estimated / ceiling 三态 · 价目表 · purpose 标签
```

### D1. 双数模型（本方案的核心决策）

```python
@dataclass(frozen=True)
class UsageRecord:
    turn_id: str
    conversation_id: str
    user_id: str
    department: str | None
    purpose: Literal["chat", "intent", "retrieval_query", "rerank", "title", "judge"]
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int | None
    usage_source: Literal["reported", "estimated", "ceiling", "missing"]
    reported_cost_usd: float | None   # 无法计价时为 None（保持既有诚实语义）
    charged_cost_usd: float           # 保守上界，恒有值；用于准入与结算
    rate_basis: str                   # 计费基期：off-peak / peak
    latency_ms: float
    outcome: Literal["ok", "error", "timeout", "abandoned"]
    occurred_at: float
```

- `reported_cost_usd` 沿用 `PriceTable.estimate()`，未知模型仍是 `None` + `cost_unavailable`。
- `charged_cost_usd` 由 `PriceTable.ceiling(model)` 给出：优先取该模型的**峰值**单价；
  模型无条目时按策略处理（D3）。
- 只有 `charged_cost_usd` 参与配额扣减与结算；报表同时展示两者，并单列 `unpriced_share`
  指标，让"没价的部分"显式可见。

### D3. 未知模型的策略（必须显式配置）

```python
unknown_model_policy: Literal["reject", "charge_at_ceiling", "allow_unmetered"]
```
- `reject`（**生产推荐**）：模型不在 allow-list ∪ 价目表中 → 直接拒绝，错误码 `MODEL_NOT_ALLOWED`。
  这与 `context-engine-design.md` 的 `strict_model_allowlist` 是同一个开关，建议一并打开。
- `charge_at_ceiling`：按全站最高单价计费，`reported_cost_usd=None`、`usage_source="ceiling"`，
  报表单独统计。
- `allow_unmetered`：只记用量不计费，**仅限开发环境**，并在 `/ready` 里标红。

---

## 4. 关键机制

### 4.1 保守上界从哪来（准入的前提）

```text
est_input_tokens  = ContextEngine 的估算值（context-engine-design.md I6）
max_output        = 模型规格表的 max_output（同时作为请求体的 max_tokens）
reserve_cost      = est_input_tokens × in_ceiling + max_output × out_ceiling
```

三个前提缺一不可：**(a)** context v2 提供 `est_input_tokens`；**(b)** 请求体真的传 `max_tokens`；
**(c)** 价目表覆盖实际使用的模型。这正是第 1 节问题 1/4/6 必须一起修的原因。

### 4.2 预留 / 结算（reserve → settle）

```text
reserve(budget_keys, reserve_cost, ttl)
  ├─ Redis Lua 原子检查 N 个窗口（minute / day / month）的剩余额度
  ├─ 全部通过 → 各键 INCRBYFLOAT，写 reservation key（带 TTL），返回 reservation_id
  └─ 任一不足 → 返回 {allowed:false, key, retry_after} → HTTP 402/429 + Retry-After

settle(reservation_id, actual_charged)
  └─ delta = reserved − actual；spent -= delta；DEL reservation
```

实现要点：

- **原子性**：多窗口检查 + 扣减必须在**一个 Lua 脚本**里完成（现有 `rate_limiter` 用的是
  `INCR` + 事后判断，对次数限流够用，对金额不够——`rate_limit.py:93-102` 的 check-then-act
  在并发下会超发，金额场景不可接受）。
- **必须结算的路径**：`finally`、`asyncio.CancelledError`、`GeneratorExit`（`litellm.py:141-145`
  的 abandoned 分支）、异常抛出。建议把 reserve/settle 包成上下文管理器，
  让"忘记结算"在代码层面不可能：
  ```python
  async with budget_guard.charge(purpose="chat", model=m, est=est) as charge:
      async for frame in stream_completion(...):
          ...
  # 退出时无论何种方式，charge.settle() 一定执行
  ```
- **悬挂预留**：`reservation key` 设 TTL（如 10 分钟），另有对账任务扫描超时未结算的预留并按
  `charged_cost` 判定为已消耗（保守）或释放（若已确认调用未发起）。
- **超发误差**：`actual > reserved` 时（估算偏低或 `usage` 突增），照实补扣并打点
  `budget.overshoot`，同时把这次的实际值回灌校准（见 4.4）。
- **Redis 不可用**：`budget_fail_closed`（**默认 true，与 `rate_limit_fail_open` 的语义相反的
  是刻意的**）——次数限流可以放开，钱的闸门不能放开。降级行为必须在 `/ready` 与日志中可见。

### 4.3 两个维度、三档周期

| 维度 | 用途 | 键 |
| --- | --- | --- |
| 成本（USD） | 财务治理、部门预算 | `budget:{period}:{scope}:{id}` |
| Token 数 | 保护 provider TPM/RPM、防单次巨请求 | `budget_tokens:{period}:{scope}:{id}` |

周期 `minute / day / month`，`scope` 层级 `tenant → department → role → user`。
**生效限额 = 最严的那一层**（不是覆盖，是取最小值），避免"部门有额度但用户被单独放宽"这类漏洞。

### 4.4 补账与校准

| `usage` 情况 | `usage_source` | `reported_cost` | `charged_cost` |
| --- | --- | --- | --- |
| provider 上报 | `reported` | 按实际计价 | 实际值（结算时释放差额） |
| 流中断/未上报 | `estimated` | 按本地估算计价并标注 | 上界 |
| 完全无法估算 | `ceiling` | `NULL` | 上界 |
| 无价目条目 | `ceiling` | `NULL` | `ceiling` |

本地估算复用 context v2 的 tokenizer，输出侧用已累计的 chunk 长度估算。
`estimate_error_ratio`（EMA by model）同时服务于上下文预算与成本预估，一个校准喂两处。

### 4.5 降级阶梯

```text
normal
  └─(配额水位 > 80% 或 provider 429/5xx)→ aux_downgrade   标题/重排换 economy 模型
        └─(水位 > 95% 或成本熔断)→ chat_downgrade        主生成换 economy 模型
              └─(配额耗尽)→ deterministic_only           只走检索 + 规则答案
```

- 每次降级 emit 一个 SSE 事件 `degraded {"from":…,"to":…,"reason":…}`，前端显式提示
  （Java `accumulateEvent` 与前端 `sse-parser` 都会忽略未知类型，新增类型安全）。
- 降级**不改变**：护栏层、RBAC、审批、幂等账本、审计。这条要写进测试（不变量 C7）。
- 恢复：水位回落到阈值以下且冷却期结束后自动回到 normal，恢复同样 emit 事件。

### 4.6 前缀缓存友好（ROI 最高的一项）

DeepSeek/OpenAI 的 prefix caching 按**最长公共前缀**命中，且缓存输入单价通常只有未命中的
1/20–1/50（`cost.py:84-101` 的条目就是这个量级）。当前 `system` 是"角色 + Agent 目录 +
按 priority 排序的区块 + 输出规则"整体拼接（`context/builder.py:285-306`），检索证据位于中段，
**它的变化会从中间打断前缀**，导致缓存命中率极低。

方案：给 block 增加 `position: "prefix" | "tail"`，与预算权重**解耦**：

```text
[prefix 区，字节级稳定]  角色定义 · Agent 目录 · system_policy · tenant_profile · 输出规则
[tail 区，允许变化]      user_profile · conversation_summary · approval_state ·
                        task_slots · retrieval_evidence · tool_results
```

约束：

- prefix 区**不得**包含时间戳、随机 id、`context_hash`、会话 id、用户 id 等变动内容；
  用户画像应拆成"稳定部分进 prefix、可变部分进 tail"，或整体放 tail；
- 渲染顺序固定，不随优先级变化；
- 用 `llm.tokens{kind="cached"}` 验证效果，目标：稳定对话中命中率 > 50%；
- 这条与 context v2 的"按 priority 排序"有冲突，**v2 的排序规则必须按 position 分区后再排**，
  这点要回写进 `context-engine-design.md`（见第 8 节联动项）。

### 4.7 辅助调用治理

| purpose | 是否必需 | 治理手段 |
| --- | --- | --- |
| `chat` | 是 | 主预算，降级阶梯 |
| `intent` | 否（规则可兜底） | 压力下关闭 LLM 分类，走规则 |
| `retrieval_query` / `rerank` | 否 | 只对 top-N 重排；结果缓存；压力下关闭 |
| `title` | 否 | 换 economy 模型 + 失败静默（已有） |
| `judge` | 离线 | 只跑 CI/离线，不进线上预算 |

---

## 5. 数据模型

`agent-service/schema.sql` 追加（与现有 `approval_task` 等同库）：

```sql
CREATE TABLE IF NOT EXISTS model_usage (
    id                TEXT PRIMARY KEY,
    turn_id           TEXT,
    conversation_id   TEXT,
    user_id           TEXT,
    department        TEXT,
    purpose           TEXT NOT NULL,
    model             TEXT NOT NULL,
    rate_basis        TEXT,
    input_tokens      BIGINT NOT NULL DEFAULT 0,
    output_tokens     BIGINT NOT NULL DEFAULT 0,
    cached_tokens     BIGINT,
    usage_source      TEXT NOT NULL,          -- reported/estimated/ceiling/missing
    reported_cost_usd NUMERIC(18, 8),         -- NULL = 无法计价（保持诚实）
    charged_cost_usd  NUMERIC(18, 8) NOT NULL,
    latency_ms        INTEGER,
    outcome           TEXT NOT NULL,
    occurred_at       DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_usage_user_time ON model_usage(user_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_model_usage_conv      ON model_usage(conversation_id);
CREATE INDEX IF NOT EXISTS idx_model_usage_purpose   ON model_usage(purpose, occurred_at);
CREATE INDEX IF NOT EXISTS idx_model_usage_unpriced  ON model_usage(occurred_at)
    WHERE reported_cost_usd IS NULL;

CREATE TABLE IF NOT EXISTS budget_policy (
    scope_type  TEXT NOT NULL,     -- tenant/department/role/user
    scope_id    TEXT NOT NULL,
    period      TEXT NOT NULL,     -- minute/day/month
    limit_usd   NUMERIC(18, 6),
    limit_tokens BIGINT,
    on_exceed   TEXT NOT NULL DEFAULT 'reject',   -- reject/degrade
    degrade_to  TEXT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (scope_type, scope_id, period)
);

CREATE TABLE IF NOT EXISTS model_usage_daily (   -- rollup，由定时任务生成
    day DATE NOT NULL,
    user_id TEXT NOT NULL,
    department TEXT,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    calls BIGINT NOT NULL DEFAULT 0,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cached_tokens BIGINT NOT NULL DEFAULT 0,
    reported_cost_usd NUMERIC(18, 8),
    charged_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, model, purpose)
);
```

写入策略：`model_usage` 用异步批量写入（进程内队列 + 定时 flush + 上限），
**写失败不得阻断请求**（沿用 `audit.py:72-77` 的既有模式），但失败计数要打点。

---

## 6. 接口改动清单

| 文件 | 改动 |
| --- | --- |
| `app/observability/cost.py` | 新增 `PriceTable.ceiling(model)`、`max_unit_price()`、按 UTC 判定的 `rate_basis_for(now)`；`estimate()` 语义不变 |
| `app/observability/metering.py`（新） | `UsageRecord` 组装、批量落库、purpose 标签、rollup 任务 |
| `app/core/budget.py`（新） | `BudgetGuard`：策略加载（DB + TTL 缓存）、Lua 预留/结算、上下文管理器、对账任务 |
| `app/gateway/litellm.py` | `stream_completion(..., purpose, max_tokens, on_usage)`；abandoned 路径也走结算；标题调用换 economy 模型 |
| `app/retrieval/llm.py` | **接入 metering**（当前完全不计费）；改 async client，顺带修事件循环阻塞 |
| `app/api/routes/chat.py` | 准入失败映射为明确错误事件（`BUDGET_EXCEEDED` / `MODEL_NOT_ALLOWED`）与 402/429 |
| `app/api/routes/metrics.py` | 暴露成本相关指标（见第 7 节） |
| `app/api/routes/health.py` | `/ready` 增加 `pricing`（已定价模型比例）、`unpriced_models`、`budget_backend`、`budget_fail_closed` |
| `app/config.py` | `unknown_model_policy`、`budget_fail_closed`、`budget_*_default_*`、`cost_breaker_*`、`degrade_ladder_enabled` |
| Java `ChatStreamService` | `user_context` 补 `department`/`email`/`name`（`UserContext` 已有字段，`schemas.py:8-14`）；可选：请求前粗粒度预检 |
| Java `ChatStreamService.accumulateEvent` | 识别 `degraded` / `context_report` 事件用于落库与前端提示 |
| 前端 | 配额/降级提示；402 的文案与重试引导 |

---

## 7. 可观测性

| 指标 | 类型 | 标签 |
| --- | --- | --- |
| `llm.cost_usd`（已有） | counter | `model`, `rate` |
| `llm.cost_unavailable`（已有） | counter | `model`, `reason` |
| `llm.tokens`（已有） | counter | `model`, `kind`（input/output/cached） |
| `cost.charged_usd` | counter | `purpose`, `model` |
| `cost.usage_source` | counter | `source`（reported/estimated/ceiling） |
| `budget.rejections` | counter | `scope`, `period` |
| `budget.overshoot` | counter | `model` |
| `budget.settle_missing` | counter | —（悬挂预留对账命中数） |
| `budget.utilization` | timer | `scope`, `period` |
| `cache.hit_ratio` | timer | `model`（由 `kind=cached` 与 `kind=input` 推导） |
| `degradation.events` | counter | `from`, `to`, `reason` |

告警：`usage_source=ceiling` 占比 > 10%、`budget.rejections` 出现、
`budget.overshoot` > 0、单用户 1 小时 charged 成本 > 日配额 50%、`cache.hit_ratio` 环比下降 > 30%、
`estimate_error_ratio` 绝对值 > 0.25。

---

## 8. 与 context-engine-design.md 的联动项（需回写）

1. `AssembledContext` 增加 `est_input_tokens`（供 4.1 使用）。
2. block 增加 `position`，预算与排序解耦（4.6）。
3. `max_output` 从模型规格表取，同时用于预算减法与请求体 `max_tokens`。
4. `usage` 帧的消费方同时驱动 metering（第 6 节 `on_usage`）。

---

## 9. 分期落地

| PR | 内容 | 验收 |
| --- | --- | --- |
| **PR1 看得见** | `retrieval/llm.py` 接入 metering；purpose 标签；`model_usage` 表 + 批量写；价目表覆盖实际在用的模型；`/ready` 暴露 `pricing` | 一次完整会话后，库里能看到 chat/intent/title 各自的 token 与成本；`cost_unavailable` 不再因"用错模型名"而增长 |
| **PR2 两数分离** | `PriceTable.ceiling` + `rate_basis_for`；`UsageRecord` 双数；`cost.charged_usd` / `cost.usage_source` 指标 | 不变量 C4/C5 测试通过；报表能把"没价的部分"单列 |
| **PR3 准入结算** | `BudgetGuard` + Lua 原子预留 + 上下文管理器 + 悬挂对账；`budget_fail_closed` | C1/C2/C3 测试通过（含 100 并发不超发、异常路径必结算、崩溃对账） |
| **PR4 配额治理** | `budget_policy` 表 + 层级取最小 + 管理接口 + rollup + 告警 | 按人/部门配额生效；超额返回 429/402 且带 `Retry-After` |
| **PR5 降级与省钱** | 降级阶梯 + 成本熔断 + 辅助调用治理 + 前缀缓存分区 | C6/C7 测试通过；缓存命中率提升可量化；配额压力下服务降级而非报错 |

PR1/PR2 无行为破坏，可先合并；PR3 是唯一引入"请求可能被拒"的节点，需配合
`budget_enforce_shadow`（只记录不拒绝）灰度一个迭代。

---

## 10. 测试矩阵

| 类别 | 用例 |
| --- | --- |
| C1 准入 | 预留失败时不发起模型调用（mock provider 断言零调用） |
| C2 结算 | 成功/异常/超时/`CancelledError`/`GeneratorExit` 五条路径均结算，额度无泄漏 |
| C3 并发 | 100 并发请求同一配额键，断言总扣减 ≤ limit + 单次上界 |
| C3 对账 | 人为制造悬挂预留，断言 TTL + 对账任务收敛 |
| C4 双数 | 未上报 usage → `reported=NULL`、`charged>0`；报表渲染不把 charged 当支出 |
| C4 基期 | 峰值时段调用按 peak 计费，报表基期仍稳定 |
| C5 补账 | `estimated`/`ceiling` 三态各有用例；断言不会出现 `charged=0` |
| D3 未知模型 | `reject` / `charge_at_ceiling` / `allow_unmetered` 三种策略各有断言 |
| 归因 | `model_usage` 行的 user/department/purpose/conversation 正确 |
| 降级 | 水位 80%/95%/耗尽三档触发正确阶梯；每次降级都 emit 事件 |
| C6/C7 | 降级后护栏、RBAC、审批、幂等账本路径与 normal 完全一致 |
| 缓存 | 同会话连续两轮：prefix 区字节完全相同（含 evidence 变化时）；命中率指标上升 |
| 降级可用性 | Redis 不可用时按 `budget_fail_closed` 表现（true→拒绝并 503/429；false→放行并打点） |
| 回归 | `test_observability.py`、`test_metrics_endpoint.py`、`test_p0_hardening.py` 全绿 |

---

## 11. 明确不做的部分

- **不做实时精确账单**：准入用上界、结算用实际，两者之间必然有差；追求"分毫不差"只能靠
  事后对账，工程上不划算。差异由 `budget.overshoot` / 对账任务覆盖。
- **不引入 LiteLLM Router 做本方案核心**：多部署负载均衡、rpm/tpm 感知路由、fallback 链
  （见 [LiteLLM routing 文档](https://docs.litellm.ai/docs/routing)）确实成熟，但本项目当前的
  痛点是"没有价格、没有准入、没有归因"，这些 LiteLLM 也不替你解决；等 PR5 需要多部署时
  再评估引入 Router 作为 `stream_completion` 的实现之一。
- **不做跨租户结算与发票**：本项目是单租户（`tenant=default` 硬编码），不预留多租户账务模型。
- **不用成本数据改变安全策略**：护栏与审批不因预算状态降级（不变量 C7）。

---

## 12. 与 [context-engine-design.md](context-engine-design.md) 的关系

两份文档共享同一个 tokenizer、同一份模型规格表、同一条 `usage` 回传链路：

- 上下文方案解决"**送多少**"；
- 本方案解决"**能不能送、花了多少、谁花的、超了怎么办**"。

必须**先落地 context PR1**（tokenizer + `max_tokens` + `usage` 帧），否则本方案的"保守上界"
没有可用输入。
