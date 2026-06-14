"""
Accuracy Checker — DeathStarBench Hotel Reservation
====================================================
Verifies a running DeathStarBench hotelReservation deployment behaves correctly.

IMPORTANT CONSTRAINTS (see reference/meta.json):
  - No /reset endpoint. State persists in MongoDB across runs.
  - Seeded hotels: IDs "1".."80". Seeded users: index 0..500.
  - To avoid interfering with the benchmark, this checker uses:
      * hotel IDs 71-80
      * user indices 450-500
      * date pairs near the end of the valid window (2015-04-16 / 2015-04-17)

Checks
------
  C1  /hotels search returns a non-empty GeoJSON FeatureCollection with valid hotel IDs
  C2  /user authenticates correct credentials and rejects incorrect ones
  C3  /recommendations returns valid results for require=dis/rate/price
  C4  POST /reservation with valid params succeeds ("Reserve successfully!")
  C5  POST /reservation with an absurdly large `number` (exceeds any seeded
      capacity, max 300) is rejected — capacity enforcement / no overbooking.
      This check is deterministic and idempotent across repeated runs because
      `number` always exceeds capacity regardless of prior state.

Usage
-----
    python checker.py [--base-url http://localhost:5000] [--meta reference/meta.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

import httpx

BASE_DIR = Path(__file__).parent.parent
META_PATH = BASE_DIR / "reference" / "meta.json"

# Resources reserved exclusively for the checker (benchmark must not use these)
CHECKER_HOTEL_IDS = ["71", "72", "73", "74", "75", "76", "77", "78", "79", "80"]
CHECKER_USER_INDICES = range(450, 500)
CHECKER_DATE_PAIR = ("2015-04-16", "2015-04-17")


class CheckFailure(Exception):
    pass


def load_meta(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def user_credentials(i: int) -> tuple[str, str]:
    """Reproduce DeathStarBench's seeded credential scheme.

    username = "Cornell_" + hex(utf8(str(i)))
    password = str(i) repeated 10 times (plaintext; server hashes with SHA-256)
    """
    suffix = str(i)
    username = "Cornell_" + suffix.encode("utf-8").hex()
    password = suffix * 10
    return username, password


def assert_status(resp: httpx.Response, expected: int, context: str) -> None:
    if resp.status_code != expected:
        raise CheckFailure(
            f"{context}: expected HTTP {expected}, got {resp.status_code}. "
            f"Body: {resp.text[:300]}"
        )


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


async def check_c1_search(client: httpx.AsyncClient, base_url: str, meta: dict) -> None:
    """C1: /hotels search returns valid GeoJSON with known hotel IDs."""
    sp = meta["search_params"]
    resp = await client.get(f"{base_url}/hotels", params={
        "inDate": sp["inDate"], "outDate": sp["outDate"],
        "lat": sp["lat"], "lon": sp["lon"],
    })
    assert_status(resp, 200, "GET /hotels (C1)")
    body = resp.json()

    if body.get("type") != "FeatureCollection":
        raise CheckFailure(f"C1: expected GeoJSON FeatureCollection, got type={body.get('type')}")

    features = body.get("features", [])
    if not features:
        raise CheckFailure("C1: /hotels returned an empty feature list for a known-good search")

    for feat in features:
        hotel_id = feat.get("id")
        if hotel_id is None:
            raise CheckFailure(f"C1: feature missing 'id': {feat}")
        try:
            hid = int(hotel_id)
        except ValueError:
            raise CheckFailure(f"C1: hotel id '{hotel_id}' is not numeric")
        if not (1 <= hid <= 80):
            raise CheckFailure(f"C1: hotel id {hid} outside seeded range 1-80")
        if "properties" not in feat or "name" not in feat["properties"]:
            raise CheckFailure(f"C1: feature missing properties.name: {feat}")


async def check_c2_auth(client: httpx.AsyncClient, base_url: str, meta: dict) -> None:
    """C2: /user authenticates correct credentials and rejects wrong ones."""
    username, password = user_credentials(0)  # Cornell_30 / 0000000000

    ok = await client.get(f"{base_url}/user", params={"username": username, "password": password})
    assert_status(ok, 200, "GET /user valid creds (C2)")
    ok_body = ok.json()
    if "successfully" not in ok_body.get("message", "").lower():
        raise CheckFailure(f"C2: valid credentials were rejected: {ok_body}")

    bad = await client.get(f"{base_url}/user", params={"username": username, "password": "wrongpassword"})
    assert_status(bad, 200, "GET /user invalid creds (C2)")
    bad_body = bad.json()
    if "successfully" in bad_body.get("message", "").lower():
        raise CheckFailure(f"C2: invalid credentials were accepted: {bad_body}")


async def check_c3_recommendations(client: httpx.AsyncClient, base_url: str, meta: dict) -> None:
    """C3: /recommendations returns valid results for each `require` mode."""
    sp = meta["search_params"]
    for require in ("dis", "rate", "price"):
        resp = await client.get(f"{base_url}/recommendations", params={
            "lat": sp["lat"], "lon": sp["lon"], "require": require,
        })
        assert_status(resp, 200, f"GET /recommendations require={require} (C3)")
        body = resp.json()
        if body.get("type") != "FeatureCollection":
            raise CheckFailure(f"C3 require={require}: expected FeatureCollection, got {body.get('type')}")
        features = body.get("features", [])
        if not features:
            raise CheckFailure(f"C3 require={require}: empty result for known-good location")
        for feat in features:
            hid = int(feat["id"])
            if not (1 <= hid <= 80):
                raise CheckFailure(f"C3 require={require}: hotel id {hid} outside seeded range")


async def check_c4_reservation_success(client: httpx.AsyncClient, base_url: str, meta: dict) -> None:
    """C4: a valid reservation request succeeds."""
    username, password = user_credentials(450)
    hotel_id = CHECKER_HOTEL_IDS[0]  # "71"
    in_date, out_date = CHECKER_DATE_PAIR

    resp = await client.post(f"{base_url}/reservation", params={
        "hotelId": hotel_id,
        "inDate": in_date,
        "outDate": out_date,
        "customerName": username,
        "username": username,
        "password": password,
        "number": 1,
    })
    assert_status(resp, 200, "POST /reservation valid (C4)")
    body = resp.json()
    if "successfully" not in body.get("message", "").lower():
        raise CheckFailure(f"C4: valid reservation was rejected: {body}")


async def check_c5_capacity_enforced(client: httpx.AsyncClient, base_url: str, meta: dict) -> None:
    """C5: a reservation request with an absurd `number` (exceeds max seeded
    capacity of 300) must be rejected, regardless of prior state.

    This is deterministic and idempotent: 100000 > any possible remaining
    capacity on any run, so the assertion holds whether this is the first
    run or the hundredth.
    """
    username, password = user_credentials(451)
    hotel_id = CHECKER_HOTEL_IDS[1]  # "72"
    in_date, out_date = CHECKER_DATE_PAIR

    resp = await client.post(f"{base_url}/reservation", params={
        "hotelId": hotel_id,
        "inDate": in_date,
        "outDate": out_date,
        "customerName": username,
        "username": username,
        "password": password,
        "number": 100000,
    })
    assert_status(resp, 200, "POST /reservation absurd number (C5)")
    body = resp.json()
    if "successfully" in body.get("message", "").lower():
        raise CheckFailure(
            f"C5: reservation request for 100000 rooms was accepted "
            f"(capacity not enforced — overbooking risk): {body}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("C1 Search returns valid results", check_c1_search),
    ("C2 Authentication accepts/rejects correctly", check_c2_auth),
    ("C3 Recommendations valid for all modes", check_c3_recommendations),
    ("C4 Valid reservation succeeds", check_c4_reservation_success),
    ("C5 Capacity enforced (no overbooking)", check_c5_capacity_enforced),
]


async def run_checks(base_url: str, meta_path: Path) -> int:
    meta = load_meta(meta_path)
    failed = []
    passed = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Liveness check via a cheap search request (no /health endpoint exists)
        sp = meta["search_params"]
        try:
            resp = await client.get(f"{base_url}/hotels", params={
                "inDate": sp["inDate"], "outDate": sp["outDate"],
                "lat": sp["lat"], "lon": sp["lon"],
            })
            if resp.status_code != 200:
                print(f"ERROR: GET /hotels returned {resp.status_code}. Is the stack running?")
                return 1
        except Exception as exc:
            print(f"ERROR: cannot reach {base_url}/hotels — {exc}")
            return 1

        for name, fn in CHECKS:
            try:
                await fn(client, base_url, meta)
                print(f"  PASS  {name}")
                passed.append(name)
            except CheckFailure as exc:
                print(f"  FAIL  {name}: {exc}")
                failed.append((name, str(exc)))
            except Exception as exc:
                print(f"  ERROR {name}: unexpected exception: {exc}")
                failed.append((name, repr(exc)))

    print()
    print(f"Results: {len(passed)} passed, {len(failed)} failed out of {len(CHECKS)} checks.")
    if failed:
        print("\nFailed checks:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="DeathStarBench Hotel Reservation accuracy checker")
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--meta", type=Path, default=META_PATH)
    args = parser.parse_args()

    exit_code = asyncio.run(run_checks(args.base_url, args.meta))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
