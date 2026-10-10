# 上下文引擎修补方案（Context Engine v2）

范围：`agent-service/app/context/**`、`app/graph/nodes/generate.py`、`app/gateway/litellm.py`、
`app/retrieval/pipeline.py`、`app/config.py`，以及 Java 侧 `ConversationContextService` /
`ConversationSummaryService` / `ChatStreamService`。
目标：把"上下文预算"从启发式估算升级为**可验证、可审计、不静默丢信息**的引擎。

状态：**PR1–PR4 已全部落地并验证（见 `platform-gap-analysis.md` §24）**。本文保留为
方案原文；其中**问题清单有若干条已被证伪或过时**，落地后的修正记录在第 0 节，
不要只读第 1 节。

## 0. 落地后的修正（先读这一节）

1. **问题 1 的"8192 预算"是过时前提**：仓库默认模型 `deepseek-flash` 的窗口是
   **1M tokens / 最大输出 384K**（厂商页面口径，抓取日期 2026-06-02）。1M 窗口下
   真实的失效模式不是"输入超窗"，而是"证据块按占位符算 demand，导致全部证据被丢弃"
   与"渲染连接符未计入预算"。tokenizer 精度仍然重要（近似误差要有护栏），但它不是
   当前最危险的那一条。
2. **问题 2 的描述不完整**：`recent_messages` 不只是"不在分配里"——它在代码里
   **根本没有对应的块**（`BLOCK_WEIGHTS` 有 0.24，`builder` 却只从 `context_blocks`
   建块）。修法不是给它算份额，而是让它成为一个真正的竞争者。
3. **第 8 节第 3 点的 `usage` 帧方案已实现**，但消费端必须显式分支：它新增了一个
   `("usage", payload)` 事件类型，`generate.py` 之外的消费者若把所有帧当正文拼，
   会把 `null`/JSON 追加进答案。
4. **第 9 节的增量摘要需要"逐出区间可用"这个前提**，而前提本身要由打包器保证：
   逐出集合必须是**连续前缀**。第一版实现是"贪心填中间"，于是
   `covered_from=m20, covered_until=m1`，摘要永远生成不出来。
5. **第 11 节的"影子模式"只落了开关**，没有保留旧引擎，因此没有可 diff 的第二套结果
   （见 §24.5）。
6. **第 14 节"不做 embedding 语义压缩"的决定维持不变**；但"不做跨会话长期记忆"的
   理由（边界不同）在本轮得到的补充是：摘要的**覆盖区间**已经使"单轮装配"与
   "长期记忆"之间有了明确的接缝，将来做记忆子系统时应复用它而不是另建一套区间语义。

---

## 1. 现状问题清单（方案原文，部分结论已被第 0 节修正）


| # | 问题 | 位置 | 后果 |
| --- | --- | --- | --- |
| 1 | tokenizer 是 `len/3.2` 启发式，按英文字符比例估中文 | `context/builder.py:38-43` | 纯中文低估约 2×，8192 的预算实际可能送出 15k+，直接触发 provider 400 |
| 2 | `recent_messages` 被排除在分配之外，原样发送 | `context/builder.py:261-273` | 权重最高（0.24）的块完全不受预算约束；粘贴长文必炸窗口 |
| 3 | 主生成调用不传 `max_tokens` | `gateway/litellm.py:87-98` | 输出侧不受控，`output_reserve=1024` 只用于减法，输入预算与输出实际不对齐 |
| 4 | 分配是"按优先级顺序减剩余"，无回流 | `context/builder.py:153-200` | 高优先级块用不完的额度不会让给后面的块；利用率低 |
| 5 | 安全块被挤压只是写进 `report["error"]`，没有硬保证 | `context/builder.py:177-178` | 关键块可能低于安全阈值仍继续请求，且调用方无人检查该字段 |
| 6 | 历史只有 20 条硬上限 + 拼接式伪摘要 | `ConversationContextService.java:25`、`ConversationSummaryService.java:56-77` | 第 21 条以前的信息永久消失；摘要不是摘要，且每次覆盖式新增 version |
| 7 | 检索证据整块截断/降级，引用编号在裁剪前生成 | `context/builder.py:84-91`、`retrieval/pipeline.py:65-96` | 模型可能引用已被裁掉的 `[3]`，citation accuracy 有回归风险 |
| 8 | `context_report` 只在 state 里，不落库不回传 | `graph/nodes/generate.py:73-77` | 无法度量上下文质量，也无法据此触发摘要 |

