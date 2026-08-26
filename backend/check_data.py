"""
Is telemetry actually landing in the databases?

Reads /api/storage from the running backend twice, a few seconds apart, so you
see movement rather than just a total. It goes through the backend on purpose:
that process already holds an open Atlas connection, so checking costs no extra
DNS lookup — which matters on networks whose resolver is unreliable for the
SRV record a mongodb+srv:// URI needs.

Start the backend and Node-RED first, then:

    venv/Scripts/python.exe check_data.py
    venv/Scripts/python.exe check_data.py 30                      # 30s window
    venv/Scripts/python.exe check_data.py 10 http://host:8000     # other host
"""

import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BACKEND = "http://127.0.0.1:8000"


def fetch(backend: str) -> dict:
    with urllib.request.urlopen(f"{backend}/api/storage", timeout=20) as response:
        return json.loads(response.read())


# The node registry holds one document per node, rewritten in place on every
# packet, so its count stays flat however healthy the pipeline is. Only the
# telemetry stores are expected to grow.
STATIC_STORES = {"CouchDB  nodes"}


def flatten(payload: dict) -> dict:
    """One flat {label: count} mapping, so the two samples can be diffed."""

    counts = {}

    for name, total in payload.get("couchdb", {}).items():
        counts[f"CouchDB  {name}"] = total

    mongodb = payload.get("mongodb", {})
    if mongodb.get("connected"):
        for key, value in mongodb.items():
            # bool is a subclass of int, so the enabled/connected flags have to
            # be excluded explicitly or they show up as stores holding 1 document.
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if key == "queue_depth":
                continue
            counts[f"Atlas    {key}"] = value

    return counts


def main() -> int:
    window = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    backend = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BACKEND

    try:
        first = fetch(backend)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Could not reach the backend at {backend}: {e}")
        print("Start it with: venv/Scripts/python.exe -m uvicorn app.main:app --reload")
        return 1

    mongodb = first.get("mongodb", {})
    if not mongodb.get("enabled"):
        print("Atlas mirror is off (MONGODB_URI not set) — showing CouchDB only.\n")
    elif not mongodb.get("connected"):
        print(f"Atlas is enabled but not connected: {mongodb.get('error', 'unknown error')}\n")

    print(f"Sampling for {window}s...\n")
    before = flatten(first)
    time.sleep(window)
    after = flatten(fetch(backend))

    print(f"{'store':30} {'documents':>12} {'new':>8} {'rate':>11}")
    print("-" * 64)

    stalled = []
    for name, total in after.items():
        added = total - before.get(name, total)
        if name in STATIC_STORES:
            # ASCII only: the Windows console this runs on is cp1252, and an
            # em dash here comes out as mojibake.
            print(f"{name:30} {total:>12,} {'-':>8} {'registry':>11}")
            continue

        print(f"{name:30} {total:>12,} {added:>+8} {added / window:>8.1f}/s")
        if added == 0:
            stalled.append(name)

    depth = first.get("mongodb", {}).get("queue_depth")
    if depth:
        # Backlog waiting on Atlas. A one-off spike is fine; a number that keeps
        # growing between runs means the mirror can't keep up with the nodes.
        print(f"\nAtlas write queue: {depth} document(s) pending")

    if stalled:
        print("\nNo new documents in: " + ", ".join(stalled))
        print("Check that Node-RED's inject nodes are enabled and its flow is deployed.")
        return 1

    print("\nAll stores are receiving data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
