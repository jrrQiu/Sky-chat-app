# RAG 模块评审：现状、与优秀开源项目的差距、改进路线

**评审对象**：`agent-service/app/retrieval/`、`agent-service/app/knowledge/`、`agent-service/eval/`、`agent-service/schema.sql`
**评审方式**：完整阅读源码 + **对运行中的 PostgreSQL 实测** + 脚本级缺陷复现 + 与 GitHub 主流 RAG 项目对照

---

## 0. 结论摘要

**这套模块的工程质量高于它的检索能力。**

诚实标注（`lexical_only`、groundedness=`None`、哈希向量 opt-in）、权限过滤下推 SQL、检索内容护栏、
引用真实性校验——这几项做得比多数开源项目更好。但**检索本身仍是一个玩具语料实现**。

对运行中的数据库实测（`localhost:5433/skychat`）：

```
knowledge_document = 7 篇      knowledge_chunk = 26 个
knowledge_chunk 中 embedding 非空的行数 = 0 / 26
knowledge_chunk 上的 ANN 索引数量 = 0（只有 btree 主键与 document_id 索引）
retrieval_run = 0 行
```

即：**向量列建好了、pgvector 装好了、`pgvector>=0.3.0` 写进依赖了、向量索引一个都没有、一行向量都没写。**

当前实际检索形态：`BM25 + 稀疏余弦 + 精确匹配` 三路词法，在 **26 个 chunk 上全量线性扫描**。

一句话：**基础设施在位的部分是好的，缺的是"真正的检索"。**

---

## 1. 已确认缺陷（按严重度）

### P0-1 向量列是"装饰性 schema"——建了从不写入

`schema.sql:158,173`：

```sql
knowledge_document.embedding vector(1536)
knowledge_chunk.embedding    vector(1536)
```

实测证据：

| 断言 | 实测结果 |
|---|---|
| 列存在 | ✅ `knowledge_chunk.embedding` 类型 `vector` |
| 有数据 | ❌ **26 行全部为 NULL** |
| 有 ANN 索引 | ❌ 只有 `knowledge_chunk_pkey`(btree) 与 `idx_knowledge_chunk_document`(btree) |
| 代码写入 | ❌ `knowledge_repository.seed()` 的 INSERT 不含 embedding 列（`knowledge_repository.py:125-152`） |
| 代码读取 | ❌ `load_index()` 不读 embedding；检索走内存 Python 对象 |

**这是最危险的一类缺陷**：schema 里存在却从不写入的列，会让 code review 与架构评审都误判能力已具备。
`requirements-optional.txt:3` 已声明 `pgvector>=0.3.0`——说明**原本就是这么设计的，只是没写完**。

旁证：`retrieval_run` 表的 `latency_ms` 硬编码为 `0`、`token_usage` 硬编码为 `'{}'`
（`knowledge_repository.py:398-401`），`subqueries` / `rerank_scores` / `evidence_ids` 三列从不写入。
`java-service/schema.sql:155` 还有一个带 `embedding` 的 `memory` 表，DB 中 0 行，Python 侧零引用。

> 与 `docs/platform-gap-analysis.md` §15.1 的"残留"自述一致，本文用实测数据坐实。

### P0-2 稠密通道是 O(n) 次同步 HTTP，且阻塞事件循环

`hybrid_search.dense_rank()`（`hybrid_search.py:99-106`）：

```python
for chunk in chunks:                                    # 26 次串行
    chunk_embedding = embedding_provider.embed(chunk.content)
```

实测（`CountingProvider` 计数）：**26 个 chunk → 一次查询 27 次 `embed()`**（1 次 query + 每 chunk 1 次）。
DB 实测确认"一次稠密查询要现算的 chunk 数 = 26"，**且每次查询都重来一遍**。

三个问题叠加：

1. **无 embedding 缓存**：每次查询重算全语料向量。26 尚可，1000 chunk ≈ 1000 次串行 HTTP。
2. **同步 HTTP 进入 async 路径**：`HttpEmbeddingProvider.embed()` 用同步 `httpx.post`（`embedding.py:105`），
   而调用链 `retrieve_knowledge`（`app/graph/nodes/knowledge.py:11`）是 **async def**。
   在协程里发同步 HTTP 会**阻塞整个事件循环**——并发请求互相拖死，这不是"慢"，是**可用性缺陷**。
3. **批量接口是死代码**：`embed_many()`（`embedding.py:174`）**无任何调用点**。
   主流 embedding API 都支持 `input: [str,...]` 批量（OpenAI 上限 2048 条），本可 1 次请求替代 26 次。

### P0-3 检索引擎对 chunk 精度做了"生成—丢弃"

这一条是实测才发现的，**比前两条更值得修**。

实测 `run_retrieval('VPN 外网访问财务系统需要哪些审批和条件？')`：

```
evidence items returned: 4
  POL-VPN-001         score=0.2784  matched_by=bm25,exact,sparse  doc_len=101  evidence_len=101  FULL DOC
  POL-APPROVAL-001    score=0.1957  matched_by=bm25,exact,sparse  doc_len= 34  evidence_len= 34  FULL DOC
  POL-VPN-SOP-001     score=0.1939  matched_by=bm25,exact,sparse  doc_len= 90  evidence_len= 90  FULL DOC
  POL-FINANCE-001     score=0.1184  matched_by=bm25,exact,sparse  doc_len= 53  evidence_len= 53  FULL DOC
```

链路是：

1. `hybrid_retrieve_chunks` 在 **chunk 粒度**做 BM25/sparse/exact/dense + RRF + rerank，返回 top-8 **chunk**；
2. `policy_search.add_candidate()` 立刻 `seen_documents` 去重 → **每篇文档只留第一个 chunk**；
3. 然后 `_parent_content()` 把 content **替换成父 chunk（= ROOT = 整篇文档）**；
4. 最终 `documents=_documents_from_evidence(evidence)` 让下游只能看到**整篇文档**。

**净效果**：分块检索的全部精度收益（以及 rerank 成本）在装配阶段被丢掉，
context 里塞回去的是**整篇文档**。这正是主流项目严厉批评的反模式——

> RAG 反模式 #9："top_k=50 硬塞进 context"；#11："没有近重复去重，一份制度被复述五次就填满窗口"。

而这个 pipeline 里 **`evidence` 恰好是"每文档一条、每条是全文"**，两件事同时成立。
讽刺的是 route 本身可能是对的（4 篇里有 3 篇召回正确），但**返回了几百字里用户只需要的两句话**。

对照主流做法：**small-to-big / parent-document retrieval** 是"命中子块 → 返回父块"，
但**父块应该是"节"而不是"整篇"**；LlamaIndex `AutoMergingRetriever` 是"多数兄弟命中才合并回父级"，
而不是"任何命中都返回全文"。

### P0-4 Query Plan 里三个最值钱的字段是死代码

实测 `understand_query('2024年的VPN报销制度是什么')`：

