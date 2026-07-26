(() => {
  "use strict";

  const { api, toast } = window.Meet2Notes;
  const $ = (selector) => document.querySelector(selector);

  const computeTypes = [
    "auto",
    "default",
    "int8",
    "int8_float32",
    "int8_float16",
    "int8_bfloat16",
    "int16",
    "float16",
    "bfloat16",
    "float32",
  ];

  const computeLabels = {
    auto: "Auto · recommended",
    default: "Model default",
    int8: "INT8 · fastest / lowest memory",
    int8_float32: "INT8 + FP32 accumulation",
    int8_float16: "INT8 + FP16 accumulation",
    int8_bfloat16: "INT8 + BF16 accumulation",
    int16: "INT16",
    float16: "FP16 · recommended for CUDA",
    bfloat16: "BF16",
    float32: "FP32 · maximum precision",
  };

  let latestCapabilities = null;
  let currentComputeType = "auto";

  function activateSettingsTab(tabId, updateHash = false) {
    const requested = $(`[data-settings-tab="${tabId}"]`);
    const selected = requested || $('[data-settings-tab="general"]');
    if (!selected) return;
    const activeId = selected.dataset.settingsTab;
    document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab === selected));
      tab.tabIndex = tab === selected ? 0 : -1;
    });
    document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
      const active = panel.id === activeId;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
    if (updateHash && window.location.hash !== `#${activeId}`) {
      history.replaceState(null, "", `#${activeId}`);
    }
  }

  function setSavedState(message = "Saved locally") {
    $("#save-state").innerHTML = `<i></i> ${message}`;
  }

  function populateLanguages(codes, selected) {
    const select = $("#fw-language");
    const displayNames = typeof Intl.DisplayNames === "function"
      ? new Intl.DisplayNames([document.documentElement.lang || "en"], { type: "language" })
      : null;
    const languages = (codes || []).map((code) => {
      let name = code.toUpperCase();
      try {
        name = displayNames?.of(code) || name;
      } catch {
        // Some browsers may not know a valid Whisper language code.
      }
      return { code, name };
    }).sort((left, right) => left.name.localeCompare(right.name));

    select.replaceChildren(new Option("Auto detect · recommended", ""));
    languages.forEach(({ code, name }) => {
      select.add(new Option(`${name} · ${code}`, code));
    });
    select.value = selected || "";
  }

  function selectedRuntimeDevice(transcription) {
    const selected = $("#fw-device").value;
    return selected === "auto"
      ? (transcription.recommended_device || "cpu")
      : selected;
  }

  function populateComputeTypes(preferred = currentComputeType) {
    const transcription = latestCapabilities?.transcription || {};
    const device = selectedRuntimeDevice(transcription);
    const supported = new Set(
      transcription.supported_compute_types?.[device] || [],
    );
    const select = $("#fw-compute-type");
    select.replaceChildren();

    computeTypes.forEach((type) => {
      const option = new Option(computeLabels[type] || type, type);
      const supportedHere = type === "auto" || type === "default" || supported.has(type);
      option.disabled = !supportedHere;
      if (!supportedHere) {
        option.textContent += ` · unavailable on ${device.toUpperCase()}`;
      }
      select.add(option);
    });

    const preferredOption = [...select.options].find(
      (option) => option.value === preferred && !option.disabled,
    );
    select.value = preferredOption ? preferred : "auto";
    currentComputeType = select.value;

    const recommended = transcription.recommended_compute_type || (
      device === "cuda" ? "float16" : "int8"
    );
    $("#engine-compute-help").textContent =
      `Recommended on ${device.toUpperCase()}: ${recommended}. Auto validates this at runtime.`;
  }

  function updateRealtimeControls() {
    const chunk = Number($("#fw-chunk-seconds").value);
    const overlapInput = $("#fw-overlap-seconds");
    const maximumOverlap = Math.max(0, Math.min(5, chunk - 0.25));
    overlapInput.max = String(maximumOverlap);
    if (Number(overlapInput.value) > maximumOverlap) {
      overlapInput.value = String(maximumOverlap);
    }
    $("#fw-chunk-output").value = `${chunk.toFixed(1)} s`;
    $("#fw-overlap-output").value = `${Number(overlapInput.value).toFixed(2).replace(/0$/, "")} s`;
  }

  function renderEngineCapability(capabilities) {
    latestCapabilities = capabilities;
    const transcription = capabilities.transcription || {};
    const worker = transcription.worker || {};
    const state = worker.state || (transcription.available ? "idle" : "unavailable");
    const stateElement = $("#engine-runtime-state");
    const stateLabels = {
      loading: "Loading model…",
      inferencing: "Transcribing",
      ready: "Model ready",
      idle: "Worker idle",
      stopping: "Worker stopping",
      stopped: "Worker stopped",
      error: "Engine error",
      unavailable: "Engine unavailable",
    };
    stateElement.className = "engine-runtime-pill";
    if (state === "ready" || state === "idle") stateElement.classList.add("ready");
    if (!transcription.available || state === "stopped" || state === "error") {
      stateElement.classList.add("error");
    }
    stateElement.innerHTML = `<i></i> ${stateLabels[state] || state}`;

    const loaded = transcription.loaded_models || [];
    $("#engine-loaded-model").textContent = loaded.length
      ? `Resident in memory · ${loaded.join(", ")}`
      : state === "loading" ? "Loading model into memory…" : "No model loaded";

    const cudaDevices = Number(transcription.cuda_devices || 0);
    const cudaOption = [...$("#fw-device").options].find(
      (option) => option.value === "cuda",
    );
    if (cudaOption) {
      cudaOption.disabled = cudaDevices === 0;
      cudaOption.textContent = cudaDevices
        ? `CUDA · ${cudaDevices} NVIDIA GPU${cudaDevices === 1 ? "" : "s"}`
        : "CUDA · no compatible GPU detected";
    }
    $("#engine-device-note").textContent = cudaDevices
      ? `${cudaDevices} CUDA device${cudaDevices === 1 ? "" : "s"} detected; Auto will use CUDA.`
      : "No CUDA device detected; Auto will use the CPU.";

    const active = Number(worker.active_requests || 0);
    $("#engine-worker-summary").textContent = worker.last_error
      ? `Could not prepare the engine · ${worker.last_error}`
      : worker.dedicated
        ? `Dedicated worker · ${worker.dispatcher_threads || 4} dispatcher threads · ${active} active`
        : "Faster Whisper local runtime";

    currentComputeType = $("#fw-compute-type").value || currentComputeType;
    populateComputeTypes(currentComputeType);
  }

  function populateEngineSettings(preferences, capabilities) {
    const config = preferences.faster_whisper || {};
    $("#transcription-engine-select").value =
      preferences.transcription_engine || "faster-whisper";
    $("#fw-model").value = config.model || "small";
    $("#fw-device").value = config.device || "auto";
    $("#fw-device-index").value = config.device_index ?? 0;
    $("#fw-cpu-threads").value = config.cpu_threads ?? 0;
    $("#fw-num-workers").value = config.num_workers ?? 1;
    $("#fw-task").value = config.task || "transcribe";
    $("#fw-beam-size").value = config.beam_size ?? 5;
    $("#fw-vad-silence").value = config.vad_min_silence_ms ?? 500;
    $("#fw-vad-filter").checked = config.vad_filter ?? true;
    $("#fw-word-timestamps").checked = config.word_timestamps ?? false;
    $("#fw-condition-previous").checked =
      config.condition_on_previous_text ?? true;
    $("#fw-keep-loaded").checked = config.keep_model_loaded ?? true;
    $("#fw-chunk-seconds").value = config.realtime_chunk_seconds ?? 3;
    $("#fw-overlap-seconds").value = config.realtime_overlap_seconds ?? 1;
    currentComputeType = config.compute_type || "auto";
    populateLanguages(capabilities.transcription?.languages, config.language);
    renderEngineCapability(capabilities);
    updateRealtimeControls();
  }

  function populateAiSettings(preferences) {
    const config = preferences.summary_engine || {};
    $("#ai-provider").value = config.provider || "local";
    $("#ai-local-runtime").value =
      config.local_runtime || "managed-llama-cpp";
    const model = config.model || "LiquidAI/LFM2.5-1.2B-Instruct-GGUF";
    const modelSelect = $("#ai-model");
    if (![...modelSelect.options].some((option) => option.value === model)) {
      modelSelect.add(new Option(model, model));
    }
    modelSelect.value = model;
    $("#ai-model-path").value = config.model_path || "";
    $("#ai-model-file").value =
      config.model_file || "LFM2.5-1.2B-Instruct-Q4_K_M.gguf";
    $("#ai-base-url").value = config.base_url || "";
    $("#ai-key-env").value = config.api_key_env || "MEET2NOTES_AI_API_KEY";
    $("#ai-context-length").value = config.context_length ?? 16384;
    $("#ai-batch-size").value = config.batch_size ?? 512;
    $("#ai-micro-batch").value = config.micro_batch_size ?? 128;
    $("#ai-threads").value = config.threads ?? 0;
    $("#ai-batch-threads").value = config.batch_threads ?? 0;
    $("#ai-max-output").value = config.max_output_tokens ?? 1024;
    $("#ai-temperature").value = config.temperature ?? 0.2;
    $("#ai-top-p").value = config.top_p ?? 0.9;
    $("#ai-top-k").value = config.top_k ?? 40;
    $("#ai-min-p").value = config.min_p ?? 0.05;
    $("#ai-repeat-penalty").value = config.repeat_penalty ?? 1.1;
    $("#ai-seed").value = config.seed ?? -1;
    $("#ai-gpu-layers").value = config.gpu_layers ?? -1;
    $("#ai-main-gpu").value = config.main_gpu ?? 0;
    $("#ai-split-mode").value = config.split_mode || "layer";
    $("#ai-use-mmap").checked = config.use_mmap ?? true;
    $("#ai-use-mlock").checked = config.use_mlock ?? false;
    $("#ai-offload-kqv").checked = config.offload_kqv ?? true;
    $("#ai-flash-attention").checked = config.flash_attention ?? true;
    $("#ai-numa").checked = config.numa ?? false;
    $("#ai-keep-loaded").checked = config.keep_model_loaded ?? true;
    $("#ai-system-prompt").value = config.system_prompt || "";
    updateAiFields();
  }

  function populateDiarizationSettings(preferences) {
    const config = preferences.diarization || {};
    $("#diarization-segmentation").value =
      config.segmentation_model || "pyannote-3.0";
    $("#diarization-embedding").value =
      config.embedding_model || "3d-speaker";
    $("#diarization-provider").value = config.provider || "cpu";
    $("#diarization-threads").value = config.num_threads ?? 2;
    $("#diarization-speakers").value = config.num_speakers ?? -1;
    $("#diarization-threshold").value = config.cluster_threshold ?? 0.5;
    $("#diarization-min-on").value = config.min_duration_on ?? 0.3;
    $("#diarization-min-off").value = config.min_duration_off ?? 0.5;
    $("#diarization-overlap").value =
      config.minimum_overlap_ratio ?? 0.15;
    $("#diarization-quantized").checked =
      config.quantized_segmentation ?? true;
    $("#diarization-keep-loaded").checked =
      config.keep_model_loaded ?? true;
    $("#diarization-debug").checked = config.debug ?? false;
  }

  function renderWorker(element, capability, label) {
    const worker = capability?.worker || {};
    const state = worker.state || (capability?.available ? "idle" : "unavailable");
    const labels = {
      loading: "Loading…",
      inferencing: "Running",
      ready: "Ready · resident",
      idle: "Ready · unloaded",
      error: "Engine error",
      unavailable: "Dependency missing",
      stopping: "Stopping",
      stopped: "Stopped",
    };
    element.className = "engine-runtime-pill";
    if (["ready", "idle"].includes(state) && capability?.available) {
      element.classList.add("ready");
    }
    if (!capability?.available || ["error", "stopped"].includes(state)) {
      element.classList.add("error");
    }
    element.innerHTML = `<i></i> ${labels[state] || state}`;
    return worker.last_error
      ? `${label} error · ${worker.last_error}`
      : `${label} worker · ${worker.model_resident ? "model resident" : "model unloaded"} · ${worker.active_requests || 0} active`;
  }

  function renderAuxiliaryCapabilities(capabilities) {
    const diarization = capabilities.diarization || {};
    $("#diarization-worker-summary").textContent = renderWorker(
      $("#diarization-runtime-state"),
      diarization,
      "Dedicated diarization",
    );
    $("#diarization-model-state").textContent = diarization.installed
      ? "Models installed locally"
      : diarization.available
        ? "Models require installation"
        : "Install the diarization dependency first";

    const summaries = capabilities.summaries || {};
    $("#ai-worker-summary").textContent = renderWorker(
      $("#ai-runtime-state"),
      summaries,
      "Dedicated summary",
    ) + ` · ${String(summaries.backend || "CPU").toUpperCase()} backend`;
    $("#ai-install").textContent = summaries.installed
      ? "Reinstall LFM2.5 Q4"
      : "Install LFM2.5 Q4 · 731 MB";
  }

  function updateAiFields() {
    const provider = $("#ai-provider").value;
    const isRemote = provider === "openai-compatible";
    $("#ai-base-url").disabled = !isRemote;
    $("#ai-key-env").disabled = !isRemote;
    $("#ai-local-runtime").disabled = provider !== "local";
    $("#ai-model-path").disabled = provider !== "local";
    const state = $("#ai-runtime-state");
    state.innerHTML = `<i></i> ${
      provider === "disabled"
        ? "Disabled"
        : provider === "local"
          ? "Local configuration"
          : "Remote configuration"
    }`;
    state.classList.toggle("ready", provider !== "disabled");
  }

  function renderSystem(info, capabilities) {
    $("#listen-address").textContent = info.listen_address;
    $("#data-directory").textContent = info.data_directory;
    $("#platform-version").textContent = `${info.platform} · Python ${info.python}`;

    const ffmpeg = capabilities.ffmpeg;
    $("#ffmpeg-capability").innerHTML = ffmpeg.available
      ? '<span class="status-badge status-ready">Available</span>'
      : '<span class="status-badge status-failed">Not found</span>';

    const capture = capabilities.audio_capture;
    const captureLabel = capture.available
      ? `${capture.native_api || capture.backend} · ${capture.source_count || 0} sources`
      : "Optional install";
    $("#capture-capability").innerHTML = capture.available
      ? `<span class="status-badge status-ready">${captureLabel}</span>`
      : `<span class="neutral-pill">${captureLabel}</span>`;

    $("#transcription-capability").innerHTML =
      capabilities.features.transcription === "available"
        ? `<span class="status-badge status-ready">${capabilities.transcription.cuda_available ? "CUDA ready" : "CPU ready"}</span>`
        : '<span class="neutral-pill">Optional install</span>';
  }

  async function loadSettings() {
    try {
      const [preferences, info, capabilities] = await Promise.all([
        api("/api/settings"),
        api("/api/info"),
        api("/api/capabilities"),
      ]);
      $("#ui-language").value = preferences.ui_language;
      $("#retention-days").value = preferences.retention_days || "";
      $("#confirm-delete").checked = preferences.confirm_permanent_delete;
      populateEngineSettings(preferences, capabilities);
      populateAiSettings(preferences);
      populateDiarizationSettings(preferences);
      renderAuxiliaryCapabilities(capabilities);
      renderSystem(info, capabilities);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function refreshEngineCapability() {
    try {
      const capabilities = await api("/api/capabilities");
      renderEngineCapability(capabilities);
      renderAuxiliaryCapabilities(capabilities);
    } catch {
      // The initial save succeeded; a transient status refresh is non-critical.
    }
  }

  $("#fw-device")?.addEventListener("change", () => {
    populateComputeTypes("auto");
  });
  $("#fw-compute-type")?.addEventListener("change", (event) => {
    currentComputeType = event.target.value;
  });
  $("#fw-chunk-seconds")?.addEventListener("input", updateRealtimeControls);
  $("#fw-overlap-seconds")?.addEventListener("input", updateRealtimeControls);
  $("#ai-provider")?.addEventListener("change", updateAiFields);

  document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      activateSettingsTab(tab.dataset.settingsTab, true);
    });
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const tabs = [...document.querySelectorAll("[data-settings-tab]")];
      const current = tabs.indexOf(tab);
      const offset = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[(current + offset + tabs.length) % tabs.length];
      next.focus();
      activateSettingsTab(next.dataset.settingsTab, true);
    });
  });
  window.addEventListener("hashchange", () => {
    activateSettingsTab(window.location.hash.slice(1));
  });

  $("#settings-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const retention = $("#retention-days").value;
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          ui_language: $("#ui-language").value,
          retention_days: retention ? Number(retention) : null,
          confirm_permanent_delete: $("#confirm-delete").checked,
        }),
      });
      toast("General settings saved locally.");
      setSavedState("Saved just now");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  $("#engine-settings-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    const chunkSeconds = Number($("#fw-chunk-seconds").value);
    const overlapSeconds = Number($("#fw-overlap-seconds").value);
    if (overlapSeconds >= chunkSeconds) {
      toast("Overlap must be shorter than the audio chunk.", "error");
      return;
    }

    submit.disabled = true;
    try {
      const preferences = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          transcription_engine: $("#transcription-engine-select").value,
          faster_whisper: {
            model: $("#fw-model").value,
            device: $("#fw-device").value,
            device_index: Number($("#fw-device-index").value),
            compute_type: $("#fw-compute-type").value,
            language: $("#fw-language").value || null,
            task: $("#fw-task").value,
            beam_size: Number($("#fw-beam-size").value),
            vad_filter: $("#fw-vad-filter").checked,
            vad_min_silence_ms: Number($("#fw-vad-silence").value),
            word_timestamps: $("#fw-word-timestamps").checked,
            condition_on_previous_text: $("#fw-condition-previous").checked,
            cpu_threads: Number($("#fw-cpu-threads").value),
            num_workers: Number($("#fw-num-workers").value),
            keep_model_loaded: $("#fw-keep-loaded").checked,
            realtime_chunk_seconds: chunkSeconds,
            realtime_overlap_seconds: overlapSeconds,
          },
        }),
      });
      toast("Faster Whisper settings saved.");
      setSavedState("Engine saved just now");
      currentComputeType = preferences.faster_whisper.compute_type;
      await refreshEngineCapability();
      window.setTimeout(refreshEngineCapability, 1200);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  $("#ai-engine-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const provider = $("#ai-provider").value;
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          summary_engine: {
            provider,
            local_runtime: $("#ai-local-runtime").value,
            model: $("#ai-model").value,
            model_file: $("#ai-model-file").value.trim(),
            model_path: $("#ai-model-path").value.trim() || null,
            base_url: $("#ai-base-url").value.trim() || null,
            api_key_env: $("#ai-key-env").value.trim(),
            context_length: Number($("#ai-context-length").value),
            batch_size: Number($("#ai-batch-size").value),
            micro_batch_size: Number($("#ai-micro-batch").value),
            threads: Number($("#ai-threads").value),
            batch_threads: Number($("#ai-batch-threads").value),
            max_output_tokens: Number($("#ai-max-output").value),
            temperature: Number($("#ai-temperature").value),
            top_p: Number($("#ai-top-p").value),
            top_k: Number($("#ai-top-k").value),
            min_p: Number($("#ai-min-p").value),
            repeat_penalty: Number($("#ai-repeat-penalty").value),
            seed: Number($("#ai-seed").value),
            gpu_layers: Number($("#ai-gpu-layers").value),
            main_gpu: Number($("#ai-main-gpu").value),
            split_mode: $("#ai-split-mode").value,
            use_mmap: $("#ai-use-mmap").checked,
            use_mlock: $("#ai-use-mlock").checked,
            offload_kqv: $("#ai-offload-kqv").checked,
            flash_attention: $("#ai-flash-attention").checked,
            numa: $("#ai-numa").checked,
            keep_model_loaded: $("#ai-keep-loaded").checked,
            system_prompt: $("#ai-system-prompt").value.trim(),
          },
        }),
      });
      toast("AI engine settings saved locally.");
      setSavedState("AI settings saved just now");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  $("#diarization-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          diarization: {
            engine: "sherpa-onnx",
            segmentation_model: $("#diarization-segmentation").value,
            embedding_model: $("#diarization-embedding").value,
            quantized_segmentation: $("#diarization-quantized").checked,
            provider: $("#diarization-provider").value,
            num_threads: Number($("#diarization-threads").value),
            num_speakers: Number($("#diarization-speakers").value),
            cluster_threshold: Number($("#diarization-threshold").value),
            min_duration_on: Number($("#diarization-min-on").value),
            min_duration_off: Number($("#diarization-min-off").value),
            minimum_overlap_ratio: Number($("#diarization-overlap").value),
            debug: $("#diarization-debug").checked,
            keep_model_loaded: $("#diarization-keep-loaded").checked,
          },
        }),
      });
      toast("Diarization settings saved locally.");
      setSavedState("Diarization saved just now");
      await refreshEngineCapability();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  async function runtimeAction(kind, action, button, download = false) {
    button.disabled = true;
    const original = button.textContent;
    button.textContent = download ? "Downloading…" : `${action === "unload" ? "Unloading" : "Loading"}…`;
    try {
      await api(
        `/api/engines/${kind}/${action}${download ? "?download=true" : ""}`,
        { method: "POST" },
      );
      toast(
        download
          ? "Model installation completed."
          : `Engine ${action === "unload" ? "unloaded" : "loaded"}.`,
      );
      await refreshEngineCapability();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  $("#diarization-load")?.addEventListener("click", (event) =>
    runtimeAction("diarization", "prepare", event.currentTarget));
  $("#diarization-install")?.addEventListener("click", (event) =>
    runtimeAction("diarization", "prepare", event.currentTarget, true));
  $("#diarization-unload")?.addEventListener("click", (event) =>
    runtimeAction("diarization", "unload", event.currentTarget));
  $("#ai-load")?.addEventListener("click", (event) =>
    runtimeAction("summary", "prepare", event.currentTarget));
  $("#ai-install")?.addEventListener("click", (event) =>
    runtimeAction("summary", "prepare", event.currentTarget, true));
  $("#ai-unload")?.addEventListener("click", (event) =>
    runtimeAction("summary", "unload", event.currentTarget));

  activateSettingsTab(window.location.hash.slice(1) || "general");
  loadSettings();
})();
