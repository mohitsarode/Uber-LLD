"""H3 Spatial Indexing & Dispatch Engine Micro-Benchmark.

Measures:
1. H3 coordinate discretization throughput (ops/sec)
2. Hexagonal k-ring expansion latency across radii k=1, 2, 3 (microseconds)
3. Multi-candidate dispatch ranking throughput
4. Redis mesh atomic reservation throughput
"""

import asyncio
from pathlib import Path
import sys
import time

# Ensure project root is in python path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import h3
from src.engine.dispatch_scorer import DispatchScorer
from src.engine.h3_indexer import H3SpatialIndexer
from src.services.redis_mesh import redis_mesh


def benchmark_h3_discretization(iterations: int = 100_000):
    print(f"--- 1. H3 Coordinate Discretization ({iterations:,} ops) ---")
    lat, lng = 37.7749, -122.4194

    start = time.perf_counter()
    for _ in range(iterations):
        _ = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=8)
    elapsed = time.perf_counter() - start

    ops_per_sec = iterations / elapsed
    print(f"Total Time: {elapsed:.3f} s")
    print(f"Throughput: {ops_per_sec:,.0f} ops/sec ({(elapsed/iterations)*1e6:.2f} µs/op)\n")


def benchmark_kring_expansion(iterations: int = 50_000):
    print(f"--- 2. Hexagonal k-Ring Expansion Latency ({iterations:,} ops) ---")
    center = H3SpatialIndexer.latlng_to_cell(37.7749, -122.4194, resolution=8)

    for k in [1, 2, 3]:
        start = time.perf_counter()
        for _ in range(iterations):
            _ = H3SpatialIndexer.get_k_ring(center, k)
        elapsed = time.perf_counter() - start

        cell_count = len(H3SpatialIndexer.get_k_ring(center, k))
        avg_us = (elapsed / iterations) * 1e6
        print(f"k = {k} ({cell_count:2d} cells): {avg_us:.2f} µs/op ({iterations/elapsed:,.0f} ops/sec)")
    print()


def benchmark_candidate_scoring(iterations: int = 20_000):
    print(f"--- 3. Multi-Attribute Candidate Dispatch Scoring ({iterations:,} batches of 20 drivers) ---")
    scorer = DispatchScorer()
    pickup_lat, pickup_lng = 37.7749, -122.4194

    sample_candidates = [
        {
            "driver_id": f"drv-{i}",
            "lat": 37.7749 + (i * 0.001),
            "lng": -122.4194 + (i * 0.001),
            "cell": "8828308281fffff",
            "heading": float(i * 18),
            "speed": 30.0,
            "rating": 4.8 + (i % 3) * 0.1,
        }
        for i in range(20)
    ]

    start = time.perf_counter()
    for _ in range(iterations):
        _ = scorer.rank_candidates(sample_candidates, pickup_lat, pickup_lng)
    elapsed = time.perf_counter() - start

    total_candidates_scored = iterations * len(sample_candidates)
    print(f"Total Batches: {iterations:,} (20 candidates/batch)")
    print(f"Total Candidates Scored: {total_candidates_scored:,}")
    print(f"Throughput: {total_candidates_scored / elapsed:,.0f} candidates/sec")
    print(f"Latency per Batch: {(elapsed / iterations) * 1000:.3f} ms\n")


async def benchmark_redis_mesh():
    print("--- 4. In-Memory Mesh Atomic Reservation & Telemetry Roundtrip ---")
    await redis_mesh.connect()

    # Telemetry update
    t_start = time.perf_counter()
    for i in range(5_000):
        await redis_mesh.update_driver_telemetry(
            driver_id=f"bench-drv-{i % 50}",
            lat=37.7749,
            lng=-122.4194,
            speed=32.0,
            heading=90.0,
            status="IDLE",
        )
    t_elapsed = time.perf_counter() - t_start
    print(f"Telemetry Updates: 5,000 in {t_elapsed:.3f} s ({5000/t_elapsed:,.0f} ops/sec)")

    # Atomic Lua reservation
    r_start = time.perf_counter()
    successes = 0
    for i in range(2_000):
        ok = await redis_mesh.atomic_reserve_driver(
            driver_id="bench-drv-0",
            trip_id=f"trip-{i}",
            cell="8828308281fffff",
            ttl_sec=10,
        )
        if ok:
            successes += 1
            # Release back for next bench loop
            await redis_mesh.release_driver_reservation("bench-drv-0", "8828308281fffff")
    r_elapsed = time.perf_counter() - r_start
    print(f"Atomic Lua Reserve+Release Cycles: 2,000 in {r_elapsed:.3f} s ({2000/r_elapsed:,.0f} cycles/sec)")

    await redis_mesh.disconnect()
    print()


async def main():
    print("=================================================================")
    print("   H3 SPATIAL INDEXING & DISPATCH ENGINE PERFORMANCE BENCHMARK   ")
    print("=================================================================\n")
    benchmark_h3_discretization()
    benchmark_kring_expansion()
    benchmark_candidate_scoring()
    await benchmark_redis_mesh()
    print("Benchmark complete.")


if __name__ == "__main__":
    asyncio.run(main())
