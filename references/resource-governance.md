# Resource governance — the 90% ceiling

This is the load-bearing feature for a resource-constrained host. Its job: keep
the machine responsive by **never letting any resource cross 90% sustained**, and
by degrading gracefully instead of freezing when pressure rises. Read fully
before Phase 0.

## What to sample

| Resource | Source | Notes |
|---|---|---|
| GPU VRAM used / total | `pynvml` (`nvidia-ml-py`) | The binding constraint on 8GB. |
| GPU utilization % | `pynvml` | Compute saturation; matters for concurrent STT+LLM. |
| RAM used / total | `psutil.virtual_memory()` | Piper + Python + buffers live here. |
| CPU % | `psutil.cpu_percent()` | TTS on CPU + FastAPI. |
| Disk free | `psutil.disk_usage()` | Models are GB-scale; guard downloads. |

If `pynvml` is unavailable (CPU-only host), GPU samples report `None` and the
guard treats GPU as absent — the system must still run (CPU fallbacks).

## Thresholds

- `RESOURCE_CEILING = 0.90` (hard, per resource, configurable via env).
- `RESOURCE_SOFT = 0.80` (start shedding optional work).
- Sample interval default `1.0s` (configurable). Use an async background task;
  never busy-wait.
- Use a short rolling window (e.g. last 3 samples) so a single spike doesn't
  trigger degradation, but sustained pressure does.

## Guard API (shape, not prescriptive code)

```python
class ResourceGuard:
    async def start(self) -> None            # launch background sampler
    async def stop(self) -> None
    def snapshot(self) -> ResourceSnapshot   # latest ratios per resource
    def headroom(self, resource) -> float    # 1.0 - usage_ratio
    async def acquire(self, need: ResourceEstimate,
                      path: Literal["hot","cold"]) -> Admission
    @property
    def degradation_level(self) -> int       # 0 = normal .. N = severe
```

`acquire` returns an `Admission` telling the caller what it's allowed to do:
`admit_full`, `admit_degraded(params)`, or `defer` / `reject`. The caller MUST
honor it. The guard never runs the work itself — it advises and records.

## Degradation ladder (apply in order as pressure rises)

Level 0 — normal.
Level 1 (≥80% any resource): pause admitting **cold-path** jobs; queue them.
Level 2 (≥85%): reduce LLM `context` and `max_tokens`; cap concurrent evaluators.
Level 3 (≥90% approached): switch STT to smaller model / CPU `int8`; lower TTS
quality; keep only the active session's models resident.
Level 4 (would cross 90%): reject **new** sessions with a clear message; protect
the in-flight turn so the current learner isn't cut off mid-sentence.

Each transition is logged (structured) and emitted as metrics. Recovery is
hysteretic: drop a level only after usage falls a margin below the entry
threshold (e.g. re-admit cold jobs when back under 75%) so you don't flap.

## Hot vs cold treatment

- **Hot path** may degrade but must never simply block — a live speaker is
  waiting. Prefer shorter/faster responses over stalls.
- **Cold path** is always deferrable. Under any pressure, queue and back off with
  jitter; drain when headroom returns. Cold jobs must be idempotent so retries
  are safe.

## VRAM budgeting for co-residency

Default plan for 8GB, static co-residency (simplest, most predictable):

| Model | Approx VRAM |
|---|---|
| Faster-Whisper `large-v3-turbo` | ~1.5 GB |
| LLM 7–8B GGUF Q4_K_M (via Ollama) | ~5.5 GB |
| Piper TTS | CPU (≈0 VRAM) |
| Headroom / activations | remainder under 90% of 8GB (~7.2GB usable) |

At startup the guard computes whether the **minimum viable set** (STT + LLM)
fits under the ceiling. If not, it refuses to start with an actionable message
(suggest a smaller LLM quant or `distil` STT) rather than OOM-crashing later.
Prefer Ollama keep-alive so the LLM unloads when idle, freeing VRAM for batch
cold-path work.

## Required metrics

- `resource_usage_ratio{resource}` (gauge)
- `resource_ceiling_hits_total{resource}` (counter)
- `degradation_level` (gauge)
- `jobs_deferred_total`, `sessions_rejected_total` (counters)
- `guard_sample_duration_seconds` (histogram — prove the guard is cheap)

## Testing the guard (do this before trusting it)

- Unit: feed synthetic snapshots crossing 80/85/90% and assert the ladder
  transitions and hysteresis are correct.
- Load: a `scripts/` synthetic allocator that ramps VRAM/RAM and proves the guard
  defers/rejects *before* the OS starts thrashing. This must pass before any real
  model is loaded in Phase 0.
- Assert the sampler's own CPU cost is negligible (`guard_sample_duration_seconds`
  p99 well under the interval).
