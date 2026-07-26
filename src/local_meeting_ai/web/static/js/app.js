(() => {
  "use strict";

  const jobSubscribers = new Set();
  let currentLanguage = "en";
  const translations = {
    en: {
      "menu.file": "File",
      "menu.import": "Import audio or video…",
      "menu.transcription": "Transcription workspace",
      "menu.settings": "Settings",
      "menu.edit": "Edit",
      "menu.find": "Find in transcript",
      "menu.replace": "Find and replace…",
      "menu.view": "View",
      "menu.help": "Help",
      "menu.about": "About Meet2Notes",
      "menu.api": "Local API documentation",
      "brand.subtitle": "Private meeting workspace",
      "nav.transcribe": "Transcribe",
      "nav.meetings": "Meetings",
      "nav.speakers": "Speakers",
      "nav.summaries": "Summaries",
      "nav.exports": "Exports",
      "nav.settings": "Settings",
      "common.soon": "Soon",
      "common.cancel": "Cancel",
      "common.close": "Close",
      "common.optional": "optional",
      "common.installed": "installed",
      "status.ready": "Ready",
      "engine.title": "Transcription engine",
      "engine.checking": "Checking…",
      "engine.ready_cuda": "Ready · CUDA",
      "engine.ready_cpu": "Ready · CPU",
      "engine.install": "Optional install required",
      "engine.local": "Private local processing",
      "workspace.title": "Transcription",
      "workspace.subtitle": "Import a conversation and turn it into clear, editable text.",
      "workspace.meeting": "Meeting",
      "workspace.local": "Local transcription workspace",
      "workspace.edit_hint": "Edit every segment while preserving timestamps and the original recording.",
      "workspace.import": "Import MP3, WAV or video",
      "workspace.import_short": "Import media",
      "workspace.find_replace": "Find & replace",
      "workspace.start": "Start transcription",
      "workspace.overview": "Overview",
      "workspace.transcript": "Transcript",
      "workspace.speakers": "Speakers",
      "workspace.summary": "Summary",
      "workspace.chat": "Chat",
      "workspace.exports": "Exports",
      "workspace.original": "Original source · stored locally",
      "workspace.saved": "All changes saved",
      "workspace.versions": "Versions",
      "workspace.transcriptions": "Transcriptions",
      "workspace.private": "Private inference",
      "workspace.private_description": "Audio is processed on this computer.",
      "workspace.search": "Search this transcript",
      "workspace.ready": "Ready when you are",
      "workspace.create_first": "Create the first local transcript",
      "workspace.create_first_description": "Choose a quality profile and language. No recording leaves this computer.",
      "workspace.configure": "Configure transcription",
      "workspace.editor": "Transcript editor",
      "workspace.no_recording": "No recording selected",
      "workspace.empty_title": "Your transcript will appear here",
      "workspace.empty_description": "Import an audio or video file. You will choose the language and local model before processing begins.",
      "workspace.choose_file": "Choose a recording",
      "workspace.formats": "Audio: MP3, WAV, M4A, FLAC · Video: MP4, MKV, WebM",
      "import.eyebrow": "New source",
      "import.title": "Import a recording",
      "import.description": "Audio and video are copied to private local storage.",
      "import.drop": "Drop a recording here",
      "import.choose": "or choose an audio or video file",
      "import.meeting_title": "Meeting title · optional",
      "import.title_placeholder": "Uses the filename if left empty",
      "import.uploading": "Uploading securely…",
      "import.action": "Import recording",
      "transcription.local_model": "Local model",
      "transcription.new": "New transcription",
      "transcription.description": "Choose the balance between speed, memory, and accuracy.",
      "transcription.quality": "Quality profile",
      "transcription.language": "Language",
      "transcription.auto": "Automatic detection",
      "transcription.task": "Task",
      "transcription.transcribe": "Transcribe",
      "transcription.translate": "Translate to English",
      "transcription.allow_download": "Allow model download if needed",
      "transcription.model_installed": "Model already installed",
      "transcription.download_description": "The selected model is downloaded only after this explicit confirmation.",
      "transcription.local_note": "Audio stays local. A network connection is used only for a confirmed model download.",
      "transcription.start": "Start local transcription",
      "profile.fast": "Fast",
      "profile.balanced": "Balanced",
      "profile.accurate": "Accurate",
      "profile.very_accurate": "Very accurate",
      "about.local": "Local-first software",
      "about.description": "Private transcription and meeting intelligence on your own computer.",
      "speaker": "Speaker {number}",
    },
    es: {
      "menu.file": "Archivo",
      "menu.import": "Importar audio o vídeo…",
      "menu.transcription": "Espacio de transcripción",
      "menu.settings": "Configuración",
      "menu.edit": "Editar",
      "menu.find": "Buscar en la transcripción",
      "menu.replace": "Buscar y reemplazar…",
      "menu.view": "Ver",
      "menu.help": "Ayuda",
      "menu.about": "Acerca de Meet2Notes",
      "menu.api": "Documentación de la API local",
      "brand.subtitle": "Espacio privado para reuniones",
      "nav.transcribe": "Transcribir",
      "nav.meetings": "Reuniones",
      "nav.speakers": "Hablantes",
      "nav.summaries": "Resúmenes",
      "nav.exports": "Exportaciones",
      "nav.settings": "Configuración",
      "common.soon": "Pronto",
      "common.cancel": "Cancelar",
      "common.close": "Cerrar",
      "common.optional": "opcional",
      "common.installed": "instalado",
      "status.ready": "Listo",
      "engine.title": "Motor de transcripción",
      "engine.checking": "Comprobando…",
      "engine.ready_cuda": "Listo · CUDA",
      "engine.ready_cpu": "Listo · CPU",
      "engine.install": "Requiere instalación opcional",
      "engine.local": "Procesamiento local y privado",
      "workspace.title": "Transcripción",
      "workspace.subtitle": "Importa una conversación y conviértela en texto claro y editable.",
      "workspace.meeting": "Reunión",
      "workspace.local": "Espacio de transcripción local",
      "workspace.edit_hint": "Edita cada intervención conservando sus marcas de tiempo y el audio original.",
      "workspace.import": "Importar MP3, WAV o vídeo",
      "workspace.import_short": "Importar archivo",
      "workspace.find_replace": "Buscar y reemplazar",
      "workspace.start": "Empezar a transcribir",
      "workspace.overview": "Vista general",
      "workspace.transcript": "Transcripción",
      "workspace.speakers": "Hablantes",
      "workspace.summary": "Resumen",
      "workspace.chat": "Chat",
      "workspace.exports": "Exportaciones",
      "workspace.original": "Fuente original · guardada localmente",
      "workspace.saved": "Todos los cambios guardados",
      "workspace.versions": "Versiones",
      "workspace.transcriptions": "Transcripciones",
      "workspace.private": "Procesamiento privado",
      "workspace.private_description": "El audio se procesa en este equipo.",
      "workspace.search": "Buscar en esta transcripción",
      "workspace.ready": "Todo listo",
      "workspace.create_first": "Crea la primera transcripción local",
      "workspace.create_first_description": "Elige el perfil de calidad y el idioma. La grabación nunca sale de este equipo.",
      "workspace.configure": "Configurar transcripción",
      "workspace.editor": "Editor de transcripción",
      "workspace.no_recording": "Ninguna grabación seleccionada",
      "workspace.empty_title": "La transcripción aparecerá aquí",
      "workspace.empty_description": "Importa un archivo de audio o vídeo. Antes de procesarlo podrás elegir el idioma y el modelo local.",
      "workspace.choose_file": "Elegir una grabación",
      "workspace.formats": "Audio: MP3, WAV, M4A, FLAC · Vídeo: MP4, MKV, WebM",
      "import.eyebrow": "Nueva fuente",
      "import.title": "Importar una grabación",
      "import.description": "El audio o vídeo se copia al almacenamiento privado local.",
      "import.drop": "Suelta aquí una grabación",
      "import.choose": "o elige un archivo de audio o vídeo",
      "import.meeting_title": "Título de la reunión · opcional",
      "import.title_placeholder": "Si se deja vacío se utilizará el nombre del archivo",
      "import.uploading": "Importando de forma segura…",
      "import.action": "Importar grabación",
      "transcription.local_model": "Modelo local",
      "transcription.new": "Nueva transcripción",
      "transcription.description": "Elige el equilibrio entre velocidad, memoria y precisión.",
      "transcription.quality": "Perfil de calidad",
      "transcription.language": "Idioma",
      "transcription.auto": "Detección automática",
      "transcription.task": "Tarea",
      "transcription.transcribe": "Transcribir",
      "transcription.translate": "Traducir al inglés",
      "transcription.allow_download": "Permitir la descarga del modelo si hace falta",
      "transcription.model_installed": "El modelo ya está instalado",
      "transcription.download_description": "El modelo seleccionado solo se descarga después de esta confirmación explícita.",
      "transcription.local_note": "El audio permanece local. La red solo se utiliza para una descarga de modelo confirmada.",
      "transcription.start": "Iniciar transcripción local",
      "profile.fast": "Rápido",
      "profile.balanced": "Equilibrado",
      "profile.accurate": "Preciso",
      "profile.very_accurate": "Muy preciso",
      "about.local": "Software local por diseño",
      "about.description": "Transcripción privada e inteligencia de reuniones en tu propio equipo.",
      "speaker": "Hablante {number}",
    },
  };

  function t(key, replacements = {}) {
    const template = translations[currentLanguage]?.[key]
      || translations.en[key]
      || key;
    return Object.entries(replacements).reduce(
      (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
      template,
    );
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(path, { ...options, headers });
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = typeof body === "object" ? body.detail : body;
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return body;
  }

  function escapeHTML(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value, style = "medium") {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const options = style === "short"
      ? { month: "short", day: "numeric" }
      : { month: "short", day: "numeric", year: "numeric" };
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }

  function formatDuration(milliseconds) {
    if (!milliseconds && milliseconds !== 0) return "—";
    const totalSeconds = Math.max(0, Math.round(milliseconds / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
    if (minutes) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
    return `${seconds}s`;
  }

  function formatBytes(bytes) {
    if (!bytes && bytes !== 0) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
  }

  function toast(message, type = "success") {
    const region = document.querySelector("#toast-region");
    if (!region) return;
    const element = document.createElement("div");
    element.className = `toast ${type}`;
    element.innerHTML = `<i></i><span>${escapeHTML(message)}</span>`;
    region.append(element);
    window.setTimeout(() => element.remove(), 4500);
  }

  function jobLabel(jobType) {
    return {
      import_media: "Inspect media",
      normalize_audio: "Normalize audio",
      transcribe: "Transcribe recording",
      diarize: "Identify speakers",
      summarize: "Generate summary",
      export: "Prepare export",
    }[jobType] || jobType.replaceAll("_", " ");
  }

  function renderJobCard(job, cancellable = true) {
    const progress = Math.round((job.progress || 0) * 100);
    const active = ["queued", "running", "paused"].includes(job.status);
    const error = job.error_text
      ? `<p class="job-error">${escapeHTML(job.error_text)}</p>`
      : `<p>${escapeHTML(job.message || "Waiting")}</p>`;
    return `
      <article class="job-card" data-job-id="${escapeHTML(job.uuid)}">
        <div class="job-card-head">
          <strong>${escapeHTML(jobLabel(job.job_type))}</strong>
          <span class="status-badge status-${escapeHTML(job.status)}">${escapeHTML(job.status)}</span>
        </div>
        ${error}
        ${active ? `
          <div class="progress-track"><span style="width:${progress}%"></span></div>
          <div class="job-card-actions">
            <small>${progress}%</small>
            ${cancellable ? `<button data-cancel-job="${escapeHTML(job.uuid)}">Cancel</button>` : ""}
          </div>` : ""}
      </article>`;
  }

  function subscribeJobs(callback) {
    jobSubscribers.add(callback);
    return () => jobSubscribers.delete(callback);
  }

  function connectEvents() {
    if (!window.EventSource) return;
    const source = new EventSource("/api/events");
    source.addEventListener("jobs", (event) => {
      try {
        const jobs = JSON.parse(event.data);
        jobSubscribers.forEach((callback) => callback(jobs));
      } catch {
        // Ignore a malformed update and let the next event recover the UI.
      }
    });
  }

  function bindNavigation() {
    const toggle = document.querySelector("#menu-toggle");
    toggle?.addEventListener("click", () => {
      const isOpen = document.body.classList.toggle("menu-open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });
    document.addEventListener("click", (event) => {
      if (!document.body.classList.contains("menu-open")) return;
      if (event.target.closest("#sidebar") || event.target.closest("#menu-toggle")) return;
      document.body.classList.remove("menu-open");
      toggle?.setAttribute("aria-expanded", "false");
    });
  }

  function applyLanguage(language) {
    currentLanguage = translations[language] ? language : "en";
    document.documentElement.lang = currentLanguage;
    document.querySelectorAll("[data-ui-language]").forEach((select) => {
      select.value = currentLanguage;
    });
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      element.placeholder = t(element.dataset.i18nPlaceholder);
    });
    document.dispatchEvent(new CustomEvent("localmeet:languagechange", {
      detail: { language: currentLanguage },
    }));
  }

  function bindLanguageControls() {
    const controls = document.querySelectorAll("[data-ui-language]");
    controls.forEach((control) => {
      control.addEventListener("change", async () => {
        const requestedLanguage = control.value;
        applyLanguage(requestedLanguage);
        try {
          await api("/api/settings", {
            method: "PUT",
            body: JSON.stringify({ ui_language: requestedLanguage }),
          });
        } catch (error) {
          toast(error.message, "error");
        }
      });
    });
    api("/api/settings")
      .then((preferences) => applyLanguage(preferences.ui_language || "en"))
      .catch(() => applyLanguage("en"));
  }

  function closeClassicMenus() {
    document.querySelectorAll(".classic-menu-group.open").forEach((group) => {
      group.classList.remove("open");
    });
  }

  function bindClassicMenu() {
    document.querySelectorAll("[data-menu-toggle]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const group = button.closest(".classic-menu-group");
        const shouldOpen = !group.classList.contains("open");
        closeClassicMenus();
        group.classList.toggle("open", shouldOpen);
      });
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".classic-menu-group")) closeClassicMenus();
    });
    document.querySelectorAll(".classic-menu-popover a, .classic-menu-popover [data-open-import]")
      .forEach((item) => item.addEventListener("click", closeClassicMenus));
    document.querySelectorAll("[data-open-about]").forEach((button) => {
      button.addEventListener("click", () => {
        closeClassicMenus();
        document.querySelector("#about-dialog")?.showModal();
      });
    });
    document.querySelectorAll("[data-close-about]").forEach((button) => {
      button.addEventListener("click", () => document.querySelector("#about-dialog")?.close());
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeClassicMenus();
    });
  }

  function loadGlobalEngineState() {
    const card = document.querySelector("#global-engine-card");
    if (!card) return;
    api("/api/capabilities").then((capabilities) => {
      const available = capabilities.features.transcription === "available";
      const state = available
        ? t(capabilities.transcription.cuda_available ? "engine.ready_cuda" : "engine.ready_cpu")
        : t("engine.install");
      card.classList.toggle("unavailable", !available);
      card.querySelector(".engine-state b").textContent = state;
    }).catch(() => {
      card.classList.add("unavailable");
      card.querySelector(".engine-state b").textContent = t("engine.install");
    });
  }

  function bindComingSoon() {
    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-coming-soon]");
      if (!target) return;
      toast(`${target.dataset.comingSoon} is planned for the next product phase.`);
    });
  }

  function bindJobCancellation() {
    document.addEventListener("click", async (event) => {
      const target = event.target.closest("[data-cancel-job]");
      if (!target) return;
      target.disabled = true;
      try {
        await api(`/api/jobs/${target.dataset.cancelJob}/cancel`, { method: "POST" });
        toast("Cancellation requested.");
      } catch (error) {
        toast(error.message, "error");
        target.disabled = false;
      }
    });
  }

  function bindImportDialog() {
    const dialog = document.querySelector("#import-dialog");
    const form = document.querySelector("#import-form");
    const input = document.querySelector("#media-file");
    const zone = document.querySelector("#drop-zone");
    if (!dialog || !form || !input || !zone) return;

    const pageMeeting = document.querySelector("[data-meeting-id]");
    const existingMeetingId = pageMeeting && !pageMeeting.classList.contains("workspace-page")
      ? pageMeeting.dataset.meetingId
      : null;
    if (existingMeetingId) {
      document.querySelector("#import-title")?.closest(".field")?.classList.add("hidden");
    }

    document.querySelectorAll("[data-open-import]").forEach((button) => {
      button.addEventListener("click", () => {
        resetImport();
        dialog.showModal();
      });
    });
    document.querySelectorAll("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!form.dataset.busy) dialog.close();
      });
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog && !form.dataset.busy) dialog.close();
    });
    input.addEventListener("change", () => showSelectedFile(input.files[0]));
    ["dragenter", "dragover"].forEach((name) => {
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.remove("dragging");
      });
    });
    zone.addEventListener("drop", (event) => {
      const file = event.dataTransfer.files[0];
      if (!file) return;
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      showSelectedFile(file);
    });
    form.addEventListener("submit", submitImport);

    function showSelectedFile(file) {
      if (!file) return;
      zone.classList.add("has-file");
      document.querySelector("#drop-title").textContent = file.name;
      document.querySelector("#drop-help").textContent = formatBytes(file.size);
    }

    function resetImport() {
      form.reset();
      delete form.dataset.busy;
      zone.classList.remove("has-file", "dragging");
      document.querySelector("#drop-title").textContent = t("import.drop");
      document.querySelector("#drop-help").textContent = t("import.choose");
      document.querySelector("#upload-progress").classList.add("hidden");
      document.querySelector("#upload-bar").style.width = "0%";
      document.querySelector("#import-submit").disabled = false;
    }

    async function submitImport(event) {
      event.preventDefault();
      const file = input.files[0];
      if (!file) {
        toast("Choose an audio or video file first.", "error");
        return;
      }
      form.dataset.busy = "true";
      document.querySelector("#import-submit").disabled = true;
      document.querySelector("#upload-progress").classList.remove("hidden");

      let meetingId = existingMeetingId;
      try {
        if (!meetingId) {
          const requestedTitle = document.querySelector("#import-title").value.trim();
          const fallbackTitle = file.name.replace(/\.[^.]+$/, "").replaceAll(/[_-]+/g, " ");
          const meeting = await api("/api/meetings", {
            method: "POST",
            body: JSON.stringify({
              title: requestedTitle || fallbackTitle || "Imported meeting",
              source_type: "imported",
            }),
          });
          meetingId = meeting.id;
        }
        await uploadFile(meetingId, file);
        toast("Recording stored. Local inspection is now running.");
        window.setTimeout(() => {
          dialog.close();
          window.location.href = `/?meeting=${meetingId}&imported=1`;
        }, 350);
      } catch (error) {
        toast(error.message, "error");
        delete form.dataset.busy;
        document.querySelector("#import-submit").disabled = false;
        document.querySelector("#upload-status").textContent = "Import could not be completed";
      }
    }

    function uploadFile(meetingId, file) {
      return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        const data = new FormData();
        data.append("file", file);
        request.open("POST", `/api/meetings/${meetingId}/import`);
        request.upload.addEventListener("progress", (event) => {
          if (!event.lengthComputable) return;
          const percent = Math.round((event.loaded / event.total) * 100);
          document.querySelector("#upload-percent").textContent = `${percent}%`;
          document.querySelector("#upload-bar").style.width = `${percent}%`;
        });
        request.addEventListener("load", () => {
          let result = {};
          try { result = JSON.parse(request.responseText); } catch { /* no-op */ }
          if (request.status >= 200 && request.status < 300) {
            document.querySelector("#upload-status").textContent = "Stored · queued for inspection";
            resolve(result);
          } else {
            reject(new Error(result.detail || `Upload failed (${request.status})`));
          }
        });
        request.addEventListener("error", () => reject(new Error("Could not reach the local server.")));
        request.send(data);
      });
    }
  }

  bindNavigation();
  document.addEventListener("localmeet:languagechange", loadGlobalEngineState);
  bindClassicMenu();
  bindLanguageControls();
  bindComingSoon();
  bindJobCancellation();
  bindImportDialog();
  connectEvents();
  loadGlobalEngineState();

  window.Meet2Notes = {
    api,
    escapeHTML,
    formatDate,
    formatDuration,
    formatBytes,
    renderJobCard,
    subscribeJobs,
    toast,
    t,
    get currentLanguage() {
      return currentLanguage;
    },
  };
})();
