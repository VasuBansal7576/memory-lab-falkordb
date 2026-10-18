# Memory Lab

Persistent memory for Codex, backed by FalkorDB. It saves decisions from one session and retrieves them in the next, with the original source attached.

**Graph Hacks 2026 · Solo · Agent Memory and Coordination + Company Brain**

[Watch / download the 2:44 proof demo](https://github.com/VasuBansal7576/memory-lab-falkordb/releases/tag/v0.1.0)

## What it does

- Imports Markdown notes and connects people, projects, policies and decisions.
- Captures Codex conversations, then summarizes, distils and indexes them in the background.
- Retrieves graph context, note passages, or both.
- Shows the answer’s relationship paths, quotations, source revisions and Cypher query.

Example: a refund question follows policy → role → role holder → temporary delegate. The retrieved sources retain the approval threshold and delegation dates.

## Run locally

You need Docker with Compose, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Python 3.12+.

```sh
docker compose -p graph-memory-lab up -d --wait
uv sync --python 3.12
uv run python -m memory_lab
```

Open **http://127.0.0.1:8791**. The included company data is synthetic. Start in **Evidence mode**, which needs no model account. Embeddings download on first use; set `MEMORY_SEMANTIC=0` to skip that download and use keyword search.

For model answers, install the [Codex CLI](https://developers.openai.com/codex/cli/), run `codex login`, then select **Codex · local login**. Set `MEMORY_CODEX_MODEL` to change the default `gpt-6.1-sol`. The app runs locally and uses your login.

Settings are listed in [.env.example](.env.example). Put overrides in an untracked `.env`.

## Give Codex memory

```sh
uv run python -m memory_lab.install_hooks --assistant codex
codex
```

In Codex, run `/hooks` and review/trust the four project hooks. Trust is local to your checkout; it is not carried in this repository.

The hooks retrieve context on prompts and capture conversations on close or compaction. A background worker filters the transcript, writes notes, extracts facts and publishes a graph snapshot. Retries do not duplicate notes.

```sh
uv run python -m memory_lab.doctor
```

This checks the database, login and hook events. Private state stays in ignored `.memory/`.

## Import and retrieve

```sh
uv run python -m memory_lab.ingest_cli /absolute/path/to/notes
uv run python -m memory_lab.ingest_cli /absolute/path/to/notes --distil --extract

uv run python -m memory_lab.recall_cli --mode graph 'Who owns Beacon?'
uv run python -m memory_lab.recall_cli --mode rag 'How do I claim travel expenses?'
uv run python -m memory_lab.recall_cli --mode hybrid 'Who approves a £900 refund on 15 October 2026?'
```

Raw imports need no answering model. `--distil` and `--extract` use your local Codex login. Add `--json` to recall commands for the complete evidence and trace.

Reference-format extraction JSON (`--extractions DIR`) and distilled Markdown (`--distilled DIR`) are also supported.

## FalkorDB model

FalkorDB stores the entities, relationships, source chunks and vectors used for recall. It is required for the memory service.

| Nodes | Relationships |
|---|---|
| Person, Team, Service, Project | LEADS, OWNS, USES, DEPENDS_ON |
| Policy, Role, Process, Document | GOVERNED_BY, APPROVED_BY, HELD_BY, DELEGATES_TO, PART_OF, REFERENCES |

Facts carry a source ID, revision, quotation and text offsets. A source update stages an immutable snapshot; compare-and-swap publishes it only after indexing succeeds. Docker’s volume and AOF retain committed state across container recreation.

Graph recall resolves names/aliases and walks up to three hops, returning eight facts by default. Omitted evidence is flagged.

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

Note retrieval fuses keyword ranking with local BGE-small embeddings using reciprocal rank fusion. The meaning search uses FalkorDB’s native vector index:

```cypher
CALL db.idx.vector.queryNodes('Chunk', 'embedding', 10, vecf32($vector))
YIELD node, score
RETURN node.id, score
ORDER BY score ASC
```

Cosine scores here are distances: lower is closer. Graph-only mode skips both note-retrieval legs.

## Proof

Open **http://127.0.0.1:8791/proof** to inspect the recorded runs. [Current benchmark](proof/model-comparison/codex-v4-20261018), [native Codex lifecycle](proof/reference-parity/native-lifecycle.json), and [publication details](PUBLICATION.json) are included in the repository.

32 attempts compare real local search/read with graph-only context on 16 synthetic documents and six questions. The approval question is repeated three times per cell.

| Model / method | Approval median | Input tokens | Valid approval repeats | Strict checks, all attempts |
|---|---:|---:|---:|---:|
| Sol / search | 13.15 s | 52,729 | 3/3 | 8/8 |
| Sol / graph | 6.70 s | 16,764 | 3/3 | 8/8 |
| Luna / search | 9.92 s | 50,383 | 2/3 | 5/8 |
| Luna / graph | 5.89 s | 15,713 | 3/3 | 7/8 |

Three Luna baselines skipped required retrieval. One Luna graph answer returned “A Support Agent” instead of the exact expected label “Support Agent” and failed grading. All failures and raw provider traces remain available.

Small synthetic pilot; cached tokens are included. Cache, service load and model order affect timings. Precomputed graph annotations exclude extraction/indexing costs. A strong RAG baseline was not tested.

## Verify

```sh
uv run pytest -q
uv run python -m scripts.verify_reference_import
uv run python -m scripts.verify_reference_rag
uv run python -m scripts.verify_native_hooks
```

[Publication test receipt](proof/submission-check.json): **123 tests passed**, **5/5 original graph cases**, **4/4 original RAG checks**, and native capture → worker → recall in a separate Codex session. The native verifier needs trusted hooks and an authenticated Codex CLI.

Run the persistence check separately from tests and active workers; it recreates this project’s database container while retaining its volume:

```sh
uv run python -m scripts.verify_durability
```

## Credits and limits

Built on 18 October 2026 with substantial Codex assistance for implementation, verification, documentation and video. The participant directed the concept, experiment, reference choice and revisions.

The design is inspired by [Glitch Cat Club’s graph-memory-starter](https://github.com/Glitch-Cat-Club/graph-memory-starter/tree/496a6e9d9b9578943ec5ed34c2780de5a0fa5510). This independent implementation uses FalkorDB and Codex. Compatibility checks use its MIT-licensed example data; see [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).

This is a single-workspace prototype. Summaries and extracted facts can be wrong; a matching quotation does not prove entailment. Tenant authorization, multi-agent handoffs, snapshot retention limits and production-scale evaluation are not implemented.
