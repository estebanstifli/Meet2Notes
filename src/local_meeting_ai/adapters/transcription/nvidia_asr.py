from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.metadata
import importlib.util
import logging
import math
import re
import subprocess
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.adapters.model_files import remove_managed_model_tree
from local_meeting_ai.domain.entities import (
    ModelProfile,
    SegmentDraft,
    TranscriptionEngineRequest,
    TranscriptionResult,
)
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    ProgressReporter,
    SegmentReporter,
)

logger = logging.getLogger(__name__)

PARAKEET_REPOSITORY = "nvidia/parakeet-tdt-0.6b-v3"
NEMOTRON_REPOSITORY = "nvidia/nemotron-3.5-asr-streaming-0.6b"


class NvidiaAsrEngine:
    """Isolated Transformers worker for NVIDIA's open-weight ASR models."""

    def __init__(
        self,
        models_dir: Path,
        *,
        name: str,
        repository: str,
        folder: str,
        display_name: str,
        supports_live: bool,
        languages: str,
        download_size: str,
    ) -> None:
        self.name = name
        self.repository = repository
        self.model_dir = models_dir / folder
        self.display_name = display_name
        self.supports_live = supports_live
        self.languages = languages
        self.download_size = download_size
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self._model: Any | None = None
        self._processor: Any | None = None
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        runtime = _nvidia_runtime_available()
        cuda_available = _torch_cuda_available()
        with self._state_lock:
            state = self._state
            error = self._last_error
        return {
            "engine": self.name,
            "display_name": self.display_name,
            "kind": "local",
            "available": runtime,
            "runtime_available": runtime,
            "installed": self._installed(),
            "installed_models": [self.repository] if self._installed() else [],
            "loaded_models": [self.repository] if self._model is not None else [],
            "supports_live": self.supports_live,
            "supports_final": True,
            "supports_speakers": False,
            "supports_timestamps": self.name == "nvidia-parakeet",
            "languages": self.languages,
            "download_size": self.download_size,
            "cuda_available": cuda_available,
            "memory_note": (
                "The 600M-parameter checkpoint fits comfortably on an 8 GB NVIDIA GPU."
                if cuda_available
                else "PyTorch currently has no CUDA support, so this model will run on CPU."
            ),
            "install_command": 'python -m pip install -e ".[nvidia-asr]"',
            "worker": {
                "dedicated": True,
                "thread_prefix": self.name,
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": 1 if state in {"loading", "inferencing"} else 0,
                "model_resident": self._model is not None,
                "last_error": error,
            },
        }

    async def prepare(
        self,
        profile: ModelProfile,
        *,
        allow_model_download: bool,
    ) -> None:
        await self._submit(self._prepare_sync, profile, allow_model_download)

    async def uninstall(self, profile: ModelProfile) -> None:
        del profile
        await self._submit(self._uninstall_model_sync)

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        return cast(
            TranscriptionResult,
            await self._submit(
                self._transcribe_sync,
                request,
                progress,
                is_cancelled,
                segment_ready,
            ),
        )

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._processor = None
        _release_torch_memory()
        with self._state_lock:
            if not self._shutdown:
                self._state = "idle"

    def shutdown(self) -> None:
        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._state = "stopping"
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.unload()
        with self._state_lock:
            self._state = "stopped"

    async def _submit(self, function: Any, *args: Any) -> Any:
        with self._state_lock:
            if self._shutdown:
                raise CapabilityUnavailableError(f"The {self.display_name} worker is shutting down")
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(self, profile: ModelProfile, allow_model_download: bool) -> None:
        self._set_state("loading")
        try:
            if allow_model_download:
                self._download()
                self._set_state("idle")
                return
            self._load(profile.device)
            self._set_state("ready")
        except Exception as error:
            self._set_error(error)
            raise

    def _download(self) -> None:
        _ensure_nvidia_runtime()
        hub = importlib.import_module("huggingface_hub")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Downloading %s (%s) into %s",
            self.display_name,
            self.download_size,
            self.model_dir,
        )
        hub.snapshot_download(repo_id=self.repository, local_dir=str(self.model_dir))
        (self.model_dir / ".meet2notes-installed").write_text(
            self.repository,
            encoding="utf-8",
        )
        logger.info("%s model download completed", self.display_name)

    def _load(self, device: str) -> tuple[Any, Any]:
        if not self._installed():
            raise CapabilityUnavailableError(f"{self.display_name} is not installed")
        if not _nvidia_runtime_available():
            raise CapabilityUnavailableError(
                "NVIDIA ASR requires Transformers 5.13+. Run: "
                'python -m pip install -e ".[nvidia-asr]"'
            )
        with self._lock:
            if self._model is not None and self._processor is not None:
                return self._model, self._processor
            torch = importlib.import_module("torch")
            transformers = importlib.import_module("transformers")
            processor_class = transformers.AutoProcessor
            model_class_name = (
                "AutoModelForTDT" if self.name == "nvidia-parakeet" else "AutoModelForRNNT"
            )
            model_class = getattr(transformers, model_class_name, None)
            if model_class is None:
                raise CapabilityUnavailableError(
                    f"{self.display_name} requires Transformers 5.13 or newer"
                )
            cuda_available = bool(torch.cuda.is_available())
            if device == "cuda" and not cuda_available:
                raise CapabilityUnavailableError(
                    f"{self.display_name} was configured for CUDA, but this PyTorch "
                    "installation has no CUDA support"
                )
            use_cuda = device != "cpu" and cuda_available
            device_map: Any = {"": "cuda:0"} if use_cuda else "cpu"
            logger.info(
                "Loading %s on %s",
                self.display_name,
                "CUDA" if use_cuda else "CPU",
            )
            self._processor = processor_class.from_pretrained(
                str(self.model_dir),
                local_files_only=True,
            )
            self._model = model_class.from_pretrained(
                str(self.model_dir),
                device_map=device_map,
                dtype="auto",
                local_files_only=True,
            )
            return self._model, self._processor

    def _transcribe_sync(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        self._set_state("inferencing")
        try:
            if is_cancelled():
                raise JobCancelledError("Transcription was cancelled")
            progress(0.04, f"Loading {self.display_name}")
            model, processor = self._load(request.device)
            sampling_rate = int(processor.feature_extractor.sampling_rate)
            audio = _load_audio(request.audio_path, target_sample_rate=sampling_rate)
            processor_kwargs: dict[str, Any] = {
                "sampling_rate": sampling_rate,
                "return_tensors": "pt",
            }
            if self.name == "nvidia-nemotron":
                processor_kwargs["language"] = _nemotron_language(request.language)
            inputs = processor(audio, **processor_kwargs)
            inputs = inputs.to(model.device, dtype=model.dtype)
            if is_cancelled():
                raise JobCancelledError("Transcription was cancelled")
            progress(0.20, f"Running {self.display_name}")
            output = model.generate(**inputs, return_dict_in_generate=True)
            sequences = getattr(output, "sequences", output)
            duration_ms = _wave_duration_ms(request.audio_path)
            if self.name == "nvidia-parakeet":
                text, timestamp_tokens = _decode_parakeet_with_timestamps(
                    processor,
                    sequences,
                    output,
                )
                drafts = _parakeet_drafts(
                    text=text,
                    timestamp_tokens=timestamp_tokens,
                    duration_ms=duration_ms,
                    provider=self.name,
                    model=self.repository,
                )
            else:
                text = processor.decode(sequences[0], skip_special_tokens=True).strip()
                drafts = _single_draft(
                    text=text,
                    duration_ms=duration_ms,
                    provider=self.name,
                    model=self.repository,
                )
            for draft in drafts:
                segment_ready(draft)
            progress(0.98, f"{self.display_name} transcription complete")
            if request.keep_model_loaded:
                self._set_state("ready")
            else:
                self.unload()
            return TranscriptionResult(
                language=request.language,
                language_probability=None,
                duration_ms=duration_ms,
                segments=drafts,
            )
        except Exception as error:
            self._set_error(error)
            raise

    def _installed(self) -> bool:
        return (self.model_dir / ".meet2notes-installed").is_file() or (
            (self.model_dir / "config.json").is_file()
            and (self.model_dir / "model.safetensors").is_file()
        )

    def _uninstall_model_sync(self) -> None:
        with self._state_lock:
            if self._state in {"loading", "inferencing"}:
                raise CapabilityUnavailableError(
                    f"Wait for the active {self.display_name} task to finish before uninstalling it"
                )
        self.unload()
        removed = remove_managed_model_tree(
            root=self.model_dir.parent,
            target=self.model_dir,
            label=self.display_name,
        )
        if not removed:
            raise CapabilityUnavailableError(f"{self.display_name} is not installed locally")
        logger.info("Removed %s model files from %s", self.display_name, self.model_dir)

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state
            self._last_error = None

    def _set_error(self, error: Exception) -> None:
        with self._state_lock:
            self._state = "error"
            self._last_error = str(error)


def build_parakeet_engine(models_dir: Path) -> NvidiaAsrEngine:
    return NvidiaAsrEngine(
        models_dir,
        name="nvidia-parakeet",
        repository=PARAKEET_REPOSITORY,
        folder="nvidia-parakeet-tdt-0.6b-v3",
        display_name="NVIDIA Parakeet TDT 0.6B v3",
        supports_live=False,
        languages="25 European languages including Spanish",
        download_size="~2.6 GB",
    )


def build_nemotron_engine(models_dir: Path) -> NvidiaAsrEngine:
    return NvidiaAsrEngine(
        models_dir,
        name="nvidia-nemotron",
        repository=NEMOTRON_REPOSITORY,
        folder="nvidia-nemotron-3.5-asr-streaming-0.6b",
        display_name="NVIDIA Nemotron 3.5 ASR Streaming 0.6B",
        supports_live=True,
        languages="40 language-locales including Spanish",
        download_size="~2.6 GB",
    )


def _nemotron_language(language: str | None) -> str:
    if not language or language == "auto":
        return "auto"
    aliases = {"es": "es-ES", "en": "en-US", "fr": "fr-FR", "de": "de-DE"}
    return aliases.get(language.lower(), language)


def _decode_parakeet_with_timestamps(
    processor: Any,
    sequences: Any,
    output: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """Decode Parakeet output and retain its token timing information.

    Parakeet's processor only returns timestamps when the generated durations
    are passed back to ``decode``.  Keep the fallback deliberately harmless so
    a Transformers compatibility change cannot make a transcription fail.
    """

    durations = getattr(output, "durations", None)
    if durations is None:
        logger.warning("Parakeet did not return token durations; storing plain text only")
        return processor.decode(sequences[0], skip_special_tokens=True).strip(), []

    decoded = processor.decode(
        sequences,
        durations=durations,
        skip_special_tokens=True,
    )
    if not isinstance(decoded, tuple) or len(decoded) != 2:
        logger.warning("Parakeet timestamp decoding returned an unexpected result")
        return str(decoded).strip(), []

    decoded_text, timestamp_batches = decoded
    if isinstance(decoded_text, (list, tuple)):
        text = str(decoded_text[0]).strip() if decoded_text else ""
    else:
        text = str(decoded_text).strip()
    if not isinstance(timestamp_batches, (list, tuple)) or not timestamp_batches:
        return text, []
    first_batch = timestamp_batches[0]
    if not isinstance(first_batch, (list, tuple)):
        return text, []
    return text, [dict(token) for token in first_batch if isinstance(token, dict)]


def _parakeet_drafts(
    *,
    text: str,
    timestamp_tokens: list[dict[str, Any]],
    duration_ms: int,
    provider: str,
    model: str,
) -> list[SegmentDraft]:
    """Build reasonably sized timestamped segments from Parakeet token output."""

    words = _parakeet_timestamped_words(timestamp_tokens)
    if not words:
        return _single_draft(
            text=text,
            duration_ms=duration_ms,
            provider=provider,
            model=model,
        )

    drafts: list[SegmentDraft] = []
    current_words: list[dict[str, Any]] = []
    segment_start = float(words[0]["start"])
    for word in words:
        current_words.append(word)
        elapsed = float(word["end"]) - segment_start
        ends_sentence = bool(re.search(r"[.!?…](?:\s*)$", str(word["word"])))
        if elapsed < 18.0 or (not ends_sentence and elapsed < 30.0):
            continue
        drafts.append(
            _timestamped_draft(
                index=len(drafts),
                words=current_words,
                duration_ms=duration_ms,
                provider=provider,
                model=model,
            )
        )
        current_words = []
        segment_start = float(word["end"])
    if current_words:
        drafts.append(
            _timestamped_draft(
                index=len(drafts),
                words=current_words,
                duration_ms=duration_ms,
                provider=provider,
                model=model,
            )
        )
    return drafts


def _single_draft(
    *,
    text: str,
    duration_ms: int,
    provider: str,
    model: str,
) -> list[SegmentDraft]:
    if not text:
        return []
    return [
        SegmentDraft(
            index=0,
            start_ms=0,
            end_ms=duration_ms,
            text=text,
            metadata={"provider": provider, "model": model},
        )
    ]


def _timestamped_draft(
    *,
    index: int,
    words: list[dict[str, Any]],
    duration_ms: int,
    provider: str,
    model: str,
) -> SegmentDraft:
    start_ms = max(0, round(float(words[0]["start"]) * 1000))
    end_ms = max(start_ms + 1, round(float(words[-1]["end"]) * 1000))
    if duration_ms:
        if duration_ms == 1:
            start_ms = 0
            end_ms = 1
        else:
            start_ms = min(start_ms, duration_ms - 1)
            end_ms = min(max(start_ms + 1, end_ms), duration_ms)
    return SegmentDraft(
        index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        text="".join(str(word["word"]) for word in words).strip(),
        metadata={"provider": provider, "model": model, "words": words},
    )


def _parakeet_timestamped_words(
    timestamp_tokens: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join Parakeet subword tokens into speaker-attributable words.

    The diarization layer expects ``word``, ``start`` and ``end`` values in
    seconds.  Keeping trailing whitespace preserves natural joins when it
    rebuilds each speaker's text.
    """

    pieces: list[tuple[int, int, str, float, float]] = []
    combined = ""
    for timestamp in timestamp_tokens:
        token = timestamp.get("token")
        if not isinstance(token, str) or not token:
            continue
        try:
            start = float(timestamp["start"])
            end = float(timestamp["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        end = max(start, end)
        token_start = len(combined)
        combined += token
        pieces.append((token_start, len(combined), token, start, end))

    words: list[dict[str, Any]] = []
    for match in re.finditer(r"\S+", combined):
        matched_pieces = [
            piece
            for piece in pieces
            if piece[0] < match.end() and piece[1] > match.start()
        ]
        if not matched_pieces:
            continue
        trailing_space = re.match(r"\s*", combined[match.end() :])
        trailing = trailing_space.group(0) if trailing_space else ""
        words.append(
            {
                "word": match.group(0) + trailing,
                "start": min(piece[3] for piece in matched_pieces),
                "end": max(piece[4] for piece in matched_pieces),
            }
        )
    return words


def _wave_duration_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as audio:
            return round(audio.getnframes() / audio.getframerate() * 1000)
    except (OSError, wave.Error, ZeroDivisionError):
        return 0


def _load_audio(path: Path, *, target_sample_rate: int) -> Any:
    """Load the normalized capture WAV without importing librosa/numba.

    The first NVIDIA installer used librosa merely for WAV loading. On recent
    Python installations that can pull a NumPy version newer than numba
    supports, causing every live window to fail before inference. SoundFile and
    SciPy provide the required mono conversion and resampling without numba.
    """

    soundfile = importlib.import_module("soundfile")
    numpy = importlib.import_module("numpy")
    audio, source_rate = soundfile.read(str(path), dtype="float32", always_2d=False)
    audio = numpy.asarray(audio)
    if audio.ndim == 2:
        audio = audio.mean(axis=1, dtype=numpy.float32)
    if audio.ndim != 1 or audio.size == 0:
        raise CapabilityUnavailableError("The NVIDIA ASR input audio is empty or invalid")
    if int(source_rate) == target_sample_rate:
        return audio
    scipy_signal = importlib.import_module("scipy.signal")
    divisor = math.gcd(int(source_rate), target_sample_rate)
    return scipy_signal.resample_poly(
        audio,
        target_sample_rate // divisor,
        int(source_rate) // divisor,
    ).astype(numpy.float32, copy=False)


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _nvidia_runtime_available() -> bool:
    if not all(
        _module_available(name)
        for name in ("torch", "transformers", "soundfile", "scipy")
    ):
        return False
    try:
        raw_version = importlib.metadata.version("transformers")
        numbers = [int(part) for part in re.findall(r"\d+", raw_version)]
        return tuple([*numbers, 0, 0][:2]) >= (5, 13)
    except (ValueError, importlib.metadata.PackageNotFoundError):
        return False


def _torch_cuda_available() -> bool:
    if not _module_available("torch"):
        return False
    try:
        torch = importlib.import_module("torch")
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _ensure_nvidia_runtime() -> None:
    if _nvidia_runtime_available():
        return
    requirements = (
        "torch>=2.6,<3",
        "transformers>=5.13,<6",
        "accelerate>=1.2,<2",
        "soundfile>=0.12,<1",
        "scipy>=1.12,<2",
    )
    logger.info("Installing the optional NVIDIA ASR Transformers runtime")
    process = subprocess.Popen(
        [sys.executable, "-m", "pip", "install", *requirements],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        if message := line.strip():
            logger.info("NVIDIA ASR runtime · %s", message)
    return_code = process.wait()
    if return_code:
        raise CapabilityUnavailableError(
            f"The NVIDIA ASR runtime installation failed with code {return_code}"
        )
    importlib.invalidate_caches()


def _release_torch_memory() -> None:
    gc.collect()
    if not _module_available("torch"):
        return
    try:
        torch = importlib.import_module("torch")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("Could not release the optional PyTorch CUDA cache", exc_info=True)
