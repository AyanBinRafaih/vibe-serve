# Accuracy Checker

Verifies correctness of a running DeathStarBench hotelReservation deployment
before VibeServe accepts it as a candidate.

All 5 checks must pass for a candidate to be accepted.

- **C1** — /hotels search returns a non-empty result with valid hotel IDs (1-80)
- **C2** — /user authenticates correct credentials and rejects incorrect ones
- **C3** — /recommendations returns valid results for require=dis/rate/price
- **C4** — POST /reservation with valid params succeeds
- **C5** — POST /reservation with number=100000 (exceeds max seeded capacity
  of 300) is rejected — capacity enforcement / no overbooking

C5 is deterministic and idempotent across repeated runs regardless of prior
reservation state, since 100000 always exceeds capacity.

Uses hotel IDs 71-80 and user indices 450-500, reserved exclusively for the
checker (see meta.json) so it does not interfere with the benchmark.

## Running

Deploy DeathStarBench first (see reference/README.md), then:

    pip install httpx
    python checker.py [--base-url http://localhost:5000] [--meta ../reference/meta.json]

Exits 0 if all pass, 1 if any fail.
