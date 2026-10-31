#!/opt/data/projects/finn-house-scraper/.venv/bin/python
"""Benchmark /api/sets/{code}/cards endpoint before and after optimization."""
import requests, time, statistics, json, concurrent.futures

BASE = "http://localhost:5005"
SET_CODES = [
    "de_bw4", "de_sv04", "de_30th", "de_A1",
    "PMCG1", "E1", "VS1", "PCG1", "web1",
    "sv04", "A4b", "B3", "B4", "S8b", "S12a",
    "MC", "M-P", "smp", "swshp", "tk",
]

def bench(code, n=10):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = requests.get(f"{BASE}/api/sets/{code}/cards", timeout=30)
        t1 = time.perf_counter()
        if r.status_code == 200:
            data = r.json()
            cards = data.get("cards", [])
            with_img = sum(1 for c in cards if "/static/" in str(c.get("images", {})))
            times.append(((t1 - t0) * 1000, len(cards), with_img, r.headers.get("cache-control", "none")))
    return times

print("=== BASELINE ===")
results = {}
for code in SET_CODES:
    times = bench(code)
    if times:
        ms = [t[0] for t in times]
        results[code] = {
            "p50": statistics.median(ms),
            "p95": sorted(ms)[int(len(ms)*0.95)],
            "p99": sorted(ms)[int(len(ms)*0.99)],
            "avg": statistics.mean(ms),
            "cards": times[0][1],
            "with_img": times[0][2],
            "cache": times[0][3],
        }
        print(f"  {code:12} {results[code]['cards']:4} cards, "
              f"p50={results[code]['p50']:6.1f}ms p95={results[code]['p95']:6.1f}ms "
              f"avg={results[code]['avg']:6.1f}ms cache={results[code]['cache']}")

# Save baseline
with open("/opt/data/cache/scratch/bench_baseline.json", "w") as f:
    json.dump(results, f, indent=2)

# Concurrent test
print("\n=== CONCURRENT (10 parallel) ===")
t0 = time.perf_counter()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    futs = [ex.submit(bench, code, 3) for code in SET_CODES]
    for f in concurrent.futures.as_completed(futs):
        pass
t1 = time.perf_counter()
print(f"  10 parallel sets x 3 requests: {1000*(t1-t0):.0f}ms total")