---

## 2. 设计不变量（必须由测试守住）

1. **I1 硬上限**：送往模型的 `system + messages` 总 token ≤ `window − max_output − safety_margin`。
   任何块、任何消息都不得突破。
2. **I2 安全块下限**：`hard` 类块（`system_policy`、`approval_state`、权限/身份摘要）不得低于其
   `min_tokens`；放不下就抛 `BudgetInfeasible` 并显式失败，**不得静默降级**。
3. **I3 最新用户消息优先**：当轮用户消息必须完整保留；若单条即超预算，走 `head_tail` 截断并
   在文本中留下可见的省略标记，绝不静默丢弃。
4. **I4 无静默丢失**：任何被裁剪/丢弃的内容必须在 report 里可计数、可定位（消息 id、证据 id）。
5. **I5 可复现**：同一输入 → 同一分配 → 同一 `context_hash`（保留现机制）。
6. **I6 计量一致**：预算用的计数与实际发给模型的 tokenizer 一致；provider 回报的 `usage`
   必须回填到 report，用于在线校准与告警。

---

## 3. 目标架构与职责划分

```text
Java  ConversationContextService（取材）
  ├─ 画像 / 画像记忆 / 待决审批（结构化，非自由文本）
  ├─ 滚动摘要（上一版，带覆盖区间）
  └─ 候选历史：最近 W 条（W 可配，默认 40），只做上限保护，不做 token 决策
        │  context_envelope (schema_version=2)
        ▼
Python ContextEngine（决策）
  Counter ─► PolicyRegistry ─► BudgetPlanner ─► Packer ─► Renderer ─► Report
                                                                      │
                                        prompt(system + messages) ◄───┘
        │
        ▼  DeepSeek（stream + include_usage + max_tokens）
  Report ◄─ 回填 provider usage ─► metrics / 校准
        └─► SSE `context_report` 事件 ─► Java 落 conversation_summary / 诊断日志
```

**职责归属（关键决策）**

| 关注点 | 归属 | 理由 |
| --- | --- | --- |
| token 计数与预算 | Python | 只有 Python 能拿到 provider 的 `usage` 做校准；Java 无 tokenizer |
| 历史候选窗口 | Java | 只有 Java 能查库；它只负责"取够"，不负责"用多少" |
| kind → policy（min/max/weight/保护级） | Python 单一真源 | 现在两侧各写一份优先级，已出现漂移（Java `task_slots=50`、Python `BLOCK_WEIGHTS` 另一套） |
| 摘要生成 | Python | Java 没有模型客户端 |
| 摘要落库 | Java | 保持业务库单一写入者，避免两侧争 `version` |
| report 消费 | Java + metrics | 落库、告警、驱动摘要 |

---

## 4. 模块与接口

新增（`agent-service/app/context/`）：

