"""
Benchmark — DeathStarBench Hotel Reservation
=============================================
Drives mixed load (80% search, 20% reservation) against a running
DeathStarBench hotelReservation deployment and reports latency/throughput.

Resource partitioning (see reference/meta.json and accuracy_checker/checker.py):
  - Benchmark uses hotel IDs 1-40 and user indices 0-199.
  - Hotels 71-80 and users 450-500 are reserved for the checker.
  - Reservation requests use number=1 and rotate through 8 date pairs in the
    valid window, spreading load so no single (hotel, date-pair) combination
    approaches its seeded capacity (minimum capacity is 200).

Primary metric: p50_ms (lower is better).

Usage
-----
    python benchmark.py [--base-url http://localhost:5000] [--meta reference/meta.json]
                        [--load-level light|medium|heavy] [--seed 42]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import httpx

BASE_DIR = Path(__file__).parent.parent
META_PATH = BASE_DIR / "reference" / "meta.json"

# Benchmark's exclusive resource pools (must not overlap with checker)
BENCH_HOTEL_IDS = [str(i) for i in range(1, 41)]
BENCH_USER_RANGE = range(0, 200)

DATE_PAIRS = [
    ("2015-04-09", "2015-04-10"),
    ("2015-04-10", "2015-04-11"),
    ("2015-04-11", "2015-04-12"),
    ("2015-04-12", "2015-04-13"),
    ("2015-04-13", "2015-04-14"),
    ("2015-04-14", "2015-04-15"),
    ("2015-04-15", "2015-04-16"),
    ("2015-04-16", "2015-04-17"),
]

LOAD_LEVELS = {
    "light":  {"concurrent_clients": 10, "total_requests": 100},
    "medium": {"concurrent_clients": 30, "total_requests": 400},
    "heavy":  {"concurrent_clients": 60, "total_requests": 1000},
}

RESERVATION_FRACTION = 0.2  # 20% reservation, 80% search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_meta(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def user_credentials(i: int) -> Tuple[str, str]:
    suffix = str(i)
    username = "Cornell_" + suffix.encode("utf-8").hex()
    password = suffix * 10
    return username, password


def percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * pct / 100)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]


def emit(metric: str, value) -> None:
    print(json.dumps({"metric": metric, "value": value}), flush=True)


def parse_mem_to_mb(mem_str: str) -> float:
    """Parse Docker MemUsage value like '123.4MiB' or '1.2GiB' to MB."""
    mem_str = mem_str.strip()
    try:
        if mem_str.endswith("GiB"):
            return float(mem_str[:-3]) * 1024
        if mem_str.endswith("MiB"):
            return float(mem_str[:-3])
        if mem_str.endswith("KiB"):
            return float(mem_str[:-3]) / 1024
        if mem_str.endswith("B"):
            return float(mem_str[:-1]) / (1024 * 1024)
    except ValueError:
        return 0.0
    return 0.0


def sample_docker_stats() -> Tuple[float, float]:
    """Take one snapshot of aggregate CPU% and memory (MB) across
    hotelReservation containers.

    Returns (0.0, 0.0) if docker is unavailable or no matching containers found.
    This is a blocking call (~0.3-1s for `docker stats --no-stream`) and is
    intended to be run inside a thread executor by the async poller below.
    """
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return 0.0, 0.0

    if result.returncode != 0:
        return 0.0, 0.0

    total_cpu = 0.0
    total_mem = 0.0
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = container.get("Name", "").lower()
        if "hotel" not in name and "hotelreservation" not in name:
            continue
        cpu_str = container.get("CPUPerc", "0%").rstrip("%")
        try:
            total_cpu += float(cpu_str)
        except ValueError:
            pass
        mem_usage = container.get("MemUsage", "0B / 0B").split("/")[0].strip()
        total_mem += parse_mem_to_mb(mem_usage)

    return total_cpu, total_mem


async def poll_docker_stats(
    samples: List[Tuple[float, float]], stop_event: asyncio.Event, interval: float = 0.5,
) -> None:
    """Background task: repeatedly sample docker stats until stop_event is set.

    Each `docker stats --no-stream` call is blocking (~0.3-1s), so it is run
    in a thread executor to avoid blocking the event loop and starving the
    request workers.
    """
    loop = asyncio.get_event_loop()
    while not stop_event.is_set():
        cpu, mem = await loop.run_in_executor(None, sample_docker_stats)
        samples.append((cpu, mem))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Workload
# ---------------------------------------------------------------------------


async def search_request(
    client: httpx.AsyncClient, base_url: str, meta: dict,
    latencies_ms: List[float], results: Dict[str, int],
) -> None:
    sp = meta["search_params"]
    t0 = time.perf_counter()
    try:
        resp = await client.get(f"{base_url}/hotels", params={
            "inDate": sp["inDate"], "outDate": sp["outDate"],
            "lat": sp["lat"], "lon": sp["lon"],
        })
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies_ms.append(elapsed_ms)
        if resp.status_code == 200:
            results["search_ok"] = results.get("search_ok", 0) + 1
        else:
            results["search_err"] = results.get("search_err", 0) + 1
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies_ms.append(elapsed_ms)
        results["search_err"] = results.get("search_err", 0) + 1


async def reservation_request(
    client: httpx.AsyncClient, base_url: str,
    hotel_id: str, user_idx: int, in_date: str, out_date: str,
    latencies_ms: List[float], reservation_latencies_ms: List[float],
    results: Dict[str, int],
) -> None:
    username, password = user_credentials(user_idx)
    t0 = time.perf_counter()
    try:
        resp = await client.post(f"{base_url}/reservation", params={
            "hotelId": hotel_id,
            "inDate": in_date,
            "outDate": out_date,
            "customerName": username,
            "username": username,
            "password": password,
            "number": 1,
        })
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies_ms.append(elapsed_ms)
        reservation_latencies_ms.append(elapsed_ms)
        if resp.status_code == 200 and "successfully" in resp.json().get("message", "").lower():
            results["reservation_ok"] = results.get("reservation_ok", 0) + 1
        else:
            results["reservation_err"] = results.get("reservation_err", 0) + 1
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies_ms.append(elapsed_ms)
        reservation_latencies_ms.append(elapsed_ms)
        results["reservation_err"] = results.get("reservation_err", 0) + 1


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


async def run_benchmark(base_url: str, meta: dict, load: dict, seed: int = 42) -> dict:
    rng = random.Random(seed)
    n_clients = load["concurrent_clients"]
    n_total = load["total_requests"]
    n_reservation = int(n_total * RESERVATION_FRACTION)
    n_search = n_total - n_reservation

    latencies_ms: List[float] = []
    reservation_latencies_ms: List[float] = []
    results: Dict[str, int] = {}

    # Build task list
    tasks = []
    for _ in range(n_search):
        tasks.append(("search",))
    for _ in range(n_reservation):
        hotel_id = rng.choice(BENCH_HOTEL_IDS)
        user_idx = rng.choice(BENCH_USER_RANGE)
        in_date, out_date = rng.choice(DATE_PAIRS)
        tasks.append(("reservation", hotel_id, user_idx, in_date, out_date))
    rng.shuffle(tasks)

    # Warmup (10% of total, not measured)
    n_warmup = max(5, n_total // 10)

    async with httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=n_clients + 10)) as client:
        # Warmup
        warmup_tasks = []
        for _ in range(n_warmup):
            warmup_tasks.append(("search",))
        semaphore = asyncio.Semaphore(n_clients)

        async def warmup_one(task):
            async with semaphore:
                if task[0] == "search":
                    await search_request(client, base_url, meta, [], {})

        await asyncio.gather(*[warmup_one(t) for t in warmup_tasks])

        # Start background docker stats polling for the measured window
        docker_samples: List[Tuple[float, float]] = []
        stop_event = asyncio.Event()
        poller_task = asyncio.create_task(poll_docker_stats(docker_samples, stop_event))

        # Measured run
        wall_start = time.perf_counter()

        async def bounded_task(task):
            async with semaphore:
                if task[0] == "search":
                    await search_request(client, base_url, meta, latencies_ms, results)
                else:
                    _, hotel_id, user_idx, in_date, out_date = task
                    await reservation_request(
                        client, base_url, hotel_id, user_idx, in_date, out_date,
                        latencies_ms, reservation_latencies_ms, results,
                    )

        await asyncio.gather(*[bounded_task(t) for t in tasks])
        wall_elapsed = time.perf_counter() - wall_start

        # Stop poller; take one final sample to catch tail-end usage
        stop_event.set()
        await poller_task
        docker_samples.append(sample_docker_stats())

    # Metrics
    sorted_lat = sorted(latencies_ms)
    sorted_res_lat = sorted(reservation_latencies_ms)

    p50 = percentile(sorted_lat, 50)
    p95 = percentile(sorted_lat, 95)
    p99 = percentile(sorted_lat, 99)

    total_ok = results.get("search_ok", 0) + results.get("reservation_ok", 0)
    total_err = results.get("search_err", 0) + results.get("reservation_err", 0)
    total_requests = total_ok + total_err

    throughput_rps = len(latencies_ms) / wall_elapsed if wall_elapsed > 0 else 0
    reservation_throughput_rps = (
        results.get("reservation_ok", 0) / wall_elapsed if wall_elapsed > 0 else 0
    )
    success_rate = total_ok / total_requests if total_requests > 0 else 0.0

    # Docker stats: peak and average across all polled samples during the run
    if docker_samples:
        cpu_values = [c for c, _ in docker_samples]
        mem_values = [m for _, m in docker_samples]
        cpu_peak = max(cpu_values)
        cpu_avg = sum(cpu_values) / len(cpu_values)
        mem_peak = max(mem_values)
        mem_avg = sum(mem_values) / len(mem_values)
    else:
        cpu_peak = cpu_avg = mem_peak = mem_avg = 0.0

    return {
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "reservation_p50_ms": round(percentile(sorted_res_lat, 50), 2),
        "throughput_rps": round(throughput_rps, 2),
        "reservation_throughput_rps": round(reservation_throughput_rps, 2),
        "success_rate": round(success_rate, 4),
        "error_count": total_err,
        "search_ok": results.get("search_ok", 0),
        "search_err": results.get("search_err", 0),
        "reservation_ok": results.get("reservation_ok", 0),
        "reservation_err": results.get("reservation_err", 0),
        "cpu_percent": round(cpu_peak, 1),
        "cpu_percent_avg": round(cpu_avg, 1),
        "memory_mb": round(mem_peak, 1),
        "memory_mb_avg": round(mem_avg, 1),
        "docker_samples_count": len(docker_samples),
        "wall_time_sec": round(wall_elapsed, 3),
        "total_requests": total_requests,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="DeathStarBench Hotel Reservation benchmark")
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--meta", type=Path, default=META_PATH)
    parser.add_argument("--load-level", choices=list(LOAD_LEVELS.keys()), default="medium")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    meta = load_meta(args.meta)
    load = LOAD_LEVELS[args.load_level]

    print(f"# DeathStarBench Hotel Reservation Benchmark — load_level={args.load_level}", flush=True)
    print(f"# concurrent_clients={load['concurrent_clients']}  "
          f"total_requests={load['total_requests']}  "
          f"(reservation_fraction={RESERVATION_FRACTION})", flush=True)

    metrics = asyncio.run(run_benchmark(args.base_url, meta, load, seed=args.seed))

    for key, val in metrics.items():
        emit(key, val)

    print(json.dumps({"summary": metrics}), flush=True)
    print(f"\nPrimary metric (p50_ms): {metrics['p50_ms']:.2f}", flush=True)

    if metrics["success_rate"] < 0.95:
        print(
            f"WARNING: success_rate {metrics['success_rate']:.2%} is below 0.95 threshold.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()