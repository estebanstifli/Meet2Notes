from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.domain.entities import SummaryResult
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    JobCancelledError,
)
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter

DEFAULT_REPOSITORY = "LiquidAI/LFM2.5-1.2B-Instruct-GGUF"
DEFAULT_FILE = "LFM2.5-1.2B-Instruct-Q4_K_M.gguf"


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
            if config.get("provider") == "openai-compatible":
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
            response_language = str(config.get("response_language") or "").lower()
            if response_language == "es":
                task_prompt = (
                    "Responde exclusivamente en español. Resume esta reunión "
                    "con: visión general, puntos clave, decisiones explícitas, "
                    "tareas y preguntas pendientes. No inventes responsables, "
                    "decisiones, fechas ni hechos. Una persona solo es responsable "
                    "si el texto lo dice expresamente."
                )
            else:
                task_prompt = (
                    "Write the answer in the same language as the transcript. "
                    "Create a useful meeting summary with: overview, key points, "
                    "explicit decisions, action items, and unresolved questions. "
                    "Never invent an owner, decision, deadline, or fact; say that "
                    "it was not specified or omit it."
                )
            messages = [
                {
                    "role": "system",
                    "content": str(config.get("system_prompt", "")),
                },
                {
                    "role": "user",
                    "content": (
                        task_prompt + "\n\nTRANSCRIPT:\n" + transcript
                    ),
                },
            ]
            if config.get("provider") == "openai-compatible":
                result = self._remote_completion(messages, config)
            else:
                path = self._resolve_model_path(config, False)
                model = self._get_model(path, config)
                progress(0.2, "Generating the summary locally")
                maximum_tokens = int(config.get("max_output_tokens", 1024))
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
                        raise JobCancelledError(
                            "Summary generation was cancelled"
                        )
                    choice = chunk.get("choices", [{}])[0]
                    text = choice.get("delta", {}).get("content") or choice.get(
                        "text",
                        "",
                    )
                    if text:
                        parts.append(str(text))
                    if index % 8 == 0:
                        progress(
                            min(0.94, 0.2 + index / max(1, maximum_tokens) * 0.7),
                            f"Generated approximately {index} tokens",
                        )
                result = {
                    "choices": [{"message": {"content": "".join(parts)}}],
                    "usage": {},
                }
            if is_cancelled():
                raise JobCancelledError("Summary generation was cancelled")
            progress(0.98, "Finalizing the summary")
            choice = result.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content") or choice.get("text")
            if not content:
                raise CapabilityUnavailableError(
                    "The AI engine returned an empty summary"
                )
            usage = result.get("usage", {})
            return SummaryResult(
                content_markdown=str(content).strip(),
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
            )
        except Exception as error:
            failure = error
            raise
        finally:
            if not bool(config.get("keep_model_loaded", True)):
                self.unload()
            self._request_finished(failure)

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
        configured = config.get("model_path")
        path = Path(str(configured)).expanduser().resolve() if configured else (
            self.models_dir / str(config.get("model_file", DEFAULT_FILE))
        )
        if path.is_file():
            return path
        if not allow_download:
            raise CapabilityUnavailableError(
                "LFM2.5 1.2B Q4_K_M is not installed. "
                "Use the explicit installation action in Settings."
            )
        if importlib.util.find_spec("huggingface_hub") is None:
            raise CapabilityUnavailableError(
                'huggingface-hub is not installed. Run: python -m pip install -e ".[summaries]"'
            )
        hub = importlib.import_module("huggingface_hub")
        try:
            downloaded = hub.hf_hub_download(
                repo_id=str(config.get("model", DEFAULT_REPOSITORY)),
                filename=str(config.get("model_file", DEFAULT_FILE)),
                local_dir=str(self.models_dir),
            )
        except Exception as error:
            raise CapabilityUnavailableError(
                f"Could not download LFM2.5: {error}"
            ) from error
        return Path(downloaded)

    def _remote_completion(
        self,
        messages: list[dict[str, str]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        base_url = str(config.get("base_url", "")).rstrip("/")
        key = os.getenv(str(config.get("api_key_env", "")), "")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(
                {
                    "model": config.get("model"),
                    "messages": messages,
                    "max_tokens": config.get("max_output_tokens", 1024),
                    "temperature": config.get("temperature", 0.2),
                    "top_p": config.get("top_p", 0.9),
                    "seed": config.get("seed", -1),
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {key}"} if key else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return cast(dict[str, Any], json.load(response))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise CapabilityUnavailableError(
                f"The compatible AI provider could not complete the request: {error}"
            ) from error

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
