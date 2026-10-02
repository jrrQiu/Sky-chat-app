# AI-Native Service Desk & Agent Platforms — State of the Art

Research report for auditing an in-house LLM service desk built on LangGraph (the `sky-chat-app` agent service).
All claims below are anchored to primary sources (official docs, specs, model cards, or vendor documentation repos).
Anything not verifiable from a primary source is explicitly marked **`unverified`**.

**Access note:** `docs.nvidia.com`, `docs.onyx.app`, `docs.dify.ai`, and `docs.langchain.com` all serve a clean-Markdown
variant of any page when you append `.md`. `raw.githubusercontent.com` was not reachable from this environment for
fetch, but ServiceNow's documentation is published as raw markdown under `github.com/ServiceNow/ServiceNowDocs`, and
that path *was* reachable.

---

## Part 1 — Capability Inventory

### 1. Retrieval quality: hybrid search, reranking, rewriting, chunking, filters, freshness

| Mechanism | What leading systems expose | Source |
|---|---|---|
| Hybrid (semantic + keyword) | Dify exposes **Vector Search, Full-Text Search, and Hybrid Search** as three discrete retrieval strategies; Hybrid "combines full-text search and vector search, performing both simultaneously" with a **Weight settings** control for "semantic priority and keyword priority" (weight 1 = semantic only, weight 1 = keyword only, or custom ratio). | [Dify — index methods](https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods) |
| Reranking | Dify ships **Rerank Model disabled by default** per retrieval strategy, third-party, token-billed; **TopK (default 3)** and **Score Threshold (default 0.5)** "are only effective during the Rerank phase" — i.e. without a rerank model, top-k/threshold do nothing. Multimodal embeddings require a multimodal rerank model or images are dropped. | [Dify — index methods](https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods) |
| Reranking (managed) | Bedrock Knowledge Bases support "a reranking model to retrieve more relevant sources" as a documented test-time option. | [AWS — test a knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-test.html) |
| Query decomposition | Bedrock **managed** knowledge bases offer agentic retrieval that will "decompose complex queries into sub-queries and iteratively retrieve" via `AgenticRetrieveStream`. | [AWS — test a knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-test.html) |
| Metadata filters | Bedrock exposes "optional metadata filters to specify which documents in your data source can be used." Onyx's Search UI filters by time range/offset, authors, and tags. | [AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-test.html), [Onyx](https://docs.onyx.app/overview/core_features/internal_search.md) |
| Freshness model | Glean documents three concrete retrieval modes — **Indexed**, **Live retrieval (query-time fetching)**, and **Hybrid** — with per-source justification (email/calendar/chat default to live because "minutes-to-hours freshness dominates"), and explicitly warns that crawls, incremental updates and webhooks must be healthy "so high-churn systems (mail, storage, ticketing, chat) are not stale beyond what you expect." | [Glean — connectors](https://docs.glean.com/connectors/connectors-power-glean) |
| Freshness (RAG platform) | Onyx states "Documents, metadata, and access permissions are all kept up to date in near real time." | [Onyx — search](https://docs.onyx.app/overview/core_features/internal_search.md) |

**Audit questions this raises for us:** is reranking enabled or merely available? If top-k/threshold are only applied
in the rerank stage (as Dify documents), a system without rerank silently ignores its own score threshold. Do we have a
documented freshness bound per source, or only a re-index schedule?

**RAGFlow (gap):** RAGFlow is widely described as offering DeepDoc layout parsing, template/naive chunking, hybrid
recall with a `vector_similarity_weight`, and configurable rerank models, and its repo contains a retrieval-test guide.
I could **not** fetch RAGFlow's own documentation from this environment (`ragflow.io/docs/dev/` returned an empty body;
GitHub blob/raw fetches failed) and DeepWiki returned HTTP 429, so **all RAGFlow-specific capability claims are
`unverified` here.** Treat RAGFlow as a coverage gap and re-check against `ragflow.io/docs` and the repo's own
`docs/guides/` directory.

### 2. Permission-aware retrieval: enforcing per-user ACLs at retrieval time

This is where the platforms are most explicit, and where the warnings are most useful.

- **Azure AI Search** documents four enforcement approaches and — critically — the exact place enforcement happens.
  With **security filters**, "Your application passes in a user or group identity as a string, which populates a filter
  on a query, excluding any documents that don't match on the string." With native **POSIX-like ACL / RBAC scopes
  (preview)**, the caller's Entra token is attached as the `x-ms-query-source-authorization` request header and the
  service compares token claims "to the permission metadata stored alongside indexed documents." Azure also offers
  **Purview sensitivity labels (preview)** and **SharePoint in Microsoft 365 ACLs (preview)**.
  ([Azure — document-level access control](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))
- **The permission-staleness failure mode, stated by the vendor:** "Permission changes in the source system … are only
  reflected in search results **after that metadata is synchronized to the index** through the source-specific
  mechanism, for example, a subsequent indexer run, a push-API update, or a Purview-driven refresh." For SharePoint,
  ACL changes on uniquely-permissioned items are picked up incrementally per indexer run, but "changes inherited from
  parent scopes (site, library, list, or folder) **require an explicit refresh**."
  ([Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))
- **Feature-interaction trap:** for indexed knowledge sources, `ingestionPermissionOptions` cannot be combined with
  `assetStore`, so image serving is unavailable when native document-level permission ingestion is on. Purview label
  enforcement "is limited to single-tenant scenarios and requires RBAC authentication," and during preview autocomplete
  and suggest APIs are unavailable on Purview-enabled indexes. If a skillset chunks documents, ACL fields must be
  **projected onto each chunk row** or "chunk-level references aren't filtered."
  ([Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))
- **Onyx** enforces at the retrieval boundary and says so plainly: "Onyx's context retrieval respects user level
  permissions **which is only configurable via the Enterprise Edition**." Document visibility is governed separately
  from admin RBAC by connector access settings — **Private**, **Public**, or **Auto Sync Permissions** ("Restricted
  using permissions synced from the source system") — and the docs warn: "a user can have a management permission
  without automatically seeing every document in search."
  ([Onyx — permissions](https://docs.onyx.app/admins/permissions/understanding_permissions.md),
  [Onyx — access controls](https://docs.onyx.app/security/architecture/access_controls.md))
- **Glean** frames this as *permission mirroring* and extends it past search: the connector "also ingests the
  permission model from that source (ACLs, group memberships, role assignments). At query time, Glean evaluates the
  signed-in user's identity against those mirrored permissions before returning any results." Enforcement is claimed to
  apply uniformly to search results, AI answers and citations, **Agents and actions** ("Agents execute with the
  identity and permissions of the signed-in user who triggered the run, **regardless of who created or published the
  agent**"), **MCP servers**, and embedded integrations. Notably: "When a user shares a chat conversation, each
  recipient still only sees the sources they're individually authorized to access."
  ([Glean — security principles](https://docs.glean.com/security/security-principles))
- **Azure's stated benefit over app-side filtering** is worth noting as a design argument: it "Eliminates custom
  permission code: You don't need to implement nested group resolution, multilevel ACL traversal, or post-query
  trimming in your application" and "Filtering inside the search pipeline is faster than loading larger result sets
  into your application and trimming there."
  ([Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))

**Audit questions:** where exactly is our ACL predicate applied — pre-retrieval filter, post-retrieval trim, or not at
all? Who can *author* a knowledge document, and does the authoring path set the ACL? Are chunk rows carrying the ACL, or
only parent rows?

### 3. Answer quality controls: citations, abstention, grounding checks, hallucination detection

- **Citation/attribution.** Glean: "For grounded answers, Glean typically surfaces citations (for example, deep links)
  so users can open sources and verify," while honestly bounding the guarantee — "Citation coverage can vary by surface,
  workflow, and whether the model returns a grounded response; not every short or non-retrieval reply includes the same
  citation pattern." Glean also ties citation to permission: citations "link to source documents that the user already
  has permission to open."
  ([Glean](https://docs.glean.com/connectors/connectors-power-glean),
  [Glean security](https://docs.glean.com/security/security-principles))
- **Grounding/hallucination detection as a service.** Azure AI Content Safety **Groundedness detection** checks whether
  a response "is based on your provided source material." It exposes two modes — **Non-Reasoning** (binary
  grounded/ungrounded, for online latency) and **Reasoning** (segment-level explanations for debugging) — plus **domain**
  (`MEDICAL` / `GENERIC`) and **task** (`Summarization` / `QnA`) selectors. The API includes an optional **correction**
  feature returning `correctedText`. Documented limits: **English only**, region-limited, rate-limited.
  ([Azure — groundedness detection](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/groundedness))
- **"I don't know."** Zammad's knowledge-base suggestion path has an explicit abstention surface: suggestions appear
  "if they meet the admin-configured **score threshold**," and "If no suggestions are available, the message
  '**No suggestions.**' is displayed instead." Zammad also ships an anti-duplication check — if a similar KB answer
  exists, "Zammad shows it in a dialog before creating a new one."
  ([Zammad — AI features](https://next.zammad.org/en/documentation/use/guides/ai.html))
- **Grounding checks inside guardrails.** NeMo Guardrails supports writer-side checks as *configurable flows*:
  `self check output`, `self check facts`, and `self check hallucination`.
  ([NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))
- **Disclosure of AI generation.** Zammad marks generated knowledge-base answers with "a note in the content and a tag
  (`ai-generated`)," sets the human as the answer's author, and creates the answer as a **draft** rather than
  publishing it.
  ([Zammad](https://next.zammad.org/en/documentation/use/guides/ai.html))

### 4. Agent orchestration: single vs multi, planning, tool routing, retries, timeouts, durability, resumability

LangGraph is unusually precise here, and its own docs double as the failure-mode list.

- **Two persistence systems, two scopes.** Checkpointers persist graph state snapshots scoped to *a single thread*
  (conversation continuity, HITL, time travel, fault tolerance); **Stores** persist "application-defined key-value data"
  *across threads* (user preferences, facts, shared knowledge). `thread_id` in graph config is the access pattern.
  ([LangGraph — persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
- **Durability caveat the docs call out by name:** `MemorySaver`/`InMemorySaver` "store checkpoints in RAM. When the
  process restarts, all checkpoints are lost." Recommended production path is `PostgresSaver` (or local `SqliteSaver`).
  ([LangGraph — persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
- **Retries are per-node and exception-aware.** `RetryPolicy(max_attempts=3, …)` on `add_node`; defaults are
  `initial_interval=0.5`, `backoff_factor=2.0`, `max_interval=128.0`, `jitter=True`; `default_retry_on` retries everything
  *except* `ValueError`, `TypeError`, `ArithmeticError`, `ImportError`, `LookupError`, `NameError`, `SyntaxError`,
  `RuntimeError`, `ReferenceError`, `StopIteration`, `StopAsyncIteration`, `OSError`, and for `requests`/`httpx` only
  retries 5xx.
  ([LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
- **Timeouts are async-only and have two flavors.** `run_timeout` is "a hard wall-clock cap on a single attempt… never
  refreshed"; `idle_timeout` is "a progress-resetting cap" refired when the node stops making observable progress.
  Documented limitation: "sync nodes with a `timeout` are rejected at compile time." Timeout raises `NodeTimeoutError`,
  "clears any writes from the failed attempt," and hands control to the retry policy.
  ([LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
- **Error handlers / compensation.** `error_handler=` (requires `langgraph>=1.2`) runs after retries are exhausted and
  can return `Command(update=…, goto=…)` to implement Saga compensation. It receives a typed `NodeError(node, error)`.
  Guardrail: `interrupt()` raised inside a node "is **not** routed to the error handler" — it uses `GraphBubbleUp` and
  bypasses retries and handlers.
  ([LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
- **Graph-wide defaults + precedence.** `set_node_defaults(retry_policy=…, error_handler=…, timeout=…)`; per-node values
  override. Error-handler nodes are excluded from `error_handler` and `cache_policy` defaults ("Handlers must never catch
  themselves"). **Defaults set on a parent graph are not inherited by subgraphs.**
  ([LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
- **Graceful shutdown.** `RunControl().request_drain("sigterm")` stops the run *between supersteps*, saves a resumable
  checkpoint, and raises `GraphDrained`; resume with `invoke(None, config)`. Drain "never preempts work that is already
  running," and "`request_drain()` does not cancel running asyncio tasks or kill threads."
  ([LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
- **Unbounded-growth caveat, stated in the docs:** "Over long conversations, checkpoints accumulate. This can increase
  latency and storage costs." The documented fix is to "Prune old checkpoints periodically or set a retention policy."
  Also documented: `thread_id` is stored in a length-limited column, so keep it under 255 characters.
  ([LangGraph — persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
- **Subgraph state-visibility caveat:** "When a subgraph updates state, the parent graph may not see the changes
  immediately. This is because each subgraph manages its own checkpoint namespace."
  ([LangGraph — persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
- **Multi-agent / orchestration at the platform layer.** Glean positions Agents as "multi-step workflows that plan and
  execute over enterprise context," with a documented runtime sequence: understand the request → choose data path
  (index / live / hybrid) → retrieve in parallel "enforcing source permissions on all retrieved items" → plan and act →
  generate output "and record any side effects from tools."
  ([Glean](https://docs.glean.com/connectors/connectors-power-glean))
- **Loop-control primitives at the platform layer.** Dify ships a distinct **Loop** node — "Execute repetitive workflows
  with progressive refinement" — separate from **Iteration** ("Process arrays by applying workflows to each element"),
  which is the visible knob for bounded vs unbounded iteration.
  ([Dify docs index](https://docs.dify.ai/_llms/en/cloud.md))

### 5. Human-in-the-loop: approval, editing output before send, escalation, handoff

- **LangGraph's contract is explicit and has sharp edges.**
  `interrupt(value)` saves state and "waits indefinitely until you resume execution with a response"; resume via
  `Command(resume=…)` whose value "becomes the return value of the `interrupt()` call." Payload surfaces under
  `stream.interrupts` (event streaming v3) or `result["__interrupt__"]` (default `invoke`); `stream.interrupted` is the
  pause flag. ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts))
- **Non-obvious semantics that break naive implementations:**
  - "the runtime restarts the entire node from the beginning—it does not resume from the exact line where `interrupt` was
    called. This means any code that ran before the `interrupt` will execute again."
  - Multiple interrupts in one node are matched **strictly index-based**, so conditionally skipping or reordering
    `interrupt()` calls causes mismatch.
  - "**Avoid `while True` + `interrupt()` loops inside a single node**" — each resume replays all prior iterations,
    producing "exponential re-execution." The documented correct pattern is one `interrupt()` per node invocation plus a
    conditional edge.
  - "Do not wrap `interrupt` calls in try/except" — it pauses by raising a special exception.
  - "`Command(resume=...)` is the **only** `Command` pattern intended as input to `invoke()`."
  ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts))
- **Side-effect ordering is a documented rule, not a suggestion:** "Side effects called before `interrupt` must be
  idempotent… you might have an API call to update a record inside of a node. If `interrupt()` is called after that call
  is made, it will be re-run multiple times when the node is resumed, potentially overwriting the initial update or
  creating duplicate records." Recommended: upsert semantics, side effects *after* the interrupt, or separate nodes.
  ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts))
- **Approval, review-and-edit, and interrupt-inside-tools are first-class documented patterns.**
  `approve/reject` via `Command(goto=…)` branching; **review and edit state** where the resume value replaces
  `generated_text`; and placing `interrupt()` *inside the tool function* so "the tool itself pause[s] for approval
  whenever it's called, and allows for human review and editing of the tool call before it is executed" — with the
  resume payload able to **override tool arguments** (`response.get("to", to)`).
  ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts))
- **Static breakpoints are explicitly *not* the HITL mechanism:** "Static interrupts are **not** recommended for
  human-in-the-loop workflows. Use the `interrupt` function instead."
  ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts))
- **Dify's Human Input node** makes the same pattern declarative and adds operational semantics worth copying:
  delivery via **web app** or **email** ("Anyone with the link can respond, no Dify account required"); form fields
  **Paragraph / Select / Single File / File List** with per-type upload limits (10 MB images, 15 MB documents, 50 MB
  audio, 100 MB video, max 10 files) and "The form's action buttons stay disabled until all mandatory fields are filled";
  decision buttons routing to different branches, exposed downstream as `__action_id` / `__action_value`; rendered
  content as `__rendered_content`; and a **timeout strategy defaulting to 3 days** with a dedicated timeout branch —
  "If no timeout branch is connected, the workflow ends." Crucially: "**The request closes after the first response
  regardless of delivery method.**"
  ([Dify — Human Input](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input))
- **Editing model output before send** is documented in Dify's worked example: the reviewer sees the AI draft rendered
  from the upstream LLM's `text` variable, edits it in a Paragraph field pre-filled with that same variable, and the
  branch **Apply Edit** returns the reviewer's edited content while **Approve** returns the original draft.
  ([Dify — Human Input](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input))
- **Write-action approval as a platform default.** Glean: "Write actions in agents running in the Glean web app pause
  for user approval before executing. When a write step starts, the user sees a confirmation panel showing the target
  application and the planned change. The user can review, edit (for actions that support inline editing), and then
  approve or cancel." Admins can opt specific actions into **Run without user confirmation**, and the docs are explicit
  that this "is an explicit opt-in to remove the confirmation step, not the default." Scope boundary: this default does
  *not* apply to read-only actions, and "doesn't change behavior in Slack or Microsoft Teams."
  ([Glean — security principles](https://docs.glean.com/security/security-principles))
- **Agent/action-level approval policy.** Glean lets admins "Restrict which tools run automatically versus
  human-in-the-loop approval."
  ([Glean](https://docs.glean.com/connectors/connectors-power-glean))
- **Escalation to a human.** ServiceNow documents **sensitive topic filters** for Virtual Agent that "redirect users
  when subject matter is detected that should be handled by a human agent or HR case."
  ([ServiceNow — configure security controls](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md))
- **Analogue in traditional desks.** Zammad's AI *agents* behave like colleagues for concurrency purposes: running AI
  agents "are displayed like other agents in the live user section in the bottom bar. This helps to avoid duplicate work
  and losing unsaved changes," and a running agent changes the overview status circle. Zammad also emits a ticket
  history entry naming the AI agent that applied changes.
  ([Zammad — AI features](https://next.zammad.org/en/documentation/use/guides/ai.html))

### 6. Memory & personalization: summarisation, long-term memory, profile facts, retention

- **LangGraph draws the line explicitly:** checkpointers = "short-term, thread-scoped memory"; stores = "long-term,
  cross-thread memory" for "user preferences, facts, and shared knowledge." Reads/writes happen "from nodes or
  application code."
  ([LangGraph — persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
- **Cross-boundary memory has a documented hook:** use "shared state via Store for data that needs to cross graph
  boundaries."
  ([LangGraph — persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
- **Retention is explicitly the operator's job at the checkpoint layer** — "Prune old checkpoints periodically or set a
  retention policy," with the example of a cron job deleting checkpoints older than N days.
  ([LangGraph — persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
- **Conversation summarisation as a product feature.** Zammad generates a **ticket summary** on update/open with
  configurable sections: Customer intent, Conversation summary, Open questions (optional), Upcoming events (optional),
  Customer sentiment (optional) — positioned for "many hand-overs between agents."
  ([Zammad — AI features](https://next.zammad.org/en/documentation/use/guides/ai.html))
- **Named long-term memory product.** Chatwoot ships **Captain Memories** as a discrete capability.
  ([Chatwoot help centre — Captain](https://www.chatwoot.com/hc/user-guide/articles/1788726513-captain))
- **Memory in the graph editor.** Dify has **Variable Assigner** for "persistent conversation variables in Chatflow
  applications" and **Conversation Variables** semantics.
  ([Dify docs index](https://docs.dify.ai/_llms/en/cloud.md))

**Not verified:** I found no primary-source description of a *per-user profile-fact store with user-visible
edit/delete controls* in the systems surveyed. Treat "profile facts memory" as **`unverified`** as a current
state-of-the-art expectation; LangGraph Stores is the mechanism, but the governance UX is not documented by these vendors.

### 7. Tool/action layer: registry, schema validation, idempotency, MCP, sandboxing, least privilege

- **MCP primitives.** Servers offer **Resources** (context/data), **Prompts** (templated messages/workflows), and
  **Tools** (functions for the model to execute); clients offer **Sampling**, **Roots**, and **Elicitation**. Base
  protocol is JSON-RPC 2.0 over *stateful* connections with **server and client capability negotiation**; additional
  utilities include configuration, progress tracking, cancellation, error reporting and logging. The spec is explicit
  that its own canonical requirements derive from the TypeScript schema, and that MUST/SHOULD carry RFC 2119/8174 meaning.
  ([MCP spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18))
- **MCP's stated security posture — read this as the tool-layer threat model.** Key principles: users "must explicitly
  consent to and understand all data access and operations"; "Tools represent arbitrary code execution and must be
  treated with appropriate caution. In particular, **descriptions of tool behavior such as annotations should be
  considered untrusted, unless obtained from a trusted server**"; "Hosts must obtain explicit user consent before
  invoking any tool"; and users "must explicitly approve any LLM sampling requests" with control over "the actual prompt
  that will be sent." The spec admits the limit honestly: "While MCP itself cannot enforce these security principles at
  the protocol level, implementors **SHOULD**… Build robust consent and authorization flows into their applications."
  ([MCP spec](https://modelcontextprotocol.io/specification/2025-06-18))
- **Tool registry surfaces.** Onyx exposes tool administration as APIs — `Create Custom Tool`, `Update Custom Tool`,
  `Delete Custom Tool`, **`Validate Tool`**, `List Tools`, `List OpenAPI Tools`, `Get Custom Tool` — plus admin pages for
  **MCP** and **OpenAPI** actions, and a separate **LLM Gateway** for routing LLM requests "through the models and access
  controls configured in Onyx." A dedicated `Validate Tool` endpoint is the notable primitive: tool definitions are
  validated *before* they are registered.
  ([Onyx developer docs index](https://docs.onyx.app/llms.txt))
- **Sandboxing.** Onyx runs a sandboxed Python runtime for the LLM and documents deployment guidance for running Code
  Interpreter "on restricted clusters and OpenShift, with dedicated nodes and capacity limits." Glean documents an
  **Agent Sandbox and Programmatic Tool Calling (PTC)** security page.
  ([Onyx](https://docs.onyx.app/llms.txt), [Glean security index](https://docs.glean.com/security/security-principles))
- **Least privilege / identity binding.** Glean: agents run with the triggering user's identity, and "Glean never grants
  broader access than the source system provides" — this is the least-privilege statement to benchmark against.
  ([Glean — security principles](https://docs.glean.com/security/security-principles))
- **Per-tool action policy.** Glean lets admins "Enable or disable native tools per connector," and "Restrict which
  tools run automatically versus human-in-the-loop approval."
  ([Glean](https://docs.glean.com/connectors/connectors-power-glean))
- **Write-back is explicitly scoped:** Glean documents "write-back through tools" as the mechanism that "extend[s]
  Glean from read-heavy search and chat into action," with behavior conditional on admin configuration — "it is not
  implied that Glean passively watches every field change without those workflows."
  ([Glean](https://docs.glean.com/connectors/connectors-power-glean))
- **Idempotency.** The strongest primary-source statement is LangGraph's: side effects before `interrupt()` **must** be
  idempotent or they re-run on resume. Idempotency of *write tools* as a general platform feature is otherwise
  **`unverified`** across these vendors — none of the platform docs I fetched promise server-side idempotency keys.
  ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts))
- **Schema validation.** MCP tools carry JSON Schema via the canonical `schema.ts`; OpenAI-compatible custom actions in
  Onyx are validated through the explicit `Validate Tool` API. **`unverified`** as to whether any platform enforces
  output-schema validation on tool *results*.
  ([MCP spec](https://modelcontextprotocol.io/specification/2025-06-18), [Onyx](https://docs.onyx.app/llms.txt))

### 8. Guardrails/security: prompt injection, PII, jailbreak, output filtering, tenant isolation, audit of model I/O

- **Rail taxonomy (the cleanest public model).** NeMo Guardrails organises controls by *trigger point*, which is a better
  mental model than a flat list:
  | Category | Trigger point | Purpose |
  |---|---|---|
  | Input rails | when user input is received | validate, filter, or modify user input |
  | Retrieval rails | after RAG retrieval completes | process retrieved chunks |
  | Dialog rails | after canonical form is computed | control conversation flow |
  | Execution rails | before/after action execution | control custom action calls |
  | Output rails | when the LLM generates output | validate, filter, or modify bot responses |
  ([NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))
- **Concrete built-in flows** named in the docs: `self check input`, `jailbreak detection heuristics`,
  `mask sensitive data on input`; `self check output`, `self check facts`, `self check hallucination`,
  `mask sensitive data on output`, `check output sensitive data`; `check retrieval sensitive data`; `check tool input`,
  `check tool output`. PII-driven flows are configured by **entity lists** (`PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`,
  `CREDIT_CARD`) separately for input and output. Note the **retrieval rail** and the **execution rails** — a retrieved
  chunk is a first-class subject of policy, as is a tool call's input and output.
  ([NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))
- **Streaming-aware output rails and a documented race-condition warning.** NeMo supports
  `rails.output.streaming: {enabled, chunk_size, context_size, stream_first}`. On parallel rails it warns: "Input rail
  mutations can lead to erroneous results during parallel execution because of race conditions arising from the execution
  order and timing of parallel operations. This can result in output divergence compared to sequential execution."
  ([NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))
- **Cost/latency trade-off made explicit as "speculative generation."** Input rails and the main LLM call start
  concurrently; if the input rail wins and says unsafe, the LLM call is cancelled; if the LLM finishes first, "the engine
  waits for the input-rail verdict. If unsafe, the generated response is discarded." The docs quantify the bet: "Assuming
  a 2% rate of unsafe requests, the remaining 98% of safe requests will hide the input-rail latency… The cost of this
  latency saving is that tokens for the 2% of unsafe requests will be generated and then discarded." Output rails
  "always run after the main LLM completes."
  ([NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))
- **Classifier-based safety with published categories and rates.** Llama Guard 3-8B is "a Llama-3.1-8B pretrained model,
  fine-tuned for content safety classification," usable for both "LLM inputs (prompt classification)" and "LLM responses
  (response classification)"; it emits a safe/unsafe verdict plus violated categories, and "In order to produce
  classifier scores, we look at the probability for the first token, and use that as the 'unsafe' class probability. We
  can then apply **score thresholding** to make binary decisions." It predicts **14 categories** (S1–S14, MLCommons
  taxonomy + Code Interpreter Abuse), supports 8 languages, and was "optimized to support safety and security for search
  and code interpreter tool calls." Reported English response-classification F1 0.939 at FPR 0.040.
  ([Llama Guard 3-8B model card](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/Llama-Guard3/8B/MODEL_CARD.md))
- **Llama Guard's own stated limitations matter for a service desk:** S5 Defamation, S8 IP, and S13 Elections "may
  require factual, up-to-date knowledge to be evaluated"; deploying it "might increase refusals to benign prompts (False
  Positives)"; and "as an LLM, Llama Guard 3 may be susceptible to adversarial attacks or prompt injection attacks that
  could bypass or alter its intended use."
  ([Llama Guard 3 model card](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/Llama-Guard3/8B/MODEL_CARD.md))
- **Commercial runtime guardrail with logging-before-blocking as documented practice.** ServiceNow's **AI Guardian**
  "monitors requests sent to large language models and their responses… It detects offensive or harmful content, prompt
  injection attempts, and sensitive subjects, and can log or block detected content depending on your configuration."
  Configuration steps are enumerated: review Guardian behaviour, configure the Guardrail service provider, activate
  offensiveness protection, configure prompt-injection protection, configure sensitive-topic filters, and enable Guardian
  for AI agents. The operational advice is notable: "**Configure Guardian to log before enabling blocking.** Reviewing
  logs from your test runs will help you understand what your agent's interactions look like before deciding whether to
  block content." There is also an **AI Guardian analytics** surface tracking how often offensive content and prompt
  injection attempts are detected, plus log export.
  ([ServiceNow — AI threat protection](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/australia/markdown/platform-security/naai-threat-protection.md),
  [ServiceNow — configure security controls](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md))
- **Architectural boundary worth copying from ServiceNow:** "AI Guardian monitors prompts sent to large language models
  and their responses. It operates **independently of your ACL and user identity configuration** from Phase 3 — it is a
  **runtime layer, not an access layer**." This is the correct separation: guardrails do not substitute for ACLs.
  ([ServiceNow — configure security controls](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md))
- **PII de-identification before the model.** ServiceNow documents privacy policies "to control how PII is
  de-identified **before it reaches the large language model**," owned by a designated **data steward** role, plus
  explicit data-sharing opt-out.
  ([ServiceNow — configure security controls](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md))
- **Tenant isolation.** Glean: "Connector-fetched data for indexing is routed into your isolated Glean tenant… At rest,
  indexed data in the tenant is encrypted and remains within your tenant boundaries relative to other customers' data."
  Azure's Purview label enforcement "is limited to single-tenant scenarios."
  ([Glean](https://docs.glean.com/connectors/connectors-power-glean),
  [Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))
- **Audit of model I/O and admin action.** Onyx documents **Audit Logs & SIEM Integration** ("Forward Onyx's structured
  audit-event stream to your SIEM") and **Query History**. Glean documents admin audit logs covering "changes to
  connectors, roles, global configuration, agent subscriptions, and **action executions**," with filtering by
  time-range/user/action-type and CSV export or SIEM streaming, plus separate **customer event logs** — "a structured
  record of user-facing events (searches, chats, workflow runs, **citation clicks**)."
  ([Onyx](https://docs.onyx.app/llms.txt), [Glean — security principles](https://docs.glean.com/security/security-principles))
- **Onyx runtime hardening.** A dedicated **Security & Hardening** page covers "authentication, passwords, admin
  controls, and outbound network safety" — the last being relevant to exfiltration via agent-initiated egress.
  ([Onyx](https://docs.onyx.app/llms.txt))

**See also the shared taxonomy in §9 of Part 2** for the OWASP identifiers that map onto these rails.

### 9. Evaluation & quality ops: offline sets, golden datasets, LLM-as-judge, CI gates, RAG metrics, review queues

- **LangSmith's offline/online split and the full evaluator taxonomy.** Offline evals target *examples* in *datasets* with
  reference outputs (benchmarking, regression testing, unit testing, backtesting); online evals target *runs* and
  *threads* from production traces with no references. Evaluator techniques: **Human, Code, LLM-as-judge, Decision model,
  Pairwise**. Reference-free evaluators (safety, format, heuristics, reference-free judge) work in both modes;
  reference-based ones (correctness, factual accuracy, exact match) are offline-only.
  ([LangSmith — evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts))
- **Concrete CI-gate mechanics.** "Evaluation metrics can be converted into tests. For example, **regression tests can
  assert that new versions must outperform baseline versions** on relevant metrics." Evals can be written with standard
  tooling — `pytest`, Vitest/Jest. Dataset **versions** are created automatically on change and can be **tagged**, and
  the docs recommend targeting specific versions "in CI pipelines to ensure dataset updates don't break workflows."
  ([LangSmith — evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts))
- **Human review queues as a documented product surface.** LangSmith **Annotation queues** come in two types:
  *Single-run queues* ("Review one run at a time against custom rubric items") and *Pairwise queues* ("Compare two runs
  side-by-side to judge which is better"). Features include "configuring multiple reviewers per run, enabling
  reservations to prevent conflicts, and exporting annotated runs directly to datasets for future evaluations." The
  closing loop is stated: "Online evaluations surface issues that become offline test cases."
  ([LangSmith — evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts))
- **Prompt tuning is not optional for LLM judges:** "LLM-as-judge evaluators require careful review of scores and prompt
  tuning. Few-shot evaluators… often improve performance."
  ([LangSmith — evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts))
- **RAG-specific metric names (RAGAS).** Documented metric set includes **Context Precision**, **Context Recall**,
  **Context Entities Recall**, **Noise Sensitivity**, **Response Relevancy**, **Faithfulness**; plus Nvidia metrics
  (**Answer Accuracy**, **Context Relevance**, **Response Groundedness**); plus agent/tool metrics — **Topic Adherence**,
  **Tool Call Accuracy**, **Tool Call F1**, **Agent Goal Accuracy**; plus **Factual Correctness**, **Semantic Similarity**,
  classical NLP metrics (BLEU/CHRF/ROUGE/exact match/string presence), SQL metrics, and general-purpose scorers
  (**Aspect Critic**, **Simple Criteria Scoring**, **Rubrics Based Scoring**, **Instance Specific Rubrics Scoring**).
  RAGAS also ships **test set generation** (synthetic testset generation for RAG and for agents/tool use) and an
  "Align an LLM as a Judge" guide — i.e. it treats judge alignment as a task in its own right.
  ([RAGAS — available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/))
- **Red-team-in-CI as the security regression gate.** promptfoo's documented workflow: generate adversarial inputs →
  evaluate responses with "deterministic and model-graded metrics" → analyze; then run it either as one-off reports or as
  **CI/CD integration** "to catch regressions and anomalies," plus post-deployment monitoring. Its stated application-layer
  threat list is the most useful checklist for a RAG service desk: **indirect prompt injections**, **PII leaks from
  context (e.g. in RAG architectures)**, **tool-based vulnerabilities** (unauthorized data access, privilege escalation,
  SQL injection), **hijacking (off-topic use)**, and **data/chat exfiltration techniques (markdown images, link
  unfurling)**. It names **BOLA** (broken object-level authorization) and **BFLA** (broken function-level authorization)
  plugins explicitly — these are the authorization tests a permission-aware RAG system needs.
  ([promptfoo — red teaming](https://www.promptfoo.dev/docs/red-team/))
- **Cost of red teaming is material and should be budgeted:** "Certain automated attack strategies consume a large number
  of tokens, and a single red team can range anywhere from a few cents to hundreds of dollars!"
  ([promptfoo — red teaming](https://www.promptfoo.dev/docs/red-team/))
- **Adopt-a-convention practice from a real incident.** The Discord/Clyde case study documents the operating rule that
  emerged: "Setting a convention in which **every prompt/workflow change required an evaluation**," and "Making
  evaluations as automatic and frictionless as possible."
  ([promptfoo — red teaming](https://www.promptfoo.dev/docs/red-team/))
- **DeepEval** (as surfaced; **`unverified`** — I did not fetch a primary DeepEval doc page in this session) is positioned
  as "The LLM Evaluation Framework" with pytest-style CI usage.
  ([deepeval.com](https://deepeval.com/))
- **Dify treats retrieval testing as a first-class step**, with a dedicated "Test Knowledge Retrieval" topic and a
  "Learn how to test and **cite** your knowledge base retrieval" pointer.
  ([Dify — index methods](https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods))
- **Observability vendors ship eval eval too.** Langfuse's docs include an Evaluation product alongside Observability and
  Prompt Management, and its observability feature list includes **User Feedback**, **Corrections**, **Comments**, and
  **Datasets**.
  ([Langfuse — prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control),
  [Langfuse — cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking))

### 10. Observability & cost: tracing every step, token/cost per conversation, latency, version pinning, feedback

- **Cost/token accounting with explicit precedence and inference fallback.** Langfuse records, per generation,
  **usage details** ("number of units consumed per usage type") and **cost details** ("USD cost per usage type"), broken
  down by usage type including `input`, `output`, and provider-specific `cached_tokens` / `audio_tokens`. Values may be
  **ingested** (from the LLM response) or **inferred** from a **model definition** storing price per usage type — and
  "When both are available, **ingested values take priority** over inferred ones."
  ([Langfuse — token & cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking))
- **Model matching is regex-based and user overrides win.** Generations match model definitions via `match_pattern`
  ("Uses regular expressions, e.g. `(?i)^(gpt-4-0125-preview)$`"), and "User-defined models take priority over models
  maintained by Langfuse." Custom definitions matter for "self-hosted or fine-tuned models which are not included in the
  list of Langfuse maintained models."
  ([Langfuse — cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking))
- **Tokenizer accuracy caveat straight from the vendor:** "According to Anthropic, their tokenizer is not accurate for
  Claude 3 models. If possible, send us the tokens from their API response." Free-tier inference is a fallback, not a
  source of truth.
  ([Langfuse — cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking))
- **Budgets/alerts as a product feature:** "Set up alerts: get notified automatically when spend crosses a threshold";
  "Query with the Metrics API: retrieve aggregated usage and cost, filtered by application type, user, or tags, **for
  analytics, billing, and rate-limiting**."
  ([Langfuse — cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking))
- **Budgets at the platform layer.** Onyx ships **Spending Limits**: "configure LLM rate limits and throttling
  settings… Rate limits can be applied **globally, by User, or by User Group**," and it exposes API endpoints
  `Get/Create Global Token Limit Settings`, `Update Token Limit Settings`, `Delete Token Limit Settings`. Onyx also has
  **Usage Analytics**, **Query History** (view and export), **Custom Analytics** (configure a custom provider), and
  **Tracing** ("Send LLM call traces to observability platforms").
  ([Onyx — spending limits](https://docs.onyx.app/admins/advanced_configs/spending_limits.md),
  [Onyx developer index](https://docs.onyx.app/llms.txt))
- **Model-level access control as a cost/security control.** Onyx documents **Language Model Access Controls** —
  "Configure access controls for language model providers" — and an **LLM Gateway** that routes "application LLM requests
  through the models and access controls configured in Onyx." This is the centralization pattern: one gateway, one budget
  and access policy, rather than per-service keys.
  ([Onyx developer index](https://docs.onyx.app/llms.txt), [Onyx — access controls](https://docs.onyx.app/security/architecture/access_controls.md))
- **Per-credential rate limiting on knowledge.** Dify documents **Knowledge Request Rate Limit**, **Knowledge Data
  Storage Limit** ("What counts toward your plan's knowledge storage, what happens when you reach the limit, and how to
  free space"), and **AI credits** accounting for Captain in Chatwoot.
  ([Dify docs index](https://docs.dify.ai/_llms/en/cloud.md),
  [Chatwoot — Captain](https://www.chatwoot.com/hc/user-guide/articles/1788726513-captain))
- **Feedback capture as a first-class observability feature.** Langfuse lists **User Feedback** and **Corrections** as
  observability features, and **Sessions**, **User Tracking**, **Environments**, **Tags**, **Metadata**, and **Releases &
  Versioning** as grouping/tracing dimensions. Onyx exposes `like/dislike`-style agent analytics via **View Agent
  Analytics**, scoped so users see analytics only for agents they own unless they hold **Manage Agents**.
  ([Langfuse](https://langfuse.com/docs/observability/features/token-and-cost-tracking),
  [Onyx — permissions](https://docs.onyx.app/admins/permissions/understanding_permissions.md))
- **Latency.** LangSmith records "latency metrics" on runs and recommends using heuristics to "Identify interesting runs
  (e.g., long latency, errors)." LangGraph exposes `execution_info` with `node_attempt`, `node_first_attempt_time`,
  `thread_id`, `run_id`, `checkpoint_id`, `task_id` — enough to build per-node latency and retry-rate metrics.
  ([LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts),
  [LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
- **Prompt/version pinning is how you attribute cost to a version.** Langfuse's prompt labels (below) plus **Releases &
  Versioning** and **Tags** are the documented mechanism.
  ([Langfuse — cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking))

**OpenTelemetry gap — explicit `unverified`.** I attempted to fetch the OTel GenAI semantic conventions
(`https://opentelemetry.io/docs/specs/semconv/gen-ai/`). The page redirects (it returned "Moved: Generative AI semantic
conventions"), and the navigation exposed the target sections — `gen-ai-spans`, `gen-ai-metrics`, `gen-ai-agent-spans`,
and a **`Gen AI` attribute registry** under semconv **1.44.0**, alongside a **`MCP` attribute registry** — but I could
not retrieve the normative attribute names through this environment. **Therefore: do not treat any specific
`gen_ai.*` attribute name in this report as verified.** The durable, verifiable fact is that OTel maintains GenAI
semantic conventions covering spans, metrics, agent spans, and a dedicated MCP attribute namespace, under version
1.44.0. Verify exact names against the semconv attribute registry before implementing.
([OpenTelemetry semconv — Gen AI](https://opentelemetry.io/docs/specs/semconv/gen-ai/),
[GenAI agent spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/),
[Gen AI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/))

### 11. Prompt & model lifecycle: versioning, canary/A-B, fallback, caching, streaming

- **Versions + labels as the deployment primitive.** Langfuse: "Each prompt version is automatically assigned a
  `version ID`. Additionally, you can assign `labels` to follow your own versioning scheme. Labels can be used to assign
  prompts to **environments (staging, production), tenants (tenant-1, tenant-2), or experiments (prod-a, prod-b)**."
  Deployment = "assign the label `production`… to that prompt version." The special `latest` label "points to the most
  recently created version."
  ([Langfuse — prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control))
- **Fail-loud label resolution (a genuinely good default to copy):** "When using a prompt without specifying a label,
  Langfuse will serve the version with the `production` label. If no version carries the requested label, the request
  fails with `404 Not Found`. **Langfuse never silently falls back to `production` or `latest`.**"
  ([Langfuse — prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control))
- **Canary by label; A/B as a product feature.** Langfuse ships a dedicated **A/B Testing** feature page under prompt
  management, alongside **Playground**, **Prompt Experiments**, **Caching** (prompt-level), **Composability**,
  **Variables**, **Message Placeholders**, **Config**, **Webhooks**, **GitHub Integration**, **Folders**, and
  **Guaranteed Availability**.
  ([Langfuse — prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control))
- **App-level versioning in the builder.** Dify documents **Version Control** for apps and **Run History** for
  debugging, plus **Annotation System** — "Build a curated library of high-quality responses to improve consistency and
  **bypass AI generation**" (a documented cache-like fast path).
  ([Dify docs index](https://docs.dify.ai/_llms/en/cloud.md))
- **Prompt-adjacent configuration in Onyx.** **Hook Extensions** let you "Inject custom logic into Onyx's pipeline at
  defined stages without modifying source code" — the extension point for custom retrieval/prompt policy.
  ([Onyx](https://docs.onyx.app/llms.txt))
- **Model fallback.** LangGraph documents an explicit fallback pattern: inspect `runtime.execution_info.node_attempt` and
  "switch to a fallback when the primary call keeps failing" (`if node_attempt > 1: return call_fallback_api()`).
  Provider-level fallback is also implied by Onyx's multi-provider model config (OpenAI, Anthropic, Bedrock, Vertex,
  Azure OpenAI, Ollama, LM Studio, OpenRouter, Bifrost, LiteLLM Proxy, custom OpenAI-compatible), and Onyx ships a
  **LiteLLM Proxy** integration — but **automatic multi-provider failover is `unverified`** in the docs I read.
  ([LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance),
  [Onyx](https://docs.onyx.app/llms.txt))
- **Caching.** LangGraph supports `cache_policy=` per node and via `set_node_defaults` — with the safety rule that
  caching is **not** applied to error-handler nodes ("Caching handler results is unsafe"). Langfuse has prompt caching.
  ([LangGraph — fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance),
  [Langfuse](https://langfuse.com/docs/prompt-management/features/prompt-version-control))
- **Streaming.** LangGraph event streaming v3 exposes `stream.messages` (token deltas via `message.text`),
  `stream.values` (full state snapshots per step), `stream.interrupts`, `stream.interrupted`, and `stream.output`;
  nested subgraph messages are read from `stream.subgraphs[*].messages`. NeMo Guardrails supports streaming output rails
  with `chunk_size`/`context_size` and a `stream_first` option.
  ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts),
  [NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))

### 12. UX for AI answers: streaming, reasoning display, source previews, follow-ups, feedback, regeneration, inline citations

- **Streaming with typed projections.** LangGraph's documented HITL loop streams tokens concurrently with state
  inspection: iterate `stream.messages` → `message.text` for deltas, observe `stream.values`, detect
  `stream.interrupted`, resume with `Command(resume=…)` and loop "until `stream.interrupted` is false."
  ([LangGraph — interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts))
- **Reasoning/thinking display is a *separable* concern, not automatic.** Dify: "Reasoning models emit their thinking
  process alongside the final answer. Referencing the `text` output variable shows both by default. To show only the
  answer, toggle on **Enable Reasoning Tag Separation** for the corresponding LLM node." This is the concrete control
  point for whether chain-of-thought leaks into the user-visible or human-review surface.
  ([Dify — Human Input](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input))
- **Source previews and click-through.** Zammad's **Suggested knowledge** shows "the title of the answer and more details
  on hover," with "an additional relevance score… only shown to users with the corresponding admin permissions," and
  click-to-open in the knowledge base. Glean opens results in the source system, "which may enforce access again at click
  time," and surfaces citations as deep links. Onyx's Search UI gives a document-centric view "better for quickly
  accessing documents when the intent is not to get an answer," and auto-switches based on query classification.
  ([Zammad — AI features](https://next.zammad.org/en/documentation/use/guides/ai.html),
  [Glean](https://docs.glean.com/connectors/connectors-power-glean),
  [Onyx — search](https://docs.onyx.app/overview/core_features/internal_search.md))
- **Regeneration as an explicit user action with a branch.** Dify's review workflow wires a **Regenerate** button to
  "nodes that... loop back to an LLM node to revise the content," distinct from **Approve** (original draft) and
  **Apply Edit** (human-edited).
  ([Dify — Human Input](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input))
- **Inline correction of model output.** Zammad's writing assistant operates on a **text selection** in the editor:
  "you first have to select text you want to apply the changes to," then choose a tool
  (*Expand draft…*, *Fix spelling and grammar*, *Summarize section to about half its current size*, *Rewrite complex
  section and make it easy to understand*). Two operational caveats are documented: "your text gets replaced," with
  `ctrl + z` as recovery; and "**Always double-check the response.** Although the feature was carefully designed, there
  may still be minor problems in individual cases due to the nature of neural networks."
  ([Zammad — AI features](https://next.zammad.org/en/documentation/use/guides/ai.html))
- **Follow-up suggestions / next steps.** Glean ships "Agent tiles, **suggested next steps**, and search results" in
  embedded surfaces, all permission-aware. Zammad's Knowledge base assistant offers a **suggested** answer beside a
  **generated draft** — two distinct affordances.
  ([Glean — security principles](https://docs.glean.com/security/security-principles),
  [Zammad](https://next.zammad.org/en/documentation/use/guides/ai.html))
- **Feedback buttons.** Langfuse captures **User Feedback** and **Corrections** as observability features; LangSmith
  uses "user feedback" as a dataset-construction signal ("Add runs that received negative feedback to test against") and
  offers inline annotation. Onyx exposes agent analytics/feedback.
  ([Langfuse](https://langfuse.com/docs/observability/features/token-and-cost-tracking),
  [LangSmith — evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts),
  [Onyx](https://docs.onyx.app/llms.txt))
- **Structured summary as an agent-facing UX.** Zammad's ticket summary sidebar presents intent / summary / open
  questions / upcoming events / sentiment — a good template for a "case at a glance" panel.
  ([Zammad — AI features](https://next.zammad.org/en/documentation/use/guides/ai.html))
- **Concurrency UX for in-flight automation.** Zammad shows running AI agents in the live-user bar and marks the ticket
  in overviews — directly aimed at "avoid duplicate work and losing unsaved changes."
  ([Zammad — AI features](https://next.zammad.org/en/documentation/use/guides/ai.html))

---

## Part 2 — Minimum Bar for Enterprise

Ranked. "Table stakes" = a serious enterprise deployment is expected to have it today; "differentiator" = distinguishes
a strong deployment.

| # | Capability | Bar | Evidence |
|---|---|---|---|
| 1 | **Durable checkpointing on a real database, not in-memory** | Table stakes | LangGraph states `MemorySaver` "store checkpoints in RAM. When the process restarts, all checkpoints are lost" and prescribes `PostgresSaver` for production. [LG persistence](https://docs.langchain.com/oss/python/langgraph/persistence) |
| 2 | **Retrieval-time ACL enforcement with synced permissions** | Table stakes | Azure makes ACL/RBAC filtering a documented query-time concern via `x-ms-query-source-authorization`; Onyx gates permission-aware retrieval to Enterprise Edition; Glean "never grants broader access than the source system provides." [Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview), [Onyx](https://docs.onyx.app/overview/core_features/internal_search.md), [Glean](https://docs.glean.com/security/security-principles) |
| 3 | **Human approval before any write action, default-on** | Table stakes | Glean: write actions "pause for user approval before executing," and "Run without user confirmation" "is an explicit opt-in to remove the confirmation step, not the default." [Glean security](https://docs.glean.com/security/security-principles) |
| 4 | **Idempotent side effects around interrupts/resumes** | Table stakes | LangGraph: side effects before `interrupt()` "must be idempotent" or they "overwrit[e] the initial update or creat[e] duplicate records." [LG interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) |
| 5 | **Per-node retries, timeouts, and an error/compensation path** | Table stakes | `RetryPolicy` (attempts/backoff/jitter/`retry_on`), `TimeoutPolicy(run_timeout, idle_timeout)`, `error_handler` with `NodeError` and `Command(goto=…)` Saga compensation. [LG fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance) |
| 6 | **Prompt-injection defence that treats retrieved documents as untrusted** | Table stakes | promptfoo names indirect prompt injection and RAG-context PII leaks as application-layer threats; MCP says tool descriptions "should be considered untrusted, unless obtained from a trusted server." [promptfoo](https://www.promptfoo.dev/docs/red-team/), [MCP](https://modelcontextprotocol.io/specification/2025-06-18) |
| 7 | **Token/cost accounting per conversation, with budgets and alerts** | Table stakes | Langfuse records usage+cost per generation per usage type, with alerts "when spend crosses a threshold" and a Metrics API "for analytics, billing, and rate-limiting"; Onyx applies limits "globally, by User, or by User Group." [Langfuse](https://langfuse.com/docs/observability/features/token-and-cost-tracking), [Onyx](https://docs.onyx.app/admins/advanced_configs/spending_limits.md) |
| 8 | **Offline eval datasets + LLM-as-judge** | Table stakes | LangSmith datasets/examples/experiments with Human/Code/LLM-as-judge/Decision-model/Pairwise evaluators. [LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts) |
| 9 | **Regression gate in CI that blocks prompt/model changes** | Table stakes | "regression tests can assert that new versions must outperform baseline versions"; evals runnable via pytest; target a tagged dataset version so "dataset updates don't break workflows." [LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts) |
| 10 | **Citations with click-through, permission-checked at click time** | Table stakes | Glean surfaces deep-link citations and note sources "may enforce access again at click time"; Zammad exposes source title + hover detail + score. [Glean](https://docs.glean.com/connectors/connectors-power-glean), [Zammad](https://next.zammad.org/en/documentation/use/guides/ai.html) |
| 11 | **Feedback capture wired back into eval datasets** | Table stakes | LangSmith: "user feedback: Add runs that received negative feedback to test against"; annotation queues "export annotated runs directly to datasets." [LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts) |
| 12 | **Prompt versioning with environment labels and a production pointer** | Table stakes | Langfuse version+label model ("environments… tenants… experiments"), deployment = label `production`. [Langfuse](https://langfuse.com/docs/prompt-management/features/prompt-version-control) |
| 13 | **Fail-loud prompt resolution (never silent fallback)** | Differentiator | "If no version carries the requested label, the request fails with `404 Not Found`. Langfuse never silently falls back." [Langfuse](https://langfuse.com/docs/prompt-management/features/prompt-version-control) |
| 14 | **Explicit abstraction when evidence is insufficient** | Table stakes | Zammad shows "No suggestions." below a configured score threshold; reviewers get Approve / Apply Edit / Regenerate branches rather than a single accept. [Zammad](https://next.zammad.org/en/documentation/use/guides/ai.html), [Dify](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input) |
| 15 | **Grounding/hallucination check as a runtime gate** | Differentiator | Azure Groundedness detection (binary online mode vs reasoning mode, domain/task selectors, correction field); NeMo `self check facts` / `self check hallucination` output rails. [Azure](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/groundedness), [NeMo](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md) |
| 16 | **Guardrails organised by trigger point, including a retrieval rail and tool rails** | Differentiator | NeMo's five rail categories; `check retrieval sensitive data`; `check tool input` / `check tool output`. [NeMo](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md) |
| 17 | **Log-before-block rollout for every new guardrail** | Differentiator | ServiceNow: "Configure Guardian to log before enabling blocking. Reviewing logs from your test runs will help you understand what your agent's interactions look like before deciding whether to block content." [ServiceNow](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md) |
| 18 | **Guardrails architecturally separated from the access layer** | Differentiator | ServiceNow: AI Guardian "operates independently of your ACL and user identity configuration… it is a runtime layer, not an access layer." [ServiceNow](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md) |
| 19 | **PII de-identified before the prompt reaches the model, with a named owner** | Table stakes | ServiceNow privacy policies "control how PII is de-identified before it reaches the large language model," under an assigned data steward. [ServiceNow](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md) |
| 20 | **Checkpoint retention/pruning policy** | Table stakes | "Over long conversations, checkpoints accumulate. This can increase latency and storage costs" → "Prune old checkpoints periodically or set a retention policy." [LG persistence](https://docs.langchain.com/oss/python/langgraph/persistence) |
| 21 | **Per-user and per-group rate limits on the model gateway** | Table stakes | Onyx spending limits apply "globally, by User, or by User Group," with a central LLM Gateway and LM access controls. [Onyx](https://docs.onyx.app/admins/advanced_configs/spending_limits.md) |
| 22 | **Audit trail of admin actions *and* action executions, exportable to SIEM** | Table stakes | Glean admin audit logs cover "action executions," filterable and CSV-exportable or SIEM-streamed; Onyx ships "structured audit-event stream to your SIEM." [Glean](https://docs.glean.com/security/security-principles), [Onyx](https://docs.onyx.app/llms.txt) |
| 23 | **Red-team suite covering BOLA/BFLA against the RAG and tool layers** | Differentiator | promptfoo ships dedicated BOLA and BFLA plugins, plus indirect-injection and exfiltration coverage, runnable in CI/CD. [promptfoo](https://www.promptfoo.dev/docs/red-team/) |
| 24 | **RAG-specific retrieval metrics tracked over time** | Differentiator | RAGAS context precision/recall, context entity recall, noise sensitivity, response relevancy, faithfulness. [RAGAS](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) |
| 25 | **Human review queues with rubrics, multi-reviewer reservations, dataset export** | Differentiator | LangSmith single-run and pairwise annotation queues. [LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts) |
| 26 | **Documented freshness bound per source (index vs live vs hybrid)** | Differentiator | Glean's three-mode model with per-source freshness rationale and the staleness warning. [Glean](https://docs.glean.com/connectors/connectors-power-glean) |
| 27 | **Streaming that coexists with interrupts and guardrails** | Table stakes | LangGraph v3 projections (`stream.messages` / `values` / `interrupts`); NeMo streaming output rails with `chunk_size`/`context_size`. [LG](https://docs.langchain.com/oss/python/langgraph/interrupts), [NeMo](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md) |
| 28 | **Explicit control over reasoning/thinking visibility** | Differentiator | Dify's "Enable Reasoning Tag Separation" toggle, because the `text` variable "shows both by default." [Dify](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input) |
| 29 | **MCP as the tool-integration boundary, with consent semantics** | Differentiator | MCP primitives + capability negotiation + the explicit "Hosts must obtain explicit user consent before invoking any tool." [MCP](https://modelcontextprotocol.io/specification/2025-06-18) |
| 30 | **Tool definitions validated before registration** | Differentiator | Onyx's `Validate Tool` API endpoint alongside Create/Update/List Tools. [Onyx](https://docs.onyx.app/llms.txt) |
| 31 | **Human-in-the-loop approval also for scheduled/background agents** | Differentiator | Glean: for scheduled-trigger agents, "control shifts to governance: who may create and publish agents, which actions are enabled for autonomous use, and how inputs are constrained." [Glean](https://docs.glean.com/security/security-principles) |
| 32 | **OpenTelemetry GenAI conventions for vendor-neutral traces** | Table stakes (trajectory) | OTel maintains GenAI semconv covering spans, metrics, agent spans and a dedicated MCP attribute namespace (semconv 1.44.0). Exact attribute names **`unverified`** here. [OTel GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) |
| 33 | **Agent/action identity bound to the triggering user** | Table stakes | Glean: agents execute "with the identity and permissions of the signed-in user who triggered the run, regardless of who created or published the agent." [Glean](https://docs.glean.com/security/security-principles) |
| 34 | **Conversation summarisation for handover** | Table stakes | Zammad's ticket summary with intent/summary/open questions/upcoming events/sentiment sections. [Zammad](https://next.zammad.org/en/documentation/use/guides/ai.html) |
| 35 | **Map controls to a published taxonomy** | Table stakes | OWASP LLM Top 10 2025 gives the shared vocabulary: LLM01 Prompt Injection, LLM02 Sensitive Information Disclosure, LLM03 Supply Chain, LLM04 Data and Model Poisoning, LLM05 Improper Output Handling, LLM06 Excessive Agency, LLM07 System Prompt Leakage, LLM08 Vector and Embedding Weaknesses, LLM09 Misinformation, LLM10 Unbounded Consumption. [OWASP index](https://genai.owasp.org/llm-top-10/) |

---

## Part 3 — Common Failure Modes

Each is evidence-backed by a primary source.

1. **In-memory checkpointing in production.** `MemorySaver` "store checkpoints in RAM. When the process restarts, all
   checkpoints are lost," so an interrupted approval becomes unresumable and the run is orphaned.
   ([LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence))
2. **Non-idempotent side effects before `interrupt()`.** Code before the interrupt re-executes on resume, so a
   pre-interrupt API write "will be re-run multiple times when the node is resumed, potentially overwriting the initial
   update or creating duplicate records." ([LangGraph](https://docs.langchain.com/oss/python/langgraph/interrupts))
3. **Node re-entry corrupting interrupt index matching.** Resume "restarts the entire node from the beginning," and
   multiple interrupts are matched "strictly index-based," so conditional or reordered `interrupt()` calls resume with the
   wrong value. ([LangGraph](https://docs.langchain.com/oss/python/langgraph/interrupts))
4. **Exponential re-execution from `while True` + `interrupt()`.** A validation loop inside one node means "the first
   resume replays 1 iteration, the second replays 2, and so on. The result is exponential re-execution."
   ([LangGraph](https://docs.langchain.com/oss/python/langgraph/interrupts))
5. **Swallowing the interrupt exception with a bare `try/except`.** `interrupt()` pauses by raising; wrapping it "will
   catch this exception and the interrupt will not be passed back to the graph" — the pause silently never happens.
   ([LangGraph](https://docs.langchain.com/oss/python/langgraph/interrupts))
6. **ACL leakage via stale permission metadata.** Permission changes "are only reflected in search results after that
   metadata is synchronized to the index," and ACL changes "inherited from parent scopes (site, library, list, or folder)
   require an explicit refresh" — so a user who lost access keeps retrieving the document.
   ([Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))
7. **ACL metadata lost at the chunk level.** If a skillset chunks documents, ACL fields must be projected onto each chunk
   row; "Without this projection, chunk-level references aren't filtered" — chunk vectors leak while parent rows look
   correct. ([Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))
8. **Confusing admin/management permissions with document visibility.** Onyx warns "a user can have a management
   permission without automatically seeing every document in search," and that document visibility is a *separate*
   concern from the group permission list — the classic over-provisioning mistake in the other direction is assuming a
   management grant *does* grant content.
   ([Onyx](https://docs.onyx.app/admins/permissions/understanding_permissions.md))
9. **Permission-aware retrieval gated behind a paid tier, discovered late.** Onyx: "Onyx's context retrieval respects
   user level permissions which is only configurable via the Enterprise Edition," and "Different access to documents is
   only available in the Enterprise Edition." A community build can be silently non-permission-aware.
   ([Onyx](https://docs.onyx.app/overview/core_features/internal_search.md),
   [Onyx access controls](https://docs.onyx.app/security/architecture/access_controls.md))
10. **Prompt injection arriving through retrieved documents.** promptfoo lists "Indirect prompt injections" and "PII
    leaks (from context, e.g. in RAG architectures)" as application-layer threats, and MCP states tool descriptions and
    annotations "should be considered untrusted, unless obtained from a trusted server."
    ([promptfoo](https://www.promptfoo.dev/docs/red-team/), [MCP](https://modelcontextprotocol.io/specification/2025-06-18))
11. **Tool-layer authorization gaps (BOLA/BFLA).** promptfoo ships explicit plugins for "unauthorized access to resources
    belonging to other users" and "actions beyond authorized scope or role," plus markdown-image/link-unfurling
    exfiltration of chat data. These are exactly the tests a write-capable service desk needs.
    ([promptfoo](https://www.promptfoo.dev/docs/red-team/))
12. **Unbounded consumption / cost blowups.** OWASP lists **LLM10:2025 Unbounded Consumption** as its own category;
    promptfoo notes a single red team "can range anywhere from a few cents to hundreds of dollars"; NeMo's
    speculative-generation mode knowingly spends tokens on responses that are later discarded.
    ([OWASP](https://genai.owasp.org/llm-top-10/), [promptfoo](https://www.promptfoo.dev/docs/red-team/),
    [NeMo](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))
13. **Checkpoint table growth degrading latency.** "Over long conversations, checkpoints accumulate. This can increase
    latency and storage costs," with no automatic pruning by default; the docs' answer is an operator-run retention job.
    ([LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence))
14. **`thread_id` exceeding the column limit.** With `PostgresSaver`/`AsyncPostgresSaver` "the `thread_id` is stored in a
    column with limited length," producing a database error; keep IDs under 255 characters.
    ([LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence))
15. **Subgraph state invisibility to the parent graph.** "When a subgraph updates state, the parent graph may not see the
    changes immediately. This is because each subgraph manages its own checkpoint namespace" — a silent data-loss class
    of bug in multi-agent designs. ([LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence))
16. **Assuming defaults propagate into subgraphs.** `set_node_defaults` "is not inherited by subgraphs: each graph
    manages its own defaults independently," so a subgraph compiles with no retry policy or timeout.
    ([LangGraph](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
17. **Timeout configuration silently ineffective on sync nodes.** "Node timeouts only apply to **async** nodes. Sync nodes
    with a `timeout` are rejected at compile time" — meaning a blocking call in a sync node has no enforced ceiling.
    ([LangGraph](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
18. **Retries masking or amplifying non-transient failures.** `default_retry_on` retries on any exception except a
    specific denylist — so an unlisted programmer error or a 4xx-style semantic failure gets retried up to
    `max_attempts` with backoff, multiplying cost and latency.
    ([LangGraph](https://docs.langchain.com/oss/python/langgraph/fault-tolerance))
19. **Treating guardrails as an access-control layer.** ServiceNow draws the line explicitly: AI Guardian "operates
    independently of your ACL and user identity configuration… it is a runtime layer, not an access layer." Guardrails
    that filter output do not prevent unauthorized retrieval.
    ([ServiceNow](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md))
20. **Shipping a guardrail in block mode before observing its false-positive rate.** ServiceNow's own guidance is
    "Configure Guardian to log before enabling blocking," and Llama Guard's model card admits deployment "might increase
    refusals to benign prompts (False Positives)." Aggressive blocking in a service desk directly degrades deflection.
    ([ServiceNow](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/refs/heads/australia/markdown/platform-security/naai-tutorial-configure-security.md),
    [Llama Guard 3](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/Llama-Guard3/8B/MODEL_CARD.md))
21. **Relying on a safety classifier as an injection defence.** Llama Guard's card states it "may be susceptible to
    adversarial attacks or prompt injection attacks that could bypass or alter its intended use," and that some
    categories need factual/up-to-date knowledge to evaluate at all.
    ([Llama Guard 3](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/Llama-Guard3/8B/MODEL_CARD.md))
22. **Race conditions from parallelising rail mutations.** NeMo warns that "Input rail mutations can lead to erroneous
    results during parallel execution because of race conditions… This can result in output divergence compared to
    sequential execution." ([NeMo](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/yaml-schema/guardrails-configuration.md))
23. **Reading top-k and score thresholds from a config file that isn't actually applied.** In Dify, "TopK and Score
    configurations are only effective during the Rerank phase," so without a rerank model configured, both settings do
    nothing. ([Dify](https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods))
24. **Stale indexes on high-churn sources.** Glean's admin guidance names the requirement directly: crawls, incremental
    updates and webhooks must be healthy "so high-churn systems (mail, storage, ticketing, chat) are not stale beyond
    what you expect." ([Glean](https://docs.glean.com/connectors/connectors-power-glean))
25. **Eval-less prompt changes.** The documented counter-practice comes from a real rollout: "Setting a convention in
    which every prompt/workflow change required an evaluation," plus making evals "as automatic and frictionless as
    possible." The absence of that convention is the failure mode.
    ([promptfoo](https://www.promptfoo.dev/docs/red-team/))
26. **Silent prompt-version drift.** The inverse risk is a resolver that falls back quietly; Langfuse's design choice is
    a warning shot — "If no version carries the requested label, the request fails with `404 Not Found`. Langfuse never
    silently falls back to `production` or `latest`."
    ([Langfuse](https://langfuse.com/docs/prompt-management/features/prompt-version-control))
27. **Cost attribution you cannot trust.** Langfuse documents that cost may be *inferred* from a regex-matched model
    definition, that "ingested values take priority," and that Anthropic's tokenizer "is not accurate for Claude 3
    models." Budget enforcement built on inferred numbers will mis-bill or mis-throttle.
    ([Langfuse](https://langfuse.com/docs/observability/features/token-and-cost-tracking))
28. **Feature interactions that silently disable a control.** In Azure AI Search, `ingestionPermissionOptions` cannot be
    combined with `assetStore`, so enabling native document-level permission ingestion disables image serving; and
    Purview label enforcement is limited to single-tenant scenarios with RBAC auth, with autocomplete/suggest
    unavailable on such indexes. ([Azure](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview))
29. **Approval workflows with no timeout or no rejection path.** Dify's Human Input "request closes after the first
    response regardless of delivery method," and its timeout defaults to 3 days with the explicit warning that "If no
    timeout branch is connected, the workflow ends" — an approval that expires into a dead end. The parallel LangGraph
    rule is that a rejection must consume the interrupt, or the run "would stay resumable and could later be replayed as
    an approval" (the last clause is from this repo's own architecture note, not an upstream doc).
    ([Dify](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input))

---

## Coverage gaps and `unverified` items

State these plainly rather than guessing:

| Item | Status |
|---|---|
| **RAGFlow** internals (DeepDoc, hybrid weighting, RAPTOR/GraphRAG, agent operators, ACL story) | `unverified` — `ragflow.io/docs/dev/` returned an empty body, GitHub blob/raw fetches failed, DeepWiki returned 429. Re-check against `ragflow.io/docs` and the repo's `docs/guides/`. Note: RAGFlow's own docs did not surface any per-user document ACL mechanism in the searches performed; treat "RAGFlow has no per-user ACL" as a hypothesis, not a finding. |
| **Moveworks** capabilities | `unverified` — no primary vendor documentation fetch succeeded. |
| **Exact OpenTelemetry `gen_ai.*` attribute names** | `unverified` — the semconv page redirects; only the existence of GenAI span/metric/agent-span conventions and a GenAI + MCP attribute registry under semconv 1.44.0 is confirmed. |
| **DeepEval** metric names and CI integration | `unverified` — only the marketing landing page was retrieved; no primary docs page fetched. |
| **Onyx** specific hybrid-search algorithm, rerank model, and re-index scheduling defaults | `unverified` — the "Index Settings" page was indexed but not fetched. |
| **Zammad/Chatwoot** LLM data-retention and provider data-handling terms | `unverified` — Chatwoot's Captain page confirms BYOK/OpenAI-compatible endpoint/Firecrawl and Enterprise+paid-plan gating, but not retention terms; Zammad's page confirms the feature set but not provider terms. |
| **Server-side idempotency keys for write tools** | `unverified` as a *platform* feature across all vendors surveyed. The only primary-source idempotency requirement found is LangGraph's obligation on the developer. |
| **Output-schema validation of tool results** | `unverified` — not documented by any vendor surveyed. |
| **Per-user profile-fact memory with user-visible edit/delete** | `unverified` — LangGraph Stores is the mechanism; no vendor documents the governance UX. |
| **Automatic multi-provider model failover** | `unverified` — Onyx documents many providers and a LiteLLM Proxy gateway, but automatic failover semantics were not found in the fetched pages. |