```python
# tokenizer.py
class TokenCounter(Protocol):
    name: str
    def count(self, text: str) -> int: ...

class TiktokenCounter(TokenCounter):        # 首选；tiktoken 未安装时自动降级
class WeightedHeuristicCounter(TokenCounter):
    """CJK 按 ~1.7 字/token、拉丁按 ~4 字符/token、数字与标点单列权重。"""

def get_counter(model: str) -> TokenCounter   # 进程级单例 + lru_cache 按文本长度上限缓存
def describe_counter(model: str) -> dict[str, Any]

# policy.py
@dataclass(frozen=True)
class ContextPolicy:
    kind: str
    priority: int
    weight: float
    min_tokens: int
    max_tokens: int
    protection: Literal["hard", "elastic", "droppable"]
    truncation: Literal["none", "tail", "head", "head_tail", "drop"]
    count_by: Literal["tokens", "items"] = "tokens"

CONTEXT_POLICIES: dict[str, ContextPolicy]     # 单一真源
def policy_for(kind: str) -> ContextPolicy     # 未知 kind → 保守默认（elastic, weight=0.05）

# budget.py
@dataclass
class Allocation:
    blocks: list[ContextBlock]      # 已按预算裁剪
    messages: list[dict[str, str]]  # 已按预算装填
    report: ContextReport
    dropped_message_ids: list[str]
    dropped_evidence_ids: list[str]

class BudgetInfeasible(RuntimeError):
    def __init__(self, missing_tokens: int, detail: str): ...

def allocate(
    blocks: list[ContextBlock],
    messages: list[dict[str, Any]],
    *,
    window: int,
    max_output: int,
    safety_margin: int,
    counter: TokenCounter,
) -> Allocation: ...

# render.py
def render_system(agent: dict, blocks: list[ContextBlock], catalog: list[dict]) -> str: ...

# report.py
@dataclass
class ContextReport:
    engine_version: int
    counter: dict[str, Any]
    window: int
    max_output: int
    safety_margin: int
    usable_tokens: int
    used_tokens: int
    utilization: float
    blocks: list[dict[str, Any]]     # kind/budget/actual/truncated/dropped
    messages: dict[str, Any]         # kept/dropped counts, dropped ids, truncated ids
    evidence: dict[str, Any]         # kept/dropped ids, citation_map
    infeasible: bool
    needs_summary: bool
    summary_delta: dict[str, Any] | None
    provider_usage: dict[str, Any] | None   # 事后回填
    estimate_error_ratio: float | None
    def to_dict(self) -> dict[str, Any]: ...
```

`builder.py` 收缩为编排层：组装候选块 → 调 `allocate()` → `render_system()` → 返回
`AssembledContext`（字段保持兼容，`report` 换成 v2 结构）。

---

## 5. 预算算法

替换现有"按优先级顺序减剩余"，改为 **先保下限、再加权注水（water-filling）、余额回流**：

```python
usable = window - max_output - safety_margin

# 0) 候选块去重（按 kind）并计算需求
for b in blocks:
    b.content_tokens = counter.count(b.content)
    b.demand = min(b.content_tokens, policy.max_tokens)

# 1) hard 块下限必须满足，否则显式失败（不变量 I2）
need = sum(policy.min_tokens for b in hard_blocks)
if need + MIN_MESSAGES_RESERVE > usable:
    raise BudgetInfeasible(missing_tokens=need + MIN_MESSAGES_RESERVE - usable, ...)

# 2) 所有块从 min_tokens 起步；messages 作为一个伪块参与竞争
alloc = {b.kind: policy.min_tokens for b in blocks}
alloc["messages"] = min(MIN_MESSAGES_RESERVE, ...)
remaining = usable - sum(alloc.values())

# 3) 加权注水：每轮把 remaining 按权重分给"还没吃满需求"的块，
#    没吃满 grant 的余额自动留在 remaining 进入下一轮
while remaining > 0:
    active = [b for b in blocks if alloc[b.kind] < b.demand]
    if not active:
        break
    total_w = sum(weight(b) for b in active)
    granted_any = False
    for b in active:
        share = remaining * weight(b) / total_w
        grant = min(b.demand - alloc[b.kind], int(share))
        if grant > 0:
            alloc[b.kind] += grant
            remaining -= grant
            granted_any = True
    if not granted_any:
        break

# 4) 按分配额裁剪每块（I4：记录实际值与被裁原因）
# 5) 用剩余的 remaining（若有）回填 messages 与 evidence，从新到旧装填
# 6) 断言 I1：used = sum(actual) + messages_tokens <= usable，否则抛内部错误
```

要点：

- **优先级不再直接决定金额**，只决定权重与保护级；这样高优先级块用不完的额度能回流（修问题 4）。
- `hard` 块**只允许整体保留或整体失败**，不做"截到 30% 也算过"。
- 权重取值沿用现有 `BLOCK_WEIGHTS`（`messages=0.24`、`retrieval_evidence=0.16`、
  `tool_results=0.14`、`conversation_summary=0.12`…），但由 `policy.py` 提供，删掉
  `builder.py` 里那份副本。
- 迭代轮数上界 = 块数量，实现里加防御性上限（如 8 轮）+ 断言，避免异常输入死循环。

---

## 6. 消息与证据的打包

### 6.1 messages 伪块

