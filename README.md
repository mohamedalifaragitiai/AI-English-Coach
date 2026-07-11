# English Coach

Fully offline AI English Speaking & Listening Coach running on a resource-constrained local server. No cloud APIs, no Docker, no internet dependency at runtime.

## Key Constraints

- **90% resource ceiling**: No resource (GPU VRAM, RAM, CPU, disk) may cross 90% sustained
- **Per-user profiles**: Every learner has persistent progress tracking
- **No Docker**: Direct process management to save disk space
- **Uses `uv`**: Fast, lean Python package management

## Target Hardware

- RTX 4050 Laptop (8GB VRAM)
- 32GB RAM
- Intel i7
- Ubuntu 24.04 / Windows 11, CUDA 12.x

## Quick Start

```bash
# Clone and enter directory
cd english-coach

# Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create environment and install dependencies
uv sync

# Copy and configure environment
cp .env.example .env

# Run the server
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Run tests
uv run pytest
```

## Architecture

Two-path architecture sharing a resource broker:

**Hot Path (synchronous, <2s):**
`mic → Silero VAD → Faster-Whisper → LLM dialogue → Piper TTS → speaker`

**Cold Path (asynchronous, deferrable):**
`UtteranceFinalized → evaluators → scoring → profile update → reports`

The `ResourceGuard` gates all heavy operations to enforce the 90% ceiling.

## Project Structure

```
english-coach/
├── config/settings.py      # pydantic-settings configuration
├── backend/
│   ├── main.py             # FastAPI app entry point
│   ├── core/
│   │   ├── resource_guard.py  # 90% ceiling enforcer
│   │   ├── event_bus.py       # asyncio pub/sub
│   │   ├── metrics.py         # Prometheus metrics
│   │   └── logging.py         # structlog setup
│   ├── domain/             # DDD aggregates (Phase 1)
│   ├── hotpath/            # VAD, STT, dialogue, TTS (Phase 3)
│   ├── coldpath/           # Evaluators, scoring (Phase 4)
│   ├── persistence/        # SQLite, repositories (Phase 1)
│   └── api/                # REST routers (Phase 1+)
├── scripts/                # setup, benchmark, seed
├── tests/                  # pytest tests
└── frontend/               # Next.js dashboard (Phase 6)
```

## Endpoints

### System
- `GET /` - Health check
- `GET /health` - Detailed health with resource status
- `GET /metrics` - Prometheus metrics

### WebSocket
- `WS /ws/conversation/{user_id}` - Live conversation session
  - Query params: `mode` (free/roleplay), `level` (0-6 CEFR)
  - Receives: Binary audio (16-bit PCM, 16kHz, mono) or JSON control messages
  - Sends: JSON messages (state, transcript, response, audio)

### Users
- `POST /users` - Create a new user
- `GET /users` - List all users
- `GET /users/{user_id}` - Get user by ID
- `PATCH /users/{user_id}` - Update user
- `DELETE /users/{user_id}` - Delete user
- `GET /users/{user_id}/progress` - Get progress summary
- `GET /users/{user_id}/skills/{skill}/trend` - Get skill trend over time
- `POST /users/{user_id}/streak/update` - Update streak

## Model Setup

```bash
# Download models (one-time)
uv run python scripts/setup_models.py

# Benchmark models on your hardware
uv run python scripts/benchmark_models.py

# Profile hot path latency
uv run python scripts/profile_hotpath.py

# Start server with models
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Start server without models (development)
SKIP_MODELS=1 uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

## Development Phases

- [x] Phase 0: Foundation & ResourceGuard
- [x] Phase 1: Persistence & per-user profiles
- [x] Phase 2: Model serving through guard
- [x] Phase 3: Hot path (VAD → STT → LLM → TTS)
- [x] Phase 4: Cold path evaluation & scoring
- [ ] Phase 5: Gap analysis, plans, reports
- [ ] Phase 6: Dashboard
- [ ] Phase 7: Hardening
