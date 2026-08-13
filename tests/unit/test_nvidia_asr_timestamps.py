from types import SimpleNamespace

from local_meeting_ai.adapters.transcription.nvidia_asr import (
    _decode_parakeet_with_timestamps,
    _parakeet_drafts,
    _parakeet_timestamped_words,
)


class _ParakeetProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def decode(
        self,
        sequences: object,
        **kwargs: object,
    ) -> tuple[str, list[list[dict[str, object]]]]:
        self.calls.append((sequences, kwargs.get("durations")))
        return (
            "Hola mundo.",
            [[
                {"token": "Hola", "start": 0.0, "end": 0.4},
                {"token": " mundo", "start": 0.4, "end": 0.8},
                {"token": ".", "start": 0.8, "end": 0.8},
            ]],
        )


def test_parakeet_decoder_passes_generated_durations_to_processor() -> None:
    processor = _ParakeetProcessor()
    sequences = object()
    durations = object()

    text, tokens = _decode_parakeet_with_timestamps(
        processor,
        sequences,
        SimpleNamespace(durations=durations),
    )

    assert processor.calls == [(sequences, durations)]
    assert text == "Hola mundo."
    assert tokens[-1]["token"] == "."


def test_parakeet_tokens_are_joined_into_timestamped_words() -> None:
    words = _parakeet_timestamped_words(
        [
            {"token": "Hola", "start": 0.0, "end": 0.4},
            {"token": " mundo", "start": 0.4, "end": 0.8},
            {"token": ".", "start": 0.8, "end": 0.8},
            {"token": " Adiós", "start": 1.2, "end": 1.8},
        ]
    )

    assert words == [
        {"word": "Hola ", "start": 0.0, "end": 0.4},
        {"word": "mundo. ", "start": 0.4, "end": 0.8},
        {"word": "Adiós", "start": 1.2, "end": 1.8},
    ]


def test_parakeet_drafts_preserve_words_when_splitting_segments() -> None:
    drafts = _parakeet_drafts(
        text="Hola mundo. Adiós. Hasta luego.",
        timestamp_tokens=[
            {"token": "Hola", "start": 0.0, "end": 0.4},
            {"token": " mundo.", "start": 0.4, "end": 0.8},
            {"token": " Adiós.", "start": 20.0, "end": 20.6},
            {"token": " Hasta luego.", "start": 40.0, "end": 40.8},
        ],
        duration_ms=41_000,
        provider="nvidia-parakeet",
        model="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [draft.text for draft in drafts] == ["Hola mundo. Adiós.", "Hasta luego."]
    assert drafts[0].start_ms == 0
    assert drafts[0].end_ms == 20_600
    assert drafts[0].metadata is not None
    assert drafts[0].metadata["words"] == [
        {"word": "Hola ", "start": 0.0, "end": 0.4},
        {"word": "mundo. ", "start": 0.4, "end": 0.8},
        {"word": "Adiós. ", "start": 20.0, "end": 20.6},
    ]
    assert drafts[1].metadata is not None
    assert drafts[1].metadata["words"] == [
        {"word": "Hasta ", "start": 40.0, "end": 40.8},
        {"word": "luego.", "start": 40.0, "end": 40.8},
    ]