```
time_range : {'year': 2024}                    # 提取成功
entities   : {'products': ['VPN','Reimbursement'], ...}
subqueries : [('IT_SOP',        '2024年的VPN报销制度是什么'),
              ('SECURITY_POLICY','2024年的VPN报销制度是什么'),
              ('FINANCE_POLICY', '2024年的VPN报销制度是什么')]   # 三个完全相同的串
```

下游使用情况：

| 字段 | 检索是否使用 | 证据 |
|---|---|---|
| `time_range` | ❌ | `retrieve_policy_evidence` 签名**无此参数**；只用 `status == "active"` |
| `entities` | ❌（仅 ticket 侧 signature 用） | `tickets/signature.py:12` |
| `subqueries` | ❌ | 全仓库无调用点 |

`retrieve_policy_evidence` 实际签名：

```python
(index, query, kb_ids, user_context=None, top_k=8, rerank_client=None, embedding_provider=None)
```

**没有 time_range、没有 entities、没有 filters 下推。** 用户问"2024 年的制度"，
系统返回当前所有 active 制度——`effective_at` / `expires_at` **完全没参与过滤**
（`rerank.py:39` 有个 `expires_at` 减分，但上游 `retrieve_policy_evidence` 已把非 active 全过滤掉了，
所以那个减分项**永远不会触发**）。

`_subqueries_for` 的 `query=f"{query} {focus}"` 只是把 **原 query 和 KB 关键词拼接**，
三个 KB 得到三个近乎相同的串——这既不是查询改写也不是 sub-query 分解，
反而在各 KB 内**稀释**了原 query 信号。

### P0-5 中文分词：unigram/bigram 混用，凭空制造"码重"这种伪词

`knowledge/text.py:13-22`：

```python
def _cjk_bigrams(run: str) -> list[str]:
    if len(run) == 1: return [run]
    if len(run) <= 4: tokens = [run]      # ≤4 字额外加"整串"
    else: tokens = []                     # >4 字不加 → 行为不一致
    tokens.extend(run)                                # 每个单字
    tokens.extend(run[i:i+2] for i in range(len(run)-1))  # 所有 bigram
    return tokens
```

实测：

```
'密码'      -> ['密码', '密', '码', '密码']        # '密码' 出现两次
'密码重置'   -> ['密码重置','密','码','重','置','密码','码重','重置']
'密码重置流程' -> ['密','码','重','置','流','程','密码','码重','重置','置流','流程']  # 无整串
```

三个后果：

1. **词频被系统性放大**——"密码"计 2 次，污染 BM25 的 tf 与长度归一化；
2. **产生跨词伪词**："码重"、"置流" 不是任何语言的词，却进入 BM25 倒排与稀疏余弦；
3. **行为不一致**：≤4 字有"整词"信号，>4 字没有——同一个概念的长短决定它走哪条路。

对照主流做法：**接真正的分词器**（`jieba` 索引时用 `cut_for_search`、查询时用 `cut`；
PG 端用 `pg_jieba` / `zhparser` 让 BM25 在库内可用；ES 用 `ik_max_word` 索引 + `ik_smart` 查询——
**这个索引/查询不对称是刻意设计，值得照抄**）。若无分词器，**退化为纯 bigram**（Lucene `CJKBigramFilter` 方案），
**但绝不叠加 unigram**。当前 `jieba` 未安装。

### P1-6 Rerank：真正的语义重排缺失

`rerank_policy_chunks`（`rerank.py:34-47`）的加分项：

| 加分项 | 权重 | 区分度 |
|---|---|---|
| `authority_level >= 95` | +0.08 | ❌ **文档级**——同文档内所有 chunk 完全相同 |
| `status == "active"` | +0.05 | ❌ 同上，且上游已过滤 |
| `expires_at < today` | -0.12 | ❌ 同上，**且永不触发** |
| `parent_chunk_id is None` | +0.02 | ⚠️ 系统性偏向 ROOT，即"整篇文档" |
| `_phrase_overlap(query, chunk.content)` | ×0.04 | ⚠️ 见下 |
| `_phrase_overlap(query, section_path)` | ×0.12 | ⚠️ 见下 |

`_phrase_overlap`（`rerank.py:11-15`）用 `split()`，**对中文基本失效**：

```python
query_tokens = set(query.lower().split())   # 中文整句 = 1 个 token
```

实测 `_phrase_overlap('VPN 外网访问财务系统需要哪些审批和条件？', 'VPN 外网访问制度 第1条') = 0.5`
——只匹配上 `vpn` 一个 token，`0.5 = 1/2`，因为 query 被 `split()` 切成 2 个 token。

**所以 rerank 的有效信号主要来自 `document_id` 级先验（权威度、ROOT 偏好）+ 极粗的词面重叠，
不是语义相关性。** 实测它确实会改变顺序，但驱动力来自文档级加分与 ROOT 偏好。

`_model_rerank`（`rerank.py:57-86`）要求 LLM 输出 chunk id 的**完整排列**——
对 50 个候选是一次长输出、高延迟、易截断的调用，且 prompt 里只看 `content[:120]`。
主流做法是 **cross-encoder reranker**（`BAAI/bge-reranker-v2-m3`、jina-reranker-v2、
mixedbread mxbai-rerank、Cohere `rerank-v3.5`）对 (query, passage) 对批量打分。

**同时缺少 score threshold**：全仓库 `app/retrieval/` 下 grep `threshold|min_score` **0 命中**，
`top_k` 是无条件截断。Dify 的教训值得记：*"TopK 与 Score Threshold 只在 Rerank 阶段生效"*——
没有 reranker 时这两个设置等于不存在。

### P1-7 分块策略过于朴素

`chunking.py:9`：`_SECTION_SPLIT = re.compile(r"(?:\r?\n)+|(?<=[。；])\s*")`

- **无 token 预算**：`token_count` 算了但**从不用于切分决策**。
  实测 chunk token 数 `min=9 / median=40 / max=162`——9 个 token 的碎片承载不了语义；
- **无 overlap**：跨句结论（"……除外。但不适用于……"）被切断后两边都检索不到；
- **无真正的父子策略**：只有 ROOT(整篇) + 段落两档，而 `_parent_content()` 直接回退整篇（见 P0-3）。

对照主流做法：LangChain `RecursiveCharacterTextSplitter.from_tiktoken_encoder`
（默认分隔符 `["\n\n","\n"," ",""]`，中文补 `"。","！","？"`）；
**chunk_size 256–512 token、overlap 10–15%**（有父文档检索时 0% 重叠也可辩护——
避免近重复向量污染 top-k）；进阶可选 Anthropic **Contextual Retrieval**
（给每个 chunk 前置 LLM 生成的 50–100 token 定位上下文；官方数据 top-20 失败率降 35%，
叠加 contextual BM25 降 49%，再加 Cohere rerank 降 67%，成本约 $1.02/百万 token）。

### P1-8 评测不足以守护回归

`eval/golden_set.py`：**5 条 query**，标签是**文档级**（如 `POL-VPN-001`）。
实测（语料 7 文档 / 26 chunk）：

