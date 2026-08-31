# whisper-large-v3 — offline ASR serving target

A VibeServe target for **offline (batch) automatic speech recognition** with
`openai/whisper-large-v3`. The candidate synthesizes a serving system that
exposes an OpenAI-compatible `/v1/audio/transcriptions` endpoint and maximizes
transcription throughput under concurrent load while matching the HuggingFace
reference's transcripts.

This is the offline-batch counterpart to `moonshine-streaming` (streaming TTFT):
the algorithmic wins here are cross-attention K/V caching, continuous batching of
the decoder across requests, and batching the fixed-shape encoder — not per-chunk
incremental encoding. See [`OBJECTIVE.md`](OBJECTIVE.md).

## Layout

```
whisper-large-v3/
├── vibesys.input.toml     # manifest: domain, accuracy + benchmark commands, headline metric
├── OBJECTIVE.md           # what to optimize + the algorithmic levers
├── reference/             # HF WhisperForConditionalGeneration reference (correctness ground truth)
│   ├── meta.json          # model id + pinned revision
│   ├── config.json        # whisper-large-v3 config
│   └── reference.py       # reference_transcribe() used by the checker
├── accuracy_checker/      # checker.py — reference-vs-candidate word-overlap gate
├── benchmark/             # Request Factory adapter — offline requests_per_second
└── test_audio/            # LibriSpeech test-clean clips + manifest.json
```

## Candidate contract

The Implementer's `main.py` must expose:

```python
class VibeServeModel:
    @classmethod
    def from_pretrained(cls, model_dir, device, dtype) -> "VibeServeModel": ...
    def transcribe(self, audio: np.ndarray, sampling_rate: int = 16000) -> str: ...
```

and serve `/v1/audio/transcriptions` for the benchmark.

The benchmark is executed by the pinned `vibesys-evaluator-request-factory`
package. Its task-owned adapter verifies and hashes the WAV fixtures, writes
canonical Request Factory traces, runs separate warmup and measured saturated
phases, and projects the measured request throughput onto the historical
top-level `requests_per_second` result field. Separate engine processes warm the
server but do not share an HTTP connection pool. Request Factory's nonstreaming
HTTP timeout is fixed at 3600 seconds, so the retained `--request-timeout` CLI
option cannot change it. Its request-throughput window runs from the first
Request Factory submission to the last completion, while the previous harness
started timing just before it constructed the measured task set.

Cargo-tool evaluator packages currently require the Local run environment.
Docker, Modal, and SkyPilot reject this task during evaluator provisioning.

## Run it

```bash
vibesys \
  --input examples/model-serving/whisper-large-v3 \
  --runs-dir /work/vibesys-runs --local \
  --exp-name whisper-offline \
  --agent-backend cli --cli-provider codex \
  --max-rounds 4 \
  --modality speech_to_text
```

## Test set

Four LibriSpeech `test-clean` utterances (4.8–12.5 s) with ground-truth
transcripts in `test_audio/manifest.json`. The accuracy gate compares the
candidate against the HF reference (not the ground truth directly), so it is
robust to whisper's own residual errors — a candidate passes by *matching the
reference implementation*, which is the correctness contract VibeServe checks.
