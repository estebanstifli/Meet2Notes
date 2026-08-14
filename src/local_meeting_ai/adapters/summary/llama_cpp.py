from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.application.summary_templates import render_summary_template
from local_meeting_ai.domain.entities import SummaryResult
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    JobCancelledError,
)
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter

from .credentials import get_litellm_api_key, secure_storage_status

DEFAULT_REPOSITORY = "LiquidAI/LFM2.5-1.2B-Instruct-GGUF"
DEFAULT_FILE = "LFM2.5-1.2B-Instruct-Q4_K_M.gguf"
DEFAULT_PROFILE = "lfm2.5-1.2b-q4"
LOCAL_MODELS: dict[str, dict[str, Any]] = {
    DEFAULT_PROFILE: {
        "id": DEFAULT_PROFILE,
        "display_name": "LFM2.5 1.2B Q4",
        "description": "Recommended balance for private local meeting summaries.",
        "repository": DEFAULT_REPOSITORY,
        "model_file": DEFAULT_FILE,
        "download_size": "731 MB",
        "quantization": "Q4_K_M",
    },
    "qwen3-0.6b": {
        "id": "qwen3-0.6b",
        "display_name": "Qwen3 0.6B",
        "description": "Smallest multilingual local option.",
        "repository": "Qwen/Qwen3-0.6B-GGUF",
        "model_file": "Qwen3-0.6B-Q8_0.gguf",
        "download_size": "639 MB",
        "quantization": "Q8_0",
    },
    "qwen3-1.7b": {
        "id": "qwen3-1.7b",
        "display_name": "Qwen3 1.7B",
        "description": "Higher-quality multilingual local summaries.",
        "repository": "Qwen/Qwen3-1.7B-GGUF",
        "model_file": "Qwen3-1.7B-Q8_0.gguf",
        "download_size": "1.83 GB",
        "quantization": "Q8_0",
    },
}
LITELLM_PROFILE: dict[str, Any] = {
    "id": "litellm-custom",
    "display_name": "Custom local / remote via LiteLLM",
    "description": "Connect hosted APIs, Ollama, LM Studio or another LiteLLM provider.",
    "repository": None,
    "model_file": None,
    "download_size": "No local installation",
    "quantization": None,
    "managed": False,
}
CUSTOM_GGUF_PROFILE: dict[str, Any] = {
    "id": "custom-gguf",
    "display_name": "Custom GGUF",
    "description": "Load an existing GGUF file directly with llama.cpp.",
    "repository": None,
    "model_file": None,
    "download_size": "User-provided file",
    "quantization": "Detected by llama.cpp",
    "managed": False,
    "external_file": True,
}
logger = logging.getLogger(__name__)


