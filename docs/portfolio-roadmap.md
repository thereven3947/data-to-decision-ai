# Portfolio roadmap: experimentation, fine-tuning, document RAG and GenAIOps

These phases extend the pipeline in [feature-roadmap.md](feature-roadmap.md) (M0–M11, done). They run
on the Mac mini (Apple silicon M4, 32 GB, Ollama with `qwen3:14b`; see [mac-mini-setup.md](mac-mini-setup.md)).

Ground rules for every phase:

- All data is simulated or public. Never imply production or real-user data.
- Follow the existing conventions: uv, `make check` (ruff + strict mypy + pytest), the Typer CLI `d2d`,
  docs in `docs/`, ADRs in `docs/adr/`.
- One branch and one pull request per phase (Phase 3 has three). Do not merge to `main` without review.
- Do not change existing behavior or defaults except where stated. New capabilities are opt-in.
- Heavy dependencies go in optional dependency groups so the core CI stays light.
- Model artifacts, adapters, indexes, downloaded documents and tracking stores stay out of git.
- If a step fails twice, stop and report what was tried.
- Each phase ends with a report: what was built (files and commands), a results table with real
  numbers, and limitations.

| Milestone | Branch | Scope | Status |
|-----------|--------|-------|--------|
| **M12 · Experimentation** | `feature/experimentation` | A/B testing and causal inference on the HITL review flow | 🚧 in progress |
| **M13 · Fine-tuning** | `feature/finetune` | Local LoRA on Qwen with MLX, evaluated against the base model and `qwen3:14b` | ⏳ planned |
| **M14a · Document ingestion & vector search** | `feature/rag-ingest` | Parsing, chunking, pgvector, hybrid search, permission-aware retrieval, scale benchmark | ⏳ planned |
| **M14b · Multimodal & knowledge graph** | `feature/rag-multimodal` | Figure captions, table chunks, entity extraction, entity linking, graph + vector retrieval | ⏳ planned |
| **M14c · GenAIOps** | `feature/genaiops` | MLflow tracking and registry, LangGraph agent, Phoenix tracing, RAG eval gate | ⏳ planned |

## M12 · A/B testing and causal inference (`feature/experimentation`)

Module `src/d2d/experiment/`: simulate and analyze a randomized experiment on the human-in-the-loop
review flow (`src/d2d/hitl/sim_reviewer.py`) and the Copilot's suggested actions.

Design (written as a pre-registration doc before analyzing results):

- Hypothesis: ranked suggested actions (treatment) vs. no suggestions (control) raise reviewer action
  conversion (at least one suggested action taken per case).
- Randomization unit: reviewer (cluster) and, as a comparison, case; document the tradeoff and the
  interference risk.
- Primary metric: action conversion. Secondary: time to resolve. Guardrails: wrong-action or override
  rate, false-escalation rate.
- Simulator: seeded generator with a known ground-truth effect, heterogeneity (device type, change
  severity, reviewer experience), a pre-period covariate for CUPED, a configurable novelty effect,
  and an optional injected sample-ratio-mismatch bug.

