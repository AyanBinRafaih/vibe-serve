# Hotel Reservation: Place Reservation

## Deployment Goal

Optimise DeathStarBench's hotelReservation application for lower request
latency and higher throughput on a local server, while preserving correctness
as verified by accuracy_checker/checker.py.

## Target System

DeathStarBench hotelReservation — Go microservices stack (frontend, search,
geo, rate, profile, recommendation, user, reservation), MongoDB + Memcached,
gRPC internally, deployed via Docker Compose.
Repo: https://github.com/delimitrou/DeathStarBench/tree/master/hotelReservation

## Workload

Mixed HTTP load against the frontend on port 5000:
- 80% search: GET /hotels?inDate=...&outDate=...&lat=...&lon=...
- 20% reservation: POST /reservation?hotelId=...&inDate=...&outDate=...&customerName=...&username=...&password=...&number=1

## Hardware Target

Local server. x86-64 or Apple Silicon. Docker required.

## Interface

HTTP on port 5000, query-parameter API. Wire-compatible with DeathStarBench's
existing frontend contract. accuracy_checker/checker.py and
benchmark/benchmark.py can point at any candidate without modification.

## Optimisation Objective

Minimise **p50 end-to-end request latency** (`p50_ms`). `success_rate` must
stay at 1.0 and all 5 correctness checks (C1-C5) must pass.

Key optimisation directions:
- Memcached hit rate for profile, rate, and reservation services
- gRPC connection pooling between frontend and backend services
- MongoDB indexing on hotelId + date range
- Consul service discovery latency (client-side address caching)
- Go runtime tuning (GC percentage, goroutine pool sizing)