- 从**最新往旧**装填，逐条计 token；
- 单条超过剩余额度：
  - 若是**当轮用户消息** → `head_tail` 截断，插入 `\n…[已省略 N 字]…\n`（不变量 I3）；
  - 若是历史消息 → 整条丢弃（半句话比没有更糟），id 记入 `dropped_message_ids`；
- 历史消息装不下时，**不是简单丢弃**：把丢弃区间交给摘要（见第 9 节），Java 侧摘要覆盖范围
  由 `dropped_message_ids` 推导，保证"摘要 + 原文"无缝隙、无重叠；
- 保留最近若干轮（如 2 轮）作为 `min` 保护，避免极端情况下模型看不到对话上下文。

### 6.2 retrieval_evidence 按条目分配

- 输入从"已拼好的 `knowledge_context` 字符串"改为**结构化证据列表**
  （`run_retrieval` 已经返回 `result.evidence`，含 `source_id/title/content/score/metadata`）；
- 在 evidence 块自己的预算内按 `score` 从高到低贪心装填，装不下的丢弃；
- **引用编号在裁剪之后重新生成**，并把 `citation_map = {新编号: source_id}` 写进 report
  （修问题 7）。渲染时编号从 1 连续，确保模型引用的 `[n]` 一定存在；
- 保留 `knowledge_context` 字段作为兼容输出（由 kept 证据重新渲染），避免改动下游；
  `eval/metrics.py` 的 citation accuracy 校验以 report 的 `citation_map` 为准。

### 6.3 块内容策略

| kind | protection | truncation | 说明 |
| --- | --- | --- | --- |
| `system_policy` | hard | none | 缺失即失败 |
| `tenant_profile` | hard | tail | 短，固定 |
| `user_profile` | elastic | tail | 画像 + 记忆 top-N |
| `conversation_summary` | elastic | tail | 结构化摘要渲染 |
| `approval_state` | hard | none | 审批结论必须完整 |
| `task_slots` | elastic | drop | 空即不占额度 |
| `retrieval_evidence` | droppable | items | 按条目装填 |
| `tool_results` | droppable | items | 先过滤过期/不可用，再按条目装填 |
| `recent_messages` | elastic | head_tail | 作为 messages 伪块处理 |

---

## 7. 契约变更

`context_envelope` 升到 `schema_version=2`（Python 侧向后兼容 v1：缺字段走默认）：

```jsonc
{
  "schema_version": 2,
  "recent_messages": [ { "id": "...", "role": "user", "content": "...", "timestamp": "..." } ],
  "summary": {                       // 新增：结构化，替代把摘要塞进 block 的字符串
    "version": 3,
    "covered_until_message_id": "msg_123",
    "fields": { "decisions": [], "open_items": [], "facts": [], "preferences": [], "open_approvals": [] }
  },
  "blocks": [ { "id": "system_policy", "kind": "system_policy", "priority": 100,
                "content": "...", "metadata": {} } ],   // policy 仍由 Python 决定，Java 可选覆盖
  "limits": { "recent_messages": 40 }                    // Java 侧取数上限，供 Java 自用/审计
}
```

`context_report` v2（示例，实际字段以 `report.py` 为准）：

```jsonc
{
  "engine_version": 2,
  "counter": { "name": "tiktoken/cl100k_base", "version": "0.7.0" },
  "window": 65536, "max_output": 8192, "safety_margin": 512, "usable_tokens": 56832,
  "used_tokens": 41230, "utilization": 0.726,
  "blocks": [
    { "kind": "system_policy", "budget": 5683, "actual": 142, "truncated": false, "protection": "hard" },
    { "kind": "retrieval_evidence", "budget": 9093, "actual": 8871, "truncated": true,
      "dropped_ids": ["POL-19", "TC-77"], "citation_map": { "1": "POL-12", "2": "POL-31" } }
  ],
  "messages": { "kept": 26, "dropped": 14, "dropped_ids": ["msg_101", "..."], "truncated_ids": [] },
  "evidence": { "kept": 8, "dropped": 2 },
  "infeasible": false,
  "needs_summary": true,
  "summary_delta": { "covered_from_message_id": "msg_101", "covered_until_message_id": "msg_114",
                     "fields": { "facts": ["用户所在部门是网络运维"] } },
  "provider_usage": { "input_tokens": 40960, "output_tokens": 812, "cached_tokens": 0 },
  "estimate_error_ratio": 0.006
}
```

