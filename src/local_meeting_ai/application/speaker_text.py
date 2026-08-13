from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from local_meeting_ai.domain.entities import SpeakerTurn, TranscriptSegment


def speaker_turn_text(
    segments: Sequence[TranscriptSegment],
    turns: Sequence[SpeakerTurn],
) -> list[tuple[int, str]]:
    """Return transcript text clipped to each diarized speaker turn.

    Word timestamps are used when available so a turn that starts or ends in the
    middle of a transcription segment only receives the words actually spoken in
    that audio range. Segment-level speaker assignment remains the fallback for
    older transcriptions without word timestamps.
    """
    fragments: list[tuple[int, str]] = []
    ordered_segments = sorted(segments, key=lambda segment: segment.start_ms)
    for turn in sorted(turns, key=lambda item: item.start_ms):
        pieces: list[str] = []
        for segment in ordered_segments:
            if segment.end_ms <= turn.start_ms:
                continue
            if segment.start_ms >= turn.end_ms:
                break
            raw_words = segment.metadata.get("words")
            if isinstance(raw_words, list) and raw_words:
                words = "".join(
                    str(word.get("word", ""))
                    for word in raw_words
                    if isinstance(word, dict) and _word_overlaps(word, turn)
                ).strip()
                if words:
                    pieces.append(words)
            elif segment.speaker_id == turn.speaker_id and segment.text.strip():
                pieces.append(segment.text.strip())
        text = " ".join(pieces).strip()
        if text:
            fragments.append((turn.start_ms, text))
    return fragments


def _word_overlaps(word: dict[str, Any], turn: SpeakerTurn) -> bool:
    try:
        start_ms = float(word["start"]) * 1000
        end_ms = float(word["end"]) * 1000
    except (KeyError, TypeError, ValueError):
        return False
    return end_ms > turn.start_ms and start_ms < turn.end_ms
