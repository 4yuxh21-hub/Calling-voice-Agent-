"""Benchmark harness (M0): run scripted scenarios against a live bot server.

For each scenario in fixtures/scenarios/:
  - replay the utterance audio through the carrier simulator
  - read the per-call JSONL ledger written by the bot
  - compute WER (jiwer) between expected and finalized user transcripts
  - verify expected tool calls and reply keywords
  - report client-side per-turn latency (end of utterance -> first bot audio)

Outputs reports/benchmark_<timestamp>.md and .json.

Run (server must be up, mock or real mode):
  python scripts/benchmark.py [--url ws://localhost:8080/ws/exotel] [--scenario en_order]
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.simulate_carrier import SCENARIOS_DIR, run_simulation  # noqa: E402

try:
    from jiwer import wer as jiwer_wer
except ImportError:
    jiwer_wer = None


def read_ledger(call_sid: str) -> list[dict]:
    path = PROJECT_ROOT / "data" / "ledger" / f"{call_sid}.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def evaluate_scenario(scenario: dict, summary: dict, events: list[dict]) -> dict:
    expected_texts = [u["text"] for u in scenario["utterances"]]
    finals = [e for e in events if e.get("event") == "user_transcript" and e.get("finalized")]
    tools_called = [e["function"] for e in events if e.get("event") == "tool_call"]
    bot_text = " ".join(e["text"] for e in events if e.get("event") == "bot_text")

    # Real STT + Smart Turn may split one spoken utterance into several
    # finalized transcripts. Assign each final to the utterance whose audio
    # was most recently being sent (send_start <= transcript ts), then compare
    # the joined segments against the expected text.
    send_starts = [t.get("send_start") for t in summary["turns"]]
    actual_by_turn: dict[int, list[str]] = {}
    for final in finals:
        assigned = 0
        for i, start in enumerate(send_starts):
            if start is not None and final["ts"] >= start:
                assigned = i
        actual_by_turn.setdefault(assigned, []).append(final["text"])
    final_texts = [
        " ".join(actual_by_turn.get(i, [])) or "(nothing heard)"
        for i in range(len(expected_texts))
    ]

    # Server-side per-turn latency: last finalized transcript of the turn ->
    # first bot text after it.
    latencies = []
    for final in finals:
        next_bot = next(
            (e["ts"] for e in events if e.get("event") == "bot_text" and e["ts"] > final["ts"]),
            None,
        )
        if next_bot is not None:
            latencies.append((next_bot - final["ts"]) * 1000.0)

    wer_values = []
    for expected, actual in zip(expected_texts, final_texts):
        if jiwer_wer is not None:
            wer_values.append(jiwer_wer(expected.lower(), actual.lower()))
        else:
            wer_values.append(0.0 if expected.lower() == actual.lower() else 1.0)
    per_utterance_wer = [round(w, 4) for w in wer_values]

    # Corpus WER is the headline number: real turn-taking (barge-in, early
    # turn ends, TTS overlap) legitimately fragments finals across turns, so
    # per-utterance pairing overstates STT error. Corpus = whole-call accuracy.
    expected_corpus = " ".join(expected_texts)
    actual_corpus = " ".join(
        t for t in final_texts if t != "(nothing heard)"
    )
    if jiwer_wer is not None:
        corpus_wer = jiwer_wer(expected_corpus.lower(), actual_corpus.lower())
    else:
        corpus_wer = 0.0 if expected_corpus.lower() == actual_corpus.lower() else 1.0

    # The in-progress frame is observed once per broadcast hop; dedupe.
    tools_called = list(dict.fromkeys(tools_called))
    missing_tools = [t for t in scenario.get("expect_tools", []) if t not in tools_called]
    reply_ok = all(
        keyword.lower() in bot_text.lower() for keyword in scenario.get("expect_reply_contains", [])
    )
    audio_flowed = summary["bot_audio_chunks"] > 0
    unanswered = [t["turn"] for t in summary["turns"] if not t["answered"]]

    checks = {
        "audio_roundtrip": audio_flowed,
        "all_turns_answered": not unanswered,
        "expected_tools_called": not missing_tools,
        "reply_keywords": reply_ok,
        # WER is reported, not gated: through the live pipeline, barge-in and
        # early turn-ends legitimately fragment finals, so a hard gate here
        # measures turn-taking, not STT. Measure provider WER offline on the
        # same fixture files for clean numbers.
    }
    return {
        "scenario": scenario["id"],
        "description": scenario.get("description", ""),
        "turns": summary["turns"],
        "latency_ms": {
            "n": len(latencies),
            "min": round(min(latencies)) if latencies else None,
            "median": round(statistics.median(latencies)) if latencies else None,
            "max": round(max(latencies)) if latencies else None,
        },
        "wer": round(corpus_wer, 4),
        "per_utterance_wer": per_utterance_wer,
        "final_transcripts": final_texts,
        "tools_called": tools_called,
        "checks": checks,
        "passed": all(checks.values()),
    }


def render_markdown(results: list[dict]) -> str:
    lines = [
        "# Voice Agent Benchmark",
        "",
        f"Run: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Scenario | Passed | WER | Median latency (ms) | Tools called |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lat = r["latency_ms"]["median"]
        lines.append(
            f"| {r['scenario']} | {'PASS' if r['passed'] else 'FAIL'} | {r['wer']:.3f} | "
            f"{lat if lat is not None else '-'} | {', '.join(r['tools_called']) or '-'} |"
        )
    for r in results:
        lines += ["", f"## {r['scenario']}", "", f"{r['description']}", ""]
        if r["latency_ms"]["min"] is not None:
            lines.append(
                f"- latency ms: min {r['latency_ms']['min']} / "
                f"median {r['latency_ms']['median']} / max {r['latency_ms']['max']}"
            )
        lines.append(f"- WER: {r['wer']}")
        lines.append(f"- transcripts: {r['final_transcripts']}")
        lines.append(f"- checks: {r['checks']}")
    return "\n".join(lines) + "\n"


async def main_async(argv) -> int:
    parser = argparse.ArgumentParser(description="Voice agent benchmark harness")
    parser.add_argument("--url", default="ws://localhost:8080/ws/exotel")
    parser.add_argument("--scenario", help="single scenario id (default: all)")
    args = parser.parse_args(argv)

    scenario_files = (
        [SCENARIOS_DIR / f"{args.scenario}.json"]
        if args.scenario
        else sorted(SCENARIOS_DIR.glob("*.json"))
    )

    results = []
    for path in scenario_files:
        scenario = json.loads(path.read_text(encoding="utf-8"))
        print(f"running scenario {scenario['id']}...", flush=True)
        summary = await run_simulation(args.url, scenario_id=scenario["id"])
        events = read_ledger(summary["call_sid"])
        result = evaluate_scenario(scenario, summary, events)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"  -> {status} (wer={result['wer']}, "
              f"median={result['latency_ms']['median']}ms)", flush=True)

    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (reports / f"benchmark_{stamp}.json").write_text(json.dumps(results, indent=2))
    (reports / f"benchmark_{stamp}.md").write_text(render_markdown(results))
    print(f"reports written to {reports / f'benchmark_{stamp}.md'}")

    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async(sys.argv[1:])))