```
recall_at_k    1.0000  (min 0.9)  PASS
mrr            0.9000  (min 0.8)  PASS
ndcg_at_k      0.8941  (min 0.8)  PASS
precision_at_k 0.5800  (min 0.5)  PASS   <-- 余量仅 0.08
groundedness      n/a  (min 0.7)  UNAVAILABLE
```

问题：

1. **26 chunk 上 recall@5 必然≈1.0**——门槛通过只证明"语料足够小"，不证明检索有效；
2. **文档级标签掩盖 chunk 级失败**：命中文档 ≠ 命中正确段落，
   而 `precision_at_k=0.58` 已说明近一半返回不相关；
3. **无 RAGAS 语义指标**：Context Precision / Context Recall / Noise Sensitivity / Faithfulness 全缺
   （`ragas` 未安装）；`groundedness` 有实现但默认 UNAVAILABLE（诚实，但等于没测）；
4. **无合成测试集**：手工 5 条覆盖不了"无答案 / 歧义 / 多跳 / 权限受限"边界。

> 注意 RAGAS 的一个陷阱：**Context Precision/Recall 需要 ground-truth 答案**，
> 而 Faithfulness/Answer Relevancy 不需要。另外用 chunk 反生成的合成问题会被该 chunk 直接回答，
> **虚高 Context Recall**——必须显式加入"不可回答"与"多 chunk"用例。

### P2-9 其他确认项

| 项 | 位置 | 说明 |
|---|---|---|
| 遗留死代码 | `app/knowledge/base.py` | `search_knowledge` / `search_historical_tickets` / `format_knowledge_context` **全仓无调用点**；且调 `run_retrieval` 不传 `embedding_provider`，稠密通道即使配置了也不启用 |
| 破损且无人调的 SQL 检索 | `postgres_repository.py:613` | `content ILIKE '%{query}%'`（整句模糊匹配）、`ORDER BY title`（按标题而非相关性排序）、`allowed_roles ?| array[%s]` 把 Python list 塞进单占位符；**无调用点** |
| 稠密通道被配置门控 | `graph/nodes/knowledge.py:39-43` | 仅当 `retrieval_use_llm or retrieval_use_postgres` 才解析 provider——`EMBEDDING_API_KEY` 配了也可能完全不生效（`describe_dense_channel` 如实报告，但接口设计与直觉相反） |
| 检索零可观测性 | `app/retrieval/*` | grep `cache\|lru_cache\|observe\|metrics\|token_usage\|latency` → **0 命中**。检索耗时、各通道贡献、embedding 调用数、rerank 增益全不可见 |
| 零缓存 | 同上 | 无 embedding 缓存、无语义缓存、无检索结果缓存 |
| ACL 仅到文档级 | `schema.sql:163-175` | `knowledge_chunk` 无 ACL 列。当前靠父文档过滤**是正确的**，但引入 chunk 级权限时必须投影到 chunk 行——`platform-gap-analysis.md` 已正确预警 |
| 语料是硬编码常量 | `corpus.py` | 7 篇文档写死在 Python 里，无真实摄取管道（PDF/Word/HTML 解析、去重、增量） |
| 无跨副本失效 | `ingest.py` | 已自述：刷新端点只失效处理该请求的进程，其他副本等 TTL(30s) |

---

## 2. 做得好的地方（重构时不要丢掉）

1. **不伪造能力**：无 embedding key → 返回 `None` → 如实标 `lexical_only`，
   result / plan / `to_dict()` / `/ready` 四处暴露。这比"哈希向量冒充语义检索"好得多。
2. **稠密通道是增量的**：provider 失败不清空结果集（`hybrid_search.py:191`），降级为召回率下降而非空结果。
3. **ACL 下推到 SQL**：`load_index(roles=...)` 在查询层过滤 + `_can_access_*` 纵深防御；
   `lru_cache` → TTL + generation 守卫解决了"权限撤销不生效"。
4. **检索内容护栏**：`apply_retrieval_rail` 在证据进 prompt 前拦截间接提示注入——
   这对应 OWASP LLM Top 10 的 **LLM01**，**多数开源 RAG 项目没有**。
5. **引用可验证**：`citation_accuracy` 是真实解析（引用必须命中实际检索集合），不是字符串包含。
6. **RRF 实现正确**：`fusion.py` 的 `1/(k+position)`、k=60 符合 Cormack 等人原始论文。

---

## 3. 改进路线（按性价比排序）

### 阶段一：让小语料实现变成"真检索"（P0）

1. **embedding 落库 + pgvector ANN**（最高优先级，一次性解决 P0-1 与 P0-2）
   - `seed()` 时**批量** embed 并写入 `knowledge_chunk.embedding`（列已在 schema 里）；
   - 建 HNSW 索引：
     ```sql
     CREATE INDEX ON knowledge_chunk USING hnsw (embedding vector_cosine_ops)
       WITH (m = 16, ef_construction = 64);   -- 查询期 hnsw.ef_search 默认 40
     ```
   - 查询改为 `ORDER BY embedding <=> %s LIMIT k`，**ACL 与 kb 过滤一并下推到同一条 SQL**
     （当前 Python 侧全量扫描 + 事后过滤，正是"ACL 必须下推"要避免的形态）；
   - `HttpEmbeddingProvider` 改为**批量接口**（`input: [str,...]`），把 27 次请求压成 2 次。
2. **修事件循环阻塞**：embedding/LLM 调用改 `httpx.AsyncClient`，
   或至少用 `run_in_threadpool` 包住同步调用。**这是可用性问题，不是性能问题。**
3. **加 embedding 缓存**：键为 `sha256(text + model_id + dims)`，摄取与查询共用。
   **必须把模型版本放进键**，否则换模型后会静默供应过期向量。
4. **修分块**：显式 `chunk_size` / `chunk_overlap`（中文建议 300–500 字 / 50–80 字重叠），
   按 token 预算切分，保留标题层级进 `section_path`。
5. **修 `_cjk_bigrams`**：只留 bigram，或接 `jieba`（索引 `cut_for_search` / 查询 `cut`）。
   **绝不要 unigram + bigram 叠加。**
6. **修 P0-3 的装配粒度**：`_parent_content` 改为返回**所在节**而非整篇；
   或按 LlamaIndex `AutoMergingRetriever` 语义——**多数兄弟命中才合并回父级**。
   同时把 `evidence` 从"每文档一条"改为"chunk 粒度 + 所属文档引用"。

### 阶段二：让 rerank 与查询理解真正生效（P1）

7. **引入 cross-encoder reranker**（自托管 `BAAI/bge-reranker-v2-m3`，或 Cohere/jina API），
   替换/前置 `_model_rerank`；**批量 20–100 对一次打分**；保留 `_model_rerank` 作兜底。
   级联规模参考：检索 50–100 → rerank → 保留 5–10 给人看；
   Anthropic 实测的**生成侧 top-20 优于 top-10 与 top-5**，比"民间 3–5"更高。
   注意：**reranker 对长 chunk 会退化**，所以分块长度要先控制住。