Analysis: power and MDE; SRM chi-square check; difference in means with CIs and cluster-robust SEs;
CUPED with reported variance reduction; Holm or Benjamini-Hochberg correction; an A/A peeking
simulation plus a valid sequential method (O'Brien-Fleming or mSPRT); heterogeneous effects with
causal forests (EconML `CausalForestDML`) validated against the known truth; and an observational
confounding demo where the naive estimate is biased and the adjusted one recovers the truth.

Deliverables: `d2d experiment simulate|power|analyze|aa-test|report`, `docs/experimentation.md`,
`docs/experiment-decision-memo.md`, tests (A/A false-positive rate near alpha, CUPED variance
reduction, SRM detection, causal-forest recovery within tolerance, sequential error control).

## M13 · LoRA fine-tuning on Apple silicon (`feature/finetune`)

- MLX (`mlx-lm` LoRA or QLoRA) on a model that fits in 32 GB (Qwen3-4B, or Qwen3-8B 4-bit; 14B 4-bit
  only if memory allows). Record peak memory and time per run. Check current mlx-lm and Ollama docs
  before choosing commands.
- Task (chosen in an ADR): preferably grounded answering with citations or "unable to determine"
  (the abstention behavior in `src/d2d/agent/checker.py`); alternatively structured enrichment JSON
  or change-category classification.
- Data: built from the simulator and existing eval sets with train/validation/test splits. The frozen
  golden set (`evals/golden/`) is never used for training; check exact and near-duplicate leakage.
  Filter synthetic targets with the grounding checker and log the rejection rate.
- Evaluation: base small model vs. fine-tuned vs. `qwen3:14b` on the held-out test set and the golden
  set: grounding pass rate, citation correctness, abstention precision and recall, schema validity,
  answer accuracy, LLM-as-judge (with a hand-labeled agreement check), latency, tokens per second,
  bootstrap CIs.
- Serving: if it wins, fuse, convert for Ollama (verify the supported path), register as
  `d2d-qwen-ft`, make it selectable in `src/d2d/agent/provider.py` (default unchanged), add it to the
  eval gate.
- Deliverables: `d2d finetune build-data|train|eval|export`, `docs/fine-tuning.md`, an ADR, tests for
  the data builder and eval harness.

## M14 · Document RAG, vector search, multimodal knowledge and GenAIOps

Goal: extend the Copilot from structured-only grounding to governed document knowledge without
weakening the grounding and abstention guarantees. Today `src/d2d/agent/search.py` embeds stories in
memory with `nomic-embed-text`; keep that path and the current default, and add document retrieval
as an opt-in tool.

Corpus:

- Public documents only, downloaded by a script and never committed (for example NERC reliability
  standards, a public utility wildfire mitigation plan, public equipment or protection-settings
  guides). Record URL, license or terms, and checksum in a manifest.
- Simulator-generated procedures and change reports with known answers, so retrieval and answers can
  be scored against ground truth.
- A simulated access label per document (`public`, `engineering`, `restricted`) to test
  permission-aware retrieval.

### M14a · Ingestion and vector search (`feature/rag-ingest`)

1. Parsing of PDFs (text, layout, tables) and HTML. Compare at least two parsers (for example Docling
   and PyMuPDF; check current docs) on table and heading fidelity.
2. Chunking: fixed-size, recursive and structure-aware (heading, section, table), with metadata (doc
   id, section path, page, access label, checksum, parser version). Exact and near-duplicate removal.
3. Embeddings via Ollama (`nomic-embed-text` baseline plus one stronger model such as `bge-m3` or a
   Qwen3 embedding model if available; verify in Ollama docs). Batch, cache by content hash, and make
   re-ingestion incremental.
4. Postgres with pgvector (Alembic migration). Hybrid search: Postgres full-text plus vector, fused
   with reciprocal rank fusion, plus an optional local reranker.
5. Permission-aware retrieval: filter by the caller's role before ranking; tests prove restricted
   chunks never reach an unauthorized caller.
6. Scale benchmark at 1M+ chunks (synthetic expansion is fine, labeled as such): exact vs. HNSW vs.
   IVFFlat on recall@k, p50/p95 latency, build time, memory and disk.
7. CLI: `d2d rag ingest|index|search|bench`.

### M14b · Multimodal and knowledge graph (`feature/rag-multimodal`)

1. Figures: caption with a local vision-language model through Ollama (for example a Qwen VL model;
   verify availability and memory), embed the captions, link each to its page and figure.
2. Tables: keep structure (markdown or JSON) and index them as their own chunks.
3. Entity extraction from documents (equipment types, device ids, locations, standard and
   requirement ids): rules plus lookup first, then an LLM extractor; report precision and recall on a
   small hand-labeled set.
4. Link entities to the simulated assets and stories in Postgres (a lightweight knowledge graph in
   relational tables). Graph-plus-vector retrieval: expand a query through linked entities, then rank.
5. Evaluate multimodal and graph retrieval separately against text-only retrieval.

### M14c · LangGraph agent, MLflow and tracing (`feature/genaiops`)

1. MLflow (local tracking server, store git-ignored): log every retrieval experiment (parser x
   chunking x embedding x index x reranker) with parameters, metrics and artifacts. Also log the M13
   fine-tuning runs and register models in the MLflow model registry.
2. LangGraph: an opt-in document-grounded agent graph (route → retrieve → grade evidence → answer or
   abstain) reusing the grounding checker in `src/d2d/agent/checker.py`. The existing agent stays the
   default.
3. Tracing: instrument retrieval and LLM calls with OpenTelemetry and view them in Arize Phoenix
   (local); save example traces as screenshots for the docs.
4. Evaluation set with gold chunks and gold answers, including unanswerable and permission-restricted
   questions. The frozen golden set stays untouched.
5. Metrics: recall@k, MRR, nDCG; grounding pass rate, citation correctness, abstention precision and
   recall, permission-leak rate (must be 0), answer accuracy, latency and cost per question, with
   bootstrap CIs. Compare structured-only, document-only and combined grounding.
6. Add the RAG eval to the existing eval gate.

M14 deliverables: `docs/rag-platform.md` (architecture diagram, corpus manifest, design choices,
benchmark and eval tables, failure examples, limitations); ADRs for parser, chunking, vector index and
LangGraph adoption; tests for chunk metadata and dedup, incremental re-ingestion, permission
filtering, RRF fusion, retrieval-eval math, graph linking and the LangGraph abstain path; README
updates (features and docs links).
