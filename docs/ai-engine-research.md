# Lightweight local AI engine research

Research snapshot: 2026-07-25.

## Implemented baseline

- sherpa-onnx 1.13.4 with quantized Pyannote 3.0 segmentation and 3D-Speaker
  embeddings.
- llama-cpp-python 0.3.34 with the official
  `LFM2.5-1.2B-Instruct-Q4_K_M.gguf` (731 MB).
- Separate resident workers, explicit install/load/unload actions and persistent
  validated runtime parameters.

## Speaker diarization

### Recommended default: sherpa-onnx

sherpa-onnx exposes an offline diarization pipeline built from a pyannote
segmentation model, a 3D-Speaker embedding model and clustering. It has ONNX and
INT8 model options and avoids making PyTorch part of the default application
runtime. Its native and Python interfaces also fit the existing platform-adapter
architecture.

Primary references:

- https://k2-fsa.github.io/sherpa/onnx/c-api/html/speaker_diarization.html
- https://k2-fsa.github.io/sherpa/onnx/index.html

### Optional accuracy tier: pyannote.audio Community-1

pyannote.audio is the stronger full-featured option when diarization quality is
more important than installation size. It is based on PyTorch and therefore
should be an optional engine, not a mandatory dependency.

- https://github.com/pyannote/pyannote-audio

### Not selected as the default: WeSpeaker

WeSpeaker provides strong speaker embeddings and diarization tooling, but its
recommended Python installation includes PyTorch and torchaudio. It does not
offer enough footprint advantage over pyannote to be the lightweight default.

- https://github.com/wenet-e2e/wespeaker

## Meeting-summary models

### Runtime

Use a provider interface with two implementations:

1. Managed llama.cpp for local GGUF models.
2. An OpenAI-compatible HTTP client for existing local servers or remote APIs.

llama.cpp is a compact C/C++ runtime with GGUF quantization, CPU and multiple GPU
backends, an OpenAI-compatible server and Python bindings.

- https://github.com/ggml-org/llama.cpp

### Recommended managed presets

1. **LFM2.5 1.2B Instruct Q4**: best small balanced preset for extraction and
   structured meeting summaries, multilingual including Spanish, with GGUF and
   ONNX distributions.
2. **Qwen3 0.6B Q4**: minimum-footprint multilingual preset.
3. **Qwen3 1.7B Q4**: quality preset for machines with more memory.
4. **Gemma 3 1B Q4**: capable multilingual alternative, but its gated Gemma
   license makes unattended installation less friendly.

Primary references:

- https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct
- https://huggingface.co/Qwen/Qwen3-0.6B
- https://huggingface.co/google/gemma-3-1b-it

### Assessment of the original candidates

- **Llama 3.2 1B/3B** remains viable and is explicitly intended for
  summarization, but newer tiny multilingual models provide a more attractive
  default footprint/quality balance.
- **Qwen2.5 0.5B/1.5B** is a good compact family; Qwen3 supersedes it for this
  new integration.
- **Gemma 2 2B** is useful but larger and gated. Gemma 3 1B is a better current
  small preset.
- **SmolLM2 360M** is primarily English and too limited for the default Spanish
  meeting workflow. The 1.7B model is viable, but Qwen3 is the stronger
  multilingual default.
- **Phi-3.5 Mini 3.8B** has good reasoning and Spanish support, but is too large
  for the lightweight default tier.

Primary references:

- https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct
- https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct
- https://huggingface.co/google/gemma-2-2b-it
- https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct
- https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct
- https://huggingface.co/microsoft/Phi-3.5-mini-instruct

Model-size labels in the interface must always include the quantization. Values
around 0.4–2.5 GB generally describe quantized GGUF files, not native BF16
weights.