8. **加 score threshold**：`top_k` 之前先按分数阈值截断，避免"返回固定条数、不管相关性"。
9. **落地 `time_range` / `entities` / `subqueries`**：
   - `time_range` 下推为 `effective_at <= now AND (expires_at IS NULL OR expires_at > now)`；
   - 真正的 sub-query 分解：让 LLM 产出**互不相同**的子查询，分别检索后 RRF 融合；
   - 或**删掉这些字段**——存在但无用的字段比没有更糟。
10. **中文查询改写**（按需路由，不要全开）：多查询扩展（`MultiQueryRetriever` 默认 3 条）、
    step-back prompting、**对话式查询改写**（多轮 RAG 不做这个就是坏的，也是最常被漏掉的一环）；
    HyDE **只对模糊 query 开启**——对错误码/专有名词这类已经精确的 query，HyDE 反而有害。
11. **清理死代码**：删除 `app/knowledge/base.py` 与 `postgres_repository.search_knowledge`，
    或修复并接线（当前二者既坏又无人调用）。

### 阶段三：评测与可观测性（P1→P2）

12. **扩语料**：至少几百篇真实文档，否则所有检索指标无意义。
13. **chunk 级标签 + RAGAS 指标**：Context Precision / Context Recall / Noise Sensitivity / Faithfulness；
    接 RAGAS（`TestsetGenerator`）或 DeepEval（pytest 原生）。
    **检索指标门槛收紧**（recall@10 跌幅 >2 点即失败），**生成指标门槛放宽**（faithfulness 逐次波动大）；
    **judge 模型与版本必须 pin**——换 judge 就换分数。
14. **补齐可观测性**：`retrieval_run` 的 `latency_ms` / `token_usage` / `subqueries` / `rerank_scores` /
    `evidence_ids` 真实写入；加各通道贡献度与 embedding 调用计数。
    按 **OpenTelemetry GenAI 语义约定**打 span（`embeddings` / `retrieve` / `invoke_agent`）；
    **必须记录检索到的 chunk id 与各阶段分数**——否则一个坏答案无法归因，
    "答案错了"就成了不可证伪的说法。
15. **跨副本失效**：Redis pub/sub 广播 `invalidate_index()`（或把 TTL 压到秒级）。
16. **chunk 级 ACL 投影**（引入 chunk 级权限时**必须先做**，否则向量泄漏而父行看起来正常）。
17. **近重复去重**：同一制度在多文档复述时，避免填满 context 窗口。
    这是反模式 #11，而 P0-3 的"每文档一条 + 全文"正好会放大它。

---

## 4. 与 GitHub 优秀项目的差距对照

| 能力 | 本项目 | Dify | RAGFlow | LlamaIndex | Onyx | Haystack |
|---|---|---|---|---|---|---|
| 向量库持久化 | ❌ **列存在 26/26 为 NULL** | ✅ 多后端 | ✅ | ✅ | ✅ | ✅ |
| ANN 索引 | ❌ **0 个，全量线性扫描** | ✅ | ✅ | ✅ | ✅ | ✅ |
| 混合检索权重可调 | ⚠️ 固定 RRF k=60 | ✅ 权重滑杆 | ✅ `vector_similarity_weight` | ✅ | ✅ | ✅ |
| 中文分词 | ⚠️ **unigram+bigram 伪词** | ✅ | ✅ DeepDoc | ✅ 可插拔 | — | ✅ 可插拔 |
| Cross-encoder rerank | ❌ 仅启发式 + LLM 排列 | ✅ Rerank Model | ✅ | ✅ | ✅ | ✅ |
| Score threshold | ❌ **0 命中** | ✅（rerank 阶段） | ✅ | ✅ | ✅ | ✅ |
| 查询改写/分解 | ⚠️ **字段存在但未接线** | ✅ | ✅ | ✅ HyDE/多查询 | ✅ | ✅ |
| 父子 / 小到大检索 | ❌ **回退整篇文档** | ✅ | ✅ | ✅ | ✅ | ✅ |
| 评测（RAGAS 式） | ⚠️ 5 query 文档级 | ✅ | ✅ 检索测试指南 | ✅ | ✅ | ✅ |
| 权限下推到检索 | ✅ SQL 下推 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 检索内容护栏 | ✅ **优于多数** | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| 诚实降级标注 | ✅ **优于多数** | ⚠️ | ⚠️ | — | — | — |

**参考架构**（共识形态）：
布局感知解析 → token 感知递归分块（256–512 token / 10% overlap）+ contextual 前缀 →
embed（MRL 模型 / 512–1024 维 / 批量 / 内容哈希缓存）入 HNSW（int8 或二值量化）→
混合检索（稠密 + **正确中文分词**的 BM25，RRF k=60）每路取 20–50 →
cross-encoder rerank 50–100 → 保留 10–20 → 对话式条件改写（HyDE/多查询按需）→
生成时把检索文本用分隔符标记为**不可信数据** → OTel GenAI 全链路追踪 → CI 门槛卡 recall@10 与 faithfulness。

> 区分 demo 与生产的两件事：**摄取期的 contextual retrieval**，和**查询期的 cross-encoder reranker**。
> 其余都是调参。

---

## 5. 一句话建议

**先做阶段一的第 1、2、6 项**（embedding 落库 + ANN、异步化、修复装配粒度）——
这三项做完，这个模块才第一次具备"语义检索"和"精准上下文"；
在此之前，扩语料或调评测门槛都是在测量一个不存在的通道。

---

---

## 6. P0 落地记录（本会话已实现并实测）

阶段一中被选中的三项**已经实现**，并对运行中的数据库做了端到端验证。

### 6.1 改了什么

| # | 改动 | 文件 |
|---|---|---|
| 1 | **embedding 批量 + 缓存**：`embed_batch` / `aembed_batch`；响应按 `index` 回填而非按位置 zip；内容寻址缓存（键含 text+model+dimension）；失败结果也缓存，避免死 provider 被每 chunk 重试 | `app/retrieval/embedding.py`、新增 `app/retrieval/cache.py` |
| 2 | **pgvector ANN + 过滤下推**：新增 `search_chunks_by_vector()`，单条 SQL 完成 kb/ACL/status 过滤 + `ORDER BY embedding <=> ?`；`ensure_ann_index()` 建 HNSW；`count_chunk_embeddings()` / `has_ann_index()` 供 `/ready` 如实上报 | `app/persistence/knowledge_repository.py`、新增 `app/retrieval/vector_store.py` |
| 3 | **摄取期落库**：`seed()` 后置一个 best-effort 的 embed 阶段（独立连接、独立事务）。provider 故障只让向量保持 NULL，**不会回滚内容写入**；内容变更的 chunk 向量被置 NULL 后重算 | `app/persistence/knowledge_repository.py` |
| 4 | **稠密通道抽象**：`PgVectorStore`（持久化向量，单次 ANN）与 `InMemoryDenseStore`（按需扫描，小语料/离线）实现同一契约，`build_dense_store()` 自动选择 | `app/retrieval/vector_store.py`、`app/retrieval/hybrid_search.py` |
| 5 | **消除事件循环阻塞**：`run_retrieval` 仍是同步函数（非 async 调用方也在用），但在 async 图节点中经 `asyncio.to_thread` 移出事件循环 | `app/graph/nodes/knowledge.py` |
| 6 | **修复装配粒度**：不再 `_parent_content` 回退整篇；改为「命中 chunk + 同父兄弟」在 token 预算内展开；每文档条数封顶；`metadata` 新增 `chunk_id` / `truncated` | `app/knowledge/policy_search.py`、`app/config.py` |
| 7 | **顺带修掉两个真实缺陷**（见 6.3） | `app/knowledge/policy_search.py`、`eval/metrics.py` |