---

## 8. 与模型对接的修补

1. **传 `max_tokens`**：`stream_completion(..., max_tokens=spec.max_output)`，与预算里的
   `max_output` **同源**（同一个 settings/规格表值，不再两处各写一份）。
2. **模型规格表**（`config.py`）：

```python
MODEL_SPECS = {
    "deepseek-chat":     {"context_window": 65536, "max_output": 8192},
    "deepseek-reasoner": {"context_window": 65536, "max_output": 8192},
}
DEFAULT_MODEL_SPEC = {"context_window": 32768, "max_output": 4096}
```

   > 表内数值必须在落地时按厂商当次官方文档核定后再写死；未知模型走 DEFAULT 并记
   > `model_unknown` 事件。结合 `strict_model_allowlist` 决定"拒绝"还是"降级到默认模型"。

3. **暴露出 `usage`**：现在 `usage` 在 `finally` 里被 `_record()` 吃掉，report 拿不到。
   改为生成器在结束前多吐一帧：

```python
yield ("usage", usage_payload)   # 可能是 None
```

   消费端必须显式处理，否则会被当成正文拼进答案（`generate.py` 目前是
   `else: answer_parts.append(chunk)`）：

```python
elif event_type == "usage":
    usage = chunk            # 回填 report，不 emit
```

4. **在线校准**：`estimate_error_ratio = (input_tokens - estimated_input_tokens) / input_tokens`，
   按模型维护 EMA；ratio > 0.25 或 <-0.25 打点告警，用于发现 tokenizer 与真实模型不匹配
   （例如厂商换了 tokenizer）。

---

## 9. 长期上下文：增量摘要

**触发**：`report.needs_summary = (dropped_message_tokens > 阈值 or 会话水位 > 0.85)`。

**生成（Python）**：把"上一版摘要 + 被逐出的消息区间"交给 `LLMStructuredClient.complete_json`，
输出固定 schema：

```json
{ "decisions": [], "open_items": [], "facts": [], "preferences": [], "open_approvals": [] }
```

约束：
- 必须带 `covered_from_message_id` / `covered_until_message_id`，保证与原文区间**不重不漏**；
- 生成失败（超时/JSON 不可解析）→ 回退到现有规则摘要，并标 `summary_quality="fallback"`；
  **摘要失败绝不阻断主流程**；
- 摘要本身受 `conversation_summary` 块的 `max_tokens` 约束，渲染成固定小标题文本。

**传输**：随 `context_report` SSE 事件回传（Java `ChatStreamService.accumulateEvent` 只识别
answer/conversation_title/error，未知类型忽略；前端 `sse-parser` 同样走 if/else 忽略未知类型，
因此**向后兼容**）。

**落库（Java）**：`ConversationSummaryService` 新增 `persistRolling(conversationId, delta)`，
用 `ConversationSummaryMapper.insert` 写 `version+1` 与覆盖区间；替换现有 `roll()` 的拼接实现。
`needs_summary=false` 时不写。

**回滚**：`context_summary_enabled=false` 时退化为"只丢弃不摘要"，但 report 仍会记录丢弃量。

---

## 10. 可观测性

新增指标（沿用现有 `observability/registry.py`，标签闭集）：

| 指标 | 类型 | 标签 |
| --- | --- | --- |
| `context.blocks` | counter | `kind`, `truncated` |
| `context.dropped_messages` | counter | `reason`(budget/expired/…) |
| `context.utilization` | timer/histogram | `model` |
| `context.estimate_error_ratio` | timer | `model` |
| `context.budget_infeasible` | counter | `window` |
| `context.needs_summary` | counter | `model` |

告警建议：`budget_infeasible > 0`、`utilization p95 > 0.95`、`estimate_error_ratio` 绝对值 > 0.25。

日志：保留"安全块被挤压到阈值以下"的 warn，并在 v2 中升级为**抛错**（I2）。
`context_report` 同时写入 Java 侧结构化日志，便于按 turn 复盘。

