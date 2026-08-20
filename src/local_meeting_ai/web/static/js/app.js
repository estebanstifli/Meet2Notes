(() => {
  "use strict";

  const jobSubscribers = new Set();
  const activitySubscribers = new Set();
  let eventSource = null;
  let currentLanguage = "en";
  let currentThemePreference = document.documentElement.dataset.themePreference || "system";
  let lastSidebarSystemState = null;
  let sidebarSystemTimer = null;
  const themeStorageKey = "meet2notes-ui-theme";
  const sidebarStorageKey = "meet2notes-sidebar-collapsed";
  const performanceLogThresholdMs = 250;

  function timingLog(event, details = {}) {
    const timestamp = new Date().toISOString();
    console.info(`[Meet2Notes][${timestamp}] ${event}`, details);
  }

  timingLog("page navigation started", {
    path: `${window.location.pathname}${window.location.search}`,
    navigationType: performance.getEntriesByType("navigation")[0]?.type || "unknown",
  });
  window.addEventListener("DOMContentLoaded", () => {
    timingLog("DOM ready", { elapsed_ms: Math.round(performance.now()) });
  }, { once: true });
  window.addEventListener("load", () => {
    timingLog("page fully loaded", { elapsed_ms: Math.round(performance.now()) });
  }, { once: true });
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
      "nav.new_meeting": "New meeting",
      "nav.transcribe": "Transcribe",
      "nav.meetings": "Meetings",
      "nav.action_items": "Action items",
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
      "engine.system_title": "Local AI status",
      "engine.process": "Python PID {pid} · in-process workers",
      "engine.role.live_transcription": "Live transcript",
      "engine.role.final_transcription": "Final transcript",
      "engine.role.diarization": "Diarization",
      "engine.role.summary": "AI notes",
      "engine.state.ready": "Ready",
      "engine.state.running": "Working",
      "engine.state.idle": "Idle",
      "engine.state.error": "Error",
      "engine.state.unavailable": "Unavailable",
      "engine.state.not_installed": "Not installed",
      "engine.state.disabled": "Disabled",
      "engine.in_memory": "In memory",
      "engine.thread_hint": "Runs as a thread inside Python PID {pid}",
      "engine.shutdown": "Shut down",
      "engine.shutdown_title": "Shut down Meet2Notes?",
      "engine.shutdown_description": "Active local work will stop, the loaded models will be released from RAM and VRAM, and the Python server will close.",
      "engine.shutdown_confirm": "Shut down",
      "engine.shutdown_requested": "Meet2Notes is shutting down safely…",
      "hardware.ram": "RAM",
      "hardware.vram": "VRAM",
      "hardware.free": "{free} free of {total}",
      "hardware.app_memory": "App {used}",
      "hardware.no_gpu": "No NVIDIA GPU detected",
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
      "nav.new_meeting": "Nueva reunión",
      "nav.transcribe": "Transcribir",
      "nav.meetings": "Reuniones",
      "nav.action_items": "Tareas",
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
      "engine.system_title": "Estado de IA local",
      "engine.process": "Python PID {pid} · motores internos",
      "engine.role.live_transcription": "Transcripción en vivo",
      "engine.role.final_transcription": "Transcripción final",
      "engine.role.diarization": "Diarización",
      "engine.role.summary": "Notas con IA",
      "engine.state.ready": "Listo",
      "engine.state.running": "Trabajando",
      "engine.state.idle": "En espera",
      "engine.state.error": "Error",
      "engine.state.unavailable": "No disponible",
      "engine.state.not_installed": "No instalado",
      "engine.state.disabled": "Desactivado",
      "engine.in_memory": "En memoria",
      "engine.thread_hint": "Se ejecuta como hilo dentro del Python PID {pid}",
      "engine.shutdown": "Apagar",
      "engine.shutdown_title": "¿Apagar Meet2Notes?",
      "engine.shutdown_description": "Se detendrá el trabajo local activo, se liberarán los modelos cargados de la RAM y la VRAM y se cerrará el servidor Python.",
      "engine.shutdown_confirm": "Apagar",
      "engine.shutdown_requested": "Meet2Notes se está apagando de forma segura…",
      "hardware.ram": "RAM",
      "hardware.vram": "VRAM",
      "hardware.free": "{free} libres de {total}",
      "hardware.app_memory": "App {used}",
      "hardware.no_gpu": "No se detectó una GPU NVIDIA",
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
    const startedAt = performance.now();
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch (error) {
      timingLog("API request failed", {
        method: options.method || "GET", path,
        elapsed_ms: Math.round(performance.now() - startedAt),
        error: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
    const elapsed = Math.round(performance.now() - startedAt);
    if (elapsed >= performanceLogThresholdMs || !response.ok) {
      timingLog("API request completed", {
        method: options.method || "GET", path, status: response.status, elapsed_ms: elapsed,
      });
    }
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

  function subscribeActivity(callback) {
    activitySubscribers.add(callback);
    return () => activitySubscribers.delete(callback);
  }

  function connectEvents() {
    if (!window.EventSource || eventSource) return;
    const source = new EventSource("/api/events");
    eventSource = source;
    source.addEventListener("jobs", (event) => {
      try {
        const jobs = JSON.parse(event.data);
        jobSubscribers.forEach((callback) => callback(jobs));
      } catch {
        // Ignore a malformed update and let the next event recover the UI.
      }
    });
    source.addEventListener("activity", (event) => {
      try {
        const entries = JSON.parse(event.data);
        activitySubscribers.forEach((callback) => callback(entries));
      } catch {
        // Ignore a malformed update and let the next event recover the UI.
      }
    });
    const closeEvents = () => {
      if (eventSource !== source) return;
      source.close();
      eventSource = null;
      timingLog("event stream closed before navigation");
    };
    window.addEventListener("pagehide", closeEvents, { once: true });
    window.addEventListener("beforeunload", closeEvents, { once: true });
  }

  function bindNavigation() {
    const toggle = document.querySelector("#menu-toggle");
    const collapseToggle = document.querySelector("#sidebar-collapse-toggle");
    const brand = document.querySelector("#sidebar-brand");
    let sidebarCollapsed = document.documentElement.classList.contains("sidebar-collapsed");
    try {
      sidebarCollapsed = window.localStorage.getItem(sidebarStorageKey) === "true";
    } catch (_error) {
      // Keep the state applied by the early bootstrap script.
    }

    const applySidebarState = () => {
      const collapsedOnDesktop = sidebarCollapsed
        && window.matchMedia("(min-width: 821px)").matches;
      document.documentElement.classList.toggle("sidebar-collapsed", collapsedOnDesktop);
      collapseToggle?.setAttribute("aria-expanded", String(!collapsedOnDesktop));
      if (collapseToggle) {
        collapseToggle.title = collapsedOnDesktop ? "Expand sidebar" : "Minimize sidebar";
        collapseToggle.setAttribute("aria-label", collapseToggle.title);
      }
      if (brand) {
        brand.title = collapsedOnDesktop ? "Expand sidebar" : "Meet2Notes home";
        brand.setAttribute("aria-label", brand.title);
      }
    };

    const saveSidebarState = () => {
      try {
        window.localStorage.setItem(sidebarStorageKey, String(sidebarCollapsed));
      } catch (_error) {
        // Keep the state for this page when browser storage is unavailable.
      }
    };

    collapseToggle?.addEventListener("click", () => {
      sidebarCollapsed = true;
      saveSidebarState();
      applySidebarState();
      brand?.focus();
    });
    brand?.addEventListener("click", (event) => {
      if (!document.documentElement.classList.contains("sidebar-collapsed")) return;
      event.preventDefault();
      sidebarCollapsed = false;
      saveSidebarState();
      applySidebarState();
    });
    window.addEventListener("resize", applySidebarState);
    applySidebarState();
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
      .then((preferences) => {
        applyLanguage(preferences.ui_language || "en");
        applyTheme(preferences.ui_theme || "system");
      })
      .catch(() => {
        applyLanguage("en");
        applyTheme(document.documentElement.dataset.themePreference || "system");
      });
  }

  function resolveTheme(preference) {
    if (preference === "light" || preference === "dark") return preference;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(preference = "system") {
    currentThemePreference = ["system", "light", "dark"].includes(preference)
      ? preference
      : "system";
    const resolved = resolveTheme(currentThemePreference);
    document.documentElement.dataset.themePreference = currentThemePreference;
    document.documentElement.dataset.theme = resolved;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content",
      resolved === "dark" ? "#0d111a" : "#ffffff",
    );
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const target = resolved === "dark" ? "light" : "dark";
      const label = `Switch to ${target} theme`;
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    });
    document.querySelectorAll("#ui-theme").forEach((select) => {
      select.value = currentThemePreference;
    });
    try {
      window.localStorage.setItem(themeStorageKey, currentThemePreference);
    } catch (_error) {
      // API persistence remains authoritative when browser storage is unavailable.
    }
    document.dispatchEvent(new CustomEvent("meet2notes:themechange", {
      detail: { preference: currentThemePreference, theme: resolved },
    }));
  }

  function bindThemeControls() {
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", async () => {
        const previous = currentThemePreference;
        const next = resolveTheme(currentThemePreference) === "dark" ? "light" : "dark";
        applyTheme(next);
        try {
          await api("/api/settings", {
            method: "PUT",
            body: JSON.stringify({ ui_theme: next }),
          });
        } catch (error) {
          applyTheme(previous);
          toast(error.message, "error");
        }
      });
    });
    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
    systemTheme.addEventListener?.("change", () => {
      if (currentThemePreference === "system") applyTheme("system");
    });
  }

  function renderGlobalEngineState(status) {
    const card = document.querySelector("#global-engine-card");
    if (!card) return;
    lastSidebarSystemState = status;
    card.querySelector("#engine-process-label").textContent = t(
      "engine.process",
      { pid: status.process_id || "—" },
    );
    card.querySelector("#global-engine-list").innerHTML = (status.engines || [])
      .map((engine) => {
        const role = t(`engine.role.${engine.role}`);
        const state = t(`engine.state.${engine.status}`);
        const memoryBadge = engine.in_memory
          ? `<em>${escapeHTML(t("engine.in_memory"))}</em>`
          : "";
        const title = engine.execution === "thread" && engine.process_id
          ? t("engine.thread_hint", { pid: engine.process_id })
          : engine.name;
        return `
          <div class="engine-status-row ${escapeHTML(engine.status)}" title="${escapeHTML(title)}">
            <i class="engine-status-dot"></i>
            <span class="engine-status-copy">
              <b>${escapeHTML(role)}</b>
              <small>${escapeHTML(engine.name)}</small>
            </span>
            <span class="engine-status-value">
              <strong>${escapeHTML(state)}</strong>
              ${memoryBadge}
            </span>
          </div>`;
      }).join("");

    const memory = status.memory || {};
    const memoryRow = memory.total_bytes
      ? `
        <div class="hardware-status-row">
          <span>
            <b>${escapeHTML(t("hardware.ram"))}</b>
            <small>${escapeHTML(t("hardware.app_memory", {
              used: formatBytes(memory.process_bytes || 0),
            }))}</small>
          </span>
          <strong>${escapeHTML(t("hardware.free", {
            free: formatBytes(memory.available_bytes),
            total: formatBytes(memory.total_bytes),
          }))}</strong>
        </div>`
      : "";
    const gpuRows = (status.gpus || []).map((gpu) => `
      <div class="hardware-status-row gpu-status-row">
        <span>
          <b>${escapeHTML(gpu.name)}</b>
          <small>${escapeHTML(t("hardware.vram"))} · ${gpu.utilization_percent}% GPU</small>
        </span>
        <strong>${escapeHTML(t("hardware.free", {
          free: formatBytes(gpu.free_bytes),
          total: formatBytes(gpu.total_bytes),
        }))}</strong>
      </div>`).join("");
    card.querySelector("#global-hardware-list").innerHTML = memoryRow + (gpuRows || `
      <div class="hardware-empty">${escapeHTML(t("hardware.no_gpu"))}</div>`);
    card.classList.toggle(
      "unavailable",
      (status.engines || []).some((engine) => ["error", "unavailable"].includes(engine.status)),
    );
  }

  function loadGlobalEngineState() {
    const card = document.querySelector("#global-engine-card");
    if (!card) return;
    api("/api/sidebar-system")
      .then(renderGlobalEngineState)
      .catch(() => {
        card.classList.add("unavailable");
        card.querySelector("#engine-process-label").textContent = t("engine.install");
      });
    if (!sidebarSystemTimer) {
      sidebarSystemTimer = window.setInterval(loadGlobalEngineState, 10000);
    }
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

  function bindApplicationShutdown() {
    const trigger = document.querySelector("#application-shutdown");
    const dialog = document.querySelector("#shutdown-dialog");
    const confirm = document.querySelector("#confirm-application-shutdown");
    if (!trigger || !dialog || !confirm) return;

    trigger.addEventListener("click", () => dialog.showModal());
    confirm.addEventListener("click", async () => {
      trigger.disabled = true;
      confirm.disabled = true;
      try {
        await api("/api/application/shutdown", { method: "POST" });
        dialog.close();
        document.querySelector("#engine-process-label").textContent = t("engine.shutdown_requested");
        toast(t("engine.shutdown_requested"));
      } catch (error) {
        toast(error.message, "error");
        trigger.disabled = false;
        confirm.disabled = false;
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
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (link && link.origin === window.location.origin && !link.target) {
      timingLog("navigation requested", { href: link.getAttribute("href") });
    }
  });
  bindThemeControls();
  bindLanguageControls();
  bindComingSoon();
  bindJobCancellation();
  bindApplicationShutdown();
  bindImportDialog();
  connectEvents();
  document.addEventListener("localmeet:languagechange", () => {
    if (lastSidebarSystemState) renderGlobalEngineState(lastSidebarSystemState);
  });
  loadGlobalEngineState();

  window.Meet2Notes = {
    api,
    escapeHTML,
    formatDate,
    formatDuration,
    formatBytes,
    renderJobCard,
    subscribeJobs,
    subscribeActivity,
    toast,
    t,
    applyTheme,
    timingLog,
    get currentLanguage() {
      return currentLanguage;
    },
  };
})();
