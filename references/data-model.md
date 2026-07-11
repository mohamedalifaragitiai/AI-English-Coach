# Data model — per-user profiles & versioned scoring

SQLite in **WAL mode** (single file, concurrent reads, no server). Every score
records the scoring-model version that produced it so trends survive retuning.
Raw evaluator output is stored separately from aggregated scores so you can
recompute retroactively without re-running inference. Read before Phase 1.

## Aggregates (DDD)

- **LearnerProfile** — the durable per-user root (e.g. `abu_ali`). Owns current
  level, per-skill scores, gap vector, streak, and points at full history.
- **Session** — one practice sitting for a user; owns its utterances.
- **Utterance** — atomic unit: audio ref, transcript, confidence, timestamps.
  Everything (assessments, pronunciation) attaches here.
- **Assessment** — per-dimension scores for an utterance/session + the
  `scoring_model_version`.
- **Plan / Exercise / Report** — derived, regenerable.

## Schema (SQL sketch)

```sql
-- Users own everything. user_id is a stable slug like 'abu_ali'.
CREATE TABLE users (
  user_id        TEXT PRIMARY KEY,
  display_name   TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  current_level  INTEGER NOT NULL DEFAULT 0,   -- 0..5 custom scale
  streak_days    INTEGER NOT NULL DEFAULT 0,
  settings_json  TEXT
);

CREATE TABLE sessions (
  session_id   TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  mode         TEXT NOT NULL,          -- free, interview, ielts, business, ...
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  difficulty   REAL                    -- adaptive difficulty at start
);
CREATE INDEX idx_sessions_user_time ON sessions(user_id, started_at);

CREATE TABLE utterances (
  utterance_id TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL REFERENCES sessions(session_id),
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  role         TEXT NOT NULL,          -- 'learner' | 'coach'
  audio_path   TEXT,                   -- ref to stored wav (learner turns)
  transcript   TEXT,
  stt_confidence REAL,
  start_ms     INTEGER,
  end_ms       INTEGER,
  created_at   TEXT NOT NULL
);
CREATE INDEX idx_utt_session ON utterances(session_id);

-- Aggregated, versioned scores. Never overwrite; append.
CREATE TABLE assessments (
  assessment_id        TEXT PRIMARY KEY,
  user_id              TEXT NOT NULL REFERENCES users(user_id),
  session_id           TEXT REFERENCES sessions(session_id),
  utterance_id         TEXT REFERENCES utterances(utterance_id),
  scoring_model_version TEXT NOT NULL,
  pronunciation REAL, grammar REAL, vocabulary REAL, listening REAL,
  fluency REAL, confidence REAL, coherence REAL, relevance REAL,
  overall REAL,
  created_at           TEXT NOT NULL
);
CREATE INDEX idx_assess_user_time ON assessments(user_id, created_at);

-- Raw evaluator payloads, kept separate for recompute & audit.
CREATE TABLE evaluator_outputs (
  id            TEXT PRIMARY KEY,
  utterance_id  TEXT REFERENCES utterances(utterance_id),
  evaluator     TEXT NOT NULL,          -- grammar|vocab|fluency|pronunciation|...
  version       TEXT NOT NULL,
  payload_json  TEXT NOT NULL,          -- full typed output
  created_at    TEXT NOT NULL
);

-- Point-in-time gap vector so "which gap improved most" is answerable.
CREATE TABLE gap_snapshots (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(user_id),
  taken_at    TEXT NOT NULL,
  gaps_json   TEXT NOT NULL            -- ranked {skill: severity}
);

CREATE TABLE plans (
  plan_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
  created_at TEXT NOT NULL, horizon TEXT, plan_json TEXT NOT NULL
);
CREATE TABLE reports (
  report_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
  period TEXT NOT NULL, created_at TEXT NOT NULL, format TEXT, path TEXT
);
CREATE TABLE achievements (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
  code TEXT NOT NULL, earned_at TEXT NOT NULL
);
```

## Progress queries the system must answer (per user)

- Skill trend: `SELECT created_at, fluency FROM assessments WHERE user_id=? AND
  created_at >= ? ORDER BY created_at` — feeds trend charts.
- Most-improved gap this month: diff latest vs month-ago `gap_snapshots`.
- Time-to-next-level estimate: fit recent `overall` slope vs the level threshold
  in `scoring.md`.
- Streak: derived from distinct practice days; update on session end.

Because every `assessments` row carries `scoring_model_version`, when you retune
weights you can normalize or filter old rows instead of corrupting the trend.
This is why history is append-only.

## Optional vector store

Only when building spaced repetition / semantic vocab recall: embed target
vocabulary and missed words, store in Chroma or FAISS keyed by `user_id`. Not
required for Phases 1–6. Keep it optional so the base system stays light.
