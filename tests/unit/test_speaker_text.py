from local_meeting_ai.application.speaker_text import speaker_turn_text
from local_meeting_ai.domain.entities import SpeakerTurn, TranscriptSegment


def test_speaker_turn_text_clips_segment_using_word_timestamps() -> None:
    segment = TranscriptSegment(
        id=1,
        transcription_id=3,
        segment_index=0,
        start_ms=0,
        end_ms=4000,
        text="Hola mundo",
        speaker_id=10,
        confidence=0.9,
        is_final=True,
        metadata={
            "words": [
                {"start": 0.0, "end": 1.8, "word": " Hola"},
                {"start": 2.1, "end": 3.8, "word": " mundo"},
            ]
        },
    )
    turns = [
        SpeakerTurn(1, 2, 3, 10, 0, 2000),
        SpeakerTurn(2, 2, 3, 11, 2000, 4000),
    ]

    assert speaker_turn_text([segment], turns) == [
        (0, "Hola"),
        (2000, "mundo"),
    ]


def test_speaker_turn_text_falls_back_to_assigned_segment() -> None:
    segment = TranscriptSegment(
        id=1,
        transcription_id=3,
        segment_index=0,
        start_ms=0,
        end_ms=2000,
        text="Legacy transcript",
        speaker_id=10,
        confidence=None,
        is_final=True,
        metadata={},
    )
    turns = [
        SpeakerTurn(1, 2, 3, 10, 0, 1000),
        SpeakerTurn(2, 2, 3, 11, 1000, 2000),
    ]

    assert speaker_turn_text([segment], turns) == [(0, "Legacy transcript")]