### 6.2 实测证据（对 `localhost:5433/skychat`）

```
改动前：chunks = 26，chunks with embedding = 0，ANN index = False
改动后：chunks = 26，chunks with embedding = 26，ANN index = True
        idx_knowledge_chunk_embedding_hnsw
        USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)

ANN 检索：返回带分数的邻居（score ∈ [0,1]，`ORDER BY <=> LIMIT 5`）
kb 过滤：kb_ids={FINANCE_POLICY} → 3 行，0 条越界（过滤确实在 SQL 里）
ACL 过滤：roles={no-such-role} → 24 行（26 − 2 个带 allowed_roles 的敏感 chunk），
          证明 ACL 在 SQL 层生效而非事后裁剪
后端选择：build_dense_store() → "pgvector"（向量已落库时不再退化为内存扫描）
缓存命中：重复查询 embedding 调用数 0 增长（1 text → 1 text）
索引可用：`SET enable_seqscan=off` 下 EXPLAIN 显示
          `Index Scan using idx_knowledge_chunk_embedding_hnsw`
```

> 关于 26 行时 planner 选 seq scan：这是**正确的**代价选择，不是缺陷。
> 但有一个**规模化限制**必须记录：当前 SQL 用 `JOIN knowledge_document d` 来取
> `d.status` / `d.allowed_roles`，而 JOIN 会阻止 HNSW 索引被用于 `ORDER BY`。
> 语料长大后应把 `status` 与 ACL **投影到 `knowledge_chunk` 行**
> （`platform-gap-analysis.md` 对分块 ACL 已有同样预警），或在 CTE 中先做 ANN
> 再 join。当前 26 分片下可接受，**上规模前必须处理**。

### 6.3 过程中发现并修复的两个真实缺陷

1. **reranker 的结论被最后一行的 `evidence.sort(key=score)` 丢弃。**
   RRF 融合 → 启发式 → LLM 重排产出的顺序，被一句按原始分数重排覆盖；
   一个被提升的 chunk 分数不高，所以排序会把它打回去。已删除该排序并加注释，
   同时补了回归测试 `test_evidence_order_preserves_the_reranker_decision`。
2. **评测指标算出不可能的值。** 装配粒度改变后同一文档会出现多条证据，
   而 recall/MRR/nDCG 是**文档级**指标，重复计数导致 `recall_at_k = 1.7`、
   `ndcg = 1.26`（两者上限都是 1.0）。已在 `eval/metrics.py` 加
   `unique_in_order()` 去重后再计算；修复后指标回到 `recall 1.0 / mrr 0.9 /
   ndcg 0.894`，与改动前一致，但现在是**语义正确**的。

### 6.4 测试状态（诚实记录）

```
471 passed, 6 skipped, 9 failed
```

新增 25 个测试（`tests/test_retrieval_vector_store.py`），全部通过。
另有 5 个数据库相关测试因测试环境 `conftest` 把 `DATABASE_URL` 置空而 skip，
其逻辑改由 `.buildcheck/rag_p0_live_check.py` 对真实库验证。

**关于 9 个 failed：经判定为既有失败，与本次改动无关。** 判断依据：

- 它们全部位于 `tests/test_function_calling.py`（5 个）与
  `tests/test_loop_improvements.py`（4 个）；
- 这 9 个测试测的是工具调用循环，而 `app/graph/nodes/tools.py` 的 import
  列表里**没有任何 retrieval / knowledge 模块**；
- 关键实验：把本次唯一触及图节点的改动（`app/graph/nodes/knowledge.py`
  的 `asyncio.to_thread`）单独还原后，**这 9 个失败一模一样地复现**；
- 失败现象是 `adapter` 未被调用，而 `tool_routing == "model"` 与
  `tool_rounds == 1` 两条断言**都通过**——说明循环本身跑通了，问题在
  「模型选了工具但没执行」这一步，落在工作区中既有的未提交改动
  （`app/tools/calling.py`、`app/tools/catalog.py`、`app/tools/adapters/*`）上。

> 环境备注：本会话的沙箱禁止写入 `agent-service/app`，因此无法用
> 「还原到 HEAD 跑一遍」的方式取基准；上述结论是用「只还原我的那一处改动」
> 的等价实验得出的。建议你本地 `git stash` 后跑一次这 9 个测试确认。

### 6.5 复现方式

```powershell
cd agent-service

# 全量测试
.\.venv\Scripts\python.exe -m pytest tests -q

# 评测门槛
.\.venv\Scripts\python.exe -m eval.run_eval

# 对真实数据库端到端验证 P0（0→26 向量、建索引、下推过滤、缓存命中）
.\.venv\Scripts\python.exe ..\.buildcheck\rag_p0_live_check.py

# 改前的向量落库/索引事实
.\.venv\Scripts\python.exe ..\.buildcheck\rag_embedding_facts.py
```

### 6.6 仍未做（原报告阶段二的候选，按优先级）

- **cross-encoder reranker**：当前仍是启发式 + LLM 排列（P1-6 未动）；
- **score threshold**：仍是无条件 `top_k` 截断；
- **中文分词**：`_cjk_bigrams` 的 unigram+bigram 叠加与伪词问题**仍在**（P0-5 未选中）；
- **真实分块**：token 预算 / overlap 仍缺失（P1-7 未选中）；
- **`subqueries` 仍未接线**（`time_range` 这次已接线）；
- **JOIN 阻止 HNSW 用于 ORDER BY** 的规模化问题（见 6.2）；
- **没有任何 embedding 凭据**（更正见下方 6.7）。要让稠密通道真正工作必须补上
  一项真实 embedding 配置，否则 `default_embedding_provider()` 返回 `None`、
  如实降级为 `lexical_only`。**当前配置下稠密通道依然不生效——这是设计使然，不是回归。**

---

## 6.7 更正：稠密通道为何不生效

**本文早前给出的诊断是错的，此处更正。**

早前写的「`EMBEDDING_API_KEY` 在仓库根 `.env`，而服务只读 `agent-service/.env`，
两个文件不一致」——**不成立**。逐字节核对后的事实：

