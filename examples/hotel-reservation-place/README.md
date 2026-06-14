# Hotel Reservation: Place Reservation

Closes #51. Part of the Hotel Reservation application family (#47).

Use:

* `--ref examples/hotel-reservation-place/reference`
* `--acc-checker examples/hotel-reservation-place/accuracy_checker`
* `--bench examples/hotel-reservation-place/benchmark`

Target: DeathStarBench hotelReservation — a Go microservices stack deployed
via Docker Compose. Mixed workload: 80% hotel search, 20% reservation
placement. Primary metric: `p50_ms`. `success_rate` must stay at 1.0.

The reference folder contains deployment instructions and the confirmed API
contract (`meta.json`). It does not contain a server implementation —
DeathStarBench is the server.

## Running

    # 1. Deploy DeathStarBench
    git clone https://github.com/delimitrou/DeathStarBench.git
    cd DeathStarBench/hotelReservation && docker compose up -d
    cd -

    # 2. Run checker
    python accuracy_checker/checker.py

    # 3. Run benchmark
    python benchmark/benchmark.py --load-level light
    python benchmark/benchmark.py --load-level medium
    python benchmark/benchmark.py --load-level heavy

    # 4. Teardown
    cd DeathStarBench/hotelReservation && docker compose down -v

## Reference Baseline

| Load | Clients | Requests | p50_ms | p99_ms | success_rate | cpu_percent_avg |
|------|---------|----------|--------|--------|--------------|-----------------|
| light | 10 | 100 | 85.5 | 388.7 | 1.0 | 9.5 |
| medium | 30 | 400 | 316.8 | 2581.9 | 1.0 | 134.8 |
| heavy | 60 | 1000 | 711.5 | 4495.3 | 1.0 | 226.9 |
