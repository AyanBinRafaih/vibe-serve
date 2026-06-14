# Benchmark

Drives mixed load (80% search, 20% reservation) against a running
DeathStarBench hotelReservation deployment and reports latency, throughput,
and resource usage.

## Primary metric

`p50_ms` — overall p50 request latency. Lower is better. `success_rate` must
stay at 1.0.

## Load levels

| Level | Clients | Requests |
|-------|---------|----------|
| light | 10 | 100 |
| medium | 30 | 400 |
| heavy | 60 | 1000 |

## Resource usage

`cpu_percent` / `memory_mb` are peak values and `cpu_percent_avg` /
`memory_mb_avg` are averages, sampled every 0.5s during the run via
`docker stats` across all hotelReservation containers. Polling adds
measurable overhead to latency numbers — known limitation.

Uses hotel IDs 1-40 and user indices 0-199, rotating through 8 date pairs
in the seeded window (2015-04-09 to 2015-04-17) to spread load.

## Running

Deploy DeathStarBench first (see reference/README.md), then:

    pip install httpx
    python benchmark.py --load-level light
    python benchmark.py --load-level medium
    python benchmark.py --load-level heavy

Output is one JSON line per metric, plus a summary.