| 文件 | `EMBEDDING_API_KEY` |
|---|---|
| 仓库根 `.env` | 该行**存在但值为空**（捕获到的原始值 repr 为 `[""]`） |
| `agent-service/.env` | 该行**完全不存在** |

所以**根本不存在"密钥放错文件"这回事：两份配置里都没有任何 key**，
无论服务从哪个目录启动都不会生效。这个误判来自我最初用
`if k -match '(KEY|TOKEN)' { '<set>' if $v else '<empty>' }` 做掩码时，
对根 `.env` 的读取结果被后续输出误导——正确做法是直接看 `EMBEDDING_` 前缀的行，
而不是依赖掩码后的汇总。

顺带确认的一点（这个仍然成立）：服务从 `agent-service/` 启动
（`README.md` 的运行方式是 `uvicorn app.main:app`），而 `app/config.py` 里
`env_file=".env"` 是**相对路径**，所以它读的是 `agent-service/.env`，
**永远不会读仓库根 `.env`**。根 `.env` 更像是给 front-service / Prisma 用的。
这一点在配置真实密钥时仍然重要——见 6.8。

### 另一个必须知道的硬约束

`schema.sql:158,173` 两处都是：

```sql
embedding vector(1536)
```

而 `app/config.py` 的默认值是 `embedding_model = "text-embedding-3-small"`、
`embedding_dimension = 1536`——**两者当前是一致的**。

但**代码里没有任何维度校验**：`embedding.py` / `vector_store.py` /
`knowledge_repository.py` 都不检查向量长度是否等于 1536。
如果换成 1024 维的模型（bge-m3、通义 text-embedding-v3 等）而没改 schema，
PostgreSQL 会在写入时直接报 `expected 1536 dimensions, not 1024`——
这是一次**运行时失败**，而不是启动时的配置校验。换模型时必须同时改
`EMBEDDING_DIMENSION` 与 `schema.sql` 的 `vector(N)`（并对已有列做
`ALTER TABLE ... ALTER COLUMN embedding TYPE vector(N)` + 重建 HNSW 索引，
因为**不同模型的向量不可混用**）。

---

## 6.8 如何让稠密通道真正生效

配置本身只有一步（设置 embedding 凭据），但**先要选一个 provider**，
因为默认值指向 OpenAI，而本项目的 LLM 用的是 DeepSeek。

### 前置判断

1. **DeepSeek 的 key 不能用于 embedding。** DeepSeek 提供 chat 模型，
   但其 API **不提供 `/embeddings` 端点**。把 `DEEPSEEK_API_KEY` 填进
   `EMBEDDING_API_KEY` 只会让每个请求 404 → `embed_batch` 返回 `[None, ...]`
   → 稠密通道**静默降级**（按现在的设计，这在日志里是一条 warning，不会报错）。
   所以必须用一个**真实的 embedding 服务**。
2. **维度必须与 schema 一致**（见 6.7 的硬约束）。当前 schema 是 `vector(1536)`。
3. **要同时打开 wiring 开关。** 这一点容易漏：`app/graph/nodes/knowledge.py`
   只在 `RETRIEVAL_USE_LLM or RETRIEVAL_USE_POSTGRES` 为真时才去解析 provider。
   也就是说**只配 key 还不够**，否则 `describe_dense_channel()` 会如实报告
   `provider: null` / `mode: lexical_only`。

### 方案 A：用 1536 维的托管 embedding（改动最小）

任何 OpenAI 兼容 `/embeddings` 且输出 1536 维的服务都可以直接用。
在 **`agent-service/.env`**（不是仓库根 `.env`）里加：

```dotenv
EMBEDDING_BASE_URL="https://<你的服务>/v1"
EMBEDDING_API_KEY="<真实密钥>"
EMBEDDING_MODEL="text-embedding-3-small"
EMBEDDING_DIMENSION="1536"
# 必须同时打开，否则 provider 根本不会被解析
RETRIEVAL_USE_LLM="true"
```

若走 OpenAI 官方，`EMBEDDING_BASE_URL` / `MODEL` / `DIMENSION` 保持默认即可，
只需填 key 与 `RETRIEVAL_USE_LLM=true`。

### 方案 B：用 1024 维的开源/国产 embedding（通常更划算）

bge-m3、通义 `text-embedding-v3`、智谱 `embedding-3` 等都是 1024 维，
**必须同步改 schema**，否则写入即失败：

```dotenv
EMBEDDING_BASE_URL="https://<你的服务>/v1"
EMBEDDING_API_KEY="<真实密钥>"
EMBEDDING_MODEL="bge-m3"
EMBEDDING_DIMENSION="1024"
RETRIEVAL_USE_LLM="true"
```

```sql
-- 先清掉旧向量（不同模型不可混用），再改宽度，再让服务重建索引
ALTER TABLE knowledge_chunk    ALTER COLUMN embedding TYPE vector(1024);
ALTER TABLE knowledge_document ALTER COLUMN embedding TYPE vector(1024);
DROP INDEX IF EXISTS idx_knowledge_chunk_embedding_hnsw;
```

### 方案 C：本地自托管 embedding（无外部依赖、无按量计费）

用 Ollama / TEI / vLLM 起一个 OpenAI 兼容端点，例如：

```dotenv
EMBEDDING_BASE_URL="http://localhost:11434/v1"
EMBEDDING_API_KEY="ollama"          # 本地服务不校验，占位即可
EMBEDDING_MODEL="bge-m3"
EMBEDDING_DIMENSION="1024"
RETRIEVAL_USE_LLM="true"
```

注意 `HttpEmbeddingProvider` 在 `api_key` 为空时**直接返回 `None` 而不发请求**，
所以即使是本地免鉴权的服务，也必须给一个非空占位值。

### 验证清单（务必逐条确认，避免"看着配了其实没生效"）

```powershell
cd agent-service

# 1) provider 是否被解析出来（应为 http，不是 null）
.\.venv\Scripts\python.exe -c "from app.retrieval.embedding import describe_dense_channel as d; print(d())"

# 2) 真实调用一次 embedding，确认凭据/维度/网络都对
.\.venv\Scripts\python.exe -c "from app.retrieval.embedding import default_embedding_provider as p; v=p().embed('测试'); print('dim =', len(v) if v else None, '| vector is None => 调用失败')"

# 3) 把向量落库并建索引
.\.venv\Scripts\python.exe -c "from app.persistence.knowledge_repository import PostgresKnowledgeRepository as R; from app.knowledge.ingest import build_seed_index; r=R(); r.ensure_schema(); r.seed(build_seed_index()); print('embedded chunks =', r.count_chunk_embeddings(), '| ann index =', r.has_ann_index())"

# 4) 端到端：检索模式应变成 hybrid，且 matched_by 里出现 vector
.\.venv\Scripts\python.exe -c "from app.retrieval.pipeline import run_retrieval; from app.retrieval.embedding import default_embedding_provider as p; r=run_retrieval('VPN 外网访问财务系统需要哪些审批', embedding_provider=p()); print('mode =', r.retrieval_mode, '| backend =', r.plan['dense_backend']); print([(e['source_id'], e['matched_by']) for e in r.evidence])"
```

