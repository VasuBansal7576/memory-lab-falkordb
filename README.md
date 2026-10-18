# Memory Lab

**Graph Hacks 2026 submission — solo — Track 02: Agent Memory and Coordination; Track 03: Company Brain.**

Memory Lab turns company documents and session decisions into persistent, source-backed context for Codex. FalkorDB performs the multi-hop retrieval and stores the published graph, chunks and vectors. Removing FalkorDB breaks the memory service; it is the product's context layer.

The project runs locally rather than exposing a user's Codex subscription through a public server. Complete setup is below. The submission demo and downloadable evidence are published with the repository's release.

### AI assistance and original work

Built during October 18, 2026, with substantial Codex assistance for implementation, tests, debugging, documentation and demo production. The participant supplied the project concept, graph-versus-search experiment, reference selection, architecture direction and iterative review requests. Verification includes real provider calls, native Codex lifecycle runs, original-source compatibility checks and database recreation; it is not a claim that a human manually ran every check.

This is an independent FalkorDB/Codex implementation inspired by the public design of Glitch Cat Club's graph-memory-starter, not a clone or fork. Compatibility checks temporarily use its original MIT-licensed example data. See [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES). The benchmark's “hand-authored annotations” means pre-authored synthetic annotations rather than extraction during the benchmark; Codex assisted in preparing and checking those fixtures. Model-generated summaries and annotations can still be wrong.

