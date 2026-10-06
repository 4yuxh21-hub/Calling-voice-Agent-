"""Generate 8 kHz PCM16 mono speech fixtures for benchmark scenarios.

Uses Windows SAPI (via PowerShell) to synthesize each scenario utterance to
WAV, then resamples to 8 kHz mono with soxr. Hindi/Hinglish texts are Latin
script, so the default English SAPI voice can read them (accented - fine for
VAD/turn-detection/timing tests; STT accuracy is a real-provider concern).

Run:  python scripts/make_fixtures.py
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = PROJECT_ROOT / "fixtures" / "scenarios"
AUDIO_DIR = PROJECT_ROOT / "fixtures" / "audio"


def sapi_tts(text: str, out_wav: Path) -> None:
    escaped = text.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{out_wav}'); "
        f"$s.Speak('{escaped}'); "
        "$s.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=True,
        capture_output=True,
        text=True,
    )


def to_8k_mono(src_wav: Path, dst_wav: Path) -> None:
    data, sr = sf.read(src_wav, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 8000:
        data = soxr.resample(data, sr, 8000)
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    sf.write(dst_wav, pcm, 8000, subtype="PCM_16")


def main() -> int:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AUDIO_DIR / "_tmp"
    tmp.mkdir(exist_ok=True)

    generated = set()
    for scenario_path in sorted(SCENARIOS_DIR.glob("*.json")):
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        for utterance in scenario.get("utterances", []):
            name = utterance["audio"]
            if name in generated:
                continue
            raw = tmp / name
            print(f"synthesizing {name}: {utterance['text'][:60]}...")
            sapi_tts(utterance["text"], raw)
            to_8k_mono(raw, AUDIO_DIR / name)
            raw.unlink()
            generated.add(name)

    tmp.rmdir()
    print(f"done: {len(generated)} fixture(s) in {AUDIO_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