第 3 步的 `embedded chunks` 必须 > 0，第 4 步的 `mode` 必须是 `hybrid`
且 `matched_by` 里出现 `vector`。**如果 `mode` 仍是 `lexical_only`，
就是 `RETRIEVAL_USE_LLM` 没打开**（方案 A/B/C 里那一行）。

### 还能顺手补的两个工程缺口

1. **`.env.example` 完全没有 embedding 段**（`agent-service/.env.example:39-41`
   只有 `DEEPSEEK_*`）。新克隆仓库的人无从得知要配这些——建议把
   `EMBEDDING_*` 与 `RETRIEVAL_USE_LLM` 一并写进去。
2. **没有启动期维度校验**。目前维度不匹配要等到第一次写库才炸。
   建议在 `ensure_schema()` 或 `/ready` 里比对
   `settings.embedding_dimension` 与 `atttypmod`，把运行时失败提前成启动期失败。

> 这两项我没有代改：本会话的沙箱对 `agent-service/` 是只读的
> （`agent-service/app` 与 `agent-service/.env` 的写入都被拒绝），
> 上述改动需要你在本地执行。

---

## 6.9 国产 1024 维 embedding 落地方案（本次选定）

### 三个候选的准确参数（已核对官方文档）

| provider | 推荐模型 | 默认维度 | 是否可自定义维度 | OpenAI 兼容 base_url |
|---|---|---|---|---|
| **硅基流动**（推荐，最省事） | `BAAI/bge-m3` | **1024** | 否（固定 1024） | `https://api.siliconflow.cn/v1` |
| **阿里云百炼 / DashScope** | `text-embedding-v3` | **1024** | 是 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| **智谱 BigModel** | `embedding-3` | 需确认（256–2048 可自定义） | 是 | `https://open.bigmodel.cn/api/paas/v4` |