FalkorDB memory rebuilt independently from the design in [graph-memory-starter](https://github.com/Glitch-Cat-Club/graph-memory-starter/tree/496a6e9d9b9578943ec5ed34c2780de5a0fa5510). It supports separate graph recall, note RAG, and combined recall, plus session-to-daily-note memory. This implementation uses FalkorDB instead of SQLite and defaults to an authenticated Codex CLI instead of Claude Code.

## Run

Prerequisites: Docker with Compose, `uv`, and Python 3.12 or newer. Model-free indexing downloads the local embedding model on first use; set `MEMORY_SEMANTIC=0` for keyword-only operation. Evidence mode needs no model account.

```sh
docker compose -p graph-memory-lab up -d --wait
uv sync --python 3.12
uv run python -m memory_lab
```

Open http://127.0.0.1:8791. The app binds to loopback, and FalkorDB uses loopback port 6387. Configure the app, CLI, hooks and worker through the same project `.env`; `.env.example` lists the settings. Existing namespaces and source edits survive migration. Docker's persistent volume and AOF retain committed data.

For a quick model-free graph demo, set `MEMORY_SEMANTIC=0` before starting the app, leave **Evidence mode** selected and ask “Who leads the team accountable for Beacon?”. This needs Docker and Python, with no paid account or API key. Codex answers and write-time memory require the separately authenticated local CLI described below. The default corpus is synthetic; never import restricted or private data for a hackathon demonstration.

For Codex model answers, install the `codex` CLI, run `codex login`, then choose **Codex · local login**. `MEMORY_CODEX_MODEL` defaults to `gpt-6.1-sol`. Optional Anthropic API answers require `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` locally. Those API credentials are independent of a Claude Code subscription. Never put credentials in source files.

```sh
uv run python -m memory_lab.doctor
```

The doctor reports database/index state, provider authentication, installed hook definitions, and observed hook events without exposing credentials. Protocol-test hook receipts are marked separately from native events.

## Recall modes

```sh
uv run python -m memory_lab.recall_cli --mode graph 'Who leads the team accountable for Beacon?'
uv run python -m memory_lab.recall_cli --mode rag 'How do employees claim travel expenses?'
uv run python -m memory_lab.recall_cli --mode hybrid 'Who signs off a £900 refund on 15 October 2026?'
```

Use `--json` for full evidence and trace. `MEMORY_RECALL_MODE=graph|rag|hybrid` chooses the default for application recall and hooks. Graph mode does no note search. RAG mode searches notes without graph seeding or graph ambiguity handling. Hybrid combines both. Recall is context, not a resolved decision: the answering model must apply the quoted conditions. General native prompt recall no longer has the former 1,000-character limit; HTTP question fields accept up to 100,000 characters.

Entity names and aliases seed a native bounded undirected Cypher walk, default three hops/eight facts. Returned edges keep their true direction. Domain facts rank ahead of document metadata links, then by traversal distance. Duplicate edge quotations share a source marker. Ambiguous names require clarification. Raw evidence, source revisions, native queries and truncation warnings stay inspectable.

BM25 and local `BAAI/bge-small-en-v1.5` 384-dimensional embeddings are fused with reciprocal rank fusion (k=60, zero-based ranks). Equal fusion scores use meaning rank, then keyword rank, rather than an opaque source hash. This tie-break is an explicit adaptation. Both raw and distilled chunks include the relative source/section label in their indexed text; raw chunks use a 1,600-character target. Keyword scoring uses source chunks read from FalkorDB; vector search uses its native approximate index. Cosine results are distances, sorted ascending. The default meaning leg returns the nearest ten, like the reference; an optional distance cutoff can reduce irrelevant hits. Native approximate retrieval is a deliberate difference from the reference's exact brute-force vectors. Search expands matches by six neighboring source lines, retaining exact original offsets and the matched span separately. Hook recall requests five supplementary note hits before applying its context budgets.

Model context is bounded. Graph statements and their full supporting quotation are kept or omitted together; a budget never silently cuts a graph condition into an unconditional assertion. Omission adds an explicit incompleteness warning. Prompt hooks also have a supplementary-note budget and a per-note sentence excerpt limit. Shortened note excerpts warn that conditions may be missing. The UI/JSON retains the full quotations. A total context budget is not a fixed token-count or fixed-latency guarantee.

## Import Markdown and reference extraction files

```sh
uv run python -m memory_lab.ingest_cli /absolute/path/to/notes
uv run python -m memory_lab.ingest_cli /absolute/path/to/notes --distil --extract --limit 20
uv run python -m memory_lab.ingest_cli /absolute/path/to/notes --extractions /absolute/path/to/extraction-json
uv run python -m memory_lab.ingest_cli /absolute/path/to/notes --distilled /absolute/path/to/distilled-markdown
```

Visible `.md` files are discovered recursively. Hidden folders, dependency folders, symlink files, private runtime state, and this implementation itself when nested under the notes root are excluded. Heading chunks keep exact source offsets. Deleted notes are removed only from that imported root. Stable origins and completed-processing flags skip unchanged work.

Reference-style YAML `title`, `type`, names-only `entities`, and `links` are accepted as raw source metadata. `type` sets the source node kind. `links` resolve in two passes by relative filename, stem or title and become explicit source-backed `REFERENCES` edges. Unresolved links are reported rather than invented. Names alone do not provide entity types or typed relationships: use `--extract` or the original-format extraction JSON to model those.

The extraction adapter accepts `source_doc`, `nodes` (`name`, `type`, optional `description`), `edges` (`source`, `predicate`, `target`, optional `quote`), and `aliases` (`entity`, `alias`). Uppercase reference types and lowercase predicates are normalized. A global first pass resolves cross-document endpoints and aliases to canonical identities. Original paraphrased descriptions are not passed off as quotations; non-verbatim descriptions are omitted and complete source text supports the edges. For source bodies longer than 4,000 characters, supply explicit edge quotes. Rich local `entities`/`facts` annotations remain supported.

Question distillation stores question, summary, optional rule and a verbatim supporting quote. Extraction checks ontology, endpoints, descriptions and quotations. These checks establish source presence and schema validity, not logical entailment. Sources remain untrusted input to every model.

`--distilled` reads the reference's Markdown sidecars: `source: relative/path.md`, one or more `- question` lines, optional `summary:` and `rule:`, and `quote:`. A whitespace-equivalent quotation is mapped back to the exact original substring before indexing. Unknown sources, traversal paths and unsupported quotations are rejected per file while the raw source stays searchable. Imported entries have a content fingerprint: sidecar changes replace them, and deleting a sidecar or invalidating its quote removes stale entries on the next explicit sidecar import. Sidecar folders nested under notes are excluded from raw-note discovery. Unchanged imports do not rebuild the graph or call a model.

A malformed metadata file falls back to raw text, and one failed model task no longer blocks the folder. Structured errors name the file and processing stage. Raw notes remain searchable while failed processing can be retried. Unreadable or oversized sources keep their prior indexed revision and report an error. A processing limit bounds **model work**; it does not hide the remaining raw notes. Completed distillation/extraction is checkpointed before publication, so retrying an index failure reuses it. Reused model usage is reported separately from newly incurred usage.

## Graph model and publication

Entities have stable IDs, canonical names, kinds, aliases and source-backed conditions. Kinds are `Person`, `Team`, `Service`, `Project`, `Policy`, `Role`, `Process`, and `Document`.

| Predicate | Allowed endpoints |
|---|---|
| OWNS | Person/Team → Service/Project |
| LEADS | Person → Team |
| USES | Project/Service → Service |
| DEPENDS_ON | Project/Service → Project/Service |
| GOVERNED_BY | Project/Service → Policy |
| APPROVED_BY | Policy → Role/Person |
| HELD_BY | Role → Person |
| DELEGATES_TO | Person → Person |
| PART_OF, REFERENCES | Any declared kind → Any declared kind |

Every fact stores source ID/title, revision, original quote and offsets. Source manifests, graph entities/edges, note chunks and vectors belong to one immutable snapshot. Redis compare-and-swap publishes the head only after staging succeeds. A failed embedding setup leaves keyword recall available; later initialization can repair the semantic index without editing sources or overwriting the failed snapshot. Old snapshots remain inspectable.

The general graph walk in `memory_lab/memory.py` is:

```cypher
MATCH p=(s:Entity)-[*1..3]-(n:Entity)
WHERE s.id IN $seeds
UNWIND relationships(p) AS r
WITH r, min(length(p)) AS depth
RETURN startNode(r), r, endNode(r), depth
ORDER BY CASE WHEN type(r) = 'REFERENCES' THEN 1 ELSE 0 END,
         CASE WHEN r.quote STARTS WITH '---' THEN 1 ELSE 0 END,
         depth, type(r), startNode(r).name, endNode(r).name, r.fact_id
LIMIT $limit
```

Explicit domain relationships rank ahead of document references within the three-hop neighborhood. This prevents a dense set of links from displacing an approval/holder/delegate chain from the default eight-fact budget. Reference links remain available, and omitted facts are flagged as incomplete evidence.

For judges: the native vector query used by note RAG is `CALL db.idx.vector.queryNodes('Chunk','embedding',10,vecf32($vector)) YIELD node,score RETURN node.id,score ORDER BY score ASC`. Source chunks are read with `MATCH (c:Chunk) RETURN ...`. Graph-only benchmarks use the entity traversal above and exclude both note-retrieval legs. These queries are executed by the official FalkorDB Python client, not a drawn graph over another database.

The source editor validates replacement documents. Saved investigations retain their question and original snapshot; resuming reruns against current knowledge. Legacy deterministic answers apply only to explicitly supported unconditional questions; the general model path uses retrieved evidence.

## Session memory and native assistant hooks

App model conversations can be saved using **Save session to memory**; closing the page also stages them. **Retry pending sessions** retries failed work. Refresh data to open generated notes after indexing. Standalone chat works independently of native hooks:

```sh
uv run python -m memory_lab.chat_cli
```

The pipeline is: filtered conversation → durable pending job → grounded summary → marked daily entry → optional distillation/extraction → atomic indexing → later recall. Tools, results, analysis, injected controls, command messages, bash wrappers and interrupted-request notices are filtered before model use. Input is streamed and capped to configured whole turns/characters, keeping the end. `.memory/` stores private runtime state and is excluded from git.

Completed jobs are skipped; retries do not duplicate the daily entry. Successful processing results survive publication failures. A failed session note remains staged for retry even if its raw text was indexed. Notes retain exact supplied statements, but model-written summaries can still be wrong.

Install either or both assistant adapters:

```sh
uv run python -m memory_lab.install_hooks --assistant codex
uv run python -m memory_lab.install_hooks --assistant claude
uv run python -m memory_lab.install_hooks --assistant both
```

Both are already configured locally. Generated definitions contain checkout-specific paths and are excluded from git; rerun the installer in each checkout. Installers preserve unrelated settings, model choices, permissions and hooks. Codex uses `.codex/hooks.json`; Claude Code uses `.claude/settings.json`. Both wire `UserPromptSubmit`, `SessionEnd`, `PreCompact` and `SessionStart` to the same implementation.

**Codex requires the project configuration layer and exact hook definitions to be reviewed/trusted using `/hooks`.** This is an explicit Codex prerequisite, not a code bypass: [official hooks documentation](https://learn.chatgpt.com/docs/hooks). The four local Codex definitions have now been reviewed and trusted through the CLI, and a real native prompt run fired SessionStart, UserPromptSubmit and SessionEnd. A complete native capture → worker → later-session recall also passed. Handler fixtures and native runs are recorded separately. Claude Code must reload its project settings after review; its CLI is currently installed but unauthenticated here.

The selected write provider is the authenticated Codex CLI, using the local ChatGPT subscription. Set `MEMORY_WRITE_PROVIDER=codex`; no Claude subscription or Anthropic API key is required for this path. The optional Claude adapter remains available but is outside the selected runtime and has no live verification on this unauthenticated machine.

Capture modes: `headless` starts the worker on close; `next_session` (alias `session`) stages input until SessionStart; `off` disables new capture. The next-session adaptation starts an independent worker rather than injecting a subagent task into the host conversation. Both modes need the selected provider's local authentication.

## Settings

| Setting | Default / purpose |
|---|---|
| MEMORY_CONTEXT_CHARS | 6000; combined model/hook context budget, excluding the original user prompt |
| MEMORY_NOTE_CONTEXT_CHARS | 1500; supplementary note context inside hooks |
| MEMORY_NOTE_HIT_CHARS | 280; hook note-excerpt limit |
| MEMORY_NEIGHBOR_LINES | 6; source expansion around search hits |
| MEMORY_RECALL_MODE | hybrid; graph, rag or hybrid |
| MEMORY_MAX_TURNS / MEMORY_MAX_CHARS | 30 / 15000; captured conversation budget |
| MEMORY_MIN_TURNS | 2; reference-style value 5 can be selected |
| MEMORY_CAPTURE_MODE | headless; next_session/session or off |
| MEMORY_LOG_DIR | empty; set daily for a subfolder inside MEMORY_NOTES_DIR |
| MEMORY_DISTIL / MEMORY_AUTO_EXTRACT | 1 / 1; independently disable either model step |
| MEMORY_PROCESS_LIMIT | 20; changed notes processed per import/worker pass |
| MEMORY_DIGEST_PROMPT | memory_lab/prompts/digest.txt; editable digest voice |
| MEMORY_WRITE_PROVIDER | codex; optional claude |
| MEMORY_WRITE_MODEL / MEMORY_DIGEST_MODEL | optional shared write-model override / summary-only model override |
| MEMORY_CODEX_MODEL / MEMORY_CLAUDE_MODEL | gpt-6.1-sol / sonnet |
| MEMORY_STATE_DIR / MEMORY_NOTES_DIR | .memory / .memory/notes; optional absolute paths |
| MEMORY_SEMANTIC | 1; 0 disables local embeddings |
| MEMORY_VECTOR_MAX_DISTANCE | unset; optional cosine-distance cutoff between 0 and 2 |

## Verification and recorded evidence

```sh
uv run pytest -q
uv run python scripts/verify_reference_parity.py
uv run python scripts/verify_reference_import.py
uv run python scripts/verify_reference_rag.py
uv run python scripts/verify_native_hooks.py
uv run python -m scripts.verify_durability
```

The reference-parity verifier creates a fresh isolated namespace, exercises the installed hook entrypoint in subprocesses with **synthetic native-format payloads**, launches the actual detached next-session worker, makes real Codex write-time calls, recalls from a later prompt and validates a real source-cited answer. It checks long prompts, filtering, complete approval conditions, bounded context and duplicate closes. Fresh receipts live under `proof/reference-parity/`; `latest.json` points to the newest. Protocol fixtures are explicitly distinguished from trusted native UI sessions. `proof/reference-parity/native-lifecycle.json` records real trusted native Codex sessions: actual transcript capture on exit, background indexing, then memory injection and a source-cited answer in a separate later process. `scripts/verify_reference_import.py` also imports the pinned original example data temporarily and checks all five documented retrieval cases; it downloads data only, never source code, and removes its test database afterward. The prior lifecycle receipt remains historical evidence.

The durability command briefly recreates this project's database container and retains its volume. Run it **separately** from tests, benchmarks and active model workers. Never use `docker compose down -v` unless intentionally deleting memory.

The reference RAG verifier temporarily downloads the original twelve raw notes and three distilled Markdown sidecars, imports them without model calls, checks the expected first source for all four documented questions (including a distilled hit for the absence question), and checks idempotent reimport. It removes its test files/database and records source URLs, license and exact hits in `proof/reference-parity/reference-rag.json`.

http://127.0.0.1:8791/proof displays recorded evidence. The comparison in `proof/hybrid-v3` belongs to the previous retrieval revision; raw traces and archived implementation copies are preserved, and its numbers are not measurements of the latest parity fixes. Re-run into a fresh directory for a current measurement:

```sh
uv run python scripts/compare_models.py --output proof/model-comparison/new-run --models gpt-6.1-sol,gpt-6-luna --repeats 3
```

Version 4 runs a two-model × two-method matrix through the Codex subscription. Each requested model runs both actual local search/read and graph-only recall; graph mode excludes BM25, vectors and distilled-note hits and must use zero tools. Approval repeats alternate method order. Model blocks run sequentially, so cache/service load remain confounders for cross-model latency. The schema grades verdict, entity identity and required source IDs, not prose quality. Every raw prompt, tool/model trace, provider token total, error and immutable implementation copy is retained. Invalid and incorrect runs remain in the denominator. Index build time is separate; hand-authored graph annotations mean write-time extraction cost is not included in answer-time savings. Report measured results, never assume a lighter model wins. `compare_graph.py` can run a single-model pair; its default is now graph-only, with `--graph-mode hybrid` an explicitly different experiment. `proof/current` is older version-2 evidence, and `proof/invalid-v1` is explicitly invalid for graph savings. No video is produced.

This is a local single-workspace prototype. It has no tenant authentication, multi-agent handoff product, AST index, bounded snapshot retention, or production-scale evaluation. Those are outside the reference workflow parity work.
