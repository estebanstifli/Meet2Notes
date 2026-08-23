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
  const defaultLanguage = "en";
  const loadedLanguages = new Set();
  const originalText = new WeakMap();
  const originalAttributes = new WeakMap();
  const originalDocumentTitle = document.title;
  const immutableUiTerms = new Set(["RAG", "Webhook", "Webhooks", "Plugin", "Plugins", "Prompt", "Faster Whisper", "Word", "Markdown"]);
  const immutableTranslationKeys = new Set(["nav.prompt"]);
  const languageNames = loadStaticJson("index") || { en: "English" };
  let translations = {};

  function loadStaticJson(name) {
    try {
      const request = new XMLHttpRequest();
      request.open("GET", `/static/locales/${encodeURIComponent(name)}.json`, false);
      request.send();
      if (request.status < 200 || request.status >= 300) return false;
      const catalog = JSON.parse(request.responseText);
      if (!catalog || typeof catalog !== "object") return false;
      return catalog;
    } catch {
      return false;
    }
  }

  function loadCatalog(language) {
    if (loadedLanguages.has(language)) return true;
    const catalog = loadStaticJson(language);
    if (!catalog) return false;
      translations[language] = catalog;
      loadedLanguages.add(language);
      return true;
  }

  function t(key, replacements = {}, count) {
    if (immutableTranslationKeys.has(key)) return "Prompt";
    const template = translations[currentLanguage]?.[key]
      || translations[defaultLanguage]?.[key]
      || key;
    const selected = typeof template === "object"
      ? template[new Intl.PluralRules(currentLanguage).select(Number(count ?? replacements.count ?? 0))]
        || template.other
      : template;
    return Object.entries(replacements).reduce(
      (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
      selected,
    );
  }

  function formatLocaleDate(value, options = {}) {
    const date = value instanceof Date ? value : new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(currentLanguage, options);
  }

  function translateLiteral(value) {
    if (immutableUiTerms.has(value)) return value;
    const translated = translations[currentLanguage]?.literal?.[value] || value;
    if (value.includes("Faster Whisper") && !translated.includes("Faster Whisper")) return value;
    if (value === "Export Word" && !translated.includes("Word")) return value;
    if (value === "Export Markdown" && !translated.includes("Markdown")) return value;
    return translated;
  }

  function translateNodeTree(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || parent.closest("[data-i18n]") || ["SCRIPT", "STYLE", "SVG"].includes(parent.tagName)) {
          return NodeFilter.FILTER_REJECT;
        }
        return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const source = originalText.get(node) || node.nodeValue;
      originalText.set(node, source);
      node.nodeValue = source.replace(source.trim(), translateLiteral(source.trim()));
    });
    root.querySelectorAll?.("[title], [aria-label], [placeholder]").forEach((element) => {
      const sources = originalAttributes.get(element) || {};
      ["title", "aria-label", "placeholder"].forEach((attribute) => {
        if (!element.hasAttribute(attribute)) return;
        const source = sources[attribute] || element.getAttribute(attribute);
        sources[attribute] = source;
        element.setAttribute(attribute, translateLiteral(source));
      });
      originalAttributes.set(element, sources);
    });
  }

  function observeLiteralTranslations() {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) translateNodeTree(node);
        if (node.nodeType === Node.TEXT_NODE && node.parentElement) translateNodeTree(node.parentElement);
      }));
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  loadCatalog(defaultLanguage);

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
    const requestedLanguage = loadCatalog(language) ? language : defaultLanguage;
    currentLanguage = requestedLanguage;
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
    document.querySelectorAll("[data-i18n-title]").forEach((element) => {
      element.title = t(element.dataset.i18nTitle);
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
      element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
    });
    document.querySelectorAll("[data-i18n-value]").forEach((element) => {
      element.value = t(element.dataset.i18nValue);
    });
    translateNodeTree(document.body);
    document.title = translateLiteral(originalDocumentTitle);
    document.dispatchEvent(new CustomEvent("localmeet:languagechange", {
      detail: { language: currentLanguage },
    }));
  }

  function populateLanguageControls() {
    const options = Object.entries(languageNames)
      .map(([code, name]) => `<option value="${escapeHTML(code)}">${escapeHTML(name)}</option>`)
      .join("");
    document.querySelectorAll("[data-ui-language]").forEach((select) => {
      if (select.dataset.languagesLoaded) return;
      select.innerHTML = options;
      select.dataset.languagesLoaded = "true";
    });
  }

  function bindLanguageControls() {
    populateLanguageControls();
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
        applyLanguage(preferences.ui_language || defaultLanguage);
        applyTheme(preferences.ui_theme || "system");
      })
      .catch(() => {
        applyLanguage(defaultLanguage);
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
  observeLiteralTranslations();
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
    formatLocaleDate,
    t,
    applyLanguage,
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
