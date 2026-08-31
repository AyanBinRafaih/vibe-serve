# Benchmark — whisper-large-v3 (offline)

`benchmark.py` materializes the `test_audio/` pool as Request Factory
`multimodal-independent-v1` traces. The pinned evaluator engine drives the
candidate's `/v1/audio/transcriptions` endpoint with `--concurrency` active
requests and reports the headline metric `requests_per_second` (declared in the
manifest's `[benchmark.result]`), plus audio-s/wall-s and success-only latency
percentiles for humans.

The candidate server must already be running.

```bash
uv run python benchmark/benchmark.py \
    --request-factory-engine /path/to/session_runner \
    --url http://localhost:8000 --concurrency 8 \
    --num-requests 64 --output-json out.json
```

Normal VibeSys runs inject the pinned engine path through the evaluator package;
`--request-factory-engine` is needed only when invoking the adapter directly.

The adapter runs `min(concurrency, audio clips)` warmup requests to completion,
then starts a separate measured Request Factory process. This preserves the
server warmup barrier but does not preserve the prior harness's HTTP connection
pool across phases. Request Factory also uses a fixed 3600-second timeout for
this nonstreaming endpoint. The legacy `--request-timeout` argument remains
accepted, but cannot override that engine timeout. Request throughput uses
Request Factory's first-submit-to-last-complete window; the prior harness
started its wall clock immediately before constructing the measured task set.

The pinned Cargo tool is currently supported only by the Local run environment.
Docker, Modal, and SkyPilot reject tool-backed evaluator packages during
provisioning.