class LlamaCppSummaryEngine:
    """Dedicated resident llama.cpp worker for LFM2.5 and compatible GGUFs."""

    name = "llama-cpp"

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir / "summaries" / "llama-cpp"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="llama-summary",
        )
        self._model_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._model: Any | None = None
        self._model_key: tuple[Any, ...] | None = None
        self._state = "idle"
        self._active_requests = 0
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        dependency = importlib.util.find_spec("llama_cpp") is not None
        system_info = ""
        if dependency:
            try:
                llama_cpp = importlib.import_module("llama_cpp")
                system_info = llama_cpp.llama_print_system_info().decode(
                    "utf-8",
                    errors="replace",
                )
            except (AttributeError, ImportError, OSError, RuntimeError):
                system_info = ""
        upper_info = system_info.upper()
        backend = next(
            (
                name
                for name in ("CUDA", "VULKAN", "METAL", "SYCL", "ROCM")
                if name in upper_info
            ),
            "CPU",
        )
        with self._state_lock:
            state = self._state
            active = self._active_requests
            error = self._last_error
        profiles = []
        for profile in LOCAL_MODELS.values():
            item = dict(profile)
            item.update(
                {
                    "managed": True,
                    "installed": (self.models_dir / str(profile["model_file"])).is_file(),
                    "runtime_available": dependency,
                }
            )
            profiles.append(item)
        profiles.append(
            {
                **CUSTOM_GGUF_PROFILE,
                "installed": False,
                "runtime_available": dependency,
            }
        )
        profiles.append(
            {
                **LITELLM_PROFILE,
                "installed": True,
                "runtime_available": importlib.util.find_spec("litellm") is not None,
            }
        )
        return {
            "engine": self.name,
            "display_name": "llama.cpp · LFM2.5 1.2B Q4_K_M",
            "available": dependency,
            "installed": self._default_model_path().is_file(),
            "install_command": 'python -m pip install -e ".[summaries]"',
            "repository": DEFAULT_REPOSITORY,
            "model_file": DEFAULT_FILE,
            "models_directory": str(self.models_dir),
            "backend": backend.lower(),
            "system_info": system_info.strip(),
            "models": profiles,
            "secure_credentials": secure_storage_status(),
            "worker": {
                "dedicated": True,
                "thread_prefix": "llama-summary",
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": active,
                "model_resident": self._model is not None,
                "last_error": error,
            },
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        await self._submit(self._prepare_sync, config, allow_model_download)

    async def uninstall(self, profile_id: str) -> None:
        await self._submit(self._uninstall_sync, profile_id)

    async def summarize(
        self,
        transcript: str,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> SummaryResult:
        return cast(
            SummaryResult,
            await self._submit(
                self._summarize_sync,
                transcript,
                config,
                progress,
                is_cancelled,
            ),
        )

    def unload(self) -> None:
        with self._model_lock:
            self._model = None
            self._model_key = None
        gc.collect()
        with self._state_lock:
            if not self._active_requests and not self._shutdown:
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
                raise CapabilityUnavailableError(
                    "The summary worker is shutting down"
                )
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(
        self,
        config: dict[str, Any],
        allow_model_download: bool,
    ) -> None:
        self._request_started("loading")
        failure: Exception | None = None
        try:
            if config.get("provider") in {"litellm", "openai-compatible"}:
                if importlib.util.find_spec("litellm") is None:
                    raise CapabilityUnavailableError(
                        'LiteLLM is not installed. Run: python -m pip install -e ".[summaries]"'
                    )
                return
            path = self._resolve_model_path(config, allow_model_download)
            self._get_model(path, config)
        except Exception as error:
            failure = error
            raise
        finally:
            self._request_finished(failure)

    def _summarize_sync(
        self,
        transcript: str,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> SummaryResult:
        self._request_started("inferencing")
        failure: Exception | None = None
        try:
            if is_cancelled():
                raise JobCancelledError("Summary generation was cancelled")
            progress(0.05, "Preparing the meeting transcript")
            prompt_mode = bool(config.get("prompt_mode"))
            task_prompt, template_system = self._summary_instructions(config)
            system_prompt = "\n\n".join(
                part
                for part in (str(config.get("system_prompt", "")), template_system)
                if part
            )
            remote = config.get("provider") in {"litellm", "openai-compatible"}
            model = None
            if not remote:
                path = self._resolve_model_path(config, False)
                model = self._get_model(path, config)
            context_length = max(1024, int(config.get("context_length", 16384)))
            maximum_tokens = min(
                max(128, int(config.get("max_output_tokens", 1024))),
                max(128, context_length // 2),
            )
            messages = self._summary_messages(
                system_prompt,
                task_prompt,
                transcript,
                label="MEETING CONTEXT" if prompt_mode else "TRANSCRIPT",
            )
            if self._fits_context(messages, maximum_tokens, context_length, model):
                progress(
                    0.2,
                    "Generating the summary through LiteLLM"
                    if remote
                    else "Generating the summary locally",
                )
                result = self._complete_once(
                    model,
                    messages,
                    config,
                    maximum_tokens,
                    progress,
                    is_cancelled,
                    0.2,
                    0.94,
                    "Generating notes",
                )
                content, prompt_tokens, completion_tokens = self._completion_content(
                    result
                )
            else:
                content, prompt_tokens, completion_tokens = self._hierarchical_summary(
                    transcript=transcript,
                    task_prompt=task_prompt,
                    system_prompt=system_prompt,
                    config=config,
                    model=model,
                    context_length=context_length,
                    maximum_tokens=maximum_tokens,
                    progress=progress,
                    is_cancelled=is_cancelled,
                )
            if is_cancelled():
                raise JobCancelledError("Summary generation was cancelled")
            progress(0.98, "Finalizing the summary")
            return SummaryResult(
                content_markdown=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception as error:
            failure = error
            raise
        finally:
            if not bool(config.get("keep_model_loaded", True)):
                self.unload()
            self._request_finished(failure)

    def _hierarchical_summary(
        self,
        *,
        transcript: str,
        task_prompt: str,
        system_prompt: str,
        config: dict[str, Any],
        model: Any | None,
        context_length: int,
        maximum_tokens: int,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> tuple[str, int | None, int | None]:
        partial_tokens = min(maximum_tokens, max(256, context_length // 12))
        extraction_prompt = (
            "Extract a compact, faithful evidence report from this part of a longer "
            "meeting. Preserve speaker names, timestamps, facts, explicit decisions, "
            "action items, owners, deadlines, open questions, disagreements and important "
            "context. Do not invent or resolve missing information. Write in the transcript "
            "language. The final requested output will follow this task:\n\n"
            + task_prompt
        )
        overhead = self._estimate_message_tokens(
            self._summary_messages(system_prompt, extraction_prompt, ""),
            model,
        )
        block_tokens = max(
            256,
            self._input_budget(context_length, partial_tokens) - overhead,
        )
        blocks = self._split_transcript(transcript, block_tokens * 3)
        blocks = self._fit_transcript_blocks(
            blocks,
            system_prompt,
            extraction_prompt,
            partial_tokens,
            context_length,
            model,
        )
        estimated = self._estimate_message_tokens(
            self._summary_messages(system_prompt, task_prompt, transcript),
            model,
        )
        progress(
            0.08,
            (
                f"Transcript is approximately {estimated:,} tokens; "
                f"processing {len(blocks)} hierarchical blocks"
            ),
        )
        reports: list[str] = []
        prompt_usage = 0
        completion_usage = 0
        has_usage = False
        for index, block in enumerate(blocks, 1):
            start = 0.1 + (index - 1) / len(blocks) * 0.58
            end = 0.1 + index / len(blocks) * 0.58
            progress(start, f"Extracting evidence from block {index}/{len(blocks)}")
            result = self._complete_once(
                model,
                self._summary_messages(
                    system_prompt,
                    extraction_prompt,
                    block,
                    label=f"PARTIAL TRANSCRIPT {index}/{len(blocks)}",
                ),
                config,
                partial_tokens,
                progress,
                is_cancelled,
                start,
                end,
                f"Block {index}/{len(blocks)}",
            )
            report, prompt_tokens, completion_tokens = self._completion_content(result)
            reports.append(f"## Evidence block {index}\n{report}")
            if prompt_tokens is not None or completion_tokens is not None:
                has_usage = True
            prompt_usage += prompt_tokens or 0
            completion_usage += completion_tokens or 0

        round_number = 0
        final_messages = self._summary_messages(
            system_prompt,
            task_prompt,
            "\n\n".join(reports),
            label="CONSOLIDATED MEETING EVIDENCE",
        )
        while not self._fits_context(
            final_messages,
            maximum_tokens,
            context_length,
            model,
        ):
            round_number += 1
            if round_number > 8:
                raise CapabilityUnavailableError(
                    "The meeting evidence could not be reduced to the configured context window"
                )
            reduction_prompt = (
                "Consolidate these meeting evidence reports into a shorter faithful report. "
                "Deduplicate repeated facts but preserve speakers, timestamps, decisions, "
                "tasks, owners, deadlines, open questions and disagreements. Never invent "
                "missing details. Write in the source language."
            )
            groups = self._pack_reports_for_context(
                reports,
                system_prompt,
                reduction_prompt,
                partial_tokens,
                context_length,
                model,
            )
            progress(
                0.7,
                f"Consolidating evidence · round {round_number} · {len(groups)} groups",
            )
            reduced: list[str] = []
            for index, group in enumerate(groups, 1):
                start = 0.7 + (index - 1) / len(groups) * 0.15
                end = 0.7 + index / len(groups) * 0.15
                result = self._complete_once(
                    model,
                    self._summary_messages(
                        system_prompt,
                        reduction_prompt,
                        group,
                        label=f"EVIDENCE GROUP {index}/{len(groups)}",
                    ),
                    config,
                    partial_tokens,
                    progress,
                    is_cancelled,
                    start,
                    end,
                    f"Consolidating group {index}/{len(groups)}",
                )
                report, prompt_tokens, completion_tokens = self._completion_content(
                    result
                )
                reduced.append(f"## Consolidated evidence {index}\n{report}")
                if prompt_tokens is not None or completion_tokens is not None:
                    has_usage = True
                prompt_usage += prompt_tokens or 0
                completion_usage += completion_tokens or 0
            reports = reduced
            final_messages = self._summary_messages(
                system_prompt,
                task_prompt,
                "\n\n".join(reports),
                label="CONSOLIDATED MEETING EVIDENCE",
            )

        progress(0.86, "Creating final notes from consolidated meeting evidence")
        result = self._complete_once(
            model,
            final_messages,
            config,
            maximum_tokens,
            progress,
            is_cancelled,
            0.86,
            0.96,
            "Creating final notes",
        )
        content, prompt_tokens, completion_tokens = self._completion_content(result)
        if prompt_tokens is not None or completion_tokens is not None:
            has_usage = True
        prompt_usage += prompt_tokens or 0
        completion_usage += completion_tokens or 0
        return (
            content,
            prompt_usage if has_usage else None,
            completion_usage if has_usage else None,
        )

    def _complete_once(
        self,
        model: Any | None,
        messages: list[dict[str, str]],
        config: dict[str, Any],
        maximum_tokens: int,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        progress_start: float,
        progress_end: float,
        phase: str,
    ) -> dict[str, Any]:
        context_length = max(1024, int(config.get("context_length", 16384)))
        if not self._fits_context(messages, maximum_tokens, context_length, model):
            raise CapabilityUnavailableError(
                "An internal summary block exceeded the configured context window"
            )
        if config.get("provider") in {"litellm", "openai-compatible"}:
            progress(progress_start, phase)
            return self._litellm_completion(
                messages,
                config,
                maximum_tokens=maximum_tokens,
            )
        if model is None:
            raise CapabilityUnavailableError("The local summary model is not loaded")
        chunks = model.create_chat_completion(
            messages=messages,
            max_tokens=maximum_tokens,
            temperature=float(config.get("temperature", 0.2)),
            top_p=float(config.get("top_p", 0.9)),
            top_k=int(config.get("top_k", 40)),
            min_p=float(config.get("min_p", 0.05)),
            repeat_penalty=float(config.get("repeat_penalty", 1.1)),
            seed=int(config.get("seed", -1)),
            stream=True,
        )
        parts: list[str] = []
        for index, chunk in enumerate(chunks):
            if is_cancelled():
                raise JobCancelledError("Summary generation was cancelled")
            choice = chunk.get("choices", [{}])[0]
            text = choice.get("delta", {}).get("content") or choice.get("text", "")
            if text:
                parts.append(str(text))
            if index % 16 == 0:
                progress(
                    min(
                        progress_end,
                        progress_start
                        + index / max(1, maximum_tokens) * (progress_end - progress_start),
                    ),
                    f"{phase} · generated approximately {index} tokens",
                )
        return {
            "choices": [{"message": {"content": "".join(parts)}}],
            "usage": {},
        }

    @staticmethod
    def _summary_messages(
        system_prompt: str,
        task_prompt: str,
        context: str,
        *,
        label: str = "TRANSCRIPT",
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{task_prompt}\n\n{label}:\n{context}"},
        ]

    @staticmethod
    def _estimate_message_tokens(
        messages: list[dict[str, str]],
        model: Any | None = None,
    ) -> int:
        characters = sum(len(message.get("content", "")) for message in messages)
        estimate = math.ceil(characters / 3) + len(messages) * 12
        tokenizer = getattr(model, "tokenize", None)
        if not callable(tokenizer):
            return estimate
        serialized = "\n\n".join(
            f"{message.get('role', 'user')}:\n{message.get('content', '')}"
            for message in messages
        )
        try:
            exact = len(tokenizer(serialized.encode("utf-8"), add_bos=True, special=True))
        except (RuntimeError, TypeError, ValueError):
            return estimate
        return max(estimate, exact + 64)

    @classmethod
    def _fits_context(
        cls,
        messages: list[dict[str, str]],
        maximum_tokens: int,
        context_length: int,
        model: Any | None = None,
    ) -> bool:
        return cls._estimate_message_tokens(messages, model) <= cls._input_budget(
            context_length,
            maximum_tokens,
        )

    @staticmethod
    def _input_budget(context_length: int, maximum_tokens: int) -> int:
        return max(256, context_length - maximum_tokens - max(128, context_length // 20))

    @staticmethod
    def _split_transcript(transcript: str, maximum_chars: int) -> list[str]:
        maximum_chars = max(256, maximum_chars)
        blocks: list[str] = []
        current: list[str] = []
        current_length = 0
        for line in transcript.splitlines() or [transcript]:
            pieces = [
                line[offset : offset + maximum_chars]
                for offset in range(0, max(1, len(line)), maximum_chars)
            ] or [""]
            for piece in pieces:
                addition = len(piece) + (1 if current else 0)
                if current and current_length + addition > maximum_chars:
                    blocks.append("\n".join(current))
                    current = []
                    current_length = 0
                current.append(piece)
                current_length += len(piece) + (1 if len(current) > 1 else 0)
        if current:
            blocks.append("\n".join(current))
        return blocks or [""]

    @classmethod
    def _fit_transcript_blocks(
        cls,
        blocks: list[str],
        system_prompt: str,
        task_prompt: str,
        maximum_tokens: int,
        context_length: int,
        model: Any | None,
    ) -> list[str]:
        fitted: list[str] = []
        pending = list(blocks)
        while pending:
            block = pending.pop(0)
            messages = cls._summary_messages(system_prompt, task_prompt, block)
            if cls._fits_context(messages, maximum_tokens, context_length, model):
                fitted.append(block)
                continue
            if len(block) <= 256:
                raise CapabilityUnavailableError(
                    "The summary instructions leave too little room for transcript text"
                )
            midpoint = max(128, len(block) // 2)
            pieces = cls._split_transcript(block, midpoint)
            if len(pieces) == 1:
                pieces = [block[:midpoint], block[midpoint:]]
            pending = [piece for piece in pieces if piece] + pending
        return fitted

    @classmethod
    def _pack_reports_for_context(
        cls,
        reports: list[str],
        system_prompt: str,
        task_prompt: str,
        maximum_tokens: int,
        context_length: int,
        model: Any | None,
    ) -> list[str]:
        groups: list[str] = []
        current: list[str] = []
        for report in reports:
            proposed = "\n\n".join([*current, report])
            messages = cls._summary_messages(system_prompt, task_prompt, proposed)
            if current and not cls._fits_context(
                messages,
                maximum_tokens,
                context_length,
                model,
            ):
                groups.append("\n\n".join(current))
                current = [report]
            else:
                current.append(report)
        if current:
            group = "\n\n".join(current)
            if not cls._fits_context(
                cls._summary_messages(system_prompt, task_prompt, group),
                maximum_tokens,
                context_length,
                model,
            ):
                raise CapabilityUnavailableError(
                    "An intermediate evidence report exceeded the configured context window"
                )
            groups.append(group)
        return groups

    @staticmethod
    def _completion_content(
        result: dict[str, Any],
    ) -> tuple[str, int | None, int | None]:
        choice = result.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content") or choice.get("text")
        if not content:
            raise CapabilityUnavailableError("The AI engine returned an empty summary")
        usage = result.get("usage", {})
        return (
            str(content).strip(),
            _optional_int(usage.get("prompt_tokens")),
            _optional_int(usage.get("completion_tokens")),
        )

    @staticmethod
    def _summary_instructions(config: dict[str, Any]) -> tuple[str, str]:
        response_language = str(config.get("response_language") or "").lower()
        speaker_scope = config.get("summary_scope") == "speaker"
        speaker_name = str(config.get("speaker_name") or "the speaker")
        if config.get("prompt_mode"):
            question = str(config.get("prompt_question") or "").strip()
            history = str(config.get("prompt_history") or "").strip()
            task_prompt = (
                "Answer the user's question in the same language as the question. "
                "Use only the supplied meeting context for claims about meetings. "
                "If the context does not contain the answer, say so clearly. "
                "When R1/R2 source labels exist, cite them inline exactly as [R1]."
                + (f"\n\nRECENT CONVERSATION:\n{history}" if history else "")
                + f"\n\nQUESTION:\n{question}"
            )
        elif speaker_scope and response_language == "es":
            task_prompt = (
                f"Responde exclusivamente en español. Resume únicamente lo que "
                f"ha dicho {speaker_name}: sus ideas, argumentos, datos, opiniones, "
                "propuestas y compromisos explícitos. No resumas la reunión completa, "
                "no atribuyas palabras de otras personas y no inventes información."
            )
        elif speaker_scope:
            task_prompt = (
                f"Write in the transcript language. Summarize only what {speaker_name} "
                "said: their ideas, arguments, facts, opinions, proposals, and explicit "
                "commitments. Do not summarize the whole meeting, attribute other "
                "speakers' words, or invent information."
            )
        elif response_language == "es":
            template = config.get("summary_template")
            task_prompt = "Responde exclusivamente en español. " + (
                render_summary_template(template)
                if isinstance(template, dict)
                else "Resume la reunión con puntos clave, decisiones y tareas."
            )
        else:
            template = config.get("summary_template")
            task_prompt = "Write in the transcript language. " + (
                render_summary_template(template)
                if isinstance(template, dict)
                else "Create a useful meeting summary with decisions and actions."
            )
        template_system = ""
        if not speaker_scope and isinstance(config.get("summary_template"), dict):
            template_system = str(config["summary_template"].get("system_prompt") or "")
        return task_prompt, template_system

    def _get_model(self, path: Path, config: dict[str, Any]) -> Any:
        if importlib.util.find_spec("llama_cpp") is None:
            raise CapabilityUnavailableError(
                'llama-cpp-python is not installed. Run: python -m pip install -e ".[summaries]"'
            )
        key = (
            str(path),
            int(config.get("context_length", 16384)),
            int(config.get("batch_size", 512)),
            int(config.get("micro_batch_size", 128)),
            int(config.get("threads", 0)),
            int(config.get("batch_threads", 0)),
            int(config.get("gpu_layers", -1)),
            int(config.get("main_gpu", 0)),
            str(config.get("split_mode", "layer")),
            bool(config.get("use_mmap", True)),
            bool(config.get("use_mlock", False)),
            bool(config.get("offload_kqv", True)),
            bool(config.get("flash_attention", True)),
            bool(config.get("numa", False)),
        )
        with self._model_lock:
            if self._model is not None and self._model_key == key:
                return self._model
            llama_cpp = importlib.import_module("llama_cpp")
            split_modes = {
                "none": llama_cpp.LLAMA_SPLIT_MODE_NONE,
                "layer": llama_cpp.LLAMA_SPLIT_MODE_LAYER,
                "row": llama_cpp.LLAMA_SPLIT_MODE_ROW,
            }
            threads = int(config.get("threads", 0))
            batch_threads = int(config.get("batch_threads", 0))
            self._model = llama_cpp.Llama(
                model_path=str(path),
                n_ctx=int(config.get("context_length", 16384)),
                n_batch=int(config.get("batch_size", 512)),
                n_ubatch=int(config.get("micro_batch_size", 128)),
                n_threads=threads or None,
                n_threads_batch=batch_threads or None,
                n_gpu_layers=int(config.get("gpu_layers", -1)),
                main_gpu=int(config.get("main_gpu", 0)),
                split_mode=split_modes[str(config.get("split_mode", "layer"))],
                use_mmap=bool(config.get("use_mmap", True)),
                use_mlock=bool(config.get("use_mlock", False)),
                offload_kqv=bool(config.get("offload_kqv", True)),
                flash_attn=bool(config.get("flash_attention", True)),
                numa=bool(config.get("numa", False)),
                seed=int(config.get("seed", -1)),
                verbose=False,
            )
            self._model_key = key
            return self._model

    def _resolve_model_path(
        self,
        config: dict[str, Any],
        allow_download: bool,
    ) -> Path:
        if config.get("profile_id") == "custom-gguf":
            configured = str(config.get("model_path") or "").strip()
            if not configured:
                raise CapabilityUnavailableError(
                    "Choose a local GGUF file in Settings before loading this model."
                )
            path = Path(configured).expanduser().resolve()
            if path.suffix.lower() != ".gguf":
                raise CapabilityUnavailableError("The selected file must use the .gguf extension")
            if not path.is_file():
                raise CapabilityUnavailableError(f"The selected GGUF file does not exist: {path}")
            return path
        profile = self._profile(config.get("profile_id"))
        path = self.models_dir / str(profile["model_file"])
        if path.is_file():
            return path
        if not allow_download:
            raise CapabilityUnavailableError(
                f"{profile['display_name']} is not installed. "
                "Use the explicit installation action in Settings."
            )
        if importlib.util.find_spec("huggingface_hub") is None:
            raise CapabilityUnavailableError(
                'huggingface-hub is not installed. Run: python -m pip install -e ".[summaries]"'
            )
        hub = importlib.import_module("huggingface_hub")
        try:
            logger.info(
                "Downloading %s from %s into %s",
                profile["model_file"],
                profile["repository"],
                self.models_dir,
            )
            downloaded = hub.hf_hub_download(
                repo_id=str(profile["repository"]),
                filename=str(profile["model_file"]),
                local_dir=str(self.models_dir),
            )
        except Exception as error:
            raise CapabilityUnavailableError(
                f"Could not download {profile['display_name']}: {error}"
            ) from error
        logger.info("Saved local summary model %s", downloaded)
        return Path(downloaded)

    def _litellm_completion(
        self,
        messages: list[dict[str, str]],
        config: dict[str, Any],
        *,
        maximum_tokens: int | None = None,
    ) -> dict[str, Any]:
        if importlib.util.find_spec("litellm") is None:
            raise CapabilityUnavailableError(
                'LiteLLM is not installed. Run: python -m pip install -e ".[summaries]"'
            )
        litellm = importlib.import_module("litellm")
        key = get_litellm_api_key() or os.getenv(str(config.get("api_key_env", "")), "")
        base_url = str(config.get("base_url") or "").rstrip("/")
        arguments: dict[str, Any] = {
            "model": str(config.get("model", "")),
            "messages": messages,
            "max_tokens": maximum_tokens or int(config.get("max_output_tokens", 1024)),
            "temperature": float(config.get("temperature", 0.2)),
            "top_p": float(config.get("top_p", 0.9)),
            "drop_params": True,
            "timeout": 300,
        }
        if base_url:
            arguments["api_base"] = base_url
        if key:
            arguments["api_key"] = key
        try:
            response = litellm.completion(**arguments)
            if hasattr(response, "model_dump"):
                return cast(dict[str, Any], response.model_dump())
            return cast(dict[str, Any], response)
        except Exception as error:
            raise CapabilityUnavailableError(
                f"LiteLLM could not complete the request: {error}"
            ) from error

    def _uninstall_sync(self, profile_id: str) -> None:
        profile = self._profile(profile_id)
        path = (self.models_dir / str(profile["model_file"])).resolve()
        if path.parent != self.models_dir.resolve():
            raise CapabilityUnavailableError(
                "Refusing to remove a model outside the managed folder"
            )
        with self._model_lock:
            loaded_path = Path(str(self._model_key[0])).resolve() if self._model_key else None
            if loaded_path == path:
                self._model = None
                self._model_key = None
        if path.is_file():
            path.unlink()
            logger.info("Removed local summary model %s", path)
        gc.collect()

    @staticmethod
    def _profile(profile_id: Any) -> dict[str, Any]:
        profile = LOCAL_MODELS.get(str(profile_id or DEFAULT_PROFILE))
        if not profile:
            raise CapabilityUnavailableError("The selected local AI model is not managed")
        return profile

    def _default_model_path(self) -> Path:
        return self.models_dir / DEFAULT_FILE

    def _request_started(self, state: str) -> None:
        with self._state_lock:
            self._active_requests += 1
            self._state = state
            self._last_error = None

    def _request_finished(self, failure: Exception | None) -> None:
        with self._state_lock:
            self._active_requests = max(0, self._active_requests - 1)
            if failure is not None:
                self._last_error = str(failure)
            if not self._active_requests:
                self._state = (
                    "error"
                    if failure is not None
                    else ("ready" if self._model is not None else "idle")
                )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