---

## 11. 兼容、灰度与回滚

- 新增 `context_engine_version`（1=旧，2=新），默认 2，配置一键回退；老实现冻结保留一个迭代后删除。
- **影子模式**：`context_shadow_mode=true` 时新引擎只计算并把 diff 写进 report/metrics，
  实际 prompt 仍用旧结果。观测差异（丢弃量、利用率、citation 变化）后再切换。
- `context_hash` 会变化：任何依赖 hash 的缓存/评测基线需同步刷新；
  `eval/run_eval.py` 门禁必须重跑并记录新基线。
- 现有 `tests/test_context_builder.py` 中 `assert assembled.messages == state["messages"]`
  在预算充足时仍成立；预算不足时的裁剪是**有意的行为变更**，测试需补"预算不足 → 裁到预算内
  且最新用户消息保留"的用例。

---

## 12. 测试矩阵

| 类别 | 用例 |
| --- | --- |
| 不变量 I1 | 种子随机生成块/消息（≥500 轮，不用 hypothesis，保证可复现），断言 `used ≤ usable` |
| 不变量 I2 | 构造 hard 块无法满足预算 → 断言抛 `BudgetInfeasible`，且**不**发起模型调用 |
| 不变量 I3 | 单条 5 万字用户消息 → 断言被截断且模型仍收到首尾片段与省略标记 |
| 不变量 I4 | 断言 report 的 dropped 计数与实际丢弃的 id 集合一致 |
| 不变量 I5 | 同输入两次调用 → `context_hash` 相同 |
| 不变量 I6 | mock 一个 `usage` 帧 → 断言回填与 `estimate_error_ratio` 计算正确 |
| 计量精度 | 中/英/混排/代码样本，与 tiktoken 基准误差 ≤5%；无 tiktoken 时用快照基线 |
| 长文本 | 50k 字粘贴不触发 provider 400（mock provider 校验总 token） |
| 引用一致性 | 证据被裁剪后 `citation_map` 连续且与渲染文本一致；`eval` citation accuracy 不回归 |
| 摘要 | 覆盖区间不重不漏；LLM 失败回退规则摘要且主流程成功 |
| 回归 | 现有 `test_context_builder.py`、`test_context_envelope.py`、`test_root_workflow.py` 全绿 |

---

## 13. 落地顺序

| PR | 内容 | 验收 |
| --- | --- | --- |
| PR1 | `tokenizer.py` + `max_tokens` 透传 + 模型规格表 + usage 帧暴露 + 校准指标 | 计量误差测试通过；线上 `estimate_error_ratio` 可见；行为无变化 |
| PR2 | `policy.py` + `budget.py` + messages 参与预算 + report v2 + 影子模式 | 不变量 I1/I2/I3/I5 测试通过；影子 diff 可观测 |
| PR3 | 证据按条目打包 + 引用重编号 + eval 校验 | citation accuracy 不回归；`dropped_ids` 可审计 |
| PR4 | 增量摘要 + `context_report` 事件 + Java 落库 | 长会话（>40 轮）信息不再丢失；摘要覆盖区间无缝隙 |

每个 PR 独立可回滚；PR2 是唯一改变 prompt 内容的节点，务必先跑影子模式。

---

## 14. 明确不做的部分（含理由）

- **不做 embedding 语义压缩**：确定性裁剪 + 结构化摘要已能覆盖当前 8k–64k 窗口的绝大多数场景，
  语义压缩的额外模型调用与不可解释性不划算。留作窗口超过 200k 后的选项。
- **不引入 LangChain `trim_messages` / 现成 summarization 节点**：可审计的 report 与确定性是
  本方案的硬需求，薄实现（预计 ~400 行）比加深框架耦合更可控。若团队更倾向框架方案，
  可用 `trim_messages` 作为 `TokenCounter` 之外的第二实现做对照。
- **不做跨会话长期记忆**：那是独立的"记忆子系统"（写入路径、去重、遗忘策略），
  与本方案的"单轮上下文装配"边界不同，另开文档。
- **不改 `retrieve_knowledge` 的同步阻塞问题**：本方案会顺带把它变成纯数据入口，
  但"移出事件循环"属于检索性能议题，单独处理。
