"""Line-delimited JSON worker for the isolated CPU-only ``diarize`` runtime."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def main() -> int:
    # wespeakerruntime hard-codes Path.home()/.wespeaker for its small
    # embedding weight. Redirect that dependency only inside this isolated
    # child so all diarize assets remain under Meet2Notes' model directory.
    runtime_home = os.environ.get("M2N_DIARIZE_RUNTIME_HOME")
    if runtime_home:
        Path.home = classmethod(lambda cls: Path(runtime_home))  # type: ignore[assignment]
    from diarize import diarize  # type: ignore[import-not-found]

    for raw_request in sys.stdin:
        try:
            request = json.loads(raw_request)
            if request.get("action") != "diarize":
                raise ValueError("Unsupported diarize worker request")
            options: dict[str, Any] = {}
            number = request.get("num_speakers")
            if isinstance(number, int) and number > 0:
                options["num_speakers"] = number
            result = diarize(str(request["audio_path"]), **options)
            payload = {
                "ok": True,
                "speaker_count": result.num_speakers,
                "segments": [
                    {
                        "start_ms": round(float(segment.start) * 1000),
                        "end_ms": round(float(segment.end) * 1000),
                        "speaker": str(segment.speaker),
                    }
                    for segment in result.segments
                ],
            }
        except Exception as error:  # The host must receive failures as JSON.
            payload = {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
                "traceback": "".join(traceback.format_exception(error)).strip(),
            }
        print(json.dumps(payload), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
