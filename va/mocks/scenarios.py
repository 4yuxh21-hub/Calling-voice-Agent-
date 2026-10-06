"""Benchmark scenario scripts for the mock STT.

A scenario id (e.g. "en_order") maps to fixtures/scenarios/<id>.json whose
utterances carry the ordered transcripts MockSTT emits. The simulator passes
the scenario id via the carrier `start` event custom parameters, making the
mock end-to-end run deterministic.
"""

import json
from pathlib import Path

from loguru import logger

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "scenarios"


def load_script_transcripts(script_id: str | None) -> list[str]:
    if not script_id:
        return []
    path = SCENARIOS_DIR / f"{script_id}.json"
    if not path.exists():
        logger.warning("Scenario script not found: {}", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [u["text"] for u in data.get("utterances", []) if u.get("text")]
