#!/opt/data/projects/finn-house-scraper/.venv/bin/python
"""Quick benchmark for DB-backed sets."""
import requests, time, statistics

BASE = "http://localhost:5005"
SET_CODES = [
    # Sets in DB with images
    "de_bw4", "de_sv04", "PMCG1", "E1", "VS1", "PCG1", "web1",
    "sv04", "A4b", "B3", "B4", "MC",
]

print("=== OPTIMIZED (N=5) ===")
results = {}
for code in SET_CODES:
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        r = requests.get(f"{BASE}/api/sets/{code}/cards", timeout=30)
        t1 = time.perf_counter()
        if r.status_code == 200:
            data = r.json()
            cards = data.get("cards", [])
            times.append(((t1 - t0) * 1000, len(cards)))
            print(f"  {code:12} {times[-1][0]:6.1f}ms | {cards} cards")
    if times:
        ms = [t[0] for t in times]
        results[code] = {"p50": statistics.median(ms), "cards": times[0][1]}
print("\n=== Summary ===")
for code in SET_CODES:
    if code in results:
        print(f"  {code:12} p50={results[code]['p50']:6.1f}ms cards={results[code]['cards']}")
