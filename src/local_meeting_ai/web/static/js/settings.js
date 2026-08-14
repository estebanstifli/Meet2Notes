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
  let liveAssistantCatalog = { settings: {}, models: [], capability: {}, credential: {} };
  let ragPreferences = null;
  let embeddingModels = [];
  let noteFormats = [];
  let pluginCatalog = [];
  let webhookCatalog = { settings: {}, endpoints: [], event_catalog: [], deliveries: [] };
  let activeTranscriptionPurpose = "live";
  let installActivityCursor = 0;
  let installActivityTimer = null;
  let installProgressTimer = null;
  let ragReindexTimer = null;
  let ragReindexRunning = false;
  let ragReindexLastMessage = "";

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

  function appendRagReindexLog(message) {
    const log = $("#rag-reindex-log");
    if (!log || !message) return;
    const timestamp = new Date().toLocaleTimeString();
    log.value += `${log.value ? "\n" : ""}[${timestamp}] ${message}`;
    log.scrollTop = log.scrollHeight;
  }

  function finishRagReindex(job) {
    ragReindexRunning = false;
    ragReindexTimer = null;
    const completed = job.status === "completed";
    $("#rag-reindex-percent").textContent = completed ? "100%" : "Failed";
    $("#rag-reindex-phase").textContent = completed ? "Ready" : "Review log";
    $("#rag-reindex-bar").style.width = "100%";
    $("#rag-reindex-bar").style.background = completed ? "" : "#d46161";
    $("#rag-reindex-status").textContent = completed
      ? "The historical meeting index is ready."
      : "The index could not be rebuilt.";
    if (completed) {
      const result = job.result || {};
      appendRagReindexLog(
        `Completed: ${result.indexed_meetings || 0} meetings and ${result.indexed_chunks || 0} chunks rebuilt; ${result.skipped_meetings || 0} skipped.`,
      );
      toast(`RAG rebuilt: ${result.chunks || 0} chunks across ${result.meetings || 0} meetings.`, "success");
      refreshRagStatus();
    } else {
      appendRagReindexLog(`ERROR · ${job.error_text || "The indexing job failed."}`);
      toast(job.error_text || "The RAG index could not be rebuilt.", "error");
    }
    $("#rag-reindex-close").disabled = false;
  }

  async function pollRagReindex(jobId) {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      const progress = Math.max(0, Math.min(1, Number(job.progress) || 0));
      const percent = Math.round(progress * 100);
      $("#rag-reindex-percent").textContent = `${percent}%`;
      $("#rag-reindex-bar").style.width = `${percent}%`;
      $("#rag-reindex-phase").textContent = job.status === "queued"
        ? "Queued"
        : job.status === "running" ? "Embedding meetings" : "Finalizing";
      if (job.message && job.message !== ragReindexLastMessage) {
        ragReindexLastMessage = job.message;
        appendRagReindexLog(job.message);
      }
      if (["completed", "failed", "cancelled"].includes(job.status)) {
        finishRagReindex(job);
        return;
      }
    } catch (error) {
      appendRagReindexLog(`Waiting for job status: ${error.message}`);
    }
    ragReindexTimer = window.setTimeout(() => pollRagReindex(jobId), 500);
  }

  async function startRagReindex() {
    const confirmButton = $("#rag-reindex-confirm");
    confirmButton.disabled = true;
    try {
      const job = await api("/api/rag/index/jobs", {
        method: "POST",
        body: JSON.stringify({ force: true }),
      });
      $("#rag-reindex-confirmation").hidden = true;
      $("#rag-reindex-progress").hidden = false;
      $("#rag-reindex-status").textContent = "Embedding completed meeting transcripts using the selected RAG settings.";
      ragReindexRunning = true;
      ragReindexLastMessage = "";
      appendRagReindexLog("Rebuild confirmed. The indexing job was added to the local queue.");
      await pollRagReindex(job.uuid);
    } catch (error) {
      confirmButton.disabled = false;
      toast(error.message, "error");
    }
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
    renderRagCredentialStatus(status);
  }

  function renderLiveAssistantCredentialStatus(status) {
    const label = $("#live-assistant-api-key-status");
    const clear = $("#live-assistant-api-key-clear");
    if (!label) return;
    label.textContent = !status?.available
      ? "Secure OS credential storage is unavailable."
      : status.configured
        ? "A separate Live Assistant key is stored by the operating system."
        : "No Live Assistant key is stored. Local endpoints may not require one.";
    if (clear) clear.disabled = !status?.configured;
  }

  function populateLiveAssistant(catalog) {
    liveAssistantCatalog = catalog || liveAssistantCatalog;
    const config = liveAssistantCatalog.settings || {};
    const select = $("#live-assistant-profile");
    if (!select) return;
    select.replaceChildren();
    (liveAssistantCatalog.models || [])
      .filter((profile) => profile.id !== "custom-gguf")
      .forEach((profile) => {
        const remote = profile.id === "litellm-custom";
        const ready = remote
          ? profile.runtime_available !== false
          : Boolean(profile.installed && profile.runtime_available !== false);
        const suffix = remote ? "" : profile.installed ? " · installed" : " · install in AI Engine";
        const option = new Option(`${profile.display_name}${suffix}`, profile.id);
        option.disabled = !ready && profile.id !== config.profile_id;
        select.add(option);
      });
    if (![...select.options].some((option) => option.value === config.profile_id)) {
      select.add(new Option(config.profile_id || "Unavailable model", config.profile_id || ""));
    }
    select.value = config.provider === "litellm" ? "litellm-custom" : config.profile_id;
    $("#live-assistant-enabled").checked = Boolean(config.enabled);
    $("#live-assistant-auto-start").checked = config.auto_start ?? true;
    $("#live-assistant-preload").checked = Boolean(config.preload_on_start);
    $("#live-assistant-model").value = config.provider === "litellm" ? (config.model || "") : "";
    $("#live-assistant-base-url").value = config.base_url || "";
    $("#live-assistant-system-prompt").value = config.system_prompt || "";
    $("#live-assistant-triggers").value = (config.trigger_phrases || []).join("\n");
    $("#live-assistant-interval").value = config.evaluation_interval_seconds ?? 8;
    $("#live-assistant-context-seconds").value = config.recent_context_seconds ?? 180;
    $("#live-assistant-cooldown").value = config.cooldown_seconds ?? 30;
    $("#live-assistant-rate").value = config.max_calls_per_minute ?? 6;
    $("#live-assistant-context-length").value = config.context_length ?? 8192;
    $("#live-assistant-max-output").value = config.max_output_tokens ?? 1024;
    $("#live-assistant-temperature").value = config.temperature ?? 0.2;
    $("#live-assistant-timeout").value = config.request_timeout_seconds ?? 20;
    $("#live-assistant-gpu-layers").value = config.gpu_layers ?? -1;
    renderLiveAssistantCredentialStatus(liveAssistantCatalog.credential);
    const worker = liveAssistantCatalog.capability?.worker || {};
    $("#live-assistant-worker-summary").textContent =
      `Dedicated worker · ${worker.state || "idle"} · ${worker.model_resident ? "model resident" : "no model resident"}`;
    updateLiveAssistantFields();
  }

  function updateLiveAssistantFields() {
    const selectedId = $("#live-assistant-profile")?.value;
    const remote = selectedId === "litellm-custom";
    $("#live-assistant-litellm").hidden = !remote;
    $("#live-assistant-preload").disabled = remote;
    const profile = (liveAssistantCatalog.models || []).find((item) => item.id === selectedId);
    const note = $("#live-assistant-model-note");
    if (note && profile) {
      note.textContent = remote
        ? "LiteLLM runs in the assistant's dedicated worker and can reach local or remote providers."
        : profile.installed
          ? `${profile.description || "Local GGUF model"} A separate model instance may use additional RAM or VRAM.`
          : "Install this model from AI Engine before enabling the Live Assistant.";
    }
    const runtime = $("#live-assistant-runtime");
    if (runtime) runtime.lastChild.textContent = $("#live-assistant-enabled").checked
      ? ` ${remote ? "LiteLLM" : "Independent local runtime"}`
      : " Disabled";
  }

  function renderRagCredentialStatus(status) {
    const label = $("#rag-api-key-status");
    const clear = $("#rag-api-key-clear");
    if (!label) return;
    label.textContent = !status?.available
      ? "Secure OS credential storage is unavailable."
      : status.configured
        ? "A shared LiteLLM key is stored securely by the operating system."
        : "No shared key is stored; this is normal for unauthenticated local providers.";
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
    const engineSelect = $("#diarization-engine");
    Object.entries(engines).forEach(([engineId, capability]) => {
      if (!diarizationEngines[engineId]) {
        diarizationEngines[engineId] = {
          title: capability?.display_name || engineId,
          description: capability?.description || "Plugin-provided diarization engine.",
          note: capability?.compatibility_note || "Configuration is supplied by the plugin provider.",
          providers: capability?.providers || capability?.devices || ["cpu"],
        };
      }
      if (
        engineSelect?.tagName === "SELECT"
        && !Array.from(engineSelect.options).some((option) => option.value === engineId)
      ) {
        engineSelect.add(new Option(diarizationEngines[engineId].title, engineId));
      }
    });
    if (engineSelect) engineSelect.value = selectedEngine;
    body.replaceChildren();
    Object.entries(engines).forEach(([engineId, capability]) => {
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
    renderDiarizationCatalog(capabilities);
    updateDiarizationEngineFields();
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

  function renderPlugins(catalog) {
    pluginCatalog = catalog.plugins || [];
    const apiSummary = $("#plugin-api-summary");
    if (apiSummary) {
      apiSummary.textContent = `Plugin API ${catalog.plugin_api} · Meet2Notes ${catalog.meet2notes} · ${catalog.entry_point_group}`;
    }
    const body = $("#plugin-list");
    if (!body) return;
    if (!pluginCatalog.length) {
      body.innerHTML = '<tr><td colspan="5"><span class="neutral-pill">No plugins discovered</span></td></tr>';
      return;
    }
    body.innerHTML = pluginCatalog.map((plugin) => {
      const hooks = (plugin.hooks || []).map((hook) =>
        `<span class="neutral-pill">${escapeHTML(hook.kind)} · ${escapeHTML(hook.name)}</span>`
      ).join(" ") || '<span class="muted-copy">Registered when enabled</span>';
      const permissions = (plugin.permissions || []).map((permission) =>
        `<span class="neutral-pill">${escapeHTML(permission)}</span>`
      ).join(" ") || '<span class="muted-copy">None declared</span>';
      const failure = plugin.error
        ? `<small class="plugin-error">${escapeHTML(plugin.error)}</small>`
        : "";
      const statusClass = plugin.error
        ? "status-failed"
        : plugin.enabled ? "status-ready" : "status-idle";
      const statusText = plugin.error
        ? "Error"
        : plugin.enabled ? "Enabled" : "Disabled";
      const last = plugin.last_execution
        ? `<small>Last run: ${escapeHTML(plugin.last_execution.status)} · ${plugin.last_execution.duration_ms ?? 0} ms</small>`
        : "";
      const providers = (plugin.providers || []).map((provider) =>
        `<span class="neutral-pill">${escapeHTML(provider.kind)} · ${escapeHTML(provider.display_name)}</span>`
      ).join(" ");
      const configurable = (plugin.providers || []).some(
        (provider) => (provider.settings || []).length,
      );
      return `<tr>
        <td><strong>${escapeHTML(plugin.name)}</strong><small>${escapeHTML(plugin.description)}</small><small>${escapeHTML(plugin.author)} · v${escapeHTML(plugin.version)} · ${escapeHTML(plugin.source)}</small></td>
        <td><div class="plugin-tags">${hooks}${providers ? ` ${providers}` : ""}</div></td>
        <td><div class="plugin-tags">${permissions}</div></td>
        <td><span class="status-badge ${statusClass}">${statusText}</span>${failure}${last}</td>
        <td>${configurable ? `<button class="button" type="button" data-plugin-configure="${escapeHTML(plugin.id)}">Configure</button>` : ""}<button class="button ${plugin.enabled ? "danger" : "primary"}" type="button" data-plugin-toggle="${escapeHTML(plugin.id)}" data-plugin-enabled="${plugin.enabled}" ${!plugin.enabled && (!plugin.compatible || plugin.error) ? "disabled" : ""}>${plugin.enabled ? "Disable" : "Enable"}</button></td>
      </tr>`;
    }).join("");
  }

  function pluginSettingFields(plugin) {
    const fields = new Map();
    (plugin.providers || []).forEach((provider) => {
      (provider.settings || []).forEach((field) => fields.set(field.id, field));
    });
    return [...fields.values()];
  }

  function openPluginSettings(pluginId) {
    const plugin = pluginCatalog.find((item) => item.id === pluginId);
    if (!plugin) return;
    const editor = $("#plugin-settings-editor");
    const fields = $("#plugin-settings-fields");
    $("#plugin-settings-id").value = plugin.id;
    $("#plugin-settings-title").textContent = `${plugin.name} settings`;
    fields.replaceChildren();
    pluginSettingFields(plugin).forEach((field) => {
      const label = document.createElement("label");
      label.className = "engine-field";
      const value = plugin.settings?.[field.id] ?? field.default ?? "";
      let control;
      if (field.kind === "select") {
        control = document.createElement("select");
        (field.choices || []).forEach((choice) => control.add(new Option(choice, choice)));
        control.value = value;
      } else {
        control = document.createElement("input");
        control.type = field.kind === "boolean" ? "checkbox"
          : ["integer", "number"].includes(field.kind) ? "number" : "text";
        if (field.kind === "boolean") control.checked = Boolean(value);
        else control.value = value;
        if (field.minimum != null) control.min = field.minimum;
        if (field.maximum != null) control.max = field.maximum;
        if (field.kind === "number") control.step = "any";
        control.placeholder = field.placeholder || "";
      }
      control.dataset.pluginSetting = field.id;
      control.dataset.pluginSettingKind = field.kind;
      control.required = Boolean(field.required);
      label.innerHTML = `<span>${escapeHTML(field.label)}</span>`;
      label.append(control);
      if (field.description) label.insertAdjacentHTML("beforeend", `<small>${escapeHTML(field.description)}</small>`);
      fields.append(label);
    });
    editor.hidden = false;
    editor.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function updateRagFields() {
    const profileId = $("#rag-profile-id").value || "bge-m3";
    const selectedProfile = embeddingModels.find((item) => item.id === profileId);
    const customGguf = profileId === "custom-gguf";
    const remote = profileId === "litellm-custom";
    $("#rag-bge-basic").hidden = profileId !== "bge-m3";
    $("#rag-custom-gguf-basic").hidden = !customGguf;
    $("#rag-litellm-basic").hidden = !remote;
    $("#rag-local-runtime-options").hidden = remote;
    $("#rag-threads-field").hidden = remote;
    $("#rag-gguf-runtime-options").hidden = !customGguf;
    document.querySelectorAll("[data-rag-gguf-advanced]").forEach((field) => {
      field.hidden = !customGguf;
    });
    const descriptions = {
      "bge-m3": "Direct FastEmbed/ONNX runtime, vector destination and retrieval controls.",
      "custom-gguf": "Choose a local embedding GGUF and configure its llama.cpp runtime.",
      "litellm-custom": "Connect any local or remote embedding endpoint supported by LiteLLM.",
    };
    $("#rag-basic-description").textContent = descriptions[profileId]
      || selectedProfile?.description
      || "Configuration for the selected plugin embedding provider.";
    const enabled = $("#rag-enabled").checked;
    $("#rag-reindex").disabled = !enabled;
    $("#rag-test-form button[type='submit']").disabled = !enabled;
    updateRagVectorStoreFields();
  }

  function updateRagVectorStoreFields() {
    const sqlite = $("#rag-vector-store").value === "sqlite";
    $("#rag-acceleration").disabled = !sqlite;
    if (!sqlite) $("#rag-acceleration").value = "auto";
  }

  function renderEmbeddingCatalog(models = embeddingModels, preferences = ragPreferences) {
    embeddingModels = models;
    const body = $("#rag-model-list");
    if (!body) return;
    const selected = preferences?.rag?.profile_id || "bge-m3";
    body.replaceChildren();
    models.forEach((profile) => {
      const active = profile.id === selected;
      const managed = profile.managed !== false;
      const canSelect = Boolean(
        profile.runtime_available
        && (profile.installed || !managed || profile.external_file || profile.id === "bge-m3"),
      );
      const installState = profile.external_file
        ? (profile.installed ? "File ready" : "Choose a file")
        : managed
          ? (profile.installed ? "Installed" : "Not installed")
          : "Not required";
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="model-selected${active ? " active" : ""}" aria-label="${active ? "Selected" : "Not selected"}">${active ? "✓" : ""}</td>
        <td><strong>${escapeHTML(profile.display_name)}</strong><small>${escapeHTML(profile.description || "")}</small>${profile.recommended ? '<span class="model-requirement">Recommended · CPU compatible</span>' : ""}</td>
        <td><span class="model-install-state ${profile.installed ? "installed" : ""}">${installState}</span><small>${escapeHTML(profile.download_size || "")}</small>${profile.runtime_available ? "" : '<span class="model-runtime-state">Runtime unavailable</span>'}</td>
        <td class="table-actions">
          ${managed && !profile.installed ? `<button class="table-action" type="button" data-embedding-action="install" data-profile-id="${escapeHTML(profile.id)}">Install</button>` : ""}
          ${active ? '<span class="model-active-label">Active</span>' : (canSelect ? `<button class="table-action" type="button" data-embedding-action="select" data-profile-id="${escapeHTML(profile.id)}">Select</button>` : "")}
          ${(managed || profile.external_file) && profile.installed && profile.runtime_available ? `<button class="table-action" type="button" data-embedding-action="load" data-profile-id="${escapeHTML(profile.id)}">Load</button>` : ""}
          ${profile.resident ? `<button class="table-action" type="button" data-embedding-action="unload" data-profile-id="${escapeHTML(profile.id)}">Unload</button>` : ""}
          ${managed && profile.installed ? `<button class="table-action danger" type="button" data-embedding-action="uninstall" data-profile-id="${escapeHTML(profile.id)}">Uninstall</button>` : ""}
        </td>`;
      body.append(row);
    });
  }

  function populateRagSettings(preferences) {
    ragPreferences = preferences;
    const rag = preferences.rag || {};
    $("#rag-enabled").checked = rag.enabled !== false;
    $("#rag-profile-id").value = rag.profile_id || "bge-m3";
    $("#rag-custom-gguf-path").value = rag.model_path || "";
    $("#rag-litellm-model").value = rag.profile_id === "litellm-custom"
      ? (rag.embedding_model || "")
      : "openai/text-embedding-3-small";
    $("#rag-litellm-base-url").value = rag.profile_id === "litellm-custom"
      ? (rag.base_url || "")
      : "";
    $("#rag-key-env").value = rag.api_key_env || "OPENAI_API_KEY";
    $("#rag-vector-store").value = rag.vector_store || "sqlite";
    $("#rag-acceleration").value = rag.vector_acceleration || "auto";
    $("#rag-chunk-size").value = rag.chunk_size_chars ?? 1800;
    $("#rag-chunk-overlap").value = rag.chunk_overlap_chars ?? 300;
    $("#rag-batch-size").value = rag.embedding_batch_size ?? 16;
    $("#rag-top-k").value = rag.top_k ?? 8;
    $("#rag-candidate-k").value = rag.candidate_k ?? 40;
    $("#rag-min-score").value = rag.min_score ?? 0.18;
    $("#rag-semantic-weight").value = rag.semantic_weight ?? 0.8;
    $("#rag-keyword-weight").value = rag.keyword_weight ?? 0.2;
    $("#rag-max-context").value = rag.max_context_chars ?? 14000;
    $("#rag-timeout").value = rag.request_timeout ?? 120;
    $("#rag-context-length").value = rag.context_length ?? 8192;
    $("#rag-runtime-batch-size").value = rag.runtime_batch_size ?? 512;
    $("#rag-threads").value = rag.threads ?? 0;
    $("#rag-gpu-layers").value = rag.gpu_layers ?? 0;
    $("#rag-main-gpu").value = rag.main_gpu ?? 0;
    $("#rag-use-mmap").checked = rag.use_mmap ?? true;
    $("#rag-use-mlock").checked = rag.use_mlock ?? false;
    $("#rag-keep-loaded").checked = rag.keep_model_loaded ?? true;
    $("#rag-preload-on-start").checked = rag.preload_on_start ?? false;
    updateRagFields();
  }

  function renderVectorStores(stores, selected) {
    const select = $("#rag-vector-store");
    select.replaceChildren(...stores.map((store) => new Option(
      `${store.display_name}${store.local ? " · local" : " · plugin"}`,
      store.id,
    )));
    select.value = stores.some((store) => store.id === selected) ? selected : "sqlite";
    updateRagVectorStoreFields();
  }

  async function refreshRagStatus() {
    try {
      const status = await api("/api/rag/status");
      $("#rag-index-summary").textContent = `${status.meetings} meetings · ${status.chunks} chunks · ${status.vector_acceleration}`;
      renderVectorStores(status.vector_stores || [], status.vector_store);
      if (status.provider.models) renderEmbeddingCatalog(status.provider.models, ragPreferences);
    } catch (error) {
      $("#rag-index-summary").textContent = error.message;
    }
  }

  function ragSettingsPayload() {
    const profileId = $("#rag-profile-id").value || "bge-m3";
    const selectedProfile = embeddingModels.find((item) => item.id === profileId);
    const provider = selectedProfile?.provider
      || (profileId === "bge-m3" ? "fastembed" : profileId === "custom-gguf" ? "local" : "litellm");
    const model = profileId === "bge-m3"
      ? "BAAI/bge-m3"
      : profileId === "custom-gguf"
        ? "custom-gguf"
        : profileId === "litellm-custom"
          ? ($("#rag-litellm-model").value.trim() || "openai/text-embedding-3-small")
          : (selectedProfile?.model || selectedProfile?.repository || profileId);
    const baseUrl = profileId === "litellm-custom"
        ? $("#rag-litellm-base-url").value.trim()
        : "";
    return {
      enabled: $("#rag-enabled").checked,
      profile_id: profileId,
      embedding_provider: provider,
      embedding_model: model,
      base_url: baseUrl,
      api_key_env: $("#rag-key-env").value.trim(),
      model_path: $("#rag-custom-gguf-path").value.trim() || null,
      context_length: Number($("#rag-context-length").value),
      runtime_batch_size: Number($("#rag-runtime-batch-size").value),
      threads: Number($("#rag-threads").value),
      gpu_layers: Number($("#rag-gpu-layers").value),
      main_gpu: Number($("#rag-main-gpu").value),
      use_mmap: $("#rag-use-mmap").checked,
      use_mlock: $("#rag-use-mlock").checked,
      keep_model_loaded: profileId === "litellm-custom" ? false : $("#rag-keep-loaded").checked,
      preload_on_start: profileId === "litellm-custom" ? false : $("#rag-preload-on-start").checked,
      vector_store: $("#rag-vector-store").value,
      vector_acceleration: $("#rag-acceleration").value,
      chunk_size_chars: Number($("#rag-chunk-size").value),
      chunk_overlap_chars: Number($("#rag-chunk-overlap").value),
      embedding_batch_size: Number($("#rag-batch-size").value),
      top_k: Number($("#rag-top-k").value),
      candidate_k: Number($("#rag-candidate-k").value),
      min_score: Number($("#rag-min-score").value),
      semantic_weight: Number($("#rag-semantic-weight").value),
      keyword_weight: Number($("#rag-keyword-weight").value),
      max_context_chars: Number($("#rag-max-context").value),
      request_timeout: Number($("#rag-timeout").value),
      keep_alive: "5m",
    };
  }

  function renderRagTestResults(data) {
    const target = $("#rag-test-results");
    if (!data.results.length) {
      target.innerHTML = '<span class="settings-empty-copy">No chunk passed the configured minimum score.</span>';
      return;
    }
    target.innerHTML = data.results.map((result) => `
      <article class="rag-test-result">
        <div><strong>#${result.rank} · ${escapeHTML(result.meeting_title)}</strong><span>Hybrid ${Number(result.score).toFixed(3)} · semantic ${Number(result.semantic_score).toFixed(3)} · keyword ${Number(result.keyword_score).toFixed(3)}</span></div>
        <p>${escapeHTML(result.text).slice(0, 520)}${result.text.length > 520 ? "…" : ""}</p>
      </article>`).join("");
  }

  function webhookEndpointPayload() {
    return {
      name: $("#webhook-name").value.trim(),
      url: $("#webhook-url").value.trim(),
      enabled: $("#webhook-endpoint-enabled").checked,
      mode: $("#webhook-mode").value,
      events: [...document.querySelectorAll("[data-webhook-event]:checked")]
        .map((control) => control.value),
      content_level: $("#webhook-content-level").value,
      timeout_seconds: Number($("#webhook-timeout").value),
      max_attempts: Number($("#webhook-attempts").value),
      allow_private_network: $("#webhook-allow-private").checked,
    };
  }

  function renderWebhookEvents(selected = []) {
    const target = $("#webhook-event-list");
    if (!target) return;
    const groups = Object.groupBy
      ? Object.groupBy(webhookCatalog.event_catalog || [], (item) => item.group)
      : (webhookCatalog.event_catalog || []).reduce((result, item) => {
          (result[item.group] ||= []).push(item);
          return result;
        }, {});
    target.innerHTML = Object.entries(groups).map(([group, events]) => `
      <div class="webhook-event-group"><strong>${escapeHTML(group)}</strong>${events.map((item) => `
        <label><input type="checkbox" value="${escapeHTML(item.id)}" data-webhook-event ${selected.includes(item.id) ? "checked" : ""}><span><b>${escapeHTML(item.id)}</b><small>${escapeHTML(item.description)}</small></span></label>`).join("")}</div>`).join("");
  }

  function renderWebhookDeliveries(deliveries = []) {
    const target = $("#webhook-delivery-list");
    if (!target) return;
    target.innerHTML = deliveries.length ? deliveries.map((item) => `
      <tr><td><strong>${escapeHTML(item.event_type)}</strong><small>${item.meeting_id ? `Meeting ${item.meeting_id}` : "System"}</small></td>
      <td>${escapeHTML(item.endpoint_name)}</td><td><span class="status-pill ${escapeHTML(item.status)}">${escapeHTML(item.status)}</span></td>
      <td>${Number(item.attempt_count || 0)}</td><td><small>${item.last_status_code || escapeHTML(item.last_error || "Pending")}</small>${["failed", "expired", "delivered"].includes(item.status) ? `<button class="text-button" type="button" data-webhook-retry="${escapeHTML(item.id)}">Retry</button>` : ""}</td></tr>`).join("")
      : '<tr><td colspan="5"><span class="settings-empty-copy">No deliveries yet.</span></td></tr>';
  }

  function renderWebhooks(data) {
    webhookCatalog = data;
    $("#webhook-enabled").checked = Boolean(data.settings.enabled);
    $("#webhook-retention-days").value = data.settings.retention_days || 30;
    $("#webhook-max-concurrency").value = data.settings.max_concurrency || 4;
    $("#webhook-storage-note").textContent = data.secure_storage_available
      ? "Signing secrets are stored in the operating-system credential vault."
      : "Secure credential storage is unavailable; endpoints cannot be created safely.";
    $("#webhook-new").disabled = !data.secure_storage_available;
    const target = $("#webhook-endpoint-list");
    target.innerHTML = data.endpoints.length ? data.endpoints.map((item) => `
      <tr><td><strong>${escapeHTML(item.name)}</strong><small>${escapeHTML(item.url)}</small></td><td>${item.events.length}</td><td>${escapeHTML(item.content_level)}</td>
      <td><span class="status-pill ${item.enabled ? "completed" : "neutral"}">${item.enabled ? "Enabled" : "Disabled"}</span></td>
      <td class="webhook-row-actions"><button class="text-button" type="button" data-webhook-test="${escapeHTML(item.id)}">Test</button><button class="text-button" type="button" data-webhook-edit="${escapeHTML(item.id)}">Edit</button><button class="text-button" type="button" data-webhook-rotate="${escapeHTML(item.id)}">Rotate key</button><button class="text-button danger" type="button" data-webhook-delete="${escapeHTML(item.id)}">Delete</button></td></tr>`).join("")
      : '<tr><td colspan="5"><span class="settings-empty-copy">No webhook endpoints configured.</span></td></tr>';
    renderWebhookDeliveries(data.deliveries);
  }

  function openWebhookEditor(endpoint = null) {
    $("#webhook-endpoint-id").value = endpoint?.id || "";
    $("#webhook-editor-title").textContent = endpoint ? `Edit ${endpoint.name}` : "Add webhook endpoint";
    $("#webhook-name").value = endpoint?.name || "";
    $("#webhook-url").value = endpoint?.url || "";
    $("#webhook-mode").value = endpoint?.mode || "notification";
    $("#webhook-content-level").value = endpoint?.content_level || "metadata";
    $("#webhook-timeout").value = endpoint?.timeout_seconds || 10;
    $("#webhook-attempts").value = endpoint?.max_attempts || 4;
    $("#webhook-endpoint-enabled").checked = endpoint?.enabled ?? true;
    $("#webhook-allow-private").checked = endpoint?.allow_private_network || false;
    renderWebhookEvents(endpoint?.events || []);
    $("#webhook-endpoint-form").hidden = false;
    $("#webhook-endpoint-form").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function refreshWebhooks() {
    renderWebhooks(await api("/api/webhooks"));
  }

  function revealWebhookSecret(secret) {
    window.prompt("Copy this signing secret now. It will not be shown again.", secret);
  }

  async function loadSettings() {
    try {
      const [preferences, info, capabilities, models, credential, formats, plugins, embeddings, webhooks, liveAssistant] = await Promise.all([
        api("/api/settings"),
        api("/api/info"),
        api("/api/capabilities"),
        api("/api/models/summary"),
        api("/api/settings/summary-api-key"),
        api("/api/summary-templates"),
        api("/api/plugins"),
        api("/api/models/embeddings"),
        api("/api/webhooks"),
        api("/api/live-assistant"),
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
      renderPlugins(plugins);
      renderWebhooks(webhooks);
      populateLiveAssistant(liveAssistant);
      populateDiarizationSettings(preferences);
      populateRagSettings(preferences);
      embeddingModels = embeddings;
      renderEmbeddingCatalog(embeddings, preferences);
      renderAuxiliaryCapabilities(capabilities);
      renderSystem(info, capabilities);
      defaultModelsDirectory = info.default_models_directory || "";
      const profiles = await api("/api/models/transcription");
      renderTranscriptionCatalog(profiles, preferences);
      refreshRagStatus();
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
  $("#live-assistant-profile")?.addEventListener("change", updateLiveAssistantFields);
  $("#live-assistant-enabled")?.addEventListener("change", updateLiveAssistantFields);
  $("#rag-enabled")?.addEventListener("change", updateRagFields);
  $("#rag-vector-store")?.addEventListener("change", updateRagVectorStoreFields);

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
      if (!selectedProfile) throw new Error("The selected AI model is unavailable.");
      const engine = selectedProfile.engine || "llama-cpp";
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
            engine,
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

  $("#live-assistant-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const current = liveAssistantCatalog.settings || {};
      const profileId = $("#live-assistant-profile").value;
      const selected = (liveAssistantCatalog.models || []).find((item) => item.id === profileId);
      if (!selected) throw new Error("The selected Live Assistant model is unavailable.");
      const remote = profileId === "litellm-custom";
      const enabled = $("#live-assistant-enabled").checked;
      if (enabled && !remote && (!selected.installed || selected.runtime_available === false)) {
        throw new Error("Install this local model from AI Engine before enabling Live Assistant.");
      }
      if (enabled && remote && !window.confirm(
        "The Live AI Assistant will send recent meeting transcript text to the configured LiteLLM provider. Continue?",
      )) return;
      const apiKey = $("#live-assistant-api-key")?.value.trim() || "";
      if (remote && apiKey) {
        liveAssistantCatalog.credential = await api("/api/live-assistant/api-key", {
          method: "PUT",
          body: JSON.stringify({ api_key: apiKey }),
        });
        $("#live-assistant-api-key").value = "";
        renderLiveAssistantCredentialStatus(liveAssistantCatalog.credential);
      }
      const model = remote
        ? $("#live-assistant-model").value.trim()
        : selected.repository;
      if (!model) throw new Error("Enter a LiteLLM model identifier.");
      const payload = {
        ...current,
        enabled,
        auto_start: $("#live-assistant-auto-start").checked,
        engine: "llama-cpp",
        provider: remote ? "litellm" : "local",
        profile_id: profileId,
        local_runtime: "managed-llama-cpp",
        model,
        model_file: remote ? "not-managed.gguf" : selected.model_file,
        model_path: null,
        base_url: remote ? ($("#live-assistant-base-url").value.trim() || null) : null,
        api_key_env: "MEET2NOTES_LIVE_ASSISTANT_API_KEY",
        context_length: Number($("#live-assistant-context-length").value),
        max_output_tokens: Number($("#live-assistant-max-output").value),
        temperature: Number($("#live-assistant-temperature").value),
        gpu_layers: Number($("#live-assistant-gpu-layers").value),
        preload_on_start: remote ? false : $("#live-assistant-preload").checked,
        keep_model_loaded: true,
        system_prompt: $("#live-assistant-system-prompt").value.trim(),
        trigger_phrases: $("#live-assistant-triggers").value
          .split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        evaluation_interval_seconds: Number($("#live-assistant-interval").value),
        recent_context_seconds: Number($("#live-assistant-context-seconds").value),
        cooldown_seconds: Number($("#live-assistant-cooldown").value),
        max_calls_per_minute: Number($("#live-assistant-rate").value),
        request_timeout_seconds: Number($("#live-assistant-timeout").value),
      };
      liveAssistantCatalog = await api("/api/live-assistant/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      populateLiveAssistant(liveAssistantCatalog);
      toast("Live AI Assistant settings saved locally.");
      setSavedState("Live Assistant saved just now");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  $("#live-assistant-api-key-clear")?.addEventListener("click", async () => {
    try {
      liveAssistantCatalog.credential = await api("/api/live-assistant/api-key", {
        method: "DELETE",
      });
      renderLiveAssistantCredentialStatus(liveAssistantCatalog.credential);
      toast("Live Assistant API key removed.");
    } catch (error) {
      toast(error.message, "error");
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
        const remote = profile.id === "litellm-custom" || profile.provider === "litellm";
        const customGguf = profile.id === "custom-gguf";
        const updated = await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({
            summary_engine: {
              ...current,
              engine: profile.engine || "llama-cpp",
              provider: profile.provider || (remote ? "litellm" : "local"),
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

  async function embeddingCatalogAction(action, profileId, button) {
    button.disabled = true;
    const profile = embeddingModels.find((item) => item.id === profileId);
    try {
      if (!profile) throw new Error("The selected embedding model is unavailable.");
      if (action === "select") {
        const current = ragPreferences?.rag || {};
        const defaults = profileId === "bge-m3"
          ? { embedding_model: "BAAI/bge-m3", base_url: "" }
          : profileId === "custom-gguf"
            ? { embedding_model: "custom-gguf", base_url: "" }
            : { embedding_model: "openai/text-embedding-3-small", base_url: "" };
        const preferences = await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({
            rag: {
              ...current,
              ...defaults,
              profile_id: profileId,
              embedding_provider: profile.provider,
            },
          }),
        });
        populateRagSettings(preferences);
        toast(`${profile.display_name} is now the selected embedding model.`);
      } else if (action === "uninstall") {
        if (!window.confirm(`Uninstall ${profile.display_name}? Its managed ONNX files will be removed.`)) return;
        await api(`/api/engines/embeddings/uninstall?profile_id=${encodeURIComponent(profileId)}`, { method: "POST" });
        toast("Embedding model uninstalled.");
      } else if (action === "unload") {
        await api(`/api/engines/embeddings/unload?profile_id=${encodeURIComponent(profileId)}`, { method: "POST" });
        toast("Embedding model unloaded from memory.");
      } else {
        const installing = action === "install";
        await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({ rag: ragSettingsPayload() }),
        });
        if (installing) await beginInstallModal(`Installing ${profile.display_name} · ${profile.download_size}`);
        await api(`/api/engines/embeddings/prepare?profile_id=${encodeURIComponent(profileId)}${installing ? "&download=true" : ""}`, { method: "POST" });
        if (installing) await finishInstallModal();
        toast(installing ? "Embedding model installed locally." : "Embedding model loaded into memory.");
      }
      const [models, preferences] = await Promise.all([
        api("/api/models/embeddings"),
        api("/api/settings"),
      ]);
      embeddingModels = models;
      populateRagSettings(preferences);
      renderEmbeddingCatalog(models, preferences);
      await refreshRagStatus();
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

  $("#rag-model-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-embedding-action]");
    if (button) embeddingCatalogAction(button.dataset.embeddingAction, button.dataset.profileId, button);
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

  $("#rag-api-key-clear")?.addEventListener("click", async () => {
    try {
      const status = await api("/api/settings/summary-api-key", { method: "DELETE" });
      renderCredentialStatus(status);
      toast("Shared LiteLLM API key removed from secure credential storage.");
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

  $("#rag-custom-gguf-browse")?.addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try {
      const selected = await api("/api/models/embeddings/select-file", { method: "POST" });
      if (selected.file) {
        $("#rag-custom-gguf-path").value = selected.file;
        toast("Embedding GGUF selected. Save the RAG settings before loading it.");
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

  $("#rag-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const apiKey = $("#rag-api-key")?.value.trim() || "";
      if (apiKey) {
        renderCredentialStatus(await api("/api/settings/summary-api-key", {
          method: "PUT",
          body: JSON.stringify({ api_key: apiKey }),
        }));
        $("#rag-api-key").value = "";
      }
      const preferences = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ rag: ragSettingsPayload() }),
      });
      populateRagSettings(preferences);
      embeddingModels = await api("/api/models/embeddings");
      renderEmbeddingCatalog(embeddingModels, preferences);
      await refreshRagStatus();
      toast("Historical RAG settings saved locally.", "success");
      setSavedState("RAG saved just now");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  $("#rag-unload")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await api("/api/engines/embeddings/unload", { method: "POST" });
      toast("Embedding worker unloaded.");
      embeddingModels = await api("/api/models/embeddings");
      renderEmbeddingCatalog(embeddingModels, ragPreferences);
      await refreshRagStatus();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  $("#rag-reindex")?.addEventListener("click", (event) => {
    event.preventDefault();
    $("#rag-reindex-confirmation").hidden = false;
    $("#rag-reindex-progress").hidden = true;
    $("#rag-reindex-confirm").disabled = false;
    $("#rag-reindex-close").disabled = true;
    $("#rag-reindex-status").textContent = "Every completed meeting transcript will be embedded again.";
    $("#rag-reindex-percent").textContent = "0%";
    $("#rag-reindex-phase").textContent = "Preparing";
    $("#rag-reindex-bar").style.width = "0%";
    $("#rag-reindex-bar").style.background = "";
    $("#rag-reindex-log").value = "";
    $("#rag-reindex-dialog").showModal();
  });
  $("#rag-reindex-cancel")?.addEventListener("click", () => $("#rag-reindex-dialog").close());
  $("#rag-reindex-confirm")?.addEventListener("click", startRagReindex);
  $("#rag-reindex-close")?.addEventListener("click", () => $("#rag-reindex-dialog").close());
  $("#rag-reindex-dialog")?.addEventListener("cancel", (event) => {
    if (ragReindexRunning) event.preventDefault();
  });

  $("#rag-test-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    submit.textContent = "Embedding & ranking…";
    try {
      const result = await api("/api/rag/search", {
        method: "POST",
        body: JSON.stringify({ query: $("#rag-test-query").value.trim() }),
      });
      renderRagTestResults(result);
      await refreshRagStatus();
    } catch (error) {
      toast(error.message, "error");
      $("#rag-test-results").innerHTML = `<span class="settings-empty-copy">${escapeHTML(error.message)}</span>`;
    } finally {
      submit.disabled = false;
      submit.textContent = "Run search test";
    }
  });

  $("#plugin-list")?.addEventListener("click", async (event) => {
    const configure = event.target.closest("[data-plugin-configure]");
    if (configure) {
      openPluginSettings(configure.dataset.pluginConfigure);
      return;
    }
    const button = event.target.closest("[data-plugin-toggle]");
    if (!button) return;
    button.disabled = true;
    try {
      await api(`/api/plugins/${encodeURIComponent(button.dataset.pluginToggle)}/state`, {
        method: "PUT",
        body: JSON.stringify({ enabled: button.dataset.pluginEnabled !== "true" }),
      });
      renderPlugins(await api("/api/plugins"));
      toast("Plugin state saved locally.", "success");
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  });

  $("#plugin-settings-cancel")?.addEventListener("click", () => {
    $("#plugin-settings-editor").hidden = true;
  });

  $("#plugin-settings-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const pluginId = $("#plugin-settings-id").value;
    const values = {};
    event.currentTarget.querySelectorAll("[data-plugin-setting]").forEach((control) => {
      const kind = control.dataset.pluginSettingKind;
      values[control.dataset.pluginSetting] = kind === "boolean"
        ? control.checked
        : kind === "integer" ? Number.parseInt(control.value, 10)
          : kind === "number" ? Number.parseFloat(control.value) : control.value;
    });
    try {
      const result = await api(`/api/plugins/${encodeURIComponent(pluginId)}/settings`, {
        method: "PUT",
        body: JSON.stringify({ settings: values }),
      });
      const plugin = pluginCatalog.find((item) => item.id === pluginId);
      if (plugin) plugin.settings = result.settings;
      $("#plugin-settings-editor").hidden = true;
      toast("Plugin settings saved locally.", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#plugins-rescan")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      renderPlugins(await api("/api/plugins/rescan", { method: "POST" }));
      toast("Installed plugin packages rescanned.", "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  $("#webhook-settings-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      webhookCatalog.settings = await api("/api/webhooks/settings", {
        method: "PUT",
        body: JSON.stringify({
          enabled: $("#webhook-enabled").checked,
          retention_days: Number($("#webhook-retention-days").value),
          max_concurrency: Number($("#webhook-max-concurrency").value),
        }),
      });
      toast("Webhook settings saved locally.", "success");
      setSavedState("Webhooks saved just now");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#webhook-new")?.addEventListener("click", () => openWebhookEditor());
  ["#webhook-editor-close", "#webhook-editor-cancel"].forEach((selector) => {
    $(selector)?.addEventListener("click", () => { $("#webhook-endpoint-form").hidden = true; });
  });
  $("#webhook-endpoint-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const endpointId = $("#webhook-endpoint-id").value;
    const payload = webhookEndpointPayload();
    if (!payload.events.length) {
      toast("Select at least one webhook event.", "error");
      return;
    }
    try {
      const result = await api(endpointId
        ? `/api/webhooks/endpoints/${encodeURIComponent(endpointId)}`
        : "/api/webhooks/endpoints", {
        method: endpointId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      $("#webhook-endpoint-form").hidden = true;
      await refreshWebhooks();
      if (!endpointId) revealWebhookSecret(result.signing_secret);
      toast(`Webhook endpoint ${endpointId ? "updated" : "created"}.`, "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });
  $("#webhook-refresh")?.addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try { await refreshWebhooks(); } catch (error) { toast(error.message, "error"); }
    finally { event.currentTarget.disabled = false; }
  });
  $("#webhook-endpoint-list")?.addEventListener("click", async (event) => {
    const action = event.target.closest("[data-webhook-edit],[data-webhook-test],[data-webhook-rotate],[data-webhook-delete]");
    if (!action) return;
    const endpointId = action.dataset.webhookEdit || action.dataset.webhookTest
      || action.dataset.webhookRotate || action.dataset.webhookDelete;
    const endpoint = webhookCatalog.endpoints.find((item) => item.id === endpointId);
    if (action.dataset.webhookEdit) return openWebhookEditor(endpoint);
    try {
      if (action.dataset.webhookTest) {
        await api(`/api/webhooks/endpoints/${encodeURIComponent(endpointId)}/test`, { method: "POST" });
        toast("Test delivery queued.", "success");
      } else if (action.dataset.webhookRotate) {
        if (!window.confirm(`Rotate the signing secret for ${endpoint.name}? The previous secret will stop working immediately.`)) return;
        const result = await api(`/api/webhooks/endpoints/${encodeURIComponent(endpointId)}/rotate-secret`, { method: "POST" });
        revealWebhookSecret(result.signing_secret);
      } else if (action.dataset.webhookDelete) {
        if (!window.confirm(`Delete the webhook endpoint ${endpoint.name}?`)) return;
        await api(`/api/webhooks/endpoints/${encodeURIComponent(endpointId)}`, { method: "DELETE" });
        await refreshWebhooks();
        toast("Webhook endpoint deleted.", "success");
      }
    } catch (error) {
      toast(error.message, "error");
    }
  });
  $("#webhook-delivery-list")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-webhook-retry]");
    if (!button) return;
    try {
      await api(`/api/webhooks/deliveries/${encodeURIComponent(button.dataset.webhookRetry)}/retry`, { method: "POST" });
      await refreshWebhooks();
      toast("Webhook delivery queued again.", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  activateSettingsTab(window.location.hash.slice(1) || "general");
  loadSettings();
})();
