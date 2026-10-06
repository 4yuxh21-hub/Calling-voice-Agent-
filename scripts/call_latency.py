"""Temporary: per-stage latency breakdown from the latest call ledger."""

import glob
import json
import os

f = max(glob.glob("data/ledger/*.jsonl"), key=os.path.getmtime)
print("ledger:", f)
events = [json.loads(line) for line in open(f, encoding="utf-8")]

latencies = []
last_final = None
for e in events:
    if e.get("event") == "user_transcript" and e.get("finalized"):
        last_final = e["ts"]
    elif e.get("event") == "bot_text" and last_final and e["ts"] >= last_final:
        latencies.append(round((e["ts"] - last_final) * 1000))
        last_final = None
print("transcript->bot_text ms per turn:", latencies)

for e in events:
    if e.get("event") == "metric":
        name = e.get("metric")
        value = e.get("value") or e.get("e2e_processing_time_ms")
        if value is not None:
            print(f"  {name:24s} {e.get('processor','')[:28]:28s} {round(float(value), 3)}")