来源：[硅基流动 创建嵌入请求](https://docs.siliconflow.cn/docs/api/embeddings-post)、
[阿里云百炼 OpenAI 兼容 Embedding](https://docs.modelstudio.console.alibabacloud.com/zh/model-studio/embedding-interfaces-compatible-with-openai)、
[智谱 Embedding-3（256–2048 可自定义）](https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3)。

**为什么推荐硅基流动 + bge-m3**：维度固定 1024，与目标 schema 一致，
且不需要在请求里传 `dimensions`——正好绕开下面这个坑。

### 坑：provider 目前不发送 `dimensions`

`app/retrieval/embedding.py` 的请求体只有 `{"model": ..., "input": ...}`，
**从不传 `dimensions`**。所以对「可自定义维度」的模型（智谱 embedding-3、
百炼 text-embedding-v3），实际返回维度是**服务端默认值**，不一定等于
`EMBEDDING_DIMENSION`。而 schema 是 `vector(1024)`（改完之后），
一旦默认值不是 1024，写入就会失败。

两个选择：

- **简单**：用维度固定的模型（`BAAI/bge-m3` = 1024），不碰代码；
- **稳妥**：按下面 6.10 的补丁给请求加上 `dimensions`，让服务端按配置输出。

### `agent-service/.env` 需要追加的内容

```dotenv
# --- 稠密检索（embedding） -------------------------------------------
# 硅基流动 + bge-m3：固定 1024 维，与 schema 的 vector(1024) 一致
EMBEDDING_BASE_URL="https://api.siliconflow.cn/v1"
EMBEDDING_API_KEY="<你的硅基流动密钥>"
EMBEDDING_MODEL="BAAI/bge-m3"
EMBEDDING_DIMENSION="1024"
EMBEDDING_BATCH_SIZE="64"
# 必须打开：graph 节点只在 LLM 或 Postgres 检索开启时才解析 provider
RETRIEVAL_USE_LLM="true"
```

### schema 迁移（必须，否则写入报维度错误）

```sql
-- 1) 清掉旧的 1536 维向量：不同模型的向量不可混用
UPDATE knowledge_chunk    SET embedding = NULL;
UPDATE knowledge_document SET embedding = NULL;

-- 2) 改列宽
ALTER TABLE knowledge_chunk    ALTER COLUMN embedding TYPE vector(1024);
ALTER TABLE knowledge_document ALTER COLUMN embedding TYPE vector(1024);

-- 3) 丢掉按 1536 建的 HNSW 索引，服务下次落库时会按新维度重建
DROP INDEX IF EXISTS idx_knowledge_chunk_embedding_hnsw;
```

> 若索引名不同，先 `\d knowledge_chunk` 确认。
> 第 3 步不能省：HNSW 索引与列维度绑定，改列宽后宿索引会失效。

### 执行顺序（顺序错了会白跑）

1. 定 schema：跑上面的迁移 SQL；
2. 定配置：把那段 `.env` 追加到 **`agent-service/.env`**；
3. 重启服务（`.env` 在启动时读取）；
4. 灌向量 + 建索引：跑 6.8 的验证第 3 条；
5. 验端到端：跑 6.8 的验证第 4 条，`mode` 必须是 `hybrid`。

---

## 6.10 建议的代码补丁（本会话沙箱对 `agent-service/` 只读，需你本地应用）

### (A) 让 provider 发送 `dimensions`

`app/retrieval/embedding.py`，`HttpEmbeddingProvider`：

```python
def __init__(
    self,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    batch_size: int | None = None,
    timeout: float | None = None,
    dimensions: int | None = None,
) -> None:
    self.api_key = api_key if api_key is not None else settings.embedding_api_key
    self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
    self.model = model or settings.embedding_model
    self.batch_size = max(1, batch_size or settings.embedding_batch_size)
    self.timeout = timeout or settings.llm_timeout_seconds
    self.dimensions = dimensions or settings.embedding_dimension

def _payload(self, window: list[str]) -> dict[str, Any]:
    """Request body, with `dimensions` only when it is a real value.

    Sentinel `0` means "let the provider use its own default" — some
    OpenAI-compatible servers reject an unknown `dimensions` field outright, so
    sending it unconditionally would turn a working provider into a 400.
    """
    payload: dict[str, Any] = {"model": self.model, "input": window}
    if self.dimensions and self.dimensions > 0:
        payload["dimensions"] = self.dimensions
    return payload
```

然后把两处 `json={"model": self.model, "input": window}` 换成 `json=self._payload(window)`
（`_embed_window` 与 `_aembed_window`）。

`app/config.py`：把默认维度改成 0（= 交给服务端），并在注释里说明。

```python
embedding_dimension: int = 0  # 0 = 用服务端默认维度，不发送 dimensions
```

### (B) 启动期维度校验（把运行时失败提前）

`app/persistence/knowledge_repository.py` 新增：

```python
def schema_vector_dimension(self) -> int | None:
    """The width of `knowledge_chunk.embedding`, or None when it is not a vector."""
    with self._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = 'knowledge_chunk' AND a.attname = 'embedding'
                """
            )
            row = cur.fetchone()
    return int(row[0]) if row and row[0] and row[0] > 0 else None
```

在 `ensure_schema()` 末尾（或 `/ready` 里）比对：

```python
configured = settings.embedding_dimension
actual = self.schema_vector_dimension()
if configured and actual and configured != actual:
    raise RuntimeError(
        f"EMBEDDING_DIMENSION={configured} but knowledge_chunk.embedding is "
        f"vector({actual}); writing would fail at runtime"
    )
```

### (C) `.env.example` 补 embedding 段

`agent-service/.env.example` 在 `DEEPSEEK_*` 之后插入：

```dotenv
# --- 稠密检索（可选）--------------------------------------------------
# 留空则如实降级为 lexical_only（BM25 + 稀疏 + 精确匹配），/ready 会标注。
# DeepSeek 不提供 /embeddings 端点，必须另配一个 embedding 服务。
# 维度必须与 knowledge_chunk.embedding 的 vector(N) 一致。
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
EMBEDDING_BATCH_SIZE=64
# 打开后检索才会解析 provider；否则即使配了 key 也是 lexical_only
RETRIEVAL_USE_LLM=false
```

> 本会话沙箱对 `agent-service/`（含 `.env.example`、`app/`）只读，
> 上述 (A)(B)(C) 与 `.env` 改动都未代为应用。

---

## 6.11 智谱（BigModel / GLM）embedding 专项结论

以下结论来自智谱官方 OpenAPI 文档 + 本机实测，**与本项目的 schema 组合后有一个陷阱**。

### 官方规格（[文本嵌入 API](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E6%96%87%E6%9C%AC%E5%B5%8C%E5%85%A5.md)）

| 项 | `embedding-3` | `embedding-2` |
|---|---|---|
| 别名 | `GLM Embedding` 系列 | — |
| **默认维度** | **2048** | 1024（固定） |
| 可自定义维度 | 是，仅限 **256 / 512 / 1024 / 2048** | 否 |
| 单条上限 | 3072 tokens | 512 tokens |
| **单次数组上限** | **64 条** | 8K tokens |
| 端点 | `POST https://open.bigmodel.cn/api/paas/v4/embeddings` | 同 |

### 陷阱：`embedding-3` 的默认 2048 维**建不了 HNSW 索引**

本机对真实库实测（pgvector 0.8.6）：

```
vector(1024) -> HNSW index created
vector(1536) -> HNSW index created
vector(2000) -> HNSW index created
vector(2001) -> HNSW REFUSED: column cannot have more than 2000 dimensions for hnsw index
vector(2048) -> HNSW REFUSED: column cannot have more than 2000 dimensions for hnsw index
```

而**当前代码不发送 `dimensions` 字段**（已实测请求体只有 `{"model", "input"}`），
所以直接把 `EMBEDDING_MODEL=embedding-3` 配上，拿到的就是 **2048 维**：

- 存得进 `vector(2048)`（pgvector 存没有 2000 限制），
- 但 `ensure_ann_index()` 会失败，`has_ann_index()` 恒为 `False`，
  稠密检索退化为**全表顺序扫描**——而且 `_persist_embeddings` 只在写入成功时建索引，
  失败只留一条 warning，**表现为"配好了但其实没索引"**。

这正是本文第 1 节批评过的同一类问题：看起来配了，实际没生效。

### 两条可行路线

**路线 1（零代码改动）：用 `embedding-2`**

固定 1024 维，不需要发送 `dimensions`，与目标 `vector(1024)` 天然一致。

```dotenv
# agent-service/.env
EMBEDDING_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
EMBEDDING_API_KEY="<你的智谱密钥>"
EMBEDDING_MODEL="embedding-2"
EMBEDDING_DIMENSION="1024"
EMBEDDING_BATCH_SIZE="64"
RETRIEVAL_USE_LLM="true"
```

限制：单条 512 tokens。本项目最长 chunk 为 162 tokens，**安全**。

**路线 2（推荐长期方案）：用 `embedding-3` + 6.10 (A) 补丁**

`embedding-3` 单条支持 3072 tokens，对长制度更友好，但**必须**让请求带上
`dimensions=1024`，否则会落到 2048：

```dotenv
EMBEDDING_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
EMBEDDING_API_KEY="<你的智谱密钥>"
EMBEDDING_MODEL="embedding-3"
EMBEDDING_DIMENSION="1024"
EMBEDDING_BATCH_SIZE="64"     # 恰好等于智谱的单次数组上限，别再调大
RETRIEVAL_USE_LLM="true"
```

**`EMBEDDING_BATCH_SIZE` 不要超过 64**：智谱 `embedding-3` 单次数组上限就是 64 条，
调大会直接 400。

### schema 迁移（两条路线都需要，因为当前是 1536）

```sql
UPDATE knowledge_chunk    SET embedding = NULL;
UPDATE knowledge_document SET embedding = NULL;
ALTER TABLE knowledge_chunk    ALTER COLUMN embedding TYPE vector(1024);
ALTER TABLE knowledge_document ALTER COLUMN embedding TYPE vector(1024);
DROP INDEX IF EXISTS idx_knowledge_chunk_embedding_hnsw;
```

### 一行探测脚本

`.buildcheck/rag_zhipu_probe.py` 会读取仓库根 `.env` 里的
`ZHIPU_API_KEY` / `GLM_API_KEY` / `BIGMODEL_API_KEY`（或同名环境变量），
对真实 API 跑三种组合并直接给出该用哪个模型：

```powershell
cd agent-service
.\.venv\Scripts\python.exe ..\.buildcheck\rag_zhipu_probe.py
```

它会明确告诉你 `embedding-3` 不传 `dimensions` 时**实际**返回多少维——
这是唯一能确定该值的方法，因为 2048 是文档默认值而非保证值。
密钥不会打印（只显示尾 6 位）。

---

### 附：本文所有实测结论的复现方式

| 结论 | 复现 |
|---|---|
| 26 chunk / 7 文档；`embed()` 27 次/查询 | 计数 `EmbeddingProvider` 包装后调 `dense_rank` |
| 26/26 embedding 为 NULL、0 个 ANN 索引 | `python .buildcheck/rag_embedding_facts.py`（连 `localhost:5433/skychat`） |
| 各表行数、全部索引、chunk 列结构 | `python .buildcheck/rag_storage_facts.py` |
| 中文分词产出伪词 | `tokenize('密码重置')` |
| rerank 有效信号来自文档级 | `rerank_policy_chunks` + `_phrase_overlap` 对比 |
| `time_range`/`subqueries` 未接线 | `understand_query(...)` 与 `retrieve_policy_evidence` 签名对比 |
| 评测 5 条 / 文档级标签 | `python -m eval.run_eval` |
| evidence 每条=整篇文档 | `run_retrieval(...)` 后逐条比对 `doc_len` 与 `evidence_len` |
