(() => {
  "use strict";

  const { api, toast, applyTheme, escapeHTML } = window.Meet2Notes;
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
  let defaultModelsDirectory = "";
  let currentModelsDirectory = "";
  let transcriptionProfiles = [];
  let transcriptionPreferences = null;
  let diarizationPreferences = null;
  let summaryPreferences = null;
  let summaryModels = [];
  let noteFormats = [];
  let activeTranscriptionPurpose = "live";
  let installActivityCursor = 0;
  let installActivityTimer = null;
  let installProgressTimer = null;

  const diarizationEngines = {
    "sherpa-onnx": {
      title: "Sherpa-ONNX diarization",
      description: "Recommended default · local ONNX segmentation and embeddings.",
      note: "Recommended default. Supports CPU, CUDA and CoreML.",
      providers: ["cpu", "cuda", "coreml"],
    },
    diarize: {
      title: "diarize",
      description: "Simple CPU-only local speaker diarization in its own runtime.",
      note: "CPU-only. Its private runtime is isolated so it cannot change the app's PyTorch installation.",
      providers: ["cpu"],
    },
    "pyannote-community-1": {
      title: "Pyannote Community-1",
      description: "Higher-precision local diarization with CPU or NVIDIA CUDA.",
      note: "First installation requires accepting the model conditions on Hugging Face and M2N_PYANNOTE_TOKEN in .env.",
      providers: ["cpu", "cuda"],
    },
  };

  const diarizationProviderLabels = {
    cpu: "CPU · universal",
    cuda: "CUDA · NVIDIA",
    coreml: "CoreML · Apple",
  };

  function ensureDiarizationControls() {
    const form = $("#diarization-form");
    const segmentation = $("#diarization-segmentation");
    if (!form || !segmentation) return;
    const grid = segmentation.closest(".engine-field-grid");
    if (!grid) return;
    if (!$("#diarization-engine")) {
      grid.insertAdjacentHTML("afterbegin", `
        <label class="engine-field">
          <span>Diarization engine</span>
          <select id="diarization-engine">
            <option value="sherpa-onnx">Sherpa-ONNX · recommended default</option>
            <option value="diarize">diarize · CPU-only alternative</option>
            <option value="pyannote-community-1">Pyannote Community-1 · precision alternative</option>
          </select>
        </label>`);
    }
    if (!$("#diarization-engine-note")) {
      grid.insertAdjacentHTML(
        "afterend",
        '<p class="engine-compatibility-note" id="diarization-engine-note"></p>',
      );
    }
    [
      "#diarization-segmentation",
      "#diarization-embedding",
      "#diarization-threshold",
      "#diarization-min-on",
      "#diarization-min-off",
      "#diarization-quantized",
      "#diarization-debug",
    ].forEach((selector) => {
      $(selector)?.closest("label")?.setAttribute("data-diarization-sherpa", "");
    });
    const toggleGrid = $("#diarization-quantized")?.closest(".engine-toggle-grid");
    if (!$("#pyannote-exclusive-setting")) {
      toggleGrid?.insertAdjacentHTML("beforeend", `
        <label class="engine-toggle" id="pyannote-exclusive-setting" hidden>
          <span><strong>Exclusive diarization</strong><small>Use non-overlapping turns for cleaner transcript assignment.</small></span>
          <span class="switch"><input id="diarization-pyannote-exclusive" type="checkbox"><i></i></span>
        </label>`);
    }
    const engineSelect = $("#diarization-engine");
    if (engineSelect && !engineSelect.dataset.controlsBound) {
      engineSelect.dataset.controlsBound = "true";
      engineSelect.addEventListener("change", updateDiarizationEngineFields);
    }
  }

  function updateDiarizationEngineFields() {
    const engineId = $("#diarization-engine")?.value || "sherpa-onnx";
    const details = diarizationEngines[engineId] || diarizationEngines["sherpa-onnx"];
    const provider = $("#diarization-provider");
    document.querySelectorAll("[data-diarization-sherpa]").forEach((element) => {
      element.hidden = engineId !== "sherpa-onnx";
    });
    $("#pyannote-exclusive-setting")?.toggleAttribute(
      "hidden",
      engineId !== "pyannote-community-1",
    );
    if (provider) {
      const currentProvider = provider.value;
      provider.replaceChildren(
        ...details.providers.map(
          (providerId) => new Option(
            diarizationProviderLabels[providerId] || providerId.toUpperCase(),
            providerId,
          ),
        ),
      );
      provider.value = details.providers.includes(currentProvider)
        ? currentProvider
        : details.providers[0];
    }
    const providerLabel = $("#diarization-provider-label");
    if (providerLabel) providerLabel.textContent = "Execution device";
    const providerHelp = $("#diarization-provider-help");
    if (providerHelp) {
      providerHelp.textContent = engineId === "diarize"
        ? "This isolated engine supports CPU execution only."
        : `Available for this engine: ${details.providers.map((item) => item.toUpperCase()).join(", ")}.`;
    }
    const basicTitle = $("#diarization-basic-title");
    const basicDescription = $("#diarization-basic-description");
    const basicCopy = {
      "sherpa-onnx": [
        "Basic options",
        "Speaker count, execution device and startup behavior for Sherpa-ONNX.",
      ],
      diarize: [
        "Basic options",
        "Speaker count and startup behavior for the isolated CPU engine.",
      ],
      "pyannote-community-1": [
        "Basic options",
        "Speaker count, CPU or CUDA execution, and startup behavior for Community-1.",
      ],
    }[engineId] || ["Basic options", details.description];
    if (basicTitle) basicTitle.textContent = basicCopy[0];
    if (basicDescription) basicDescription.textContent = basicCopy[1];
    const advancedTitle = $("#diarization-advanced-title");
    const advancedDescription = $("#diarization-advanced-description");
    const advancedCopy = {
      "sherpa-onnx": [
        "Advanced Sherpa-ONNX options",
        "Segmentation, embeddings, clustering and runtime tuning",
      ],
      diarize: [
        "Advanced diarize options",
        "Transcript assignment tuning",
      ],
      "pyannote-community-1": [
        "Advanced Pyannote Community-1 options",
        "Exclusive turns and transcript assignment tuning",
      ],
    }[engineId] || ["Advanced speaker options", "Model-specific tuning"];
    if (advancedTitle) advancedTitle.textContent = advancedCopy[0];
    if (advancedDescription) advancedDescription.textContent = advancedCopy[1];
    const note = $("#diarization-engine-note");
    if (note) note.textContent = details.note;
    const identity = $("#diarization-form .engine-identity-copy strong");
    if (identity) identity.textContent = details.title;
  }

  function appendInstallLog(message) {
    const log = $("#model-install-log");
    if (!log) return;
    const timestamp = new Date().toLocaleTimeString();
    log.value += `${log.value ? "\n" : ""}[${timestamp}] ${message}`;
    log.scrollTop = log.scrollHeight;
  }

  async function pollInstallActivity() {
    try {
      const entries = await api(`/api/activity?after=${installActivityCursor}&limit=100`);
      entries.forEach((entry) => {
        installActivityCursor = Math.max(installActivityCursor, Number(entry.id) || 0);
        appendInstallLog(`${String(entry.level || "info").toUpperCase()} · ${entry.message}`);
      });
    } catch {
      // The engine request remains authoritative; a transient log poll is optional.
    }
  }

  async function beginInstallModal(title) {
    const dialog = $("#model-install-dialog");
    $("#model-install-title").textContent = title;
    $("#model-install-status").textContent = "Preparing the local installation…";
    $("#model-install-percent").textContent = "5%";
    $("#model-install-phase").textContent = "Preparing";
    $("#model-install-bar").style.width = "5%";
    $("#model-install-log").value = "";
    $("#model-install-close").disabled = true;
    const existing = await api("/api/activity?limit=1");
    installActivityCursor = Number(existing.at(-1)?.id || 0);
    appendInstallLog("Installation requested from Settings.");
    dialog?.showModal();
    installActivityTimer = window.setInterval(pollInstallActivity, 500);
    let visualProgress = 5;
    installProgressTimer = window.setInterval(() => {
      visualProgress = Math.min(88, visualProgress + (visualProgress < 45 ? 4 : 1));
      $("#model-install-percent").textContent = `${visualProgress}%`;
      $("#model-install-phase").textContent = visualProgress < 45 ? "Downloading" : "Verifying files";
      $("#model-install-bar").style.width = `${visualProgress}%`;
    }, 700);
  }

  async function finishInstallModal(
    error = null,
    completedMessage = "Installation complete. The model is ready locally.",
  ) {
    if (installActivityTimer) window.clearInterval(installActivityTimer);
    installActivityTimer = null;
    if (installProgressTimer) window.clearInterval(installProgressTimer);
    installProgressTimer = null;
    await pollInstallActivity();
    const completed = !error;
    $("#model-install-status").textContent = completed
      ? completedMessage
      : "Installation could not be completed.";
    $("#model-install-percent").textContent = completed ? "100%" : "Failed";
    $("#model-install-phase").textContent = completed ? "Ready" : "Review log";
    $("#model-install-bar").style.width = completed ? "100%" : "100%";
    $("#model-install-bar").style.background = completed ? "" : "#d46161";
    appendInstallLog(error ? `ERROR · ${error.message}` : "Completed successfully.");
    $("#model-install-close").disabled = false;
  }

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
    const loadedMessage = loaded.length
      ? `Resident in memory · ${loaded.join(", ")}`
      : state === "loading" ? "Loading model into memory…" : "No model loaded";
    [$("#engine-loaded-model"), $("#engine-loaded-model-detail")]
      .filter(Boolean)
      .forEach((element) => { element.textContent = loadedMessage; });

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
    updateSelectedTranscriptionOptions();
  }

  function populateEngineSettings(preferences, capabilities) {
    const config = preferences.faster_whisper || {};
    $("#transcription-engine-select").value =
      preferences.transcription_engine || "faster-whisper";
    $("#models-directory").value = preferences.models_directory || "";
    const modelsDirectoryLabel = $("#storage-models-directory");
    if (modelsDirectoryLabel) {
      modelsDirectoryLabel.textContent = preferences.models_directory || "Not configured";
    }
    currentModelsDirectory = preferences.models_directory || "";
    const runtimeOverride = Boolean(preferences.models_directory_runtime_override);
    $("#models-directory").disabled = runtimeOverride;
    if ($("#reset-models-directory")) $("#reset-models-directory").disabled = runtimeOverride;
    if ($("#move-models-directory")) $("#move-models-directory").disabled = runtimeOverride;
    if (runtimeOverride) {
      const help = $("#models-directory-help");
      if (help) help.textContent = "This location is controlled by --models-dir or M2N_MODELS_DIR. Remove that startup override to manage it here.";
    } else {
      const help = $("#models-directory-help");
      if (help) help.textContent = "Use an absolute path on a drive with enough free space.";
    }
    $("#fw-model").value = config.model || "small";
    $("#fw-device").value = config.device || "auto";
    $("#fw-device-index").value = config.device_index ?? 0;
    $("#fw-cpu-threads").value = config.cpu_threads ?? 0;
    $("#fw-num-workers").value = config.num_workers ?? 1;
    $("#fw-task").value = config.task || "transcribe";
    $("#fw-beam-size").value = config.beam_size ?? 5;
    $("#fw-vad-silence").value = config.vad_min_silence_ms ?? 500;
    $("#fw-vad-filter").checked = config.vad_filter ?? true;
    $("#fw-word-timestamps").checked = config.word_timestamps ?? true;
    $("#fw-condition-previous").checked =
      config.condition_on_previous_text ?? true;
    $("#fw-preload-on-start").checked = config.preload_on_start ?? true;
    $("#fw-chunk-seconds").value = config.realtime_chunk_seconds ?? 3;
    $("#fw-overlap-seconds").value = config.realtime_overlap_seconds ?? 1;
    currentComputeType = config.compute_type || "auto";
    populateLanguages(capabilities.transcription?.languages, config.language);
    renderEngineCapability(capabilities);
    updateRealtimeControls();
  }

  function renderTranscriptionCatalog(profiles, preferences) {
    transcriptionProfiles = profiles || [];
    transcriptionPreferences = preferences || {};
    renderPurposeCatalog("live", preferences);
    renderPurposeCatalog("final", preferences);
    updateSelectedTranscriptionOptions();
  }

  function selectedTranscriptionProfile() {
    const profileId = transcriptionPreferences?.[
      `${activeTranscriptionPurpose}_transcription_profile`
    ] || "default";
    return transcriptionProfiles.find((profile) => profile.id === profileId)
      || transcriptionProfiles.find((profile) => profile.id === "default")
      || null;
  }

  function selectedEngineCapability(profile) {
    return latestCapabilities?.transcription?.engines?.[profile.engine] || {};
  }

  function setNativeOption(name, value, detail) {
    $(`#native-engine-${name}`).textContent = value;
    $(`#native-engine-${name}-note`).textContent = detail;
  }

  function nativeTimingOption(profile) {
    if (profile.engine === "nvidia-parakeet") {
      return ["Fine timestamps", "Parakeet emits timestamp tokens for the final transcript."];
    }
    if (profile.engine === "nvidia-nemotron") {
      return ["Streaming text", "Optimized for live text; it does not emit fine transcript timestamps."];
    }
    return ["Model timestamps", "Timing is produced by the selected model runtime."];
  }

  function updateSelectedTranscriptionOptions() {
    const profile = selectedTranscriptionProfile();
    if (!profile) return;
    const isFasterWhisper = profile.engine === "faster-whisper";
    $("#faster-whisper-advanced-options").hidden = !isFasterWhisper;
    $("#native-transcription-advanced-options").hidden = isFasterWhisper;
    $("#faster-whisper-device-option").hidden = !isFasterWhisper;
    $("#advanced-transcription-title").textContent = isFasterWhisper
      ? "Advanced Faster Whisper options"
      : `Advanced ${profile.display_name} options`;
    $("#advanced-transcription-subtitle").textContent = isFasterWhisper
      ? "Shared tuning for Faster Whisper profiles"
      : `${activeTranscriptionPurpose === "live" ? "Live" : "Final"} engine runtime and safe model defaults`;
    if (isFasterWhisper) return;

    const capability = selectedEngineCapability(profile);
    const worker = capability.worker || {};
    const cudaAvailable = Boolean(capability.cuda_available);
    const needsPytorchCuda = [
      "nvidia-parakeet",
      "nvidia-nemotron",
    ].includes(profile.engine);
    const device = profile.device === "cpu"
      ? "CPU"
      : cudaAvailable
        ? "CUDA"
        : "CPU fallback";
    const deviceNote = profile.device === "cpu"
      ? "This model is designed for CPU execution."
      : cudaAvailable
        ? "CUDA PyTorch is active for this model."
        : needsPytorchCuda
          ? "CUDA PyTorch is not active; the model will use CPU until it is installed."
          : "The model chooses the best local runtime automatically.";
    const [timing, timingNote] = nativeTimingOption(profile);
    const decoding = `Beam ${profile.beam_size || 1}`;
    const decodingNote = "The selected profile keeps the engine's recommended decoding settings.";
    const resident = worker.model_resident ? "Resident" : "Load on demand";
    const residentNote = worker.model_resident
      ? "The model is currently held in local memory."
      : profile.keep_model_loaded
        ? "It stays in memory after loading, when resources permit."
        : "The model unloads after each request.";

    $("#native-engine-options-title").textContent = profile.display_name;
    $("#native-engine-options-description").textContent = profile.description;
    setNativeOption("device", device, deviceNote);
    setNativeOption("timing", timing, timingNote);
    setNativeOption("decoding", decoding, decodingNote);
    setNativeOption("memory", resident, residentNote);
    $("#native-engine-options-note").textContent = profile.compatibility_note
      || "This engine uses its own safe model defaults. Select Faster Whisper to tune decoding manually.";
    $("#engine-worker-summary").textContent = worker.last_error
      ? `${profile.display_name} error - ${worker.last_error}`
      : `${profile.display_name} worker - ${worker.model_resident ? "model resident" : "ready to load"}`;
  }

  function renderPurposeCatalog(purpose, preferences) {
    const body = $(`#${purpose}-transcription-model-list`);
    if (!body) return;
    const activeProfile = preferences[`${purpose}_transcription_profile`] || "default";
    const activeModelKeys = new Set(
      ["live", "final"].map((stage) => {
        const profileId = preferences[`${stage}_transcription_profile`] || "default";
        const profile = transcriptionProfiles.find((item) => item.id === profileId)
          || transcriptionProfiles.find((item) => item.id === "default");
        return profile ? `${profile.engine}:${profile.model}` : "";
      }),
    );
    body.replaceChildren();
    transcriptionProfiles
      .filter((profile) => purpose === "live" ? profile.supports_live : profile.supports_final)
      .forEach((profile) => {
        const row = document.createElement("tr");
        const selected = profile.id === activeProfile;
        const usedByAnotherStage = activeModelKeys.has(`${profile.engine}:${profile.model}`);
        const installState = profile.installed
          ? (profile.runtime_available ? "Installed" : "Models installed")
          : "Not installed";
        const canSelect = profile.installed && profile.runtime_available;
        const runtimeNote = profile.installed && !profile.runtime_available
          ? '<span class="model-runtime-state">Runtime unavailable on this system</span>'
          : "";
        row.innerHTML = `
          <td class="model-selected${selected ? " active" : ""}" aria-label="${selected ? "Selected" : "Not selected"}">${selected ? "✓" : ""}</td>
          <td>
            <strong>${escapeHTML(profile.display_name)}</strong>
            <small>${escapeHTML(profile.description)}</small>
            ${profile.compatibility_note ? `<span class="model-requirement">${escapeHTML(profile.compatibility_note)}</span>` : ""}
          </td>
          <td>
            <span class="model-install-state ${profile.installed ? "installed" : ""}">${installState}</span>
            ${profile.download_size ? `<small>${escapeHTML(profile.download_size)}</small>` : ""}
            ${runtimeNote}
          </td>
          <td class="table-actions">
            ${profile.installed ? "" : `<button class="table-action" type="button" data-transcription-action="install" data-purpose="${purpose}" data-profile-id="${escapeHTML(profile.id)}">Install</button>`}
            ${selected
              ? '<span class="model-active-label">Active</span>'
              : (canSelect
                ? `<button class="table-action" type="button" data-transcription-action="select" data-purpose="${purpose}" data-profile-id="${escapeHTML(profile.id)}">Select</button>`
                : "")}
            ${canSelect ? `<button class="table-action" type="button" data-transcription-action="load" data-purpose="${purpose}" data-profile-id="${escapeHTML(profile.id)}">Load</button>` : ""}
            ${profile.installed && !usedByAnotherStage ? `<button class="table-action danger" type="button" data-transcription-action="uninstall" data-purpose="${purpose}" data-profile-id="${escapeHTML(profile.id)}">Uninstall</button>` : ""}
          </td>`;
        body.append(row);
      });
  }

  function populateAiSettings(preferences) {
    const config = preferences.summary_engine || {};
    summaryPreferences = preferences;
    const provider = config.provider === "openai-compatible" ? "litellm" : (config.provider || "local");
    $("#ai-provider").value = provider;
    $("#ai-profile-id").value = provider === "litellm"
      ? "litellm-custom"
      : (config.profile_id || profileIdForSummaryModel(config.model));
    $("#ai-local-runtime").value =
      config.local_runtime || "managed-llama-cpp";
    const model = config.model || "LiquidAI/LFM2.5-1.2B-Instruct-GGUF";
    const modelSelect = $("#ai-model");
    if (![...modelSelect.options].some((option) => option.value === model)) {
      modelSelect.add(new Option(model, model));
    }
    modelSelect.value = model;
    $("#ai-model-path").value = config.model_path || "";
    $("#ai-custom-gguf-path").value = config.model_path || "";
    $("#ai-model-file").value =
      config.model_file || "LFM2.5-1.2B-Instruct-Q4_K_M.gguf";
    $("#ai-base-url").value = config.base_url || "";
    $("#ai-litellm-model").value = provider === "litellm" ? (config.model || "") : "";
    $("#ai-litellm-base-url").value = config.base_url || "";
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
    $("#ai-preload-on-start").checked = config.preload_on_start ?? true;
    $("#ai-custom-preload-on-start").checked = config.preload_on_start ?? true;
    $("#ai-system-prompt").value = config.system_prompt || "";
    updateAiFields();
  }

  function profileIdForSummaryModel(model) {
    if (String(model || "").includes("Qwen3-0.6B")) return "qwen3-0.6b";
    if (String(model || "").includes("Qwen3-1.7B")) return "qwen3-1.7b";
    return "lfm2.5-1.2b-q4";
  }

  function renderSummaryCatalog(models = summaryModels, preferences = summaryPreferences) {
    const body = $("#ai-model-list");
    if (!body) return;
    const selected = preferences?.summary_engine?.provider === "openai-compatible"
      ? "litellm-custom"
      : (preferences?.summary_engine?.profile_id || profileIdForSummaryModel(preferences?.summary_engine?.model));
    body.replaceChildren();
    models.forEach((profile) => {
      const active = profile.id === selected;
      const managed = profile.managed !== false;
      const canUse = Boolean(
        profile.runtime_available && (profile.installed || !managed || profile.external_file),
      );
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="model-selected${active ? " active" : ""}" aria-label="${active ? "Selected" : "Not selected"}">${active ? "✓" : ""}</td>
        <td><strong>${escapeHTML(profile.display_name)}</strong><small>${escapeHTML(profile.description || "")}</small>${profile.quantization ? `<span class="model-requirement">${escapeHTML(profile.quantization)}</span>` : ""}</td>
        <td><span class="model-install-state ${profile.installed ? "installed" : ""}">${profile.external_file ? (profile.installed ? "File ready" : "Choose a file") : managed ? (profile.installed ? "Installed" : "Not installed") : "Not required"}</span><small>${escapeHTML(profile.download_size || "")}</small>${profile.runtime_available ? "" : '<span class="model-runtime-state">Runtime dependency unavailable</span>'}</td>
        <td class="table-actions">
          ${managed && !profile.installed ? `<button class="table-action" type="button" data-summary-action="install" data-profile-id="${escapeHTML(profile.id)}">Install</button>` : ""}
          ${active ? '<span class="model-active-label">Active</span>' : (canUse ? `<button class="table-action" type="button" data-summary-action="select" data-profile-id="${escapeHTML(profile.id)}">Select</button>` : "")}
          ${(managed || profile.external_file) && profile.installed ? `<button class="table-action" type="button" data-summary-action="load" data-profile-id="${escapeHTML(profile.id)}">Load</button>${managed ? `<button class="table-action danger" type="button" data-summary-action="uninstall" data-profile-id="${escapeHTML(profile.id)}">Uninstall</button>` : ""}` : ""}
        </td>`;
      body.append(row);
    });
  }

  function renderCredentialStatus(status) {
    const label = $("#ai-api-key-status");
    const clear = $("#ai-api-key-clear");
    if (!label) return;
    label.textContent = !status?.available
      ? "Secure OS credential storage is unavailable."
      : status.configured
        ? "A key is stored securely by the operating system. Leave this blank to keep it."
        : "No key is stored. This is normal for local providers without authentication.";
    if (clear) clear.disabled = !status?.configured;
  }

  function renderNoteFormats(formats = noteFormats) {
    noteFormats = formats;
    const body = $("#note-format-list");
    if (!body) return;
    body.replaceChildren();
    [...formats]
      .sort((left, right) => Number(right.is_default) - Number(left.is_default)
        || Number(right.is_builtin) - Number(left.is_builtin)
        || left.name.localeCompare(right.name))
      .forEach((format) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="model-selected${format.is_default ? " active" : ""}" aria-label="${format.is_default ? "Default" : "Not default"}">${format.is_default ? "✓" : ""}</td>
        <td><strong>${escapeHTML(format.name)}</strong><small>${escapeHTML(format.description || "")}</small><span class="note-format-kind">${format.is_builtin ? "Built-in" : "Custom"}</span></td>
        <td><span class="model-install-state installed">${format.sections.length} sections</span></td>
        <td class="table-actions">
          ${format.is_default ? '<span class="model-active-label">Default</span>' : `<button class="table-action" type="button" data-note-format-action="default" data-template-id="${format.id}">Set default</button>`}
          <button class="table-action" type="button" data-note-format-action="duplicate" data-template-id="${format.id}">Duplicate</button>
          ${format.is_builtin ? "" : `<button class="table-action" type="button" data-note-format-action="edit" data-template-id="${format.id}">Edit</button><button class="table-action danger" type="button" data-note-format-action="delete" data-template-id="${format.id}">Delete</button>`}
        </td>`;
        body.append(row);
      });
  }

  function noteFormatSection(section = {}) {
    const row = document.createElement("div");
    row.className = "note-format-section";
    row.innerHTML = `
      <label class="engine-field"><span>Heading</span><input data-note-section="title" type="text" maxlength="120" required value="${escapeHTML(section.title || "")}" placeholder="Key insights"></label>
      <label class="engine-field wide"><span>Instruction</span><input data-note-section="instruction" type="text" maxlength="1000" required value="${escapeHTML(section.instruction || "")}" placeholder="List the most important findings"></label>
      <label class="engine-field"><span>Format</span><select data-note-section="format"><option value="paragraph">Paragraph</option><option value="list">List</option><option value="text">Plain text</option></select></label>
      <label class="engine-field"><span>Optional item format</span><input data-note-section="item_format" type="text" maxlength="500" value="${escapeHTML(section.item_format || "")}" placeholder="| Task | Owner |"></label>
      <button class="icon-button danger" type="button" data-remove-note-section aria-label="Remove section">×</button>`;
    row.querySelector('[data-note-section="format"]').value = section.format || "list";
    return row;
  }

  function openNoteFormatEditor(format = null, duplicate = false) {
    const form = $("#note-format-form");
    const source = format || {
      name: "",
      description: "",
      system_prompt: "Create accurate notes using only information in the transcript.",
      user_prompt_template: "Turn the transcript into structured notes.",
      sections: [{ title: "Summary", instruction: "Summarize the main topics.", format: "paragraph", item_format: null }],
    };
    $("#note-format-id").value = format && !duplicate ? format.id : "";
    $("#note-format-editor-title").textContent = format && !duplicate ? `Edit ${format.name}` : "New custom format";
    $("#note-format-name").value = duplicate ? `${source.name} copy` : source.name;
    $("#note-format-description").value = source.description || "";
    $("#note-format-system-prompt").value = source.system_prompt;
    $("#note-format-user-prompt").value = source.user_prompt_template;
    const sections = $("#note-format-sections");
    sections.replaceChildren(...source.sections.map((section) => noteFormatSection(section)));
    form.hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function closeNoteFormatEditor() {
    $("#note-format-form").hidden = true;
  }

  async function refreshNoteFormats() {
    renderNoteFormats(await api("/api/summary-templates"));
  }

  function populateDiarizationSettings(preferences) {
    ensureDiarizationControls();
    diarizationPreferences = preferences || {};
    const config = preferences.diarization || {};
    $("#diarization-engine").value = config.engine || "sherpa-onnx";
    $("#diarization-segmentation").value =
      config.segmentation_model || "pyannote-3.0";
    $("#diarization-embedding").value =
      config.embedding_model || "3d-speaker";
    $("#diarization-provider").value = config.provider || "cpu";
    $("#diarization-threads").value = config.num_threads ?? 2;
    $("#diarization-speakers").value = config.num_speakers ?? -1;
    $("#diarization-threshold").value = config.cluster_threshold ?? 0.7;
    $("#diarization-min-on").value = config.min_duration_on ?? 0.3;
    $("#diarization-min-off").value = config.min_duration_off ?? 0.5;
    $("#diarization-overlap").value =
      config.minimum_overlap_ratio ?? 0.15;
    $("#diarization-quantized").checked =
      config.quantized_segmentation ?? true;
    $("#diarization-preload-on-start").checked =
      config.preload_on_start ?? true;
    $("#diarization-recognize-saved").checked =
      config.recognize_saved_speakers ?? true;
    $("#diarization-debug").checked = config.debug ?? false;
    $("#diarization-pyannote-exclusive").checked =
      config.pyannote_exclusive ?? true;
    updateDiarizationEngineFields();
  }

  function renderDiarizationCatalog(capabilities, preferences = diarizationPreferences) {
    const body = $("#diarization-engine-model-list");
    if (!body) return;
    const config = preferences?.diarization || {};
    const selectedEngine = config.engine || "sherpa-onnx";
    const root = capabilities?.diarization || {};
    const engines = root.engines || { [root.engine || "sherpa-onnx"]: root };
    body.replaceChildren();
    Object.entries(engines).forEach(([engineId, capability]) => {
      if (!diarizationEngines[engineId]) return;
      const details = diarizationEngines[engineId];
      const selected = engineId === selectedEngine;
      const installed = Boolean(capability?.installed);
      const runtimeAvailable = Boolean(
        capability?.available && capability?.runtime_available !== false,
      );
      const canSelect = installed && runtimeAvailable;
      const installState = installed
        ? (runtimeAvailable ? "Installed" : "Models installed")
        : "Not installed";
      const runtimeNote = installed && !runtimeAvailable
        ? '<span class="model-runtime-state">Runtime unavailable on this system</span>'
        : "";
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="model-selected${selected ? " active" : ""}" aria-label="${selected ? "Selected" : "Not selected"}">${selected ? "✓" : ""}</td>
        <td>
          <strong>${escapeHTML(capability?.display_name || details.title)}</strong>
          <small>${escapeHTML(details.description)}</small>
          ${details.note ? `<span class="model-requirement">${escapeHTML(details.note)}</span>` : ""}
        </td>
        <td>
          <span class="model-install-state ${installed ? "installed" : ""}">${installState}</span>
          ${capability?.download_size ? `<small>${escapeHTML(capability.download_size)}</small>` : ""}
          ${runtimeNote}
        </td>
        <td class="table-actions">
          ${installed ? "" : `<button class="table-action" type="button" data-diarization-action="install" data-engine-id="${escapeHTML(engineId)}">Install</button>`}
          ${selected
            ? '<span class="model-active-label">Active</span>'
            : (canSelect
              ? `<button class="table-action" type="button" data-diarization-action="select" data-engine-id="${escapeHTML(engineId)}">Select</button>`
              : "")}
          ${canSelect ? `<button class="table-action" type="button" data-diarization-action="load" data-engine-id="${escapeHTML(engineId)}">Load</button>` : ""}
          ${installed && !selected ? `<button class="table-action danger" type="button" data-diarization-action="uninstall" data-engine-id="${escapeHTML(engineId)}">Uninstall</button>` : ""}
        </td>`;
      body.append(row);
    });
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
    latestCapabilities = capabilities;
    const configuredEngine = $("#diarization-engine")?.value || "sherpa-onnx";
    const diarizationRoot = capabilities.diarization || {};
    const diarization = diarizationRoot.engines?.[configuredEngine] || diarizationRoot;
    $("#diarization-worker-summary").textContent = renderWorker(
      $("#diarization-runtime-state"),
      diarization,
      "Dedicated diarization",
    );
    const diarizationModelState = diarization.installed
      ? "Models installed locally"
      : diarization.available
        ? "Models require installation"
        : "Install the diarization dependency first";
    [$("#diarization-model-state"), $("#diarization-model-state-detail")]
      .filter(Boolean)
      .forEach((element) => { element.textContent = diarizationModelState; });
    updateDiarizationEngineFields();
    renderDiarizationCatalog(capabilities);
    $("#ai-model-state").textContent = capabilities.summaries?.installed
      ? "Installed"
      : "Not installed";

    const summaries = capabilities.summaries || {};
    if (summaries.models) {
      summaryModels = summaries.models.map((model) => {
        if (model.id !== "custom-gguf") return model;
        const current = summaryModels.find((item) => item.id === model.id);
        return current ? { ...model, ...current } : model;
      });
      renderSummaryCatalog(summaryModels);
    }
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
    const isRemote = provider === "litellm" || provider === "openai-compatible";
    const isCustomGguf = $("#ai-profile-id").value === "custom-gguf";
    $("#ai-base-url").disabled = !isRemote;
    $("#ai-key-env").disabled = !isRemote;
    $("#ai-local-runtime").disabled = provider !== "local";
    $("#ai-model-path").disabled = provider !== "local";
    $("#ai-local-basic")?.toggleAttribute("hidden", isRemote || isCustomGguf);
    $("#ai-custom-gguf-basic")?.toggleAttribute("hidden", !isCustomGguf);
    $("#ai-litellm-basic")?.toggleAttribute("hidden", !isRemote);
    if ($("#ai-basic-description")) {
      $("#ai-basic-description").textContent = isRemote
        ? "Only the connection details required by LiteLLM are shown here."
        : isCustomGguf
          ? "Choose an existing GGUF file and decide whether to load it at startup."
          : "The selected local model stays loaded after use.";
    }
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
    const address = $("#port-restart-note");
    if (address) address.textContent = `Current address: ${info.listen_address}. Save, then restart Meet2Notes to apply a new port.`;
    $("#data-directory").textContent = info.data_directory;
    const storageDataDirectory = $("#storage-data-directory");
    if (storageDataDirectory) storageDataDirectory.textContent = info.data_directory;
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
      const [preferences, info, capabilities, models, credential, formats] = await Promise.all([
        api("/api/settings"),
        api("/api/info"),
        api("/api/capabilities"),
        api("/api/models/summary"),
        api("/api/settings/summary-api-key"),
        api("/api/summary-templates"),
      ]);
      $("#ui-language").value = preferences.ui_language;
      $("#ui-theme").value = preferences.ui_theme || "system";
      $("#retention-days").value = preferences.retention_days || "";
      $("#confirm-delete").checked = preferences.confirm_permanent_delete;
      $("#http-port").value = preferences.http_port || 8765;
      populateEngineSettings(preferences, capabilities);
      populateAiSettings(preferences);
      summaryModels = models;
      renderSummaryCatalog(models, preferences);
      renderCredentialStatus(credential);
      renderNoteFormats(formats);
      populateDiarizationSettings(preferences);
      renderAuxiliaryCapabilities(capabilities);
      renderSystem(info, capabilities);
      defaultModelsDirectory = info.default_models_directory || "";
      const profiles = await api("/api/models/transcription");
      renderTranscriptionCatalog(profiles, preferences);
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
      const requestedDirectory = $("#models-directory").value.trim();
      if (requestedDirectory && requestedDirectory !== currentModelsDirectory) {
        await moveModelsDirectory(requestedDirectory);
      }
      const retention = $("#retention-days").value;
      const uiTheme = $("#ui-theme").value;
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          ui_language: $("#ui-language").value,
          ui_theme: uiTheme,
          http_port: Number($("#http-port").value),
          retention_days: retention ? Number(retention) : null,
          confirm_permanent_delete: $("#confirm-delete").checked,
        }),
      });
      applyTheme(uiTheme);
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
            keep_model_loaded: true,
            preload_on_start: $("#fw-preload-on-start").checked,
            realtime_chunk_seconds: chunkSeconds,
            realtime_overlap_seconds: overlapSeconds,
          },
        }),
      });
      toast("Transcription settings saved locally.");
      setSavedState("Engine saved just now");
      currentComputeType = preferences.faster_whisper.compute_type;
      populateEngineSettings(preferences, latestCapabilities || {});
      await refreshEngineCapability();
      window.setTimeout(refreshEngineCapability, 1200);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  $("#reset-models-directory")?.addEventListener("click", () => {
    if (!defaultModelsDirectory) return;
    $("#models-directory").value = defaultModelsDirectory;
    $("#models-directory").focus();
    toast("Program model folder selected. Save to move models there.");
  });

  document.querySelectorAll("[data-select-folder]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const location = button.dataset.selectFolder;
        const selection = await api(`/api/storage/${location}/select`, { method: "POST" });
        if (!selection.directory) return;
        if (location === "models") {
          await moveModelsDirectory(selection.directory);
        } else {
          const dialog = $("#models-move-dialog");
          $("#models-move-title").textContent = "Preparing your data move";
          $("#models-move-message").textContent = "Checking the selected folder and scheduling a safe transfer…";
          dialog?.showModal();
          const result = await api("/api/settings/data-directory/schedule", {
            method: "POST",
            body: JSON.stringify({ models_directory: selection.directory }),
          });
          $("#storage-data-directory").textContent = result.directory;
          $("#models-move-message").textContent = result.restart_required
            ? "Ready. Meet2Notes will move the database, meetings and audio safely during the next restart."
            : "This folder is already active.";
          toast(result.restart_required ? "Data folder selected. Restart Meet2Notes to complete the move." : "This data folder is already active.");
          window.setTimeout(() => dialog?.close(), 1400);
        }
      } catch (error) {
        $("#models-move-dialog")?.close();
        toast(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  });

  function confirmModelsOverwrite(inspection) {
    const dialog = $("#models-overwrite-dialog");
    if (!dialog) return Promise.resolve(false);
    $("#models-overwrite-message").textContent = `${inspection.directory} already contains ${inspection.existing_entry_count} item${inspection.existing_entry_count === 1 ? "" : "s"}.`;
    dialog.returnValue = "";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "overwrite"), { once: true });
      dialog.showModal();
    });
  }

  function confirmPytorchCudaInstall(profile, runtime) {
    const dialog = $("#pytorch-cuda-dialog");
    if (!dialog) return Promise.resolve(false);
    const gpuLabel = runtime.nvidia_gpu_detected
      ? "An NVIDIA GPU was detected."
      : "No NVIDIA GPU driver was detected.";
    $("#pytorch-cuda-message").textContent =
      `${profile.display_name} uses CUDA PyTorch for GPU acceleration. ${gpuLabel} `
      + "This downloads several GB of PyTorch CUDA packages into Meet2Notes' private .venv. "
      + "A restart is required before the model can use the GPU.";
    dialog.returnValue = "";
    return new Promise((resolve) => {
      dialog.addEventListener(
        "close",
        () => resolve(dialog.returnValue === "install"),
        { once: true },
      );
      dialog.showModal();
    });
  }

  function profileRequiresPytorchCuda(profile) {
    return ["nvidia-parakeet", "nvidia-nemotron"]
      .includes(profile.engine);
  }

  async function ensurePytorchCuda(profile) {
    if (!profileRequiresPytorchCuda(profile)) return true;
    const runtime = await api("/api/runtimes/pytorch-cuda");
    if (runtime.cuda_available) return true;
    if (runtime.restart_required) {
      toast(
        "CUDA PyTorch is already installed. Use Apagar, then start Meet2Notes again to activate the GPU. The model was not changed.",
        "error",
      );
      return false;
    }
    if (!runtime.can_install) {
      throw new Error(
        runtime.message
          || "CUDA PyTorch is not active and Meet2Notes could not detect an NVIDIA GPU driver. "
            + "Use the CPU engine or install a compatible NVIDIA driver first.",
      );
    }
    const confirmed = await confirmPytorchCudaInstall(profile, runtime);
    if (!confirmed) {
      toast("GPU runtime installation cancelled. The selected model was not changed.");
      return false;
    }
    await beginInstallModal("Installing CUDA PyTorch for GPU transcription");
    try {
      const result = await api("/api/runtimes/pytorch-cuda/install", { method: "POST" });
      await finishInstallModal(null, result.message || "CUDA PyTorch installed. Restart Meet2Notes.");
      toast("CUDA PyTorch installed in .venv. Restart Meet2Notes before using this GPU model.");
    } catch (error) {
      await finishInstallModal(error);
      throw error;
    }
    return false;
  }

  async function moveModelsDirectory(directory) {
    const inspection = await api("/api/settings/models-directory/inspect", {
      method: "POST",
      body: JSON.stringify({ models_directory: directory }),
    });
    if (inspection.requires_overwrite_confirmation) {
      const confirmed = await confirmModelsOverwrite(inspection);
      if (!confirmed) {
        toast("Model folder change cancelled.");
        return null;
      }
    }
    const dialog = $("#models-move-dialog");
    const message = $("#models-move-message");
    message.textContent = "Unloading the local AI workers and moving model files…";
    dialog?.showModal();
    try {
      const preferences = await api("/api/settings/models-directory/move", {
        method: "POST",
        body: JSON.stringify({
          models_directory: inspection.directory,
          overwrite_existing: Boolean(inspection.requires_overwrite_confirmation),
        }),
      });
      currentModelsDirectory = preferences.models_directory;
      $("#models-directory").value = preferences.models_directory;
      $("#storage-models-directory").textContent = preferences.models_directory;
      message.textContent = "The model files are ready in their new folder.";
      toast("Models moved. Restart Meet2Notes to use the new location.");
      return preferences;
    } finally {
      window.setTimeout(() => dialog?.close(), 650);
    }
  }

  $("#move-models-directory")?.addEventListener("click", async (event) => {
    const directory = $("#models-directory").value.trim();
    if (!directory) {
      toast("Enter an absolute folder path first.", "error");
      return;
    }
    event.currentTarget.disabled = true;
    try {
      await moveModelsDirectory(directory);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      event.currentTarget.disabled = false;
    }
  });

  let diagnosticsFilename = "meet2notes-diagnostics.txt";

  $("#run-diagnostics")?.addEventListener("click", async (event) => {
    const dialog = $("#diagnostics-dialog");
    const report = $("#diagnostics-report");
    const status = $("#diagnostics-status");
    const progress = $("#diagnostics-progress");
    event.currentTarget.disabled = true;
    report.value = "";
    status.textContent = "Running independent checks. A failed check will not stop the report…";
    progress.hidden = false;
    dialog?.showModal();
    try {
      const result = await api("/api/diagnostics/report");
      diagnosticsFilename = result.filename || diagnosticsFilename;
      report.value = result.report;
      report.scrollTop = 0;
      status.textContent = "Diagnostics completed. Copy or download this report when opening an issue.";
    } catch (error) {
      report.value = `Meet2Notes could not generate the diagnostic report.\n\n${error.message}`;
      status.textContent = "The report endpoint failed; this message can still be copied.";
    } finally {
      progress.hidden = true;
      event.currentTarget.disabled = false;
    }
  });

  $("#diagnostics-close")?.addEventListener("click", () => $("#diagnostics-dialog")?.close());
  $("#copy-diagnostics")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#diagnostics-report").value);
      toast("Diagnostic report copied.");
    } catch {
      $("#diagnostics-report").select();
      document.execCommand("copy");
      toast("Diagnostic report copied.");
    }
  });
  $("#download-diagnostics")?.addEventListener("click", () => {
    const blob = new Blob([$("#diagnostics-report").value], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = diagnosticsFilename;
    anchor.click();
    URL.revokeObjectURL(url);
  });

  $("#ai-engine-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const provider = $("#ai-provider").value;
      const profileId = $("#ai-profile-id").value;
      const selectedProfile = summaryModels.find((item) => item.id === profileId);
      const remote = provider === "litellm";
      const customGguf = profileId === "custom-gguf";
      const apiKey = $("#ai-api-key")?.value.trim() || "";
      if (remote && apiKey) {
        const credential = await api("/api/settings/summary-api-key", {
          method: "PUT",
          body: JSON.stringify({ api_key: apiKey }),
        });
        $("#ai-api-key").value = "";
        renderCredentialStatus(credential);
      }
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          summary_engine: {
            provider,
            profile_id: profileId,
            local_runtime: $("#ai-local-runtime").value,
            model: remote ? $("#ai-litellm-model").value.trim() : (selectedProfile.repository || "custom-gguf"),
            model_file: remote ? "not-managed.gguf" : (selectedProfile.model_file || "external.gguf"),
            model_path: customGguf ? ($("#ai-custom-gguf-path").value.trim() || null) : null,
            base_url: remote ? ($("#ai-litellm-base-url").value.trim() || null) : null,
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
            preload_on_start: customGguf
              ? $("#ai-custom-preload-on-start").checked
              : $("#ai-preload-on-start").checked,
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

  function diarizationPayload() {
    return {
      engine: $("#diarization-engine").value,
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
      recognize_saved_speakers: $("#diarization-recognize-saved").checked,
      pyannote_exclusive: $("#diarization-pyannote-exclusive").checked,
      debug: $("#diarization-debug").checked,
      keep_model_loaded: true,
      preload_on_start: $("#diarization-preload-on-start").checked,
    };
  }

  $("#diarization-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          diarization: diarizationPayload(),
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
      if (kind === "diarization" && action === "prepare") {
        await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({ diarization: diarizationPayload() }),
        });
      }
      const diarizationTitle = diarizationEngines[$("#diarization-engine")?.value]?.title || "diarization";
      if (download) {
        await beginInstallModal(`Installing ${kind === "summary" ? "LFM2.5" : diarizationTitle}`);
      }
      await api(
        `/api/engines/${kind}/${action}${download ? "?download=true" : ""}`,
        { method: "POST" },
      );
      toast(
        download
          ? "Model installation completed."
          : `Engine ${action === "unload" ? "unloaded" : "loaded"}.`,
      );
      if (download) await finishInstallModal();
      await refreshEngineCapability();
    } catch (error) {
      if (download) await finishInstallModal(error);
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function transcriptionCatalogAction(action, purpose, profileId, button) {
    button.disabled = true;
    try {
      const profile = transcriptionProfiles.find((item) => item.id === profileId);
      if (!profile) throw new Error("The selected model profile is unavailable.");
      if (action !== "uninstall" && !await ensurePytorchCuda(profile)) return;
      if (action === "select") {
        await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({
            [`${purpose}_transcription_engine`]: profile.engine,
            [`${purpose}_transcription_profile`]: profile.id,
          }),
        });
        toast(`${profile.display_name} is now used for ${purpose} transcription.`);
      } else if (action === "uninstall") {
        const confirmed = window.confirm(
          `Uninstall ${profile.display_name}? Its local model files will be removed.`,
        );
        if (!confirmed) return;
        await api(
          `/api/engines/transcription/uninstall?profile_id=${encodeURIComponent(profileId)}`,
          { method: "POST" },
        );
        toast("Model uninstalled and released from memory.");
      } else {
        if (action === "install") {
          await beginInstallModal(
            `Installing ${profile.display_name}${profile.download_size ? ` · ${profile.download_size}` : ""}`,
          );
        }
        await api(`/api/engines/transcription/prepare?profile_id=${encodeURIComponent(profileId)}${action === "install" ? "&download=true" : ""}`, { method: "POST" });
        if (action === "install") await finishInstallModal();
        toast(action === "install" ? "Model installed locally." : "Model loaded into memory.");
      }
      const [profiles, preferences] = await Promise.all([api("/api/models/transcription"), api("/api/settings")]);
      renderTranscriptionCatalog(profiles, preferences);
      await refreshEngineCapability();
    } catch (error) {
      if (action === "install") await finishInstallModal(error);
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function diarizationCatalogAction(action, engineId, button) {
    button.disabled = true;
    try {
      const details = diarizationEngines[engineId];
      if (!details) throw new Error("The selected speaker engine is unavailable.");
      if (action === "select") {
        $("#diarization-engine").value = engineId;
        updateDiarizationEngineFields();
        await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({ diarization: diarizationPayload() }),
        });
        toast(`${details.title} is now used for speaker diarization.`);
      } else if (action === "uninstall") {
        const confirmed = window.confirm(
          `Uninstall ${details.title}? Its local model files will be removed.`,
        );
        if (!confirmed) return;
        await api(
          `/api/engines/diarization/uninstall?engine_id=${encodeURIComponent(engineId)}`,
          { method: "POST" },
        );
        toast("Speaker engine uninstalled and released from memory.");
      } else {
        const installing = action === "install";
        if (installing) await beginInstallModal(`Installing ${details.title}`);
        await api(
          `/api/engines/diarization/prepare?engine_id=${encodeURIComponent(engineId)}${installing ? "&download=true" : ""}`,
          { method: "POST" },
        );
        if (installing) await finishInstallModal();
        toast(installing ? "Speaker engine installed locally." : "Speaker engine loaded into memory.");
      }
      const [preferences, capabilities] = await Promise.all([
        api("/api/settings"),
        api("/api/capabilities"),
      ]);
      populateDiarizationSettings(preferences);
      renderAuxiliaryCapabilities(capabilities);
    } catch (error) {
      if (action === "install") await finishInstallModal(error);
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function summaryCatalogAction(action, profileId, button) {
    button.disabled = true;
    const profile = summaryModels.find((item) => item.id === profileId);
    try {
      if (!profile) throw new Error("The selected AI model is unavailable.");
      if (action === "select") {
        const current = summaryPreferences?.summary_engine || {};
        const remote = profile.id === "litellm-custom";
        const customGguf = profile.id === "custom-gguf";
        const updated = await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({
            summary_engine: {
              ...current,
              provider: remote ? "litellm" : "local",
              profile_id: profile.id,
              local_runtime: "managed-llama-cpp",
              model: remote ? (current.provider === "litellm" ? current.model : "openai/gpt-4.1-mini") : (profile.repository || "custom-gguf"),
              model_file: remote ? "not-managed.gguf" : (profile.model_file || "external.gguf"),
              model_path: customGguf ? (current.profile_id === "custom-gguf" ? current.model_path : null) : null,
              base_url: remote ? current.base_url : null,
              preload_on_start: current.preload_on_start ?? true,
            },
          }),
        });
        summaryPreferences = updated;
        populateAiSettings(updated);
        toast(`${profile.display_name} is now the selected AI engine.`);
      } else if (action === "uninstall") {
        if (!window.confirm(`Uninstall ${profile.display_name}? Its local model file will be removed.`)) return;
        await api(`/api/engines/summary/uninstall?profile_id=${encodeURIComponent(profileId)}`, { method: "POST" });
        toast("Local AI model uninstalled and released from memory.");
      } else {
        const installing = action === "install";
        if (installing) await beginInstallModal(`Installing ${profile.display_name} · ${profile.download_size}`);
        await api(`/api/engines/summary/prepare?profile_id=${encodeURIComponent(profileId)}${installing ? "&download=true" : ""}`, { method: "POST" });
        if (installing) await finishInstallModal();
        toast(installing ? "AI model installed locally." : "AI model loaded into memory.");
      }
      const [models, preferences, capabilities] = await Promise.all([
        api("/api/models/summary"),
        api("/api/settings"),
        api("/api/capabilities"),
      ]);
      summaryModels = models;
      summaryPreferences = preferences;
      renderSummaryCatalog(models, preferences);
      renderAuxiliaryCapabilities(capabilities);
    } catch (error) {
      if (action === "install") await finishInstallModal(error);
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  document.querySelectorAll("[id$='-transcription-model-list']").forEach((list) => {
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-transcription-action]");
      if (button) {
        transcriptionCatalogAction(
          button.dataset.transcriptionAction,
          button.dataset.purpose,
          button.dataset.profileId,
          button,
        );
      }
    });
  });

  $("#diarization-engine-model-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-diarization-action]");
    if (!button) return;
    diarizationCatalogAction(
      button.dataset.diarizationAction,
      button.dataset.engineId,
      button,
    );
  });

  $("#ai-model-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-summary-action]");
    if (button) summaryCatalogAction(button.dataset.summaryAction, button.dataset.profileId, button);
  });

  $("#ai-api-key-clear")?.addEventListener("click", async () => {
    try {
      const status = await api("/api/settings/summary-api-key", { method: "DELETE" });
      renderCredentialStatus(status);
      toast("Saved API key removed from the operating-system credential store.");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#ai-custom-gguf-browse")?.addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try {
      const selected = await api("/api/models/summary/select-file", { method: "POST" });
      if (selected.file) {
        $("#ai-custom-gguf-path").value = selected.file;
        toast("GGUF file selected. Save the AI settings to use it.");
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      event.currentTarget.disabled = false;
    }
  });

  $("#note-format-new")?.addEventListener("click", () => openNoteFormatEditor());
  $("#note-format-cancel")?.addEventListener("click", closeNoteFormatEditor);
  $("#note-format-cancel-bottom")?.addEventListener("click", closeNoteFormatEditor);
  $("#note-format-add-section")?.addEventListener("click", () => {
    $("#note-format-sections").append(noteFormatSection());
  });
  $("#note-format-sections")?.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-note-section]");
    if (!remove) return;
    const sections = $("#note-format-sections");
    if (sections.children.length === 1) {
      toast("A note format needs at least one section.", "error");
      return;
    }
    remove.closest(".note-format-section").remove();
  });
  $("#note-format-list")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-note-format-action]");
    if (!button) return;
    const templateId = Number(button.dataset.templateId);
    const format = noteFormats.find((item) => item.id === templateId);
    if (!format) return;
    button.disabled = true;
    try {
      if (button.dataset.noteFormatAction === "default") {
        renderNoteFormats(await api(`/api/summary-templates/${templateId}/default`, { method: "POST" }));
        toast(`${format.name} will be used for new summaries.`);
      } else if (button.dataset.noteFormatAction === "duplicate") {
        openNoteFormatEditor(format, true);
      } else if (button.dataset.noteFormatAction === "edit") {
        openNoteFormatEditor(format);
      } else if (button.dataset.noteFormatAction === "delete") {
        if (!window.confirm(`Delete the custom note format “${format.name}”?`)) return;
        await api(`/api/summary-templates/${templateId}`, { method: "DELETE" });
        await refreshNoteFormats();
        closeNoteFormatEditor();
        toast("Custom note format deleted.");
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
  $("#note-format-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const sections = [...document.querySelectorAll(".note-format-section")].map((row) => ({
        title: row.querySelector('[data-note-section="title"]').value.trim(),
        instruction: row.querySelector('[data-note-section="instruction"]').value.trim(),
        format: row.querySelector('[data-note-section="format"]').value,
        item_format: row.querySelector('[data-note-section="item_format"]').value.trim() || null,
      }));
      const payload = {
        name: $("#note-format-name").value.trim(),
        description: $("#note-format-description").value.trim() || null,
        system_prompt: $("#note-format-system-prompt").value.trim(),
        user_prompt_template: $("#note-format-user-prompt").value.trim(),
        sections,
      };
      const templateId = $("#note-format-id").value;
      await api(templateId ? `/api/summary-templates/${templateId}` : "/api/summary-templates", {
        method: templateId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      await refreshNoteFormats();
      closeNoteFormatEditor();
      toast(templateId ? "Custom note format updated." : "Custom note format created.");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  document.querySelectorAll("[data-transcription-purpose]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const purpose = tab.dataset.transcriptionPurpose;
      activeTranscriptionPurpose = purpose;
      document.querySelectorAll("[data-transcription-purpose]").forEach((item) => {
        item.setAttribute("aria-selected", String(item === tab));
      });
      document.querySelectorAll("[data-transcription-purpose-panel]").forEach((panel) => {
        const active = panel.dataset.transcriptionPurposePanel === purpose;
        panel.classList.toggle("active", active);
        panel.hidden = !active;
      });
      updateSelectedTranscriptionOptions();
    });
  });
  $("#model-install-close")?.addEventListener("click", () => $("#model-install-dialog")?.close());

  $("#ai-load")?.addEventListener("click", (event) =>
    runtimeAction("summary", "prepare", event.currentTarget));
  $("#ai-install")?.addEventListener("click", (event) =>
    runtimeAction("summary", "prepare", event.currentTarget, true));
  $("#ai-unload")?.addEventListener("click", (event) =>
    runtimeAction("summary", "unload", event.currentTarget));
  $("#ai-load-table")?.addEventListener("click", (event) =>
    runtimeAction("summary", "prepare", event.currentTarget));
  $("#ai-install-table")?.addEventListener("click", (event) =>
    runtimeAction("summary", "prepare", event.currentTarget, true));

  activateSettingsTab(window.location.hash.slice(1) || "general");
  loadSettings();
})();
