---
name: english-coach-builder
description: >
  End-to-end build specification for a FULLY OFFLINE, self-hosted AI English
  Speaking & Listening Coach that runs on a resource-limited local dev server
  (e.g. RTX 4050 8GB, 32GB RAM). Use this skill whenever the user asks to build,
  scaffold, implement, extend, or continue work on the "English coach",
  "speaking coach", "AI tutor", "pronunciation/fluency evaluator", per-user
  language progress tracking, or any module described here (STT, LLM dialogue,
  TTS, evaluation, scoring, reporting, dashboard, monitoring). Trigger this even
  if the user only names one module (e.g. "add the grammar evaluator" or "wire
  up the resource guard") — the whole system shares one architecture, one
  resource budget, and one per-user profile store, so consult this skill before
  writing any code so the piece fits the whole. This project MUST NOT use Docker
  (storage-constrained host) and MUST keep every resource under a hard 90%
  ceiling to avoid hanging the machine.
---

# English Coach Builder

You are implementing a production-grade, **fully offline** AI English Speaking &
Listening Coach on a **resource-constrained local server**. No cloud APIs, no
Docker, no internet dependency at runtime. Every design choice bends to two
non-negotiable constraints:

1. **Hard 90% resource ceiling.** No single resource (GPU VRAM, GPU compute, RAM,
   CPU, disk) may cross 90% sustained. Crossing it must trigger graceful
   degradation, never a freeze. This is the difference between a usable dev box
   and a hung one.
2. **Per-user longitudinal profiles.** Every learner (e.g. "Abu Ali") owns a
   persistent profile that tracks skill scores, gaps, and progress *across time*.
   Nothing is anonymous or session-only.

Read this whole file before writing code. Then read the reference file(s)
relevant to the module you're building. Build **incrementally** — the phases at
the bottom are ordered so the system is runnable after each one.

---

## Golden rules (violating these breaks the project)

- **No Docker.** The host is storage-limited. Run processes directly in a local
  environment. If the user ever asks for containers, push back and point here.
  Model files alone are several GB; Docker layers on top would blow the disk
  budget.
- **Use `uv` for everything Python — never pip, never plain `venv`.** Create the
  environment with `uv venv`, add dependencies with `uv add`, run commands with
  `uv run`, and pin via `uv.lock`. `uv` is dramatically faster and its resolver
  keeps the dependency set lean, which matters on a storage-limited box. Do not
  invoke `pip install`, `python -m venv`, or `requirements.txt` — if you catch
  yourself typing `pip`, stop and use the `uv` equivalent.
- **No cloud / no network at runtime.** Faster-Whisper, the LLM, and TTS all run
  locally. The only network use is one-time model downloads during setup, which
  must be explicit and logged.
- **90% is a hard ceiling, checked before every heavy operation.** See
  `references/resource-governance.md`. The `ResourceGuard` is not optional
  decoration — it gates STT, LLM, and TTS calls.
- **One learner = one durable profile.** See `references/data-model.md`. Scores
  are versioned by scoring-model version so trends stay comparable when you
  retune.
- **Two-path architecture.** The live conversation loop (hot path) must stay
  under ~2s and must never block on evaluation. Evaluation, scoring, and
  reporting run on the cold path, after the turn. Never chain 10 LLM calls into
  the live loop.
- **Resource-wise model choices by default.** `whisper large-v3-turbo` or
  `distil-large-v3` (not full large-v3), an 8B-class LLM at Q4_K_M, Piper for TTS
  on CPU. Only deviate if the user explicitly accepts the VRAM cost and you've
  measured it.
- **Everything observable.** Prometheus metrics + structured logs for every
  component. If it runs, it emits metrics. A resource-constrained box that you
  can't see into is a box that hangs mysteriously.

---

## Target host (assume unless told otherwise)

Dell G15, RTX 4050 Laptop (8GB VRAM), 32GB RAM, Intel i7, Windows 11 / Ubuntu
24.04, CUDA 12.x. Treat 8GB VRAM as the binding constraint. Design so the system
also degrades sanely on a weaker box (CPU-only fallback for STT/TTS must exist).

---

## Architecture in one screen

Two paths sharing a resource broker and a per-user store.

**Hot path (synchronous, target <2s, learner waits on this):**
`mic → Silero VAD → Faster-Whisper (streaming, turbo) → single LLM dialogue call → Piper TTS (streaming) → speaker`

**Cold path (asynchronous, learner does NOT wait):**
`UtteranceFinalized event → evaluators (grammar, vocab, fluency, pronunciation) → weighted scoring → profile update → gap analysis → plan/report generation`

**Cross-cutting:** `ResourceGuard` (90% ceilings + degradation), monitoring
(Prometheus/logs), per-user profile store (SQLite, optional Chroma/FAISS for
spaced-repetition recall).

The connection between paths is an **in-process event bus** (asyncio pub/sub) —
not Kafka, not a broker. Right-sized for one host.

Detailed diagrams, sequence, and rationale live in
`references/architecture.md`. Read it before Phase 0.

---

## Technology selection (and why — defend these in review)

| Layer | Choice | Why for THIS host |
|---|---|---|
| Serving | FastAPI + Uvicorn (1 worker) + WebSockets | Async, streaming-native, tiny footprint. Multiple workers would duplicate model loads and blow VRAM — **one worker only**. |
| STT | Faster-Whisper (CTranslate2), `large-v3-turbo` | 4× faster than vanilla Whisper, ~1.5GB VRAM vs ~5GB for full large-v3. Frees VRAM for the LLM. CPU fallback via `int8`. |
| VAD | Silero VAD | Tiny, fast, standard. Cheap barge-in / silence detection. |
| LLM | Qwen2.5-7B-Instruct **or** Llama-3.1-8B, GGUF Q4_K_M, via Ollama | Ollama gives keep-alive/auto-unload for free — essential for VRAM juggling. ~5–6GB at Q4. Test both for grammar-feedback quality. |
| TTS | Piper (CPU) | Fast, offline, leaves VRAM for STT+LLM. Kokoro only if user accepts extra load. |
| Pronunciation | wav2vec2 CTC forced alignment + GOP scoring | Whisper transcripts contain ZERO pronunciation signal. This is a **separate audio pipeline**, cold-path only. v1 may ship a confidence+prosody proxy behind the same interface. |
| Store | SQLite (WAL mode) | Single-file, zero-server, perfect for one host. Postgres would be a server you don't need. |
| Vector (optional) | ChromaDB or FAISS | Only when you build spaced repetition / semantic recall. Don't pay upfront. |
| Monitoring | prometheus-client + structured logging (structlog) | In-process metrics endpoint; optional Grafana later. No heavy stack. |
| Frontend | Next.js + Tailwind + TypeScript | Dashboards, radar/trend charts, WebSocket audio. |

If asked to justify any row, explain the alternative and the trade-off, then
recommend the resource-wise option above.

---

## Resource governance — the 90% ceiling (read this carefully)

This is the feature the user cares about most. Full spec in
`references/resource-governance.md`. Summary of required behavior:

- A `ResourceGuard` samples GPU VRAM, GPU utilization, RAM, and CPU (via
  `pynvml`/`nvidia-ml-py` for GPU, `psutil` for CPU/RAM) on a background loop
  (default every 1s, configurable).
- **Ceiling = 90% per resource** (configurable per-resource, but 90% is the
  default and the point of the project). A **soft warning at 80%** starts
  shedding optional work.
- Before any heavy op (load model, run STT, run LLM, run TTS, spawn a cold-path
  job), the caller `await guard.acquire(resource_estimate)`. If admitting the op
  would cross the ceiling, the guard **degrades gracefully** instead of running
  it:
  - Hot path: shorten LLM `max_tokens`, drop to a smaller/quantized path, or
    return a brief holding response — never freeze.
  - Cold path: queue the job, back off, retry when headroom returns. Cold work is
    always deferrable.
- **Degradation ladder** (apply in order as pressure rises): pause/queue
  cold-path jobs → reduce LLM context & max_tokens → switch STT to a smaller
  model or CPU int8 → shed TTS to lower quality → as last resort, reject new
  sessions with a clear message. Every step is logged and emitted as a metric.
- The guard exposes metrics: `resource_usage_ratio{resource=...}`,
  `resource_ceiling_hits_total{resource=...}`,
  `degradation_level`, `jobs_deferred_total`.
- **Never busy-wait.** Use async sleeps and event-driven wakeups so the guard
  itself costs near-zero CPU.

Design the guard as a single injected dependency so every module shares one view
of the budget. It is the component most likely to save the machine — treat it as
core, test it hard (see testing section).

---

## Per-user profiles & progress tracking

Full schema in `references/data-model.md`. Requirements:

- Each learner has a `LearnerProfile` keyed by a stable `user_id` (e.g.
  `abu_ali`). Creating/selecting a user is step one of any session.
- The profile aggregates: current level (custom 0–5 scale, defined in
  `references/scoring.md`), per-skill scores (pronunciation, grammar, vocabulary,
  listening, fluency, confidence, coherence, relevance), a gap vector, streak,
  and a full history so progress is queryable *over time*.
- Every session produces `Utterance` rows (audio ref, transcript, confidence,
  timestamps) and `Assessment` rows (per-dimension scores + the
  `scoring_model_version` that produced them). Store **raw evaluator outputs
  separately** from aggregated scores so you can recompute retroactively without
  re-running inference.
- Progress queries the user must be able to answer from stored data: "Abu Ali's
  fluency over the last 30 days", "which gap improved most this month", "time-to-
  next-level estimate", "streak". Provide these as REST endpoints and dashboard
  widgets.
- Trends must survive retuning: because each Assessment records its scoring
  version, you can filter/normalize when weights change. Never overwrite history.

---

## Serving, evaluation, monitoring — what "full" means here

- **Serving:** one FastAPI app. REST for CRUD/reports/profile; WebSocket for the
  live audio loop (streaming partial transcripts in, streaming TTS chunks out).
  OpenAPI/Swagger auto-generated. Graceful startup that loads models through the
  ResourceGuard and refuses to start if it can't fit the minimum set in budget.
- **Evaluation:** cold-path evaluators as pluggable units sharing a contract
  (input = finalized utterance + audio ref + context; output = typed JSON scored
  against a rubric). Batch grammar/vocab/fluency/coherence into **1–2** structured
  LLM calls, not one per dimension. Pronunciation is its own audio pipeline.
  Scoring formulas and weights live in `references/scoring.md`.
- **Monitoring:** `/metrics` Prometheus endpoint + structured JSON logs with a
  per-session correlation id. Track latency per hot-path stage (VAD, STT, LLM,
  TTS), cold-path queue depth, evaluator durations, and every ResourceGuard
  signal. Optional Grafana dashboards JSON in `assets/` — but the metrics
  endpoint is mandatory, Grafana is not.
- **Profiling:** wrap hot-path stages with lightweight timing (perf_counter) and
  expose as metrics + optional per-request trace log. Provide a
  `scripts/profile_hotpath.py` that runs a synthetic turn and prints a stage
  breakdown against the <2s budget. Provide `scripts/benchmark_models.py` to
  measure STT/LLM/TTS latency and VRAM so model choices are evidence-based on the
  actual box.

---

## Repository layout to create

```
english-coach/
├── README.md                     # install + run, no-Docker, model download steps
├── pyproject.toml                # managed by uv (uv add); deps pinned in uv.lock
├── uv.lock                       # uv lockfile — commit this
├── .env.example                  # ports, model names, ceilings, paths
├── config/
│   └── settings.py               # pydantic-settings; RESOURCE_CEILING=0.90 default
├── backend/
│   ├── main.py                   # FastAPI app, lifespan loads models via guard
│   ├── core/
│   │   ├── resource_guard.py     # the 90% ceiling + degradation ladder
│   │   ├── event_bus.py          # asyncio pub/sub, UtteranceFinalized etc.
│   │   ├── metrics.py            # prometheus collectors
│   │   └── logging.py           # structlog setup, correlation ids
│   ├── domain/                   # DDD aggregates: session, utterance, assessment, profile
│   ├── hotpath/
│   │   ├── vad.py  stt.py  dialogue.py  tts.py
│   │   └── ws_session.py         # the WebSocket live loop
│   ├── coldpath/
│   │   ├── evaluators/           # grammar.py vocab.py fluency.py coherence.py
│   │   ├── pronunciation/        # alignment.py gop.py  (+ proxy.py for v1)
│   │   ├── scoring.py            # weighted model, versioned
│   │   ├── gap_analysis.py  planner.py  reporting.py
│   │   └── worker.py             # consumes events, respects guard
│   ├── persistence/              # sqlite (WAL), repositories, migrations
│   └── api/                      # routers: auth, users, sessions, scores, reports, metrics
├── frontend/                     # Next.js + Tailwind + TS dashboard
├── scripts/
│   ├── setup_models.py           # explicit one-time downloads, logged
│   ├── benchmark_models.py  profile_hotpath.py
│   └── seed_user.py              # create e.g. abu_ali
├── tests/                        # pytest: guard, scoring, evaluators, api, e2e
└── references/  (consumed by builder, optional to ship)
```

---

## Phased build plan (system runs after each phase)

Do these in order. After each, the app must start and pass its tests. Commit per
phase. Report resource usage numbers to the user after phases 1, 3, and 5.

**Phase 0 — Foundation & guardrails.** `uv venv` + `uv add` the core deps,
settings, logging, metrics endpoint,
and the `ResourceGuard` with its background sampler + degradation ladder + tests.
Prove the ceiling works with a synthetic load test *before* loading any model.
(Read `references/resource-governance.md`.)

**Phase 1 — Persistence & per-user profiles.** SQLite (WAL), domain aggregates,
repositories, migrations, `seed_user.py` for Abu Ali. REST for user CRUD and
progress queries. Tests for versioned scoring storage & history. (Read
`references/data-model.md`.)

**Phase 2 — Model serving through the guard.** `setup_models.py` (logged
downloads), load Faster-Whisper turbo + Ollama LLM + Piper through the guard at
startup; refuse to start if minimum set won't fit under budget.
`benchmark_models.py` to record real latency/VRAM on the host.

**Phase 3 — Hot path.** VAD → streaming STT → single LLM dialogue call →
streaming TTS over WebSocket. Emit `UtteranceFinalized` at turn end. Hit the <2s
budget; prove it with `profile_hotpath.py`. Guard gates every stage.

**Phase 4 — Cold path evaluation & scoring.** Event worker + evaluators
(batched) + pronunciation pipeline (proxy ok for v1) + versioned weighted
scoring → profile update. All deferrable under pressure. (Read
`references/scoring.md`.)

**Phase 5 — Gap analysis, plans, reports.** Ranked gaps, adaptive plan, and
PDF/Excel/CSV/JSON reports. Time-to-next-level estimate.

**Phase 6 — Dashboard.** Next.js: overall/level, per-skill radar, trend charts,
streak, weakest skills, recommendations, conversation history — all per user.

**Phase 7 — Hardening.** Full monitoring dashboards, load/soak test under the 90%
ceiling, CI (GitHub Actions running pytest — no Docker), docs, roadmap.

---

## Reference files (read the one for the module you're building)

- `references/architecture.md` — diagrams (Mermaid), sequence, component
  responsibilities, event contracts. Read before Phase 0/3.
- `references/resource-governance.md` — the 90% ceiling design, sampling,
  degradation ladder, guard API, metrics. Read before Phase 0.
- `references/data-model.md` — full SQL schema, aggregates, per-user profile &
  versioned scoring, progress queries. Read before Phase 1.
- `references/scoring.md` — custom 0–5 level scale, weighted score formula, per-
  dimension rubrics, evaluator contracts, adaptive difficulty. Read before
  Phase 4.

If a reference file you need isn't present, tell the user and proceed from the
summaries in this SKILL.md, flagging what you inferred.

---

## When you (Claude Code) start work

1. Confirm the current phase with the user (or infer from repo state).
2. Read this SKILL.md fully, then the reference file for the target module.
3. State the resource budget you're designing against and how this module stays
   under 90%.
4. Build the smallest runnable slice, with tests, then report actual resource
   numbers.
5. Never introduce Docker, pip, a cloud call, or a second Uvicorn worker without
   explicit user sign-off — each breaks a golden rule.

## uv command reference (use these, not pip)

```bash
uv venv                          # create the environment (.venv)
uv add fastapi uvicorn[standard] # add runtime deps -> pyproject.toml + uv.lock
uv add --dev pytest pytest-asyncio ruff   # dev-only deps
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
uv run pytest                    # run tests inside the env
uv sync                          # reproduce the locked env on a fresh checkout
uv lock                          # refresh the lockfile after manual edits
```

CI (GitHub Actions) uses `uv sync --frozen` then `uv run pytest` — no Docker, no
pip. Commit both `pyproject.toml` and `uv.lock`.
