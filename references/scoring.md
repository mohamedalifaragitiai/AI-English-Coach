# Scoring, levels & evaluator contracts

Defines the custom proficiency scale, the weighted score, per-dimension rubrics,
and the evaluator interface. Read before Phase 4. **Version every change** to the
weights/rubrics via `SCORING_MODEL_VERSION` so stored assessments stay
interpretable over time.

## Custom level scale (0–5)

| Level | Name | Shape |
|---|---|---|
| 0 | Beginner | Single words, very limited vocab, frequent errors, can't sustain conversation. |
| 1 | Intermediate | Simple answers, frequent hesitation, limited vocab, needs help. |
| 2 | Advanced | Discusses familiar topics, minor grammar slips, reasonable vocab. |
| 3 | Professional | Workplace-ready, good technical talk & presentations, strong vocab. |
| 4 | Fluent | Natural, strong listening/speaking, rare mistakes, can persuade/negotiate. |
| 5 | Native-like | Near-native rhythm, idioms, excellent pronunciation, no barriers. |

Map overall score (0–100) to a level with thresholds (tune, then version):
`0–39→0, 40–54→1, 55–69→2, 70–82→3, 83–93→4, 94–100→5`.

## Weighted overall score

```
overall =
  0.20 * pronunciation +
  0.15 * grammar +
  0.15 * vocabulary +
  0.15 * listening +
  0.15 * fluency +
  0.10 * confidence +
  0.05 * coherence +
  0.05 * relevance
```

Weights live in config, keyed by `SCORING_MODEL_VERSION`. Each dimension is 0–100.
Produce radar (per-dimension), trend (over time), and heatmap (dimension × time)
data from stored assessments.

## Per-dimension rubrics (how each 0–100 is derived)

- **Pronunciation** — from the pronunciation pipeline (GOP or v1 proxy), not the
  LLM. Aggregate phoneme goodness → word → utterance. Proxy v1: blend STT
  token confidence + prosodic features (pitch variance, speech-rate regularity).
- **Grammar** — errors per 100 words, weighted by severity (tense/agreement >
  articles/prepositions). LLM returns typed errors + corrections; score is a
  decreasing function of weighted error density.
- **Vocabulary** — diversity (type-token ratio on lemmas), sophistication
  (academic/domain word lists), penalized repetition.
- **Listening** — response correctness vs the coach's prompt/instruction,
  inference and summary quality, response delay.
- **Fluency** — speech rate in target band, filler-word rate, pause distribution,
  false starts. Derived from timestamps + transcript, not vibes.
- **Confidence** — steadiness of delivery: pause/hesitation pattern, volume/pitch
  stability, self-corrections. Proxy acceptable v1.
- **Coherence** — logical flow / discourse markers / on-topic development (LLM).
- **Relevance** — answer completeness & topic match to the prompt (LLM).

## Evaluator contract (all cold-path evaluators share this)

```
Input:  { utterance, audio_path?, context (prompt + recent turns),
          learner_level, scoring_model_version }
Output: { dimension: str, score: float(0-100), version: str,
          details: {...}, corrections?: [...], suggestions?: [...] }
```

Rules:
- **Batch** grammar+vocab+fluency+coherence+relevance into **1–2** structured LLM
  calls returning strict JSON — not one call per dimension (latency + VRAM).
- Pronunciation is a **separate audio pipeline**, never an LLM judging audio it
  can't hear.
- Evaluators are pluggable: register by name; the worker runs whichever are
  enabled. Adding one must not touch the hot path.
- All evaluator raw output goes to `evaluator_outputs`; only the aggregated,
  weighted result goes to `assessments`.

## Post-session feedback (assemble from stored outputs)

Strengths, weaknesses, specific mistakes with corrected sentences + better
alternatives, vocab suggestions, pronunciation tips, listening misses, overall
score, current level, next target, and estimated time to next level (from the
trend slope). This is generated on the cold path and surfaced in the dashboard
and reports — never blocking the live turn.

## Adaptive difficulty

Track a per-session `difficulty` in [0,1]. Raise it when recent overall trends up
and error density falls; lower it when the learner struggles (hesitation, low
relevance). Use difficulty to pick topics, vocabulary targets, and prompt
complexity for the next turn/session. Keep the adjustment on the cold path so it
never slows the live loop.
