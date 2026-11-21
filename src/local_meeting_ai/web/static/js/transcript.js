(() => {
  "use strict";

  const {
    api,
    escapeHTML,
    renderMarkdown,
    streamAnswer,
    answerActivity,
    formatBytes,
    subscribeActivity,
    subscribeJobs,
    t,
    toast,
  } = window.Meet2Notes;

  const page = document.querySelector(".minimal-transcript-page");
  const audio = document.querySelector("#meeting-audio");
  const segmentContainer = document.querySelector("#transcript-segments");
  const startDialog = document.querySelector("#transcription-dialog");
  const titleDisplay = document.querySelector("#transcription-title-display");
  const titleInput = document.querySelector("#transcription-title-input");
  const activityOutput = document.querySelector("#activity-log-output");
  const activityResizer = document.querySelector("#activity-log-resizer");
  const transcriptionWorkspace = document.querySelector("#transcription-workspace");
  const postprocessDialog = document.querySelector("#postprocess-dialog");
  const postprocessLogOutput = document.querySelector("#postprocess-log");
  const deleteAudioDialog = document.querySelector("#delete-audio-dialog");
  const speakerSummaryDialog = document.querySelector("#speaker-summary-dialog");
  const speakerRebuildDialog = document.querySelector("#speaker-rebuild-dialog");
  const rememberVoiceDialog = document.querySelector("#remember-voice-dialog");
  const aiRebuildDialog = document.querySelector("#ai-rebuild-dialog");
  const aiUnsavedDialog = document.querySelector("#ai-unsaved-dialog");
  const exportDialog = document.querySelector("#export-dialog");
  let liveQuestionBusy = false;
  const liveAssistantWidget = document.querySelector("#live-ai-assistant");
  const liveAssistantDragHandle = document.querySelector("#live-ai-assistant-drag-handle");
  const liveAssistantResizeHandle = document.querySelector("#live-ai-assistant-resize-handle");
  const liveAssistantToggle = document.querySelector("#live-ai-assistant-toggle");
  const liveAssistantWidgetBody = document.querySelector("#live-ai-assistant-widget-body");
  const liveAssistantEmpty = document.querySelector("#live-ai-assistant-empty");
  const liveAssistantQuestionForm = document.querySelector("#live-ai-assistant-question-form");
  const liveAssistantQuestionInput = document.querySelector("#live-ai-assistant-question");
  const liveAssistantSend = document.querySelector("#live-ai-assistant-send");
  const newMeetingRequested = new URL(window.location.href).searchParams.get("new") === "1";
  const liveAssistantWidgetStorageKey = "meet2notes.liveAssistantWidget.v3";

  const searchSegmentId = Number(new URL(location.href).searchParams.get("segment")) || null;
  let pendingSearchSegment = searchSegmentId;
  let transcriptRenderGeneration = 0;
  let transcriptRequestGeneration = 0;
  let speakerPanelPending = false;
  const yieldToBrowser = () => new Promise((resolve) => setTimeout(resolve, 0));

  let meetingId = page.dataset.meetingId || null;
  let draftTitle = page.dataset.defaultTitle || "New Transcription";
  let audioSources = [];
  let captureCapability = {};
  let recordings = [];
  let currentMeeting = null;
  let versions = [];
  let meetingSummaries = [];
  let noteFormats = [];
  let engineCapabilities = {};
  let preferences = {};
  let activeTranscriptionId = null;
  let activeJob = null;
  let captureSession = null;
  let capturePollTimer = null;
  let capturePollBusy = false;
  let lastInsightPollAt = 0;
  let lastAssistantPollAt = 0;
  let lastAssistantInsightId = null;
  let liveAssistantWidgetReady = false;
  let lastLiveSegmentCount = -1;
  let lastDetail = null;
  let terminalJobIds = new Set();
  let sourcePreviewTimer = null;
  let sourcePreviewBusy = false;
  const selectedDevices = { microphone: null, system: null };
  let latestActivityId = 0;
  let postprocessMeetingId = null;
  let workflowVisible = false;
  let workflowDismissed = false;
  let workflowCompleted = false;
  let startActionAvailable = newMeetingRequested;
  let audioStopAtSeconds = null;
  let speakerPlaybackRanges = [];
  let speakerPlaybackIndex = -1;
  let activeAudioPlaybackButton = null;
  let activeSpeakerSummaryJobId = null;
  let activeSpeakerSummaryId = null;
  let speakerSummaryDismissed = false;
  let activeSpeakerRebuildJobId = null;
  let speakerRebuildDismissed = false;
  let pendingPostprocessKind = null;
  let pendingRememberSpeakerId = null;
  let editingSummaryId = null;
  let aiNotesViewMode = "markdown";
  let pendingAiNavigation = null;
  let pendingExport = null;
  const activityLines = [];
  const postprocessLogLines = [];
  const postprocessJobSnapshots = new Map();

  function appendPostprocessLog(line) {
    if (!line || postprocessLogLines[postprocessLogLines.length - 1] === line) return;
    postprocessLogLines.push(line);
    if (postprocessLogLines.length > 300) {
      postprocessLogLines.splice(0, postprocessLogLines.length - 300);
    }
    postprocessLogOutput.value = postprocessLogLines.join("\n");
    postprocessLogOutput.scrollTop = postprocessLogOutput.scrollHeight;
  }

  function appendWorkflowMessage(source, message) {
    const time = new Date().toLocaleTimeString(Meet2Notes.currentLanguage, { hour12: false });
    appendPostprocessLog(`[${time}] INFO    ${source} · ${message}`);
  }

  function resetPostprocessLog(message) {
    postprocessLogLines.length = 0;
    postprocessJobSnapshots.clear();
    const time = new Date().toLocaleTimeString(Meet2Notes.currentLanguage, { hour12: false });
    appendPostprocessLog(`[${time}] INFO    workflow · ${message}`);
  }

  function renderActivity(entries) {
    for (const entry of entries || []) {
      const id = Number(entry.id || 0);
      if (id && id <= latestActivityId) continue;
      latestActivityId = Math.max(latestActivityId, id);
      const timestamp = new Date(entry.timestamp);
      const time = Number.isNaN(timestamp.getTime())
        ? "--:--:--"
        : timestamp.toLocaleTimeString(Meet2Notes.currentLanguage, { hour12: false });
      const source = String(entry.source || "meet2notes").split(".").pop();
      const line = `[${time}] ${String(entry.level || "info").toUpperCase().padEnd(7)} ${source} · ${entry.message}`;
      activityLines.push(line);
      if (workflowVisible) appendPostprocessLog(line);
    }
    if (activityLines.length > 500) activityLines.splice(0, activityLines.length - 500);
    activityOutput.value = activityLines.join("\n");
    activityOutput.scrollTop = activityOutput.scrollHeight;
  }

  function setActivityLogHeight(requestedHeight) {
    const minimum = 44;
    const mobile = window.matchMedia("(max-width: 600px)").matches;
    const maximum = mobile
      ? Math.min(220, Math.max(150, transcriptionWorkspace.clientHeight - 420))
      : Math.max(180, transcriptionWorkspace.clientHeight - 320);
    const height = Math.round(Math.min(maximum, Math.max(minimum, requestedHeight)));
    transcriptionWorkspace.style.setProperty("--activity-log-height", `${height}px`);
    activityResizer.setAttribute("aria-valuenow", String(height));
    activityResizer.setAttribute("aria-valuemax", String(maximum));
    try {
      window.localStorage.setItem("meet2notes-activity-log-height", String(height));
    } catch (_error) {
      // Resizing remains available when browser storage is disabled.
    }
  }

  function bindActivityLog() {
    let storedHeight = window.matchMedia("(max-width: 600px)").matches ? 150 : 180;
    try {
      storedHeight = Number(window.localStorage.getItem("meet2notes-activity-log-height"))
        || storedHeight;
    } catch (_error) {
      // Keep the friendly default height.
    }
    setActivityLogHeight(storedHeight);

    activityResizer.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const startY = event.clientY;
      const startHeight = Number.parseInt(
        getComputedStyle(transcriptionWorkspace).getPropertyValue("--activity-log-height"),
        10,
      ) || 180;
      activityResizer.classList.add("dragging");
      activityResizer.setPointerCapture?.(event.pointerId);
      const move = (moveEvent) => setActivityLogHeight(startHeight + startY - moveEvent.clientY);
      const finish = () => {
        activityResizer.classList.remove("dragging");
        activityResizer.removeEventListener("pointermove", move);
        activityResizer.removeEventListener("pointerup", finish);
        activityResizer.removeEventListener("pointercancel", finish);
      };
      activityResizer.addEventListener("pointermove", move);
      activityResizer.addEventListener("pointerup", finish);
      activityResizer.addEventListener("pointercancel", finish);
    });
    activityResizer.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = Number(activityResizer.getAttribute("aria-valuenow"));
      const maximum = Number(activityResizer.getAttribute("aria-valuemax"));
      if (event.key === "Home") return setActivityLogHeight(44);
      if (event.key === "End") return setActivityLogHeight(maximum);
      setActivityLogHeight(current + (event.key === "ArrowUp" ? 24 : -24));
    });
    document.querySelector("#clear-activity-log").addEventListener("click", () => {
      activityLines.length = 0;
      activityOutput.value = "";
    });
    subscribeActivity(renderActivity);
    api("/api/activity").then(renderActivity).catch((error) => {
      renderActivity([{
        id: latestActivityId + 1,
        timestamp: new Date().toISOString(),
        level: "error",
        source: "interface",
        message: `Could not load activity: ${error.message}`,
      }]);
    });
  }

  async function loadWorkspace() {
    try {
      const commonRequests = [
        api("/api/capabilities"),
        api("/api/audio/sources"),
        api("/api/capture/session"),
        api("/api/settings"),
        api("/api/summary-templates"),
      ];
      const meetingRequests = meetingId
        ? [
            api(`/api/meetings/${meetingId}`),
            api(`/api/meetings/${meetingId}/recordings`),
            api(`/api/meetings/${meetingId}/transcriptions`),
            api(`/api/jobs?meeting_id=${meetingId}`),
            api(`/api/meetings/${meetingId}/summaries`),
          ]
        : [
            Promise.resolve(null),
            Promise.resolve([]),
            Promise.resolve([]),
            Promise.resolve([]),
            Promise.resolve([]),
          ];
      const [
        capabilities,
        sourceData,
        currentCapture,
        preferenceData,
        summaryTemplates,
        meetingData,
        recordingData,
        versionData,
        jobs,
        summaryData,
      ] = await Promise.all([...commonRequests, ...meetingRequests]);

      engineCapabilities = capabilities;
      preferences = preferenceData;
      noteFormats = summaryTemplates;
      currentMeeting = meetingData;
      captureCapability = sourceData.capability || {};
      audioSources = sourceData.sources || [];
      recordings = recordingData;
      versions = versionData;
      meetingSummaries = summaryData;
      configureWorkflowLabels();
      renderSummaryPanel();
      renderSources();
      configureAudio();

      terminalJobIds = new Set(
        jobs
          .filter((job) => ["completed", "failed", "cancelled"].includes(job.status))
          .map((job) => job.uuid),
      );
      activeJob = jobs.find((job) =>
        job.job_type === "transcribe" &&
        ["queued", "running", "paused"].includes(job.status)) || null;
      renderProgress(activeJob);
      restorePostprocessing(jobs);

      const preferred = versions.find((item) => item.is_active)
        || versions.find((item) => ["running", "queued"].includes(item.status))
        || versions[0];
      if (preferred) {
        await selectTranscription(preferred.id);
      } else {
        setTitle(draftTitle);
        renderEmpty();
      }

      const captureBelongsToPage = Boolean(
        currentCapture
        && (!meetingId || String(currentCapture.meeting_id) === String(meetingId)),
      );
      if (captureBelongsToPage) {
        meetingId = String(currentCapture.meeting_id);
        page.dataset.meetingId = meetingId;
        activeTranscriptionId = Number(currentCapture.transcription_id);
        setLiveState(currentCapture);
        await refreshLiveTranscript(currentCapture, true);
        await refreshWebhookInsights(true);
        await refreshLiveAssistant(true);
      } else if (meetingId && !preferred) {
        // Imported or otherwise saved meetings still expose the complete
        // workspace, even when they do not have a transcript version yet.
        document.querySelector("#meeting-tabs").classList.remove("hidden");
      }
      if (capabilities.features.transcription !== "available") {
        document.querySelector("#start-transcription").dataset.engineUnavailable = "true";
      }
      if (meetingId && !captureBelongsToPage) {
        await refreshWebhookInsights(true);
        await refreshLiveAssistant(true);
      }
    } catch (error) {
      toast(error.message, "error");
      renderLoadError(error.message);
    }
  }

  async function loadMeetingWorkspace() {
    refreshMeetingTags();
    if (!meetingId) return;
    const [meetingData, recordingData, versionData, jobs, summaryData] = await Promise.all([
      api(`/api/meetings/${meetingId}`),
      api(`/api/meetings/${meetingId}/recordings`),
      api(`/api/meetings/${meetingId}/transcriptions`),
      api(`/api/jobs?meeting_id=${meetingId}`),
      api(`/api/meetings/${meetingId}/summaries`),
    ]);
    currentMeeting = meetingData;
    recordings = recordingData;
    versions = versionData;
    meetingSummaries = summaryData;
    configureAudio();
    renderSummaryPanel();
    activeJob = jobs.find((job) =>
      job.job_type === "transcribe" &&
      ["queued", "running", "paused"].includes(job.status)) || null;
    renderProgress(activeJob);
    renderPostprocess(jobs);
    const preferred = versions.find((item) => item.is_active)
      || versions.find((item) => ["running", "queued"].includes(item.status))
      || versions[0];
    if (preferred) await selectTranscription(preferred.id);
  }

  async function refreshMeetingTags() {
    const bar = document.querySelector("#transcription-tags");
    bar.classList.toggle("hidden", !meetingId);
    if (!meetingId) return;
    try {
      const tags = await api(`/api/meetings/${meetingId}/tags`);
      document.querySelector("#current-meeting-tags").innerHTML = tags.map((tag) =>
        `<a class="meeting-tag" href="/meetings?tag=${tag.id}">${escapeHTML(tag.name)}</a>`).join("");
    } catch (error) { toast(error.message, "error"); }
  }
  document.querySelector("#edit-meeting-tags").addEventListener("click", () => {
    if (meetingId) Meet2Notes.openTags(meetingId, currentMeeting?.title || draftTitle);
  });
  document.addEventListener("meet2notes:tagschanged", refreshMeetingTags);
  refreshMeetingTags();

  function setTitle(value) {
    draftTitle = value || page.dataset.defaultTitle || "New Transcription";
    titleDisplay.textContent = draftTitle;
    titleDisplay.title = `Click to rename “${draftTitle}”`;
  }

  function beginRename() {
    titleInput.value = draftTitle;
    titleDisplay.classList.add("hidden");
    titleInput.classList.remove("hidden");
    titleInput.focus();
    titleInput.select();
  }

  async function commitRename() {
    if (titleInput.classList.contains("hidden")) return;
    const requested = titleInput.value.trim();
    titleInput.classList.add("hidden");
    titleDisplay.classList.remove("hidden");
    if (!requested || requested === draftTitle) {
      titleInput.value = draftTitle;
      return;
    }
    const previous = draftTitle;
    setTitle(requested);
    if (!activeTranscriptionId) return;
    try {
      const updated = await api(`/api/transcriptions/${activeTranscriptionId}`, {
        method: "PATCH",
        body: JSON.stringify({ title: requested }),
      });
      setTitle(updated.title);
      toast("Transcription renamed.");
    } catch (error) {
      setTitle(previous);
      toast(error.message, "error");
    }
  }

  function cancelRename() {
    titleInput.classList.add("hidden");
    titleDisplay.classList.remove("hidden");
    titleInput.value = draftTitle;
  }

  function playbackRecording() {
    if (audioWasDeleted()) return null;
    // Timestamps refer to the normalized PCM audio. Seeking in a long VBR MP3
    // can land several seconds away even when currentTime reports the target.
    return recordings.filter((item) => item.role === "normalized").at(-1)
      || recordings.find((item) => item.role === "original")
      || null;
  }

  function configureAudio() {
    const playback = playbackRecording();
    const original = recordings.find((item) => item.role === "original");
    const row = document.querySelector("#audio-row");
    if (!playback) {
      row.classList.add("hidden");
      stopAudioPlayback();
      audio.removeAttribute("src");
      applyAudioAvailability();
      return;
    }
    row.classList.remove("hidden");
    document.querySelector("#audio-filename").textContent =
      original?.original_filename || playback.original_filename || "Original recording";
    const source = `/api/recordings/${playback.id}/media`;
    if (audio.getAttribute("src")?.split("#")[0] !== source) {
      stopAudioPlayback();
      audio.src = source;
    }
    applyAudioAvailability();
  }

  function audioWasDeleted() {
    return Boolean(currentMeeting?.audio_deleted_at);
  }

  function applyAudioAvailability() {
    const deleted = audioWasDeleted();
    const available = Boolean(playbackRecording());
    const unavailable = deleted || !available;
    const status = document.querySelector("#utility-audio-status");
    const deleteButton = document.querySelector("#delete-meeting-audio");
    if (status) {
      status.textContent = deleted
        ? `Deleted locally${currentMeeting.audio_deleted_bytes ? ` · ${formatBytes(currentMeeting.audio_deleted_bytes)} freed` : ""}`
        : "Original recording";
    }
    if (deleteButton) {
      deleteButton.disabled = unavailable;
      deleteButton.innerHTML = deleted
        ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12 4 4 8-9"/></svg>Audio deleted'
        : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/></svg>Delete audio';
    }
    document.querySelectorAll(
      "[data-audio-export], .timestamp-button, .speaker-fragment-play, [data-play-speaker], [data-remember-speaker], #speaker-rebuild-identification",
    ).forEach((control) => {
      control.disabled = unavailable;
      if (deleted) control.title = "Audio deleted from this meeting";
    });
    document.querySelectorAll('.speaker-export-actions a[href*="/audio?"]').forEach((link) => {
      link.setAttribute("aria-disabled", String(unavailable));
      if (deleted) link.title = "Audio deleted from this meeting";
    });
  }

  async function refreshSources() {
    rememberSelectedDevices();
    const list = document.querySelector("#native-source-list");
    list.innerHTML = '<div class="source-loading"><i class="mini-spinner"></i> Scanning audio devices…</div>';
    try {
      const data = await api("/api/audio/sources");
      captureCapability = data.capability || {};
      audioSources = data.sources || [];
      renderSources();
    } catch (error) {
      list.innerHTML = `<div class="source-empty">${escapeHTML(error.message)}</div>`;
      document.querySelector("#transcription-submit").disabled = true;
    }
  }

  function renderSources() {
    const platform = captureCapability.platform || "Local system";
    const nativeApi = captureCapability.native_api || captureCapability.backend || "audio";
    document.querySelector("#capture-platform").textContent =
      `${platform} · ${nativeApi}`;
    renderSourceMode();
  }

  function renderSourceMode() {
    rememberSelectedDevices();
    const mode = selectedSourceMode();
    const nativePanel = document.querySelector("#native-source-panel");
    const filePanel = document.querySelector("#file-source-panel");
    nativePanel.classList.toggle("hidden", mode === "file");
    filePanel.classList.toggle("hidden", mode !== "file");
    const submitCopy = document.querySelector("#transcription-submit span");
    submitCopy.textContent = mode === "file"
      ? "Import and transcribe"
      : "Start live transcription";
    const modes = selectedLiveModes();
    const summary = document.querySelector("#source-selection-summary");
    summary.classList.toggle("combined", mode === "combined");
    summary.textContent = t(mode === "file" ? "capture.file_summary"
      : mode === "combined" ? "capture.combined_summary"
        : modes.length ? "capture.single_summary" : "capture.choose_sources");
    if (mode === "file") {
      stopSourcePreview();
      document.querySelector("#transcription-submit").disabled = false;
      return;
    }

    const list = document.querySelector("#native-source-list");
    list.innerHTML = modes.map((kind) => {
      const candidates = audioSources.filter((source) =>
        kind === "system" ? source.kind === "system" : source.kind !== "system");
      const available = candidates.filter((source) => source.available !== false);
      // Keep choices across adding/removing an input and device refreshes.
      const selected = available.find((source) => source.id === selectedDevices[kind])
        || (!selectedDevices[kind] && (available.find((source) => source.is_default) || available[0]));
      if (selected) selectedDevices[kind] = selected.id;
      const options = candidates.map((source) => `
        <label class="native-source-option" title="${escapeHTML(source.name)}">
          <input type="radio" name="native-source-${kind}" data-native-source="${kind}" value="${escapeHTML(source.id)}"
            ${selected?.id === source.id ? "checked" : ""} ${source.available === false ? "disabled" : ""}>
          <span>
            <strong>${escapeHTML(source.name)}</strong>
            <small>${source.available === false ? escapeHTML(source.unavailable_reason || t("capture.unavailable"))
              : source.is_default ? t("capture.default_device") : t("capture.available_device")}</small>
          </span>
          <span class="source-level-preview" data-source-meter="${escapeHTML(source.id)}" aria-label="Live input level">
            <i></i>
          </span>
        </label>`).join("");
      return `<fieldset class="source-input-group"><legend>${t(`capture.${kind}_label`)}</legend>
        <p>${t(`capture.${kind}_hint`)}</p><div class="source-device-options">${options ||
          `<div class="source-empty">${t(`capture.no_${kind}`)}</div>`}</div>
        ${available.length && !selected ? `<p class="source-missing">${t("capture.device_removed")}</p>` : ""}
        </fieldset>`;
    }).join("");
    const guidance = document.querySelector("#source-guidance");
    const hasSystem = modes.includes("system");
    const platformKey = { Windows: "windows", Darwin: "macos", Linux: "linux" }[captureCapability.platform];
    guidance.textContent = !captureCapability.available
      ? captureCapability.reason || t("capture.unavailable")
      : hasSystem && platformKey ? t(`capture.help_${platformKey}`) : "";
    guidance.classList.toggle("hidden", !guidance.textContent);
    syncCaptureSelection();
    scheduleSourcePreview();
  }

  function selectedSourceMode() {
    if (document.querySelector('input[name="source-mode"][value="file"]').checked) return "file";
    const modes = selectedLiveModes();
    return modes.length === 2 ? "combined" : modes[0] || "none";
  }

  function selectedLiveModes() {
    return [...document.querySelectorAll('input[name="source-mode"]:checked')]
      .map((input) => input.value).filter((mode) => mode !== "file");
  }

  function selectedNativeSources() {
    return [...document.querySelectorAll("[data-native-source]:checked")]
      .filter((input) => selectedLiveModes().includes(input.dataset.nativeSource));
  }

  function rememberSelectedDevices() {
    document.querySelectorAll("[data-native-source]:checked").forEach((input) => {
      selectedDevices[input.dataset.nativeSource] = input.value;
    });
  }

  function syncCaptureSelection() {
    document.querySelector("#transcription-submit").disabled = selectedSourceMode() !== "file" &&
      (!selectedLiveModes().length || selectedNativeSources().length !== selectedLiveModes().length);
  }

  function stopSourcePreview() {
    if (sourcePreviewTimer) window.clearTimeout(sourcePreviewTimer);
    sourcePreviewTimer = null;
    document.querySelectorAll(".source-level-preview").forEach((meter) => {
      meter.classList.remove("active", "unavailable");
      meter.querySelector("i").style.width = "0%";
    });
  }

  function scheduleSourcePreview(delay = 120) {
    if (sourcePreviewTimer) window.clearTimeout(sourcePreviewTimer);
    sourcePreviewTimer = null;
    if (!startDialog.open || selectedSourceMode() === "file") {
      stopSourcePreview();
      return;
    }
    sourcePreviewTimer = window.setTimeout(pollSourcePreview, delay);
  }

  async function pollSourcePreview() {
    if (sourcePreviewBusy || !startDialog.open) {
      scheduleSourcePreview(250);
      return;
    }
    const selected = selectedNativeSources();
    if (!selected.length) return;
    document.querySelectorAll(".source-level-preview").forEach((item) => {
      const active = selected.some((input) => input.value === item.dataset.sourceMeter);
      item.classList.toggle("active", active);
      if (!active) item.querySelector("i").style.width = "0%";
    });
    sourcePreviewBusy = true;
    try {
      // Serialize native probes: PortAudio device manager lifecycles are not thread-safe.
      for (const input of selected) {
        if (!startDialog.open || selectedSourceMode() === "file") break;
        if (!input.isConnected || !input.checked) continue;
        const meter = document.querySelector(`[data-source-meter="${CSS.escape(input.value)}"]`);
        try {
          const result = await api(`/api/audio/sources/${encodeURIComponent(input.value)}/level`);
          if (meter?.isConnected && input.checked && startDialog.open) {
            meter.classList.remove("unavailable");
            meter.querySelector("i").style.width = `${Math.round(Number(result.level || 0) * 100)}%`;
          }
        } catch {
          meter?.classList.add("unavailable");
        }
      }
    } finally {
      sourcePreviewBusy = false;
      scheduleSourcePreview(180);
    }
  }

  async function selectTranscription(transcriptionId) {
    const requestGeneration = ++transcriptRequestGeneration;
    ++transcriptRenderGeneration;
    activeTranscriptionId = Number(transcriptionId);
    segmentContainer.setAttribute("aria-busy", "true");
    document.querySelector("#editor-meta").textContent = t("transcript.loading");
    try {
      const detail = await api(`/api/transcriptions/${activeTranscriptionId}`);
      if (requestGeneration !== transcriptRequestGeneration) return;
      await renderTranscript(detail);
      if (requestGeneration !== transcriptRequestGeneration) return;
      if (pendingSearchSegment) {
        const segment = detail.segments.find((item) => item.id === pendingSearchSegment);
        pendingSearchSegment = null;
        if (segment) {
          setActiveMeetingTab("transcript");
          const row = segmentContainer.querySelector(`[data-segment-id="${segment.id}"]`);
          row?.classList.add("search-target");
          requestAnimationFrame(() => segmentContainer.querySelector(`[data-segment-id="${segment.id}"]`)?.scrollIntoView({ block: "center" }));
          const source = audio.getAttribute("src");
          if (source) {
            // A media fragment lets the browser apply the start time after loading,
            // including when metadata is already cached. It never starts playback.
            audio.src = `${source.split("#")[0]}#t=${segment.start_ms / 1000}`;
          }
        } else toast(t("library.jump_missing"), "error");
      }
    } catch (error) {
      if (requestGeneration !== transcriptRequestGeneration) return;
      segmentContainer.removeAttribute("aria-busy");
      toast(error.message, "error");
    }
  }

  async function renderTranscript(detail) {
    const generation = ++transcriptRenderGeneration;
    lastDetail = detail;
    const transcription = detail.transcription;
    const speakers = detail.speakers || [];
    const speakerNames = new Map(
      speakers.map((speaker) => [Number(speaker.id), speaker.display_name]),
    );
    const speakerNumbers = new Map(
      speakers.map((speaker, index) => [Number(speaker.id), index + 1]),
    );
    const speakerFilter = document.querySelector("#transcript-speaker-filter");
    const previousFilter = speakerFilter.value || "all";
    speakerFilter.innerHTML = [
      '<option value="all">All speakers</option>',
      ...speakers.map((speaker) =>
        `<option value="${speaker.id}">${escapeHTML(speaker.display_name)}</option>`),
    ].join("");
    speakerFilter.value = speakers.some((speaker) => String(speaker.id) === previousFilter)
      ? previousFilter
      : "all";
    document.querySelector("#transcript-view-controls").classList.toggle(
      "hidden",
      speakers.length === 0,
    );
    let segments = [...detail.segments];
    if (speakerFilter.value !== "all") {
      segments = segments.filter((segment) =>
        String(segment.speaker_id) === speakerFilter.value);
    }
    if (document.querySelector("#transcript-order").value === "speaker") {
      segments.sort((left, right) => {
        const leftName = speakerNames.get(Number(left.speaker_id)) || "";
        const rightName = speakerNames.get(Number(right.speaker_id)) || "";
        return leftName.localeCompare(rightName) || left.start_ms - right.start_ms;
      });
    }
    setTitle(transcription.title);
    renderMeetingResults(detail);
    document.querySelector("#editor-meta").textContent =
      `${transcription.model} · ${transcription.language || Meet2Notes.t("transcript.detecting_language")} · ${segments.length} ${Meet2Notes.t("transcript.shown")} / ${detail.segments.length} ${Meet2Notes.t("transcript.segments")}${captureSession ? ` · ${Meet2Notes.t("transcript.live")}` : ""}`;
    if (!detail.segments.length) {
      segmentContainer.removeAttribute("aria-busy");
      if (["running", "queued"].includes(transcription.status)) {
        segmentContainer.innerHTML = `
          <div class="minimal-empty-state processing">
            <span class="pulse-orbit"><i></i></span>
            <h2>The local model is listening</h2>
            <p>The first timestamped segment will appear here as soon as it is ready.</p>
          </div>`;
      } else {
        renderEmpty();
      }
      return;
    }
    if (!segments.length) {
      segmentContainer.removeAttribute("aria-busy");
      segmentContainer.innerHTML = `
        <div class="result-empty">
          <strong>No transcript segments for this speaker</strong>
          <span>Choose All speakers or another participant.</span>
        </div>`;
      return;
    }
    const renderRow = (segment) => {
      const rawSpeaker = Number(segment.speaker_id);
      const hasSpeaker = segment.speaker_id !== null && Number.isFinite(rawSpeaker);
      const speakerNumber = hasSpeaker ? speakerNumbers.get(rawSpeaker) : null;
      const speakerColor = hasSpeaker ? Math.abs(speakerNumber - 1) % 6 : null;
      const provisional = !segment.is_final;
      return `
        <article class="segment-row ${segment.id === searchSegmentId ? "search-target" : ""} ${hasSpeaker ? `speaker-color-${speakerColor}` : "speaker-pending"} ${provisional ? "live-segment" : ""}" data-segment-id="${segment.id}">
          <button class="timestamp-button" data-seek-ms="${segment.start_ms}" title="Play from ${formatTimestamp(segment.start_ms)}" aria-label="Play from ${formatTimestamp(segment.start_ms)}" aria-pressed="false">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>
          </button>
          <div class="segment-cue">
            <span class="segment-speaker"><i></i>${hasSpeaker ? escapeHTML(speakerNames.get(rawSpeaker) || t("speaker", { number: speakerNumber })) : "Speaker pending"}</span>
            ${provisional ? '<span class="live-segment-badge"><i></i> Live</span>' : ""}
          </div>
          <textarea class="segment-editor" rows="1" aria-label="Transcript segment ${segment.segment_index + 1}" ${provisional ? "readonly" : ""}>${escapeHTML(segment.text)}</textarea>
          <button class="segment-save ${provisional ? "hidden" : ""}" data-save-segment="${segment.id}">Save</button>
        </article>`;
    };
    const metadata = document.querySelector("#editor-meta");
    const completeMetadata = metadata.textContent;
    segmentContainer.setAttribute("aria-busy", "true");
    segmentContainer.replaceChildren();
    for (let offset = 0; offset < segments.length; offset += 40) {
      if (generation !== transcriptRenderGeneration) return;
      segmentContainer.insertAdjacentHTML("beforeend", segments.slice(offset, offset + 40).map(renderRow).join(""));
      metadata.textContent = t("transcript.loading_progress", {
        loaded: Math.min(offset + 40, segments.length), total: segments.length,
      });
      await yieldToBrowser();
    }
    if (generation !== transcriptRenderGeneration) return;
    metadata.textContent = completeMetadata;
    segmentContainer.removeAttribute("aria-busy");
    applySearch();
    applyAudioAvailability();
    if (captureSession) {
      window.requestAnimationFrame(() => {
        segmentContainer.scrollTo({
          top: segmentContainer.scrollHeight,
          behavior: "smooth",
        });
      });
    }
  }

  function renderMeetingResults(detail) {
    const tabs = document.querySelector("#meeting-tabs");
    const transcription = detail.transcription;
    const ready = transcription.status === "completed" && !captureSession;
    tabs.classList.toggle("hidden", !ready);
    speakerPanelPending = true;
    if (document.querySelector('[data-meeting-tab="speakers"].active')) {
      renderSpeakerPanel(detail);
      speakerPanelPending = false;
    }
    renderSummaryPanel();
    applyAudioAvailability();
  }

  function renderSpeakerPanel(detail = lastDetail || {}) {
    const container = document.querySelector("#speaker-results");
    const status = document.querySelector("#speaker-result-status");
    document.querySelector("#speaker-rebuild-identification").disabled =
      Boolean(activeSpeakerRebuildJobId)
      || !activeTranscriptionId
      || detail?.transcription?.status !== "completed";
    const speakers = detail.speakers || [];
    const segments = detail.segments || [];
    const rawTurns = detail.speaker_turns || [];
    if (!speakers.length) {
      status.textContent = "Not processed";
      status.classList.remove("ready");
      container.innerHTML = `
        <div class="result-empty">
          <strong>No identified speakers yet</strong>
          <span>Speaker labels will appear here after diarization finishes.</span>
        </div>`;
      return;
    }
    const previousFilter = container.querySelector("#speaker-panel-filter")?.value || "all";
    const previousOrder = container.querySelector("#speaker-panel-order")?.value || "time";
    const speakerMap = new Map(speakers.map((speaker) => [Number(speaker.id), speaker]));
    const colorMap = new Map(speakers.map((speaker, index) => [Number(speaker.id), index]));
    status.textContent = `${speakers.length} speaker${speakers.length === 1 ? "" : "s"}`;
    status.classList.add("ready");
    const speakerColorCount = 6;
    const totalTalkTime = speakers.reduce((total, speaker) => total + speaker.talk_time_ms, 0);
    const cards = speakers.map((speaker, index) => {
      const share = totalTalkTime > 0
        ? Math.round((speaker.talk_time_ms / totalTalkTime) * 100)
        : 0;
      return `
        <article class="speaker-card speaker-color-${index % speakerColorCount}">
          <div class="speaker-card-name">
            <i></i>
            <input value="${escapeHTML(speaker.display_name)}" data-speaker-name="${speaker.id}" data-original-name="${escapeHTML(speaker.display_name)}" aria-label="Speaker name" title="Click to rename this speaker" maxlength="100">
          </div>
          <div class="speaker-card-stats">
            <span><strong>${formatTimestamp(speaker.talk_time_ms)}</strong> speaking</span>
            <span><strong>${speaker.segment_count}</strong> transcript segments</span>
            <span><strong>${share}%</strong> share</span>
          </div>
          <div class="speaker-export-actions">
            <button type="button" class="text-button speaker-card-action" data-play-speaker="${speaker.id}" title="Play this speaker's fragments" aria-label="Play this speaker's fragments" aria-pressed="false">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg> Play
            </button>
            <a class="text-button speaker-card-action" href="/api/transcriptions/${activeTranscriptionId}/speakers/${speaker.id}/text">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l4 4v14H6zM15 3v5h5M9 12h7M9 16h7"/></svg> Export TXT
            </a>
            <button type="button" class="text-button speaker-card-action" data-summarize-speaker="${speaker.id}">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 9.8 8.8 4 11l5.8 2.2L12 19l2.2-5.8L20 11l-5.8-2.2L12 3Z"/></svg>
              ${speaker.summary_status === "completed" ? "View AI summary" : "Resume by AI"}
            </button>
            <button type="button" class="text-button speaker-card-action" data-remember-speaker="${speaker.id}" title="Use this voice to recognize ${escapeHTML(speaker.display_name)} in future meetings">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21a9 9 0 1 0-9-9 9 9 0 0 0 9 9Z"/><path d="m8.5 12 2.3 2.3 4.8-5"/></svg> Remember this voice
            </button>
            <a class="text-button speaker-card-action" href="/api/transcriptions/${activeTranscriptionId}/speakers/${speaker.id}/audio?format=wav">Export WAV</a>
            <a class="text-button speaker-card-action" href="/api/transcriptions/${activeTranscriptionId}/speakers/${speaker.id}/audio?format=mp3">Export MP3</a>
          </div>
        </article>`;
    }).join("");
    let turns = rawTurns.filter((turn) =>
      previousFilter === "all" || String(turn.speaker_id) === previousFilter);
    if (previousOrder === "speaker") {
      turns.sort((left, right) => {
        const leftName = speakerMap.get(Number(left.speaker_id))?.display_name || "";
        const rightName = speakerMap.get(Number(right.speaker_id))?.display_name || "";
        return leftName.localeCompare(rightName) || left.start_ms - right.start_ms;
      });
    } else if (previousOrder === "duration") {
      turns.sort((left, right) =>
        (right.end_ms - right.start_ms) - (left.end_ms - left.start_ms));
    } else {
      turns.sort((left, right) => left.start_ms - right.start_ms);
    }
    const lines = turns.map((turn) => {
      const speaker = speakerMap.get(Number(turn.speaker_id));
      const transcriptText = transcriptTextForSpeakerTurn(turn, segments);
      const colorIndex = colorMap.get(Number(turn.speaker_id)) || 0;
      return `
        <article class="speaker-line speaker-color-${colorIndex % speakerColorCount}">
          <button class="speaker-fragment-play" data-play-range-start="${turn.start_ms}" data-play-range-end="${turn.end_ms}" title="Play this audio fragment" aria-label="Play this audio fragment" aria-pressed="false">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>
          </button>
          <time>${formatTimestamp(turn.start_ms)}–${formatTimestamp(turn.end_ms)}<small>${formatTimestamp(turn.end_ms - turn.start_ms)}</small></time>
          <div><strong><i class="speaker-line-dot" aria-hidden="true"></i>${escapeHTML(speaker?.display_name || "Unidentified")}</strong><p>${escapeHTML(transcriptText || "Diarized audio turn")}</p></div>
        </article>`;
    }).join("");
    container.innerHTML = `
      <div class="speaker-overview-grid">${cards}</div>
      <div class="speaker-panel-toolbar">
        <div><strong>Audio fragments</strong><span>${turns.length} shown</span></div>
        <label><span>Speaker</span><select id="speaker-panel-filter">
          <option value="all">All speakers</option>
          ${speakers.map((speaker) => `<option value="${speaker.id}">${escapeHTML(speaker.display_name)}</option>`).join("")}
        </select></label>
        <label><span>Order</span><select id="speaker-panel-order">
          <option value="time">Timeline</option>
          <option value="speaker">By speaker</option>
          <option value="duration">Longest first</option>
        </select></label>
      </div>
      <div class="speaker-turn-list">${lines || '<div class="result-empty"><strong>No audio fragments</strong><span>Choose another speaker.</span></div>'}</div>`;
    container.querySelector("#speaker-panel-filter").value = previousFilter;
    container.querySelector("#speaker-panel-order").value = previousOrder;
    applyAudioAvailability();
  }

  function transcriptTextForSpeakerTurn(turn, segments) {
    const pieces = segments
      .filter((segment) =>
        segment.end_ms > turn.start_ms
        && segment.start_ms < turn.end_ms)
      .sort((left, right) => left.start_ms - right.start_ms)
      .map((segment) => {
        const words = Array.isArray(segment.metadata?.words)
          ? segment.metadata.words
          : [];
        if (!words.length) return segment.text.trim();
        return words
          .filter((word) => {
            const startMs = Number(word.start) * 1000;
            const endMs = Number(word.end) * 1000;
            return endMs > turn.start_ms && startMs < turn.end_ms;
          })
          .map((word) => String(word.word || ""))
          .join("")
          .trim();
      })
      .filter(Boolean);
    return pieces.join(" ");
  }

  function showSpeakerSummary(speaker, job = null) {
    activeSpeakerSummaryId = Number(speaker.id);
    document.querySelector("#speaker-summary-title").textContent =
      `${speaker.display_name} · AI summary`;
    const content = document.querySelector("#speaker-summary-content");
    const progressWrap = document.querySelector("#speaker-summary-progress-wrap");
    const completed = speaker.summary_status === "completed" && speaker.summary_markdown;
    content.classList.toggle("hidden", !completed);
    progressWrap.classList.toggle("hidden", Boolean(completed));
    document.querySelector("#speaker-summary-close").classList.toggle("hidden", Boolean(completed));
    document.querySelector("#speaker-summary-done").classList.toggle("hidden", !completed);
    if (completed) {
      document.querySelector("#speaker-summary-description").textContent =
        `Saved locally · ${speaker.summary_model || "selected AI model"}`;
      content.innerHTML = escapeHTML(speaker.summary_markdown).replace(/\n/g, "<br>");
    } else {
      const progress = Number(job?.progress || 0);
      document.querySelector("#speaker-summary-description").textContent =
        "The selected AI engine is analyzing only this speaker's contributions.";
      document.querySelector("#speaker-summary-status").textContent =
        job?.message || "Preparing the speaker transcript";
      document.querySelector("#speaker-summary-progress").value = progress * 100;
      document.querySelector("#speaker-summary-percent").textContent =
        `${Math.round(progress * 100)}%`;
    }
    if (!speakerSummaryDismissed && !speakerSummaryDialog.open) {
      speakerSummaryDialog.showModal();
    }
  }

  async function startSpeakerSummary(speakerId) {
    const speaker = lastDetail?.speakers?.find((item) => Number(item.id) === Number(speakerId));
    if (!speaker) return;
    if (speaker.summary_status === "completed" && speaker.summary_markdown) {
      speakerSummaryDismissed = false;
      showSpeakerSummary(speaker);
      return;
    }
    try {
      const response = await api(
        `/api/transcriptions/${activeTranscriptionId}/speakers/${speaker.id}/summary`,
        { method: "POST" },
      );
      Object.assign(speaker, response.speaker);
      activeSpeakerSummaryJobId = response.job.uuid;
      speakerSummaryDismissed = false;
      showSpeakerSummary(speaker, response.job);
      renderSpeakerPanel(lastDetail);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function rememberSpeakerVoice(speakerId) {
    const speaker = lastDetail?.speakers?.find((item) => Number(item.id) === Number(speakerId));
    if (!speaker) return;
    try {
      const profile = await api(`/api/transcriptions/${activeTranscriptionId}/speakers/${speaker.id}/remember`, { method: "POST" });
      toast(`${profile.name} will be recognized in future meetings.`);
      await selectTranscription(activeTranscriptionId);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function isGeneratedSpeakerName(value) {
    return /^speaker\s*\d+$/i.test(String(value || "").trim());
  }

  function offerToRememberRenamedVoice(speaker) {
    pendingRememberSpeakerId = speaker.id;
    document.querySelector("#remember-voice-title").textContent =
      `Remember ${speaker.display_name}'s voice?`;
    document.querySelector("#remember-voice-description").textContent =
      `Use the voice sample from this meeting to recognize ${speaker.display_name} automatically in future meetings.`;
    if (!rememberVoiceDialog.open) rememberVoiceDialog.showModal();
  }

  function setAudioPlaybackButton(button, playing) {
    if (!button) return;
    const speakerSequence = button.matches("[data-play-speaker]");
    const segmentPlayback = button.matches("[data-seek-ms]");
    const playLabel = speakerSequence
      ? "Play this speaker's fragments"
      : segmentPlayback
        ? `Play from ${formatTimestamp(Number(button.dataset.seekMs))}`
        : "Play this audio fragment";
    const pauseLabel = speakerSequence
      ? "Pause this speaker's fragments"
      : segmentPlayback
        ? `Pause playback from ${formatTimestamp(Number(button.dataset.seekMs))}`
        : "Pause this audio fragment";
    button.setAttribute("aria-pressed", String(playing));
    button.setAttribute("aria-label", playing ? pauseLabel : playLabel);
    button.title = playing ? pauseLabel : playLabel;
    button.classList.toggle("playing", playing);
    button.innerHTML = playing
      ? `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="5" width="3.5" height="14" rx="1"/><rect x="13.5" y="5" width="3.5" height="14" rx="1"/></svg>${speakerSequence ? " Pause" : ""}`
      : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>${speakerSequence ? " Play" : ""}`;
  }

  function openDeleteAudioDialog() {
    if (!meetingId || audioWasDeleted() || !recordings.length) return;
    const knownBytes = recordings.reduce(
      (total, recording) => total + Number(recording.size_bytes || 0),
      0,
    );
    document.querySelector("#delete-audio-size").textContent = knownBytes
      ? `At least ${formatBytes(knownBytes)} of stored audio will be released.`
      : "All locally stored audio for this meeting will be removed.";
    if (!deleteAudioDialog.open) deleteAudioDialog.showModal();
  }

  async function deleteMeetingAudio() {
    if (!meetingId) return;
    const confirmButton = document.querySelector("#delete-audio-confirm");
    confirmButton.disabled = true;
    confirmButton.textContent = "Deleting audioâ€¦";
    try {
      currentMeeting = await api(`/api/meetings/${meetingId}/audio`, {
        method: "DELETE",
      });
      recordings = [];
      stopAudioPlayback();
      configureAudio();
      if (lastDetail) renderTranscript(lastDetail);
      deleteAudioDialog.close();
      toast("Meeting audio deleted. Transcript and notes were kept.");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      confirmButton.disabled = false;
      confirmButton.textContent = "Delete audio permanently";
    }
  }

  function clearAudioPlaybackState() {
    speakerPlaybackRanges = [];
    speakerPlaybackIndex = -1;
    audioStopAtSeconds = null;
    setAudioPlaybackButton(activeAudioPlaybackButton, false);
    activeAudioPlaybackButton = null;
  }

  function stopAudioPlayback() {
    audio.pause();
    clearAudioPlaybackState();
  }

  async function playAllSpeakerFragments(speakerId, button) {
    if (!audio.getAttribute("src")) {
      toast("The meeting audio is not available.", "error");
      return;
    }
    stopAudioPlayback();
    speakerPlaybackRanges = (lastDetail?.speaker_turns || [])
      .filter((turn) => Number(turn.speaker_id) === Number(speakerId))
      .sort((left, right) => left.start_ms - right.start_ms);
    if (!speakerPlaybackRanges.length) {
      toast("This speaker has no audio fragments.", "error");
      return;
    }
    speakerPlaybackIndex = 0;
    const range = speakerPlaybackRanges[0];
    activeAudioPlaybackButton = button;
    setAudioPlaybackButton(button, true);
    audio.currentTime = range.start_ms / 1000;
    audioStopAtSeconds = range.end_ms / 1000;
    await audio.play().catch(() => {
      clearAudioPlaybackState();
      toast("The speaker audio could not be played.", "error");
    });
  }

  function markdownToPlainText(source) {
    return String(source || "")
      .replace(/```[^\n]*\n([\s\S]*?)```/g, "$1")
      .replace(/~~~[^\n]*\n([\s\S]*?)~~~/g, "$1")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/^\s*\|?(?:\s*:?-{3,}:?\s*\|)+(?:\s*:?-{3,}:?\s*)?$/gm, "")
      .replace(/^\s*\|(.+)\|\s*$/gm, (_match, row) =>
        row.split("|").map((cell) => cell.trim()).join("\t"))
      .replace(/^\s{0,3}#{1,6}\s+/gm, "")
      .replace(/^\s*>\s?/gm, "")
      .replace(/^\s*[-+*]\s+/gm, "")
      .replace(/^\s*\d+[.)]\s+/gm, "")
      .replace(/(\*\*|__|~~|`)(.*?)\1/g, "$2")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1$2")
      .replace(/(^|[^_])_([^_\n]+)_/g, "$1$2")
      .replace(/^\s*([-*_])(?:\s*\1){2,}\s*$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function renderSummaryPanel() {
    const container = document.querySelector("#ai-report-content");
    const editor = document.querySelector("#ai-report-editor");
    const copyButton = document.querySelector("#ai-copy-notes");
    const editButton = document.querySelector("#ai-edit-notes");
    const saveButton = document.querySelector("#ai-save-notes");
    const toggleButton = document.querySelector("#ai-toggle-view");
    const status = document.querySelector("#ai-result-status");
    const summary = meetingSummaries.find(
      (item) => Number(item.transcription_id) === Number(activeTranscriptionId),
    ) || meetingSummaries[0];
    document.querySelector("#ai-rebuild-notes").disabled = !activeTranscriptionId
      || lastDetail?.transcription?.status !== "completed";
    if (!summary) {
      editingSummaryId = null;
      copyButton.hidden = true;
      editButton.hidden = true;
      saveButton.hidden = true;
      toggleButton.hidden = true;
      container.hidden = false;
      editor.hidden = true;
      status.textContent = "Not processed";
      status.classList.remove("ready");
      container.innerHTML = `
        <div class="result-empty">
          <strong>No AI report yet</strong>
          <span>The summary, decisions and action items will appear here.</span>
        </div>`;
      return;
    }
    if (editingSummaryId && Number(editingSummaryId) !== Number(summary.id)) {
      editingSummaryId = null;
    }
    const editing = Number(editingSummaryId) === Number(summary.id);
    const editable = summary.status === "completed" && Boolean(summary.content_markdown);
    copyButton.hidden = !editable;
    editButton.hidden = !editable || editing;
    saveButton.hidden = !editing;
    toggleButton.hidden = !editable;
    toggleButton.setAttribute("aria-pressed", String(aiNotesViewMode === "plain"));
    toggleButton.title = aiNotesViewMode === "markdown"
      ? "Show AI notes as plain text"
      : "Render AI notes as Markdown";
    document.querySelector("#ai-view-mode-label").textContent =
      aiNotesViewMode === "markdown" ? "Markdown" : "Plain text";
    container.hidden = editing;
    editor.hidden = !editing;
    if (editing && editor.dataset.summaryId !== String(summary.id)) {
      editor.value = summary.content_markdown || "";
      editor.dataset.summaryId = String(summary.id);
      editor.dataset.originalContent = summary.content_markdown || "";
      editor.dataset.markdownDraft = summary.content_markdown || "";
      editor.dataset.viewMode = "markdown";
    }
    if (editing && aiNotesViewMode !== editor.dataset.viewMode) {
      if (aiNotesViewMode === "plain") {
        editor.dataset.markdownDraft = editor.value;
        editor.dataset.plainOriginal = markdownToPlainText(editor.dataset.markdownDraft);
        editor.value = editor.dataset.plainOriginal;
        editor.classList.add("plain-text-mode");
      } else {
        const plainWasEdited = editor.value !== (editor.dataset.plainOriginal || "");
        editor.value = plainWasEdited
          ? editor.value
          : (editor.dataset.markdownDraft || summary.content_markdown || "");
        editor.dataset.markdownDraft = editor.value;
        delete editor.dataset.plainOriginal;
        editor.classList.remove("plain-text-mode");
      }
      editor.dataset.viewMode = aiNotesViewMode;
    }
    status.textContent = summary.status === "completed" ? "Ready" : summary.status;
    status.classList.toggle("ready", summary.status === "completed");
    container.classList.toggle("plain-text-view", aiNotesViewMode === "plain");
    if (summary.content_markdown) {
      if (aiNotesViewMode === "markdown") {
        container.innerHTML = renderMarkdown(summary.content_markdown);
      } else {
        container.textContent = markdownToPlainText(summary.content_markdown);
      }
    } else {
      container.innerHTML = `<div class="result-empty"><strong>AI analysis is ${escapeHTML(summary.status)}</strong><span>The report will appear automatically.</span></div>`;
    }
  }

  function hasUnsavedAiNotes() {
    const editor = document.querySelector("#ai-report-editor");
    const content = editor.dataset.viewMode === "plain"
      && editor.value === (editor.dataset.plainOriginal || "")
      ? (editor.dataset.markdownDraft || "")
      : editor.value;
    return Boolean(editingSummaryId)
      && content !== (editor.dataset.originalContent || "");
  }

  function discardAiNoteChanges() {
    const editor = document.querySelector("#ai-report-editor");
    editingSummaryId = null;
    delete editor.dataset.summaryId;
    delete editor.dataset.originalContent;
    delete editor.dataset.markdownDraft;
    delete editor.dataset.viewMode;
    delete editor.dataset.plainOriginal;
    editor.classList.remove("plain-text-mode");
    renderSummaryPanel();
  }

  function runAfterUnsavedAiCheck(action) {
    if (!hasUnsavedAiNotes()) {
      action();
      return;
    }
    pendingAiNavigation = action;
    if (!aiUnsavedDialog.open) aiUnsavedDialog.showModal();
  }

  function stayOnAiNotes() {
    pendingAiNavigation = null;
    aiUnsavedDialog.close();
    document.querySelector("#ai-report-editor").focus();
  }

  function discardAiNotesAndContinue() {
    const action = pendingAiNavigation;
    pendingAiNavigation = null;
    aiUnsavedDialog.close();
    discardAiNoteChanges();
    if (action) action();
  }

  function openAiRebuildDialog() {
    if (!activeTranscriptionId || lastDetail?.transcription?.status !== "completed") {
      toast("Complete the transcription before rebuilding AI notes.", "error");
      return;
    }
    const select = document.querySelector("#ai-rebuild-format");
    select.innerHTML = noteFormats.map((format) =>
      `<option value="${format.id}">${escapeHTML(format.name)}${format.is_default ? " · Default" : ""}</option>`
    ).join("");
    const selected = noteFormats.find((format) => format.is_default) || noteFormats[0];
    if (selected) select.value = String(selected.id);
    document.querySelector("#ai-rebuild-confirm").disabled = !selected;
    if (!aiRebuildDialog.open) aiRebuildDialog.showModal();
  }

  async function rebuildAiNotes() {
    const button = document.querySelector("#ai-rebuild-confirm");
    const templateId = Number(document.querySelector("#ai-rebuild-format").value);
    if (!templateId) return;
    button.disabled = true;
    try {
      const result = await api(`/api/transcriptions/${activeTranscriptionId}/summaries`, {
        method: "POST",
        body: JSON.stringify({ template_id: templateId }),
      });
      editingSummaryId = null;
      // A fast model may finish and deliver its SSE update before this POST
      // response is handled. Do not replace the completed copy with "queued".
      if (!meetingSummaries.some((item) => item.id === result.summary.id)) {
        meetingSummaries.unshift(result.summary);
      }
      renderSummaryPanel();
      aiRebuildDialog.close();
      toast("AI notes rebuild started.", "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function beginAiNotesEdit() {
    const summary = meetingSummaries.find(
      (item) => Number(item.transcription_id) === Number(activeTranscriptionId),
    ) || meetingSummaries[0];
    if (!summary?.content_markdown || summary.status !== "completed") return;
    editingSummaryId = summary.id;
    aiNotesViewMode = "markdown";
    const editor = document.querySelector("#ai-report-editor");
    editor.value = summary.content_markdown;
    editor.dataset.summaryId = String(summary.id);
    editor.dataset.originalContent = summary.content_markdown;
    editor.dataset.markdownDraft = summary.content_markdown;
    editor.dataset.viewMode = "markdown";
    renderSummaryPanel();
    editor.focus();
  }

  async function copyAiNotes() {
    const editor = document.querySelector("#ai-report-editor");
    const summary = meetingSummaries.find(
      (item) => Number(item.transcription_id) === Number(activeTranscriptionId),
    ) || meetingSummaries[0];
    const content = editingSummaryId
      ? (editor.dataset.viewMode === "plain"
        && editor.value === (editor.dataset.plainOriginal || "")
        ? editor.dataset.markdownDraft
        : editor.value)
      : (summary?.content_markdown || "");
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      toast("AI notes copied to the clipboard.");
    } catch {
      toast("The AI notes could not be copied.", "error");
    }
  }

  async function saveAiNotes() {
    const editor = document.querySelector("#ai-report-editor");
    const content = (editor.dataset.viewMode === "plain"
      && editor.value === (editor.dataset.plainOriginal || "")
      ? editor.dataset.markdownDraft
      : editor.value).trim();
    if (!content) {
      toast("AI notes cannot be empty.", "error");
      return;
    }
    const button = document.querySelector("#ai-save-notes");
    button.disabled = true;
    try {
      const updated = await api(`/api/summaries/${editingSummaryId}`, {
        method: "PATCH",
        body: JSON.stringify({ content_markdown: content }),
      });
      meetingSummaries = meetingSummaries.map((summary) =>
        Number(summary.id) === Number(updated.id) ? updated : summary);
      editingSummaryId = null;
      delete editor.dataset.summaryId;
      delete editor.dataset.originalContent;
      delete editor.dataset.markdownDraft;
      delete editor.dataset.viewMode;
      delete editor.dataset.plainOriginal;
      renderSummaryPanel();
      toast("AI notes saved.", "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function toggleAiNotesView() {
    aiNotesViewMode = aiNotesViewMode === "markdown" ? "plain" : "markdown";
    renderSummaryPanel();
    if (editingSummaryId) document.querySelector("#ai-report-editor").focus();
  }

  function syncSpeakerRebuildControls() {
    const known = document.querySelector('input[name="speaker-rebuild-mode"]:checked')?.value === "known";
    document.querySelector("#speaker-rebuild-count").disabled = !known;
  }

  function openSpeakerRebuildDialog() {
    if (activeSpeakerRebuildJobId) {
      toast("Speaker identification is already running.");
      return;
    }
    if (!activeTranscriptionId || lastDetail?.transcription?.status !== "completed") {
      toast("Complete the transcription before rebuilding speaker identification.", "error");
      return;
    }
    speakerRebuildDismissed = false;
    document.querySelector('input[name="speaker-rebuild-mode"][value="auto"]').checked = true;
    document.querySelector("#speaker-rebuild-count").value = "2";
    document.querySelector("#speaker-rebuild-options").hidden = false;
    document.querySelector("#speaker-rebuild-progress-view").hidden = true;
    document.querySelector("#speaker-rebuild-done").hidden = true;
    document.querySelector("#speaker-rebuild-background").hidden = false;
    document.querySelector("#speaker-rebuild-description").textContent =
      "Run diarization again without changing the transcript or AI notes.";
    syncSpeakerRebuildControls();
    if (!speakerRebuildDialog.open) speakerRebuildDialog.showModal();
  }

  function renderSpeakerRebuildJob(job) {
    if (!job) return;
    const terminal = ["completed", "failed", "cancelled"].includes(job.status);
    const progress = job.status === "completed"
      ? 1
      : Math.max(0, Math.min(1, Number(job.progress || 0)));
    document.querySelector("#speaker-rebuild-options").hidden = true;
    document.querySelector("#speaker-rebuild-progress-view").hidden = false;
    const bar = document.querySelector("#speaker-rebuild-progress");
    if (isIndeterminateJob(job)) bar.removeAttribute("value");
    else bar.value = progress * 100;
    document.querySelector("#speaker-rebuild-percent").textContent = isIndeterminateJob(job)
      ? t("audio.working") : `${Math.round(progress * 100)}%`;
    document.querySelector("#speaker-rebuild-status").textContent = job.status === "failed"
      ? (job.error_text || "Speaker identification failed")
      : job.status === "cancelled"
        ? "Speaker identification was cancelled"
        : (job.message || (job.status === "completed" ? "Speaker identification complete" : "Identifying speakers..."));
    document.querySelector("#speaker-rebuild-background").hidden = terminal;
    document.querySelector("#speaker-rebuild-done").hidden = !terminal;
    document.querySelector("#speaker-rebuild-description").textContent = terminal
      ? (job.status === "completed"
        ? "The speaker results have been updated."
        : "The existing speaker results remain available.")
      : "Only diarization is running. The transcript and AI notes are unchanged.";
    if (!speakerRebuildDismissed && !speakerRebuildDialog.open) {
      speakerRebuildDialog.showModal();
    }
  }

  async function startSpeakerRebuild() {
    const known = document.querySelector('input[name="speaker-rebuild-mode"]:checked')?.value === "known";
    const requestedCount = Number(document.querySelector("#speaker-rebuild-count").value);
    if (known && (!Number.isInteger(requestedCount) || requestedCount < 1 || requestedCount > 20)) {
      toast("Enter a number of speakers between 1 and 20.", "error");
      return;
    }
    const button = document.querySelector("#speaker-rebuild-confirm");
    button.disabled = true;
    try {
      const job = await api(`/api/transcriptions/${activeTranscriptionId}/diarize`, {
        method: "POST",
        body: JSON.stringify({ speaker_count: known ? requestedCount : null }),
      });
      activeSpeakerRebuildJobId = job.uuid;
      document.querySelector("#speaker-rebuild-identification").disabled = true;
      renderSpeakerRebuildJob(job);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function setActiveMeetingTab(name) {
    document.querySelectorAll("[data-meeting-tab]").forEach((button) => {
      const selected = button.dataset.meetingTab === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
    });
    document.querySelectorAll("[data-meeting-panel]").forEach((panel) => {
      const selected = panel.dataset.meetingPanel === name;
      panel.classList.toggle("active", selected);
      panel.hidden = !selected;
    });
    if (name === "speakers" && speakerPanelPending) {
      speakerPanelPending = false;
      setTimeout(() => renderSpeakerPanel(lastDetail), 0);
    }
  }

  function configureWorkflowLabels() {
    document.querySelector("#workflow-diarization-engine").textContent =
      `${engineCapabilities.diarization?.display_name || "Sherpa ONNX"} · voice clustering`;
    const config = preferences.summary_engine || {};
    const summaryName = config.provider === "local"
      ? (engineCapabilities.summaries?.display_name || config.model_file || "Local AI")
      : (config.model || config.provider || "Selected AI engine");
    document.querySelector("#workflow-summary-engine").textContent = summaryName;
  }

  function openPostprocessDialog({
    initialLabel = "Stopping capture…",
    description = "Meet2Notes is processing everything locally. You can continue in the background.",
  } = {}) {
    const continuingVisibleWorkflow = workflowVisible
      && postprocessDialog.open
      && !document.querySelector("#postprocess-progress-view").classList.contains("hidden");
    workflowDismissed = false;
    workflowVisible = true;
    workflowCompleted = false;
    pendingPostprocessKind = null;
    document.querySelector("#postprocess-options").classList.add("hidden");
    document.querySelector("#postprocess-progress-view").classList.remove("hidden");
    if (!continuingVisibleWorkflow) resetPostprocessLog("Final processing started");
    document.querySelector("#postprocess-title").textContent =
      "Turning the conversation into useful notes";
    document.querySelector("#postprocess-description").textContent = description;
    document.querySelector("#postprocess-background").classList.remove("hidden");
    document.querySelector("#postprocess-results").classList.add("hidden");
    document.querySelector("#postprocess-cancel-all").classList.remove("hidden");
    setWorkflowStep("transcription", "active", initialLabel);
    setWorkflowStep("diarization", "waiting", "Waiting");
    setWorkflowStep("summary", "waiting", "Waiting");
    document.querySelector("#postprocess-progress").value = 2;
    document.querySelector("#postprocess-percent").textContent = "2%";
    if (!postprocessDialog.open) postprocessDialog.showModal();
  }

  function syncPostprocessSpeakerControls() {
    const diarization = document.querySelector("#postprocess-diarization").checked;
    const known = document.querySelector('input[name="postprocess-speaker-mode"]:checked')?.value === "known";
    const speakerPanel = document.querySelector("#postprocess-speakers");
    speakerPanel.classList.toggle("is-disabled", !diarization);
    speakerPanel.querySelectorAll("input").forEach((input) => {
      input.disabled = !diarization || (input.id === "postprocess-speaker-count" && !known);
    });
  }

  function syncPostprocessSummaryControls() {
    const enabled = document.querySelector("#postprocess-summary").checked;
    const select = document.querySelector("#postprocess-summary-template");
    select.disabled = !enabled || !noteFormats.length;
    document.querySelector("#postprocess-summary-option").classList.toggle(
      "is-disabled",
      !enabled,
    );
    syncPostprocessSummarySizing();
  }

  function syncPostprocessSummarySizing() {
    const output = document.querySelector("#postprocess-summary-sizing");
    if (!output) return;
    if (!document.querySelector("#postprocess-summary").checked) {
      output.textContent = "AI notes are disabled for this meeting.";
      return;
    }
    const transcriptCharacters = (lastDetail?.segments || []).reduce(
      (total, segment) => total + String(segment.text || "").length + 40,
      0,
    );
    const selectedFormat = noteFormats.find(
      (format) => String(format.id) === document.querySelector("#postprocess-summary-template").value,
    );
    const promptCharacters = JSON.stringify(selectedFormat || {}).length + 900;
    const estimatedTokens = Math.ceil((transcriptCharacters + promptCharacters) / 3);
    const summaryConfig = preferences.summary_engine || {};
    const contextTokens = Number(summaryConfig.context_length) || 16384;
    const outputTokens = Number(summaryConfig.max_output_tokens) || 1024;
    const inputBudget = Math.max(256, contextTokens - outputTokens - Math.max(128, Math.floor(contextTokens / 20)));
    const blocks = Math.max(1, Math.ceil(estimatedTokens / inputBudget));
    output.textContent = blocks > 1
      ? Meet2Notes.t("postprocess.estimated_chunked", { tokens: estimatedTokens.toLocaleString(Meet2Notes.currentLanguage), blocks })
      : Meet2Notes.t("postprocess.estimated_single", { tokens: estimatedTokens.toLocaleString(Meet2Notes.currentLanguage) });
  }

  function populatePostprocessNoteFormats() {
    const select = document.querySelector("#postprocess-summary-template");
    select.innerHTML = noteFormats.length
      ? noteFormats.map((format) => `<option value="${format.id}">${escapeHTML(format.name)}${format.is_default ? " · Default" : ""}</option>`).join("")
      : '<option value="">No note formats available</option>';
    const selected = noteFormats.find((format) => format.is_default) || noteFormats[0];
    if (selected) select.value = String(selected.id);
    syncPostprocessSummaryControls();
  }

  function selectedPostprocessOptions() {
    const diarization = document.querySelector("#postprocess-diarization").checked;
    const summary = document.querySelector("#postprocess-summary").checked;
    const known = document.querySelector('input[name="postprocess-speaker-mode"]:checked')?.value === "known";
    const requestedCount = Number(document.querySelector("#postprocess-speaker-count").value);
    return {
      diarization,
      speaker_count: diarization && known && Number.isInteger(requestedCount)
        && requestedCount >= 1 && requestedCount <= 20
        ? requestedCount
        : null,
      summary,
      summary_template_id: summary
        ? Number(document.querySelector("#postprocess-summary-template").value) || null
        : null,
    };
  }

  function openPostprocessOptions(kind) {
    pendingPostprocessKind = kind;
      workflowVisible = false;
      workflowCompleted = false;
      const isImport = kind === "import";
      const finalPass = document.querySelector("#postprocess-final-pass");
      document.querySelector("#postprocess-diarization").checked = true;
    document.querySelector("#postprocess-summary").checked = true;
    populatePostprocessNoteFormats();
    document.querySelector('input[name="postprocess-speaker-mode"][value="auto"]').checked = true;
    document.querySelector("#postprocess-speaker-count").value = "2";
    document.querySelector("#postprocess-options").classList.remove("hidden");
    document.querySelector("#postprocess-progress-view").classList.add("hidden");
    document.querySelector("#postprocess-options-cancel-all").classList.toggle("hidden", isImport);
    document.querySelector("#postprocess-title").textContent = "Choose final processing";
    document.querySelector("#postprocess-description").textContent = isImport
      ? "Choose what to do after the complete file transcription."
      : "Choose how to finish this live transcription.";
    finalPass.checked = true;
    finalPass.disabled = isImport;
    document.querySelector("#postprocess-final-pass-option").classList.toggle("is-disabled", isImport);
    document.querySelector("#postprocess-final-pass-help").textContent = isImport
      ? "Required for an imported file because there is no live transcript to keep."
      : "Reprocess the complete recording for the most accurate transcript.";
    syncPostprocessSpeakerControls();
    if (!postprocessDialog.open) postprocessDialog.showModal();
  }

  function setWorkflowStep(name, state, label) {
    const row = document.querySelector(`[data-workflow-step="${name}"]`);
    row.classList.remove("active", "completed", "failed", "skipped");
    if (state !== "waiting") row.classList.add(state);
    row.querySelector(".workflow-step-state").textContent = label;
  }

  function isIndeterminateJob(job) {
    return job?.status === "running" && (job.message || "").includes("percentage unavailable");
  }

  function renderJobStep(name, job, { expected = true, pendingLabel = "Waiting" } = {}) {
    if (!expected) {
      setWorkflowStep(name, "skipped", "Not selected");
      return 1;
    }
    if (!job) {
      setWorkflowStep(name, "waiting", pendingLabel);
      return 0;
    }
    const progressPercent = Math.max(0, Math.round(Number(job.progress || 0) * 100));
    const progressLabel = isIndeterminateJob(job) ? t("audio.working") : `${progressPercent}%`;
    const snapshot = `${job.status}:${progressPercent}:${job.message || ""}:${job.error_text || ""}`;
    if (postprocessJobSnapshots.get(job.uuid) !== snapshot) {
      postprocessJobSnapshots.set(job.uuid, snapshot);
      const time = new Date().toLocaleTimeString(Meet2Notes.currentLanguage, { hour12: false });
      const detail = job.error_text || job.message || job.status;
      appendPostprocessLog(
        `[${time}] ${job.status === "failed" ? "ERROR  " : "INFO   "} ${name} · ${progressLabel} · ${detail}`,
      );
    }
    if (["queued", "running", "paused"].includes(job.status)) {
      const progress = Number(job.progress || 0);
      const state = job.status === "queued"
        ? "Queued"
        : isIndeterminateJob(job) ? t("audio.working") : `${Math.max(1, Math.round(progress * 100))}%`;
      setWorkflowStep(name, "active", state);
      return progress;
    }
    if (job.status === "completed") {
      setWorkflowStep(name, "completed", "Completed");
      return 1;
    }
    setWorkflowStep(name, "failed", job.status === "failed" ? "Needs attention" : "Cancelled");
    return 1;
  }

  async function finishWorkflow(hasWarnings) {
    if (workflowCompleted) return;
    workflowCompleted = true;
    const time = new Date().toLocaleTimeString(Meet2Notes.currentLanguage, { hour12: false });
    appendPostprocessLog(
      `[${time}] ${hasWarnings ? "WARNING" : "INFO   "} workflow · ${hasWarnings ? "Finished with warnings" : "All processing completed"}`,
    );
    document.querySelector("#postprocess-progress").value = 100;
    document.querySelector("#postprocess-percent").textContent = "100%";
    document.querySelector("#postprocess-title").textContent = hasWarnings
      ? "Your meeting is ready with some warnings"
      : "Your meeting is ready";
    document.querySelector("#postprocess-description").textContent = hasWarnings
      ? "The available results were saved. Review Settings for any engine that could not run."
      : "The selected results have been saved locally.";
    document.querySelector("#postprocess-background").classList.add("hidden");
    document.querySelector("#postprocess-results").classList.remove("hidden");
    document.querySelector("#postprocess-cancel-all").classList.add("hidden");
    await loadMeetingWorkspace();
  }

  function renderPostprocess(jobs) {
    const targetMeetingId = postprocessMeetingId || meetingId;
    if (!targetMeetingId) return;
    const workflowJobs = jobs.filter((job) =>
      String(job.meeting_id) === String(targetMeetingId) && job.payload?.postprocess);
    const transcriptionJob = workflowJobs.find((job) => job.job_type === "transcribe");
    if (!transcriptionJob) return;
    const diarizationJob = workflowJobs.find((job) => job.job_type === "diarize");
    const summaryJob = workflowJobs.find((job) => job.job_type === "summarize");
    const active = workflowJobs.some((job) => ["queued", "running", "paused"].includes(job.status));
    if (active && !workflowDismissed && !postprocessDialog.open) {
      workflowVisible = true;
      postprocessDialog.showModal();
    }

    const workflowOptions = transcriptionJob.payload?.postprocess_options || {};
    const diarizationExpected = workflowOptions.diarization !== false;
    const summaryExpected = workflowOptions.summary !== false;
    const transcriptionProgress = renderJobStep("transcription", transcriptionJob);
    const transcriptionDone = ["completed", "failed", "cancelled"].includes(transcriptionJob.status);
    if (transcriptionDone && transcriptionJob.status !== "completed") {
      setWorkflowStep("diarization", "skipped", "Skipped");
      setWorkflowStep("summary", "skipped", "Skipped");
      finishWorkflow(true).catch((error) => toast(error.message, "error"));
      return;
    }
    const diarizationProgress = renderJobStep("diarization", diarizationJob, {
      expected: diarizationExpected,
      pendingLabel: transcriptionDone ? "Preparing engine…" : "Waiting",
    });
    const diarizationDone = !diarizationExpected || Boolean(
      diarizationJob && ["completed", "failed", "cancelled"].includes(diarizationJob.status),
    );
    const summaryProgress = renderJobStep("summary", summaryJob, {
      expected: summaryExpected,
      pendingLabel: diarizationDone ? "Preparing engine…" : "Waiting",
    });
    const overall = Math.round(
      transcriptionProgress * 35 + diarizationProgress * 30 + summaryProgress * 35,
    );
    const overallBar = document.querySelector("#postprocess-progress");
    const unknownProgress = workflowJobs.some(isIndeterminateJob);
    if (unknownProgress) overallBar.removeAttribute("value");
    else overallBar.value = overall;
    document.querySelector("#postprocess-percent").textContent =
      unknownProgress ? t("audio.working") : `${overall}%`;

    const summaryDone = !summaryExpected || Boolean(
      summaryJob && ["completed", "failed", "cancelled"].includes(summaryJob.status),
    );
    if (transcriptionDone && diarizationDone && summaryDone) {
      const warnings = workflowJobs.some((job) => ["failed", "cancelled"].includes(job.status));
      finishWorkflow(warnings).catch((error) => toast(error.message, "error"));
    }
  }

  function restorePostprocessing(jobs) {
    const activeWorkflow = jobs.some((job) =>
      job.payload?.postprocess && ["queued", "running", "paused"].includes(job.status));
    if (activeWorkflow) {
      postprocessMeetingId = meetingId;
      workflowVisible = true;
      if (!postprocessLogLines.length) resetPostprocessLog("Restored active processing session");
    }
    renderPostprocess(jobs);
  }

  function renderEmpty() {
    document.querySelector("#editor-meta").textContent = captureSession
      ? "Listening to the selected source"
      : "No transcript yet";
    segmentContainer.innerHTML = captureSession
      ? `
        <div class="minimal-empty-state live-listening">
          <span class="pulse-orbit"><i></i></span>
          <h2>Listening and transcribing locally…</h2>
          <p>The first words will appear here in real time. Pause or stop whenever you need.</p>
        </div>`
      : `
        <div class="minimal-empty-state">
          <span class="minimal-empty-icon">
            <svg viewBox="0 0 64 64" aria-hidden="true"><path d="M13 18h38M13 31h26M13 44h32"/><path d="M47 40a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z"/><path d="m53 52 7 7"/></svg>
          </span>
          <h2>Your transcript will appear here</h2>
          <p>Start with a microphone, system audio, an audio interface, or a media file.</p>
          ${startActionAvailable ? '<button type="button" class="button primary" data-empty-start>Start transcription</button>' : ""}
        </div>`;
  }

  function renderLoadError(message) {
    segmentContainer.innerHTML = `
      <div class="minimal-empty-state">
        <h2>Could not load the transcription workspace</h2>
        <p>${escapeHTML(message)}</p>
      </div>`;
  }

  function openStartDialog() {
    document.querySelector("#transcription-form").reset();
    document.querySelector('input[name="source-mode"][value="microphone"]').checked = true;
    renderSources();
    resetFilePicker();
    startDialog.showModal();
    scheduleSourcePreview(50);
  }

  async function submitTranscription(event) {
    event.preventDefault();
    const submit = event.submitter;
    const mode = selectedSourceMode();
    if (mode !== "file" && (!selectedLiveModes().length ||
        selectedNativeSources().length !== selectedLiveModes().length)) {
      toast("Choose an available audio source.", "error");
      return;
    }
    stopSourcePreview();
    if (mode === "file") {
      startDialog.close();
      openPostprocessOptions("import");
      return;
    }
    submit.disabled = true;
    document.querySelector("#transcription-form").dataset.busy = "true";
    startDialog.close();
    try {
      await startNativeCapture();
    } catch (error) {
      if (!error.cancelled) toast(error.message, "error");
      submit.disabled = false;
      delete document.querySelector("#transcription-form").dataset.busy;
      if (!startDialog.open) startDialog.showModal();
      scheduleSourcePreview();
    }
  }

  function transcriptionOptions() {
    return {
      title: draftTitle,
      profile_id: "default",
      language: null,
      allow_model_download: false,
    };
  }

  async function startNativeCapture() {
    const selected = selectedNativeSources();
    if (!selected.length) throw new Error("Choose an available audio source.");
    const request = (allowModelDownload) => api("/api/capture/sessions", {
      method: "POST",
      body: JSON.stringify({
        source_ids: selected.map((input) => input.value),
        ...transcriptionOptions(),
        allow_model_download: allowModelDownload,
      }),
    });
    let session;
    try {
      session = await request(false);
    } catch (error) {
      if (error.code !== "ConfirmationRequiredError") throw error;
      // The server needs consent before it downloads a missing live model.
      const confirmed = window.confirm(
        `${error.message}

Recording starts right away. Live text appears after the download finishes.`,
      );
      if (!confirmed) {
        const cancelled = new Error("Model download was not confirmed.");
        cancelled.cancelled = true;
        throw cancelled;
      }
      session = await request(true);
    }
    captureSession = session;
    meetingId = String(session.meeting_id);
    activeTranscriptionId = Number(session.transcription_id);
    lastLiveSegmentCount = -1;
    page.dataset.meetingId = meetingId;
    setTitle(session.title);
    delete document.querySelector("#transcription-form").dataset.busy;
    document.querySelector("#transcription-submit").disabled = false;
    window.history.replaceState({}, "", `/?meeting=${meetingId}&live=${session.session_id}`);
    setLiveState(session);
    await selectTranscription(activeTranscriptionId);
    toast("Real-time local transcription started.");
  }

  async function startFileTranscription(postprocessOptions) {
    const file = document.querySelector("#capture-file").files[0];
    if (!file) throw new Error("Choose an audio or video file.");
    const meeting = await api("/api/meetings", {
      method: "POST",
      body: JSON.stringify({
        title: draftTitle,
        source_type: "imported",
      }),
    });
    await uploadFile(meeting.id, file);
    const response = await api(`/api/meetings/${meeting.id}/transcriptions`, {
      method: "POST",
      body: JSON.stringify({
        ...transcriptionOptions(),
        postprocess: true,
        postprocess_options: postprocessOptions,
      }),
    });
    meetingId = String(meeting.id);
    postprocessMeetingId = meetingId;
    activeTranscriptionId = Number(response.transcription.id);
    activeJob = response.job;
    page.dataset.meetingId = meetingId;
    startActionAvailable = false;
    syncStartAction();
    setTitle(response.transcription.title);
    window.history.replaceState({}, "", `/?meeting=${meetingId}`);
    openPostprocessDialog({
      initialLabel: "Preparing media…",
      description: "Meet2Notes is transcribing the complete recording, identifying speakers and creating AI notes locally.",
    });
    await loadMeetingWorkspace();
    toast("Media imported. The complete meeting analysis has started.");
  }

  function uploadFile(targetMeetingId, file) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      const data = new FormData();
      data.append("file", file);
      request.open("POST", `/api/meetings/${targetMeetingId}/import`);
      request.addEventListener("load", () => {
        let result = {};
        try { result = JSON.parse(request.responseText); } catch { /* no-op */ }
        if (request.status >= 200 && request.status < 300) resolve(result);
        else reject(new Error(result.detail || `Upload failed (${request.status})`));
      });
      request.addEventListener("error", () => reject(new Error("Could not reach the local server.")));
      request.send(data);
    });
  }

  function setLiveState(session) {
    captureSession = session;
    startActionAvailable = false;
    syncStartAction();
    document.querySelector("#live-actions").classList.remove("hidden");
    document.querySelector("#live-capture-strip").classList.remove("hidden");
    updateLiveState(session);
    void refreshLiveAssistant(true);
    if (capturePollTimer) window.clearInterval(capturePollTimer);
    capturePollTimer = window.setInterval(pollCapture, 500);
  }

  function clearLiveState() {
    captureSession = null;
    lastLiveSegmentCount = -1;
    capturePollBusy = false;
    if (capturePollTimer) window.clearInterval(capturePollTimer);
    capturePollTimer = null;
    syncStartAction();
    document.querySelector("#live-actions").classList.add("hidden");
    document.querySelector("#live-capture-strip").classList.add("hidden");
  }

  function updateLiveState(session) {
    const paused = session.state === "paused";
    const strip = document.querySelector("#live-capture-strip");
    strip.classList.toggle("paused", paused);
    strip.classList.toggle("transcription-error", Boolean(session.capture_error) || session.realtime_status === "error");
    const sources = session.sources?.length ? session.sources : [session.source];
    document.querySelector("#live-source-name").textContent = sources.map((source) => source.name).join(" + ");
    document.querySelector("#live-capture-state").textContent =
      session.capture_error || (paused ? "Capture paused" : session.realtime_message || "Transcribing locally");
    document.querySelector("#capture-elapsed").textContent = formatTimestamp(session.elapsed_ms);
    document.querySelector("#capture-level").style.width = `${Math.round((session.level || 0) * 100)}%`;
    const inputLevels = document.querySelector("#live-input-levels");
    inputLevels.classList.toggle("hidden", sources.length < 2);
    if (sources.length > 1) {
      inputLevels.innerHTML = sources.map((source) => {
        const label = t(source.kind === "system" ? "capture.system_label" : "capture.microphone_label");
        const level = paused ? 0 : Math.max(0, Math.min(1, session.source_levels?.[source.id] || 0));
        return `<span class="live-input-level" title="${escapeHTML(source.name)}"><span>${escapeHTML(label)}</span>
          <meter min="0" max="1" value="${level}" aria-label="${escapeHTML(label)}"></meter></span>`;
      }).join("");
    }
    document.querySelector("#pause-capture-label").textContent =
      paused ? "Resume transcription" : "Pause transcription";
    document.querySelector(".pause-symbol").classList.toggle("hidden", paused);
    document.querySelector(".resume-symbol").classList.toggle("hidden", !paused);
  }

  async function pollCapture() {
    if (capturePollBusy) return;
    capturePollBusy = true;
    try {
      const session = await api("/api/capture/session");
      if (!session) {
        clearLiveState();
        return;
      }
      captureSession = session;
      updateLiveState(session);
      await refreshLiveTranscript(session);
      await refreshWebhookInsights();
      await refreshLiveAssistant();
    } catch {
      // The next poll can recover a transient local request.
    } finally {
      capturePollBusy = false;
    }
  }

  async function refreshLiveTranscript(session, force = false) {
    if (!session?.transcription_id) return;
    const count = Number(session.segment_count || 0);
    const transcriptionChanged =
      Number(activeTranscriptionId) !== Number(session.transcription_id);
    if (!force && !transcriptionChanged && count === lastLiveSegmentCount) return;
    activeTranscriptionId = Number(session.transcription_id);
    lastLiveSegmentCount = count;
    const detail = await api(`/api/transcriptions/${activeTranscriptionId}`);
    renderTranscript(detail);
  }

  async function refreshWebhookInsights(force = false) {
    if (!meetingId) return;
    const now = Date.now();
    if (!force && now - lastInsightPollAt < 2000) return;
    lastInsightPollAt = now;
    try {
      renderWebhookInsights(await api(`/api/webhooks/meetings/${meetingId}/insights?limit=20`));
    } catch {
      // Insights are optional and must never disturb local transcription.
    }
  }

  function renderWebhookInsights(insights) {
    const panel = document.querySelector("#live-agent-insights");
    const target = document.querySelector("#live-agent-insight-list");
    const visible = (insights || []).filter((item) => item.status !== "dismissed");
    panel.classList.toggle("hidden", !visible.length);
    document.querySelector("#live-agent-insight-count").textContent = visible.length
      ? `${visible.filter((item) => item.status === "new").length} new`
      : "";
    target.innerHTML = visible.map((item) => `
      <article class="live-agent-insight" data-status="${escapeHTML(item.status)}">
        <div><p>${escapeHTML(item.text)}</p><small>${escapeHTML(item.endpoint_name)} · ${escapeHTML(item.kind)}</small></div>
        <div class="live-agent-insight-actions">${item.status === "new" ? `<button class="text-button" type="button" data-insight-status="accepted" data-insight-id="${escapeHTML(item.id)}">Accept</button>` : ""}<button class="text-button" type="button" data-insight-status="dismissed" data-insight-id="${escapeHTML(item.id)}">Dismiss</button></div>
      </article>`).join("");
  }

  function savedLiveAssistantWidgetState() {
    try {
      const value = JSON.parse(window.localStorage.getItem(liveAssistantWidgetStorageKey) || "null");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  }

  function saveLiveAssistantWidgetState() {
    if (!liveAssistantWidget || !liveAssistantWidgetReady) return;
    const rect = liveAssistantWidget.getBoundingClientRect();
    const collapsed = liveAssistantWidget.dataset.collapsed === "true";
    const state = {
      collapsed,
      width: collapsed ? null : Math.round(rect.width),
      height: collapsed ? null : Math.round(rect.height),
    };
    try {
      window.localStorage.setItem(liveAssistantWidgetStorageKey, JSON.stringify(state));
    } catch {
      // Widget geometry is a convenience; storage failures do not affect the meeting.
    }
  }

  function constrainLiveAssistantWidget() {
    if (!liveAssistantWidget || window.innerWidth <= 600 || !liveAssistantWidget.style.left) return;
    const rect = liveAssistantWidget.getBoundingClientRect();
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8));
    const top = Math.max(8, Math.min(rect.top, window.innerHeight - rect.height - 8));
    liveAssistantWidget.style.left = `${Math.round(left)}px`;
    liveAssistantWidget.style.top = `${Math.round(top)}px`;
  }

  function setLiveAssistantWidgetCollapsed(collapsed, { persist = true } = {}) {
    if (!liveAssistantWidget || !liveAssistantToggle) return;
    if (collapsed) {
      liveAssistantWidget.style.left = "auto";
      liveAssistantWidget.style.top = "auto";
      liveAssistantWidget.style.right = "24px";
      liveAssistantWidget.style.bottom = "24px";
    }
    liveAssistantWidget.dataset.collapsed = String(collapsed);
    liveAssistantToggle.setAttribute("aria-expanded", String(!collapsed));
    liveAssistantToggle.setAttribute(
      "aria-label",
      collapsed ? "Expand Live AI Assistant" : "Minimize Live AI Assistant",
    );
    constrainLiveAssistantWidget();
    if (persist) saveLiveAssistantWidgetState();
  }

  function resizeLiveAssistantWidget(width, height) {
    if (!liveAssistantWidget) return;
    const rect = liveAssistantWidget.getBoundingClientRect();
    const maximumWidth = Math.max(300, window.innerWidth - rect.left - 8);
    const maximumHeight = Math.max(210, window.innerHeight - rect.top - 8);
    liveAssistantWidget.style.width = `${Math.round(Math.max(300, Math.min(width, maximumWidth)))}px`;
    liveAssistantWidget.style.height = `${Math.round(Math.max(210, Math.min(height, maximumHeight)))}px`;
  }

  function initializeLiveAssistantWidget() {
    if (!liveAssistantWidget || !liveAssistantDragHandle ||
        !liveAssistantResizeHandle || !liveAssistantToggle) return;
    const state = savedLiveAssistantWidgetState();
    if (Number.isFinite(state.width)) liveAssistantWidget.style.width = `${Math.max(300, state.width)}px`;
    if (Number.isFinite(state.height)) liveAssistantWidget.style.height = `${Math.max(210, state.height)}px`;
    setLiveAssistantWidgetCollapsed(Boolean(state.collapsed), { persist: false });

    liveAssistantToggle.addEventListener("click", () => {
      setLiveAssistantWidgetCollapsed(liveAssistantWidget.dataset.collapsed !== "true");
    });

    liveAssistantQuestionInput?.addEventListener("input", () => {
      if (liveAssistantSend) {
        liveAssistantSend.disabled = liveAssistantQuestionInput.disabled ||
          !liveAssistantQuestionInput.value.trim();
      }
    });
    liveAssistantQuestionInput?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      event.preventDefault();
      liveAssistantQuestionForm?.requestSubmit();
    });
    liveAssistantQuestionForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = liveAssistantQuestionInput?.value.trim();
      if (!meetingId || !question || liveQuestionBusy || liveAssistantQuestionInput.disabled) return;
      liveQuestionBusy = true;
      if (liveAssistantEmpty) liveAssistantEmpty.hidden = true;
      liveAssistantQuestionInput.disabled = true;
      if (liveAssistantSend) liveAssistantSend.disabled = true;
      liveAssistantWidget.dataset.runtimeStatus = "thinking";
      const status = document.querySelector("#live-ai-assistant-status");
      if (status) status.textContent = "Thinking...";
      const pending = document.createElement("article");
      pending.className = "live-agent-insight";
      document.querySelector("#live-ai-assistant-insight-list").after(pending);
      const activity = answerActivity(pending, liveAssistantWidgetBody);
      const controller = new AbortController();
      const abort = () => controller.abort();
      window.addEventListener("pagehide", abort, { once: true });
      try {
        await streamAnswer(`/api/live-assistant/meetings/${meetingId}/questions`,
          { question }, activity.receive, controller.signal);
        activity.finish();
        pending.remove();
        liveAssistantQuestionInput.value = "";
      } catch (error) {
        if (!activity.finish(true)) pending.remove();
        toast(error.message, "error");
      } finally {
        window.removeEventListener("pagehide", abort);
        liveQuestionBusy = false;
        await refreshLiveAssistant(true);
        liveAssistantQuestionInput.focus();
      }
    });

    liveAssistantDragHandle.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 600 || event.button !== 0 || event.target.closest("button")) return;
      const rect = liveAssistantWidget.getBoundingClientRect();
      const offsetX = event.clientX - rect.left;
      const offsetY = event.clientY - rect.top;
      liveAssistantWidget.style.right = "auto";
      liveAssistantWidget.style.bottom = "auto";
      liveAssistantWidget.style.left = `${Math.round(rect.left)}px`;
      liveAssistantWidget.style.top = `${Math.round(rect.top)}px`;
      liveAssistantDragHandle.setPointerCapture(event.pointerId);

      const move = (moveEvent) => {
        const width = liveAssistantWidget.offsetWidth;
        const height = liveAssistantWidget.offsetHeight;
        const left = Math.max(8, Math.min(moveEvent.clientX - offsetX, window.innerWidth - width - 8));
        const top = Math.max(8, Math.min(moveEvent.clientY - offsetY, window.innerHeight - height - 8));
        liveAssistantWidget.style.left = `${Math.round(left)}px`;
        liveAssistantWidget.style.top = `${Math.round(top)}px`;
      };
      const finish = () => {
        liveAssistantDragHandle.removeEventListener("pointermove", move);
        liveAssistantDragHandle.removeEventListener("pointerup", finish);
        liveAssistantDragHandle.removeEventListener("pointercancel", finish);
        saveLiveAssistantWidgetState();
      };
      liveAssistantDragHandle.addEventListener("pointermove", move);
      liveAssistantDragHandle.addEventListener("pointerup", finish);
      liveAssistantDragHandle.addEventListener("pointercancel", finish);
    });

    liveAssistantResizeHandle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || liveAssistantWidget.dataset.collapsed === "true") return;
      event.preventDefault();
      event.stopPropagation();
      const rect = liveAssistantWidget.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      const startWidth = rect.width;
      const startHeight = rect.height;
      liveAssistantResizeHandle.setPointerCapture(event.pointerId);
      const move = (moveEvent) => {
        resizeLiveAssistantWidget(
          startWidth + moveEvent.clientX - startX,
          startHeight + moveEvent.clientY - startY,
        );
      };
      const finish = () => {
        liveAssistantResizeHandle.removeEventListener("pointermove", move);
        liveAssistantResizeHandle.removeEventListener("pointerup", finish);
        liveAssistantResizeHandle.removeEventListener("pointercancel", finish);
        saveLiveAssistantWidgetState();
      };
      liveAssistantResizeHandle.addEventListener("pointermove", move);
      liveAssistantResizeHandle.addEventListener("pointerup", finish);
      liveAssistantResizeHandle.addEventListener("pointercancel", finish);
    });

    liveAssistantResizeHandle.addEventListener("keydown", (event) => {
      const delta = event.shiftKey ? 50 : 20;
      const widthDelta = event.key === "ArrowRight" ? delta : event.key === "ArrowLeft" ? -delta : 0;
      const heightDelta = event.key === "ArrowDown" ? delta : event.key === "ArrowUp" ? -delta : 0;
      if (!widthDelta && !heightDelta) return;
      event.preventDefault();
      const rect = liveAssistantWidget.getBoundingClientRect();
      resizeLiveAssistantWidget(rect.width + widthDelta, rect.height + heightDelta);
      saveLiveAssistantWidgetState();
    });

    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => {
        if (liveAssistantWidget.dataset.collapsed !== "true") saveLiveAssistantWidgetState();
      });
      observer.observe(liveAssistantWidget);
    }
    window.addEventListener("resize", constrainLiveAssistantWidget);
    window.requestAnimationFrame(() => {
      liveAssistantWidgetReady = true;
      constrainLiveAssistantWidget();
    });
  }

  async function refreshLiveAssistant(force = false) {
    if (!meetingId) return;
    const now = Date.now();
    if (!force && now - lastAssistantPollAt < 1500) return;
    lastAssistantPollAt = now;
    try {
      renderLiveAssistant(await api(`/api/live-assistant/meetings/${meetingId}?limit=30`));
    } catch {
      // The assistant is optional and must never disturb recording or transcription.
    }
  }

  function renderLiveAssistant(payload) {
    const target = document.querySelector("#live-ai-assistant-insight-list");
    if (!liveAssistantWidget || !target) return;
    const insights = (payload?.insights || []).slice().reverse();
    const runtime = payload?.runtime || {};
    const meetingIsFinished = Boolean(
      currentMeeting?.status === "ready" || currentMeeting?.ended_at,
    );
    const captureMatchesMeeting = Boolean(
      captureSession && String(captureSession.meeting_id) === String(meetingId),
    );
    const isLiveMeeting = Boolean(
      !meetingIsFinished && payload?.enabled && (runtime.active || captureMatchesMeeting),
    );
    liveAssistantWidget.classList.toggle("hidden", !isLiveMeeting);
    const statusLabels = {
      active: "Listening",
      listening: "Listening",
      thinking: "Thinking...",
      waiting_question: "Waiting for a question",
      waiting_trigger: "Waiting for trigger",
      rate_limited: "Rate limited",
      error: runtime.last_error ? `Error: ${runtime.last_error}` : "Error",
      stopped: "Meeting stopped",
      interrupted: "Interrupted",
      idle: "Idle",
    };
    const status = statusLabels[runtime.status] || (runtime.active ? "Listening" : "Idle");
    const latency = runtime.last_latency_ms ? ` - ${runtime.last_latency_ms} ms` : "";
    liveAssistantWidget.dataset.runtimeStatus = runtime.status || "idle";
    document.querySelector("#live-ai-assistant-status").textContent = `${status}${latency}`;
    const canAsk = Boolean(payload?.enabled && (runtime.active || captureSession));
    if (liveAssistantQuestionInput) {
      liveAssistantQuestionInput.disabled = liveQuestionBusy || !canAsk;
      liveAssistantQuestionInput.placeholder = canAsk
        ? "Ask the Live Assistant..."
        : "Start a live meeting to ask a question";
    }
    if (liveAssistantSend) {
      liveAssistantSend.disabled = liveQuestionBusy || !canAsk || !liveAssistantQuestionInput?.value.trim();
    }
    if (liveAssistantEmpty) {
      liveAssistantEmpty.hidden = liveQuestionBusy || Boolean(insights.length);
      if (runtime.status === "waiting_question") {
        liveAssistantEmpty.textContent = "Waiting for a transcript question ending in a question mark (?).";
      } else if (runtime.status === "waiting_trigger") {
        liveAssistantEmpty.textContent = "No literal trigger has appeared yet. Trigger phrases are exact words or short phrases; put conditional behavior in Instructions.";
      } else if (runtime.status === "thinking") {
        liveAssistantEmpty.textContent = "The assistant is evaluating the latest meeting context...";
      } else if (runtime.status === "error") {
        liveAssistantEmpty.textContent = runtime.last_error || "The latest assistant evaluation failed.";
      } else {
        liveAssistantEmpty.textContent = "Listening for a useful moment in the meeting. Responses will appear here automatically.";
      }
    }
    target.innerHTML = insights.map((item) => `
      <article class="live-agent-insight" data-status="${escapeHTML(item.status)}" data-kind="${escapeHTML(item.kind)}">
        <div class="${item.kind === "user_question" ? "" : "assistant-markdown"}">${item.kind === "user_question" ? `<p>${escapeHTML(item.text)}</p>` : renderMarkdown(item.text)}</div>
      </article>`).join("");
    const latestInsight = insights.at(-1);
    if (latestInsight?.id && latestInsight.id !== lastAssistantInsightId) {
      liveAssistantWidgetBody?.scrollTo({
        top: liveAssistantWidgetBody.scrollHeight,
        behavior: "smooth",
      });
    }
    lastAssistantInsightId = latestInsight?.id || null;
  }

  async function togglePause() {
    if (!captureSession) return;
    const action = captureSession.state === "paused" ? "resume" : "pause";
    try {
      const session = await api(
        `/api/capture/sessions/${captureSession.session_id}/${action}`,
        { method: "POST" },
      );
      captureSession = session;
      updateLiveState(session);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function stopCapture() {
    if (!captureSession) return;
    openPostprocessOptions("live");
  }

  async function finalizeLiveCapture(postprocessOptions, finalTranscription) {
    if (!captureSession) return;
    const stop = document.querySelector("#stop-capture");
    const pause = document.querySelector("#pause-capture");
    stop.disabled = true;
    pause.disabled = true;
    postprocessMeetingId = String(captureSession.meeting_id);
    openPostprocessDialog({
      initialLabel: "Stopping capture...",
      description: "Saving the buffered audio locally before final processing starts.",
    });
    appendWorkflowMessage("capture", "Stop requested; finishing the local audio file");
    await new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
    const stopStartedAt = Date.now();
    const stopWaitTimer = window.setInterval(() => {
      const elapsed = Math.max(1, Math.round((Date.now() - stopStartedAt) / 1000));
      appendWorkflowMessage("capture", `Still finalizing audio locally - ${elapsed}s elapsed`);
    }, 4000);
    try {
      const response = await api(
        `/api/capture/sessions/${captureSession.session_id}/stop`,
        {
          method: "POST",
          body: JSON.stringify({
            final_transcription: finalTranscription,
            postprocess_options: postprocessOptions,
          }),
        },
      );
      meetingId = String(response.session.meeting_id);
      page.dataset.meetingId = meetingId;
      activeTranscriptionId = response.transcription.id;
      activeJob = response.transcription_job;
      setTitle(response.transcription.title);
      clearLiveState();
      appendWorkflowMessage("capture", "Audio saved; final processing job queued");
      window.history.replaceState({}, "", `/?meeting=${meetingId}`);
      openPostprocessDialog({
        initialLabel: finalTranscription ? "Preparing final pass…" : "Saving live transcript…",
        description: "Meet2Notes is processing the selected local steps.",
      });
      await loadMeetingWorkspace();
      await refreshLiveAssistant(true);
    } catch (error) {
      if (postprocessDialog.open) postprocessDialog.close();
      workflowVisible = false;
      toast(error.message, "error");
    } finally {
      window.clearInterval(stopWaitTimer);
      stop.disabled = false;
      pause.disabled = false;
    }
  }

  function renderProgress(job) {
    const card = document.querySelector("#transcription-progress");
    if (!job || !["queued", "running", "paused"].includes(job.status)) {
      card.classList.add("hidden");
      return;
    }
    activeJob = job;
    const value = Math.round((job.progress || 0) * 100);
    card.classList.remove("hidden");
    document.querySelector("#transcription-progress-message").textContent =
      job.message || "Preparing transcription…";
    const unknownProgress = isIndeterminateJob(job);
    document.querySelector("#transcription-progress-value").textContent =
      unknownProgress ? t("audio.working") : `${value}%`;
    const bar = document.querySelector("#transcription-progress-bar");
    if (unknownProgress) bar.removeAttribute("value");
    else bar.value = value;
  }

  function formatTimestamp(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    return hours
      ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
      : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  }

  function setSaveState(saving, text = "Saved") {
    const state = document.querySelector("#editor-save-state");
    state.classList.toggle("saving", saving);
    state.lastChild.textContent = ` ${text}`;
  }

  function syncStartAction() {
    document.querySelector("#start-transcription").classList.toggle(
      "hidden",
      !startActionAvailable,
    );
    document.querySelectorAll("[data-empty-start]").forEach((button) => {
      button.classList.toggle("hidden", !startActionAvailable);
    });
  }

  function applySearch() {
    const query = document.querySelector("#transcript-search").value.trim().toLowerCase();
    document.querySelectorAll(".segment-row").forEach((row) => {
      const text = row.querySelector(".segment-editor").value.toLowerCase();
      row.classList.toggle("filtered", Boolean(query) && !text.includes(query));
    });
  }

  function resetFilePicker() {
    const input = document.querySelector("#capture-file");
    input.value = "";
    document.querySelector("#capture-file-drop").classList.remove("has-file");
    document.querySelector("#capture-file-name").textContent = "Drop a file here or browse";
    document.querySelector("#capture-file-help").textContent =
      "Audio and video up to the configured local limit";
  }

  function transcriptAsText() {
    const segments = lastDetail?.segments || [];
    const speakerMap = new Map(
      (lastDetail?.speakers || []).map((speaker) =>
        [Number(speaker.id), speaker.display_name]),
    );
    return segments.map((segment) => {
      const speaker = segment.speaker_id === null
        ? ""
        : `${speakerMap.get(Number(segment.speaker_id)) || "Unidentified speaker"}: `;
      return `[${formatTimestamp(segment.start_ms)}] ${speaker}${segment.text}`;
    }).join("\n\n");
  }

  function transcriptAsPlainText() {
    const segments = lastDetail?.segments || [];
    const speakerMap = new Map(
      (lastDetail?.speakers || []).map((speaker) =>
        [Number(speaker.id), speaker.display_name]),
    );
    const groups = [];
    segments.forEach((segment) => {
      const text = String(segment.text || "").trim();
      if (!text) return;
      const speakerId = segment.speaker_id === null ? null : Number(segment.speaker_id);
      const speaker = speakerId === null
        ? null
        : speakerMap.get(speakerId) || "Unidentified speaker";
      const previous = groups[groups.length - 1];
      if (previous && previous.speakerId === speakerId) {
        previous.lines.push(text);
        return;
      }
      groups.push({ speakerId, speaker, lines: [text] });
    });
    return groups.map((group) => {
      const body = group.lines.join("\n");
      return group.speaker ? `${group.speaker}:\n${body}` : body;
    }).join("\n\n");
  }

  function transcriptForExport({ layout, timestamps, speakers }) {
    const segments = lastDetail?.segments || [];
    const speakerMap = new Map((lastDetail?.speakers || []).map((speaker) =>
      [Number(speaker.id), speaker.display_name]));
    const formatSegment = (segment) => {
      const parts = [];
      if (timestamps) parts.push(`[${formatTimestamp(segment.start_ms)}]`);
      if (speakers && segment.speaker_id !== null) {
        parts.push(`${speakerMap.get(Number(segment.speaker_id)) || "Unidentified speaker"}:`);
      }
      parts.push(String(segment.text || "").trim());
      return parts.join(" ").trim();
    };
    if (layout === "time" || !speakers) return segments.map(formatSegment).filter(Boolean).join("\n\n");
    const groups = [];
    segments.forEach((segment) => {
      const speakerId = segment.speaker_id === null ? null : Number(segment.speaker_id);
      const previous = groups[groups.length - 1];
      if (previous && previous.speakerId === speakerId) previous.segments.push(segment);
      else groups.push({ speakerId, segments: [segment] });
    });
    return groups.map((group) => group.segments.map(formatSegment).filter(Boolean).join("\n"))
      .filter(Boolean).join("\n\n");
  }

  function aiNotesForExport() {
    const editor = document.querySelector("#ai-report-editor");
    const summary = meetingSummaries.find((item) =>
      Number(item.transcription_id) === Number(activeTranscriptionId)) || meetingSummaries[0];
    return editingSummaryId ? editor.value.trim() : (summary?.content_markdown || "").trim();
  }

  function plainTextExportHtml(content) {
    return String(content || "").replace(/\r\n?/g, "\n").split(/\n{2,}/)
      .map((paragraph) => `<p>${escapeHTML(paragraph).replace(/\n/g, "<br>")}</p>`)
      .join("");
  }

  function htmlDocument(title, content, markdown = false) {
    const body = markdown ? renderMarkdown(content) : plainTextExportHtml(content);
    return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHTML(title)}</title><style>body{font:12pt/1.55 Arial,sans-serif;color:#1d2939;max-width:760px;margin:48px auto;padding:0 24px}h1{font-size:20pt;margin:0 0 18pt}h2{font-size:16pt}h3{font-size:13pt}p{margin:0 0 10pt}ul,ol{margin:0 0 10pt;padding-left:24pt}blockquote{margin:0 0 10pt;padding-left:12pt;border-left:3px solid #b8cefa}table{width:100%;margin:0 0 10pt;border-collapse:collapse}th,td{padding:6pt;border:1px solid #cfd8e5;text-align:left}pre{white-space:pre-wrap}main>:first-child{margin-top:0}@media print{body{margin:0;max-width:none}}</style></head><body><h1>${escapeHTML(title)}</h1><main>${body}</main></body></html>`;
  }

  function openExportDialog(source, format) {
    const isTranscript = source === "transcript";
    pendingExport = { source, format };
    document.querySelector("#export-dialog-title").textContent =
      `Export ${isTranscript ? "transcription" : "AI notes"}`;
    document.querySelector("#export-layout-options").hidden = !isTranscript;
    document.querySelector("#export-detail-options").hidden = !isTranscript;
    document.querySelector("#export-confirm").textContent = format === "clipboard" ? "Copy" : "Export";
    if (!exportDialog.open) exportDialog.showModal();
  }

  async function exportPendingContent() {
    if (!pendingExport) return;
    const { source, format } = pendingExport;
    const content = source === "transcript"
      ? transcriptForExport({
        layout: document.querySelector('input[name="export-layout"]:checked').value,
        timestamps: document.querySelector("#export-timestamps").checked,
        speakers: document.querySelector("#export-speakers").checked,
      })
      : aiNotesForExport();
    if (!content) {
      toast(`There is no ${source === "transcript" ? "transcription" : "AI notes"} to export.`, "error");
      return;
    }
    if (format === "clipboard") {
      try {
        await navigator.clipboard.writeText(content);
        toast("Copied to the clipboard.");
      } catch {
        toast("The content could not be copied.", "error");
        return;
      }
    } else if (format === "markdown") {
      downloadFile(exportFilename("md"), content, "text/markdown;charset=utf-8");
    } else if (format === "word") {
      downloadFile(exportFilename("doc"), htmlDocument(draftTitle, content, source === "notes"), "application/msword;charset=utf-8");
    } else if (format === "pdf") {
      const printWindow = window.open("", "_blank");
      if (!printWindow) {
        toast("Allow pop-ups to export a PDF.", "error");
        return;
      }
      printWindow.document.write(htmlDocument(draftTitle, content, source === "notes"));
      printWindow.document.close();
      printWindow.focus();
      printWindow.print();
    }
    exportDialog.close();
  }

  function exportAudio(requestedFormat) {
    if (!activeTranscriptionId) {
      toast("There is no audio recording to export.", "error");
      return;
    }
    const link = document.createElement("a");
    link.href = `/api/transcriptions/${activeTranscriptionId}/audio?format=${requestedFormat}`;
    link.download = exportFilename(requestedFormat);
    link.click();
  }

  function downloadFile(filename, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function exportFilename(extension) {
    const stem = draftTitle.trim().replace(/[^a-z0-9_-]+/gi, "-").replace(/^-|-$/g, "")
      || "meeting";
    return `${stem}.${extension}`;
  }

  titleDisplay.addEventListener("click", beginRename);
  titleInput.addEventListener("blur", commitRename);
  titleInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      titleInput.blur();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  });

  document.querySelector("#start-transcription").addEventListener("click", openStartDialog);
  document.querySelector("#refresh-sources").addEventListener("click", refreshSources);
  document.querySelectorAll('input[name="source-mode"]').forEach((input) =>
    input.addEventListener("change", () => {
      if (input.checked) {
        document.querySelectorAll('input[name="source-mode"]').forEach((other) => {
          if (input.value === "file" ? other.value !== "file" : other.value === "file") other.checked = false;
        });
      }
      renderSourceMode();
    }));
  document.querySelector("#native-source-list").addEventListener("change", (event) => {
    if (event.target.matches("[data-native-source]")) {
      rememberSelectedDevices();
      syncCaptureSelection();
      scheduleSourcePreview(20);
    }
  });
  document.querySelectorAll("[data-close-transcription]").forEach((button) =>
    button.addEventListener("click", () => {
      if (!document.querySelector("#transcription-form").dataset.busy) {
        stopSourcePreview();
        startDialog.close();
      }
    }));
  startDialog.addEventListener("close", stopSourcePreview);
  document.querySelector("#transcription-form").addEventListener("submit", submitTranscription);
  document.querySelector("#capture-file").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return resetFilePicker();
    document.querySelector("#capture-file-drop").classList.add("has-file");
    document.querySelector("#capture-file-name").textContent = file.name;
    document.querySelector("#capture-file-help").textContent = formatBytes(file.size);
  });
  document.querySelector("#pause-capture").addEventListener("click", togglePause);
  document.querySelector("#stop-capture").addEventListener("click", stopCapture);
  document.querySelector("#delete-meeting-audio").addEventListener("click", openDeleteAudioDialog);
  document.querySelector("#delete-audio-close").addEventListener("click", () => deleteAudioDialog.close());
  document.querySelector("#delete-audio-cancel").addEventListener("click", () => deleteAudioDialog.close());
  document.querySelector("#delete-audio-confirm").addEventListener("click", deleteMeetingAudio);
  document.querySelector("#postprocess-diarization").addEventListener("change", syncPostprocessSpeakerControls);
  document.querySelector("#postprocess-summary").addEventListener("change", syncPostprocessSummaryControls);
  document.querySelector("#postprocess-summary-template").addEventListener("change", syncPostprocessSummarySizing);
  document.querySelectorAll('input[name="postprocess-speaker-mode"]').forEach((input) =>
    input.addEventListener("change", syncPostprocessSpeakerControls));
  document.querySelector("#postprocess-options-cancel").addEventListener("click", () => {
    const cancelledKind = pendingPostprocessKind;
    pendingPostprocessKind = null;
    postprocessDialog.close();
    if (cancelledKind === "import") startDialog.showModal();
  });
  document.querySelector("#postprocess-options-start").addEventListener("click", async (event) => {
    const kind = pendingPostprocessKind;
    if (!kind) return;
    const known = document.querySelector('input[name="postprocess-speaker-mode"]:checked')?.value === "known";
    const requestedCount = Number(document.querySelector("#postprocess-speaker-count").value);
    if (known && (!Number.isInteger(requestedCount) || requestedCount < 1 || requestedCount > 20)) {
      toast("Enter a number of speakers between 1 and 20.", "error");
      return;
    }
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const options = selectedPostprocessOptions();
      if (kind === "import") {
        await startFileTranscription(options);
      } else {
        await finalizeLiveCapture(
          options,
          document.querySelector("#postprocess-final-pass").checked,
        );
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
  document.querySelector("#cancel-transcription").addEventListener("click", async () => {
    if (!activeJob) return;
    try {
      await api(`/api/jobs/${activeJob.uuid}/cancel`, { method: "POST" });
      toast("Cancellation requested.");
    } catch (error) {
      toast(error.message, "error");
    }
  });
  document.querySelector("#transcript-search").addEventListener("input", applySearch);
  document.querySelector("#transcript-speaker-filter").addEventListener("change", () => {
    if (lastDetail) renderTranscript(lastDetail);
  });
  document.querySelector("#transcript-order").addEventListener("change", () => {
    if (lastDetail) renderTranscript(lastDetail);
  });
  document.querySelectorAll("[data-meeting-tab]").forEach((button) =>
    button.addEventListener("click", () => {
      if (button.getAttribute("aria-selected") === "true") return;
      runAfterUnsavedAiCheck(() => setActiveMeetingTab(button.dataset.meetingTab));
    }));
  document.querySelector("#ai-rebuild-notes").addEventListener("click", () =>
    runAfterUnsavedAiCheck(openAiRebuildDialog));
  document.querySelector("#ai-rebuild-confirm").addEventListener("click", rebuildAiNotes);
  document.querySelectorAll("#ai-rebuild-close, #ai-rebuild-cancel").forEach((button) =>
    button.addEventListener("click", () => aiRebuildDialog.close()));
  aiRebuildDialog.addEventListener("click", (event) => {
    if (event.target === aiRebuildDialog) aiRebuildDialog.close();
  });
  document.querySelector("#ai-copy-notes").addEventListener("click", copyAiNotes);
  document.querySelector("#ai-edit-notes").addEventListener("click", beginAiNotesEdit);
  document.querySelector("#ai-save-notes").addEventListener("click", saveAiNotes);
  document.querySelector("#ai-toggle-view").addEventListener("click", toggleAiNotesView);
  document.querySelector("#speaker-rebuild-identification").addEventListener("click", openSpeakerRebuildDialog);
  document.querySelectorAll('input[name="speaker-rebuild-mode"]').forEach((input) =>
    input.addEventListener("change", syncSpeakerRebuildControls));
  document.querySelector("#speaker-rebuild-confirm").addEventListener("click", startSpeakerRebuild);
  document.querySelectorAll("#speaker-rebuild-close, #speaker-rebuild-cancel, #speaker-rebuild-background, #speaker-rebuild-done").forEach((button) =>
    button.addEventListener("click", () => {
      speakerRebuildDismissed = true;
      speakerRebuildDialog.close();
    }));
  speakerRebuildDialog.addEventListener("cancel", () => {
    speakerRebuildDismissed = true;
  });
  document.querySelectorAll("#ai-unsaved-close, #ai-unsaved-stay").forEach((button) =>
    button.addEventListener("click", stayOnAiNotes));
  document.querySelector("#ai-unsaved-discard").addEventListener("click", discardAiNotesAndContinue);
  aiUnsavedDialog.addEventListener("click", (event) => {
    if (event.target === aiUnsavedDialog) stayOnAiNotes();
  });
  aiUnsavedDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    stayOnAiNotes();
  });
  document.addEventListener("click", (event) => {
    const anchor = event.target.closest?.("a[href]");
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
        || event.shiftKey || event.altKey || anchor.target === "_blank" || !hasUnsavedAiNotes()) return;
    const destination = new URL(anchor.href, window.location.href);
    if (destination.href === window.location.href) return;
    event.preventDefault();
    runAfterUnsavedAiCheck(() => window.location.assign(destination.href));
  });
  window.addEventListener("beforeunload", (event) => {
    if (!hasUnsavedAiNotes()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  document.querySelectorAll("[data-export-source]").forEach((button) => button.addEventListener("click", () =>
    openExportDialog(button.dataset.exportSource, button.dataset.exportFormat)));
  document.querySelectorAll("[data-audio-export]").forEach((button) => button.addEventListener("click", () =>
    exportAudio(button.dataset.audioExport)));
  document.querySelector("#export-form").addEventListener("submit", (event) => {
    event.preventDefault();
    exportPendingContent();
  });
  document.querySelectorAll("#export-close, #export-cancel").forEach((button) =>
    button.addEventListener("click", () => exportDialog.close()));
  exportDialog.addEventListener("click", (event) => {
    if (event.target === exportDialog) exportDialog.close();
  });
  document.querySelector("#download-meeting-json").addEventListener("click", () => {
    downloadFile(
      exportFilename("json"),
      JSON.stringify({ transcript: lastDetail, summaries: meetingSummaries }, null, 2),
      "application/json;charset=utf-8",
    );
  });
  document.querySelector("#postprocess-background").addEventListener("click", () => {
    workflowDismissed = true;
    workflowVisible = false;
    postprocessDialog.close();
    toast("Processing continues safely in the background.");
  });
  document.querySelector("#postprocess-results").addEventListener("click", () => {
    workflowDismissed = true;
    workflowVisible = false;
    postprocessDialog.close();
    setActiveMeetingTab(meetingSummaries.some((item) => item.status === "completed")
      ? "intelligence"
      : "transcript");
  });
  document.querySelectorAll("#postprocess-cancel-all, #postprocess-options-cancel-all").forEach((discardButton) => discardButton.addEventListener("click", async (event) => {
    const activeSessionId = captureSession?.session_id;
    const targetMeetingId = postprocessMeetingId || meetingId;
    if (!activeSessionId && !targetMeetingId) return;
    const confirmed = window.confirm(
      "Cancel all remaining processing and permanently discard this meeting? This cannot be undone.",
    );
    if (!confirmed) return;
    const button = event.currentTarget;
    button.disabled = true;
    try {
      if (activeSessionId) {
        await api(`/api/capture/sessions/${activeSessionId}/discard`, { method: "POST" });
      } else {
        await api(`/api/meetings/${targetMeetingId}`, { method: "DELETE" });
      }
      clearLiveState();
      postprocessDialog.close();
      window.location.assign("/?new=1");
    } catch (error) {
      button.disabled = false;
      toast(error.message, "error");
    }
  }));

  document.querySelectorAll("[data-close-remember-voice]").forEach((button) => {
    button.addEventListener("click", () => {
      pendingRememberSpeakerId = null;
      rememberVoiceDialog.close();
    });
  });
  document.querySelector("#remember-renamed-voice").addEventListener("click", async (event) => {
    const speakerId = pendingRememberSpeakerId;
    if (!speakerId) return;
    event.currentTarget.disabled = true;
    rememberVoiceDialog.close();
    pendingRememberSpeakerId = null;
    await rememberSpeakerVoice(speakerId);
    event.currentTarget.disabled = false;
  });
  rememberVoiceDialog.addEventListener("click", (event) => {
    if (event.target !== rememberVoiceDialog) return;
    pendingRememberSpeakerId = null;
    rememberVoiceDialog.close();
  });
  document.querySelector("#speaker-summary-close").addEventListener("click", () => {
    speakerSummaryDismissed = true;
    speakerSummaryDialog.close();
    toast("Speaker summary continues in the background.");
  });
  document.querySelector("#speaker-summary-done").addEventListener("click", () => {
    speakerSummaryDismissed = true;
    speakerSummaryDialog.close();
  });

  document.addEventListener("click", async (event) => {
    if (event.target.closest("[data-empty-start]")) {
      openStartDialog();
      return;
    }
    const playSpeaker = event.target.closest("[data-play-speaker]");
    if (playSpeaker) {
      if (activeAudioPlaybackButton === playSpeaker && !audio.paused) {
        stopAudioPlayback();
      } else {
        await playAllSpeakerFragments(playSpeaker.dataset.playSpeaker, playSpeaker);
      }
      return;
    }
    const summarizeSpeaker = event.target.closest("[data-summarize-speaker]");
    if (summarizeSpeaker) {
      await startSpeakerSummary(summarizeSpeaker.dataset.summarizeSpeaker);
      return;
    }
    const remember = event.target.closest("[data-remember-speaker]");
    if (remember) {
      await rememberSpeakerVoice(remember.dataset.rememberSpeaker);
      return;
    }
    const range = event.target.closest("[data-play-range-start]");
    if (range) {
      if (!audio.getAttribute("src")) {
        toast("The meeting audio is not available.", "error");
        return;
      }
      if (activeAudioPlaybackButton === range && !audio.paused) {
        stopAudioPlayback();
        return;
      }
      stopAudioPlayback();
      audio.currentTime = Number(range.dataset.playRangeStart) / 1000;
      audioStopAtSeconds = Number(range.dataset.playRangeEnd) / 1000;
      activeAudioPlaybackButton = range;
      setAudioPlaybackButton(range, true);
      await audio.play().catch(() => {
        clearAudioPlaybackState();
        toast("The audio fragment could not be played.", "error");
      });
      return;
    }
    const seek = event.target.closest("[data-seek-ms]");
    if (seek) {
      if (!audio.getAttribute("src")) {
        toast("The meeting audio is not available.", "error");
        return;
      }
      if (activeAudioPlaybackButton === seek && !audio.paused) {
        stopAudioPlayback();
        return;
      }
      stopAudioPlayback();
      audio.currentTime = Number(seek.dataset.seekMs) / 1000;
      activeAudioPlaybackButton = seek;
      setAudioPlaybackButton(seek, true);
      await audio.play().catch(() => {
        clearAudioPlaybackState();
        toast("The audio could not be played.", "error");
      });
      return;
    }
    const save = event.target.closest("[data-save-segment]");
    if (!save) return;
    const row = save.closest(".segment-row");
    const editor = row.querySelector(".segment-editor");
    save.disabled = true;
    setSaveState(true, "Saving…");
    try {
      await api(`/api/transcript-segments/${save.dataset.saveSegment}`, {
        method: "PATCH",
        body: JSON.stringify({ text: editor.value.trim() }),
      });
      row.classList.remove("dirty");
      setSaveState(false);
      toast("Segment saved.");
    } catch (error) {
      toast(error.message, "error");
      setSaveState(false, "Save failed");
    } finally {
      save.disabled = false;
    }
  });

  audio.addEventListener("timeupdate", () => {
    if (audioStopAtSeconds === null || audio.currentTime < audioStopAtSeconds) return;
    if (speakerPlaybackIndex >= 0 && speakerPlaybackIndex + 1 < speakerPlaybackRanges.length) {
      speakerPlaybackIndex += 1;
      const next = speakerPlaybackRanges[speakerPlaybackIndex];
      audio.currentTime = next.start_ms / 1000;
      audioStopAtSeconds = next.end_ms / 1000;
      audio.play().catch(() => {
        clearAudioPlaybackState();
        toast("Speaker playback could not continue.", "error");
      });
      return;
    }
    stopAudioPlayback();
  });

  audio.addEventListener("pause", clearAudioPlaybackState);
  audio.addEventListener("ended", clearAudioPlaybackState);

  document.addEventListener("change", async (event) => {
    if (event.target.matches("#speaker-panel-filter, #speaker-panel-order")) {
      renderSpeakerPanel(lastDetail);
      return;
    }
    if (!event.target.matches("[data-speaker-name]")) return;
    const input = event.target;
    const name = input.value.trim();
    if (!name || name === input.dataset.originalName) {
      input.value = input.dataset.originalName;
      return;
    }
    input.disabled = true;
    const renamedGeneratedSpeaker = isGeneratedSpeakerName(input.dataset.originalName)
      && !isGeneratedSpeakerName(name);
    try {
      const updated = await api(`/api/speakers/${input.dataset.speakerName}`, {
        method: "PATCH",
        body: JSON.stringify({ display_name: name }),
      });
      const speaker = lastDetail?.speakers?.find((item) =>
        Number(item.id) === Number(updated.id));
      if (speaker) Object.assign(speaker, updated);
      renderTranscript(lastDetail);
      toast(`Speaker renamed to ${updated.display_name}.`);
      if (renamedGeneratedSpeaker) offerToRememberRenamedVoice(updated);
    } catch (error) {
      input.disabled = false;
      input.value = input.dataset.originalName;
      toast(error.message, "error");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!event.target.matches("[data-speaker-name]")) return;
    if (event.key === "Enter") {
      event.preventDefault();
      event.target.blur();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.target.value = event.target.dataset.originalName;
      event.target.blur();
    }
  });

  segmentContainer.addEventListener("input", (event) => {
    if (!event.target.matches(".segment-editor")) return;
    event.target.closest(".segment-row").classList.add("dirty");
    setSaveState(true, "Unsaved changes");
    applySearch();
  });

  document.addEventListener("localmeet:languagechange", () => {
    if (lastDetail) renderTranscript(lastDetail);
  });

  document.querySelector("#live-agent-insight-list")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-insight-id]");
    if (!button) return;
    button.disabled = true;
    try {
      await api(`/api/webhooks/insights/${encodeURIComponent(button.dataset.insightId)}`, {
        method: "PUT",
        body: JSON.stringify({ status: button.dataset.insightStatus }),
      });
      await refreshWebhookInsights(true);
    } catch (error) {
      button.disabled = false;
      toast(error.message, "error");
    }
  });

  async function applyJobUpdates(jobs) {
    if (!meetingId) return;
    if (activeSpeakerRebuildJobId) {
      const rebuildJob = jobs.find((job) => job.uuid === activeSpeakerRebuildJobId);
      if (rebuildJob) {
        renderSpeakerRebuildJob(rebuildJob);
        if (["completed", "failed", "cancelled"].includes(rebuildJob.status)) {
          activeSpeakerRebuildJobId = null;
          if (rebuildJob.status === "completed" && activeTranscriptionId) {
            await selectTranscription(activeTranscriptionId);
            toast("Speaker identification rebuilt.", "success");
          } else {
            renderSpeakerPanel();
          }
        }
      }
    }
    if (activeSpeakerSummaryJobId) {
      const speakerJob = jobs.find((job) => job.uuid === activeSpeakerSummaryJobId);
      const speaker = lastDetail?.speakers?.find(
        (item) => Number(item.id) === Number(activeSpeakerSummaryId),
      );
      if (speakerJob && speaker) {
        if (["queued", "running", "paused"].includes(speakerJob.status)) {
          speaker.summary_status = speakerJob.status;
          showSpeakerSummary(speaker, speakerJob);
        } else {
          const detail = await api(`/api/transcriptions/${activeTranscriptionId}`);
          lastDetail = detail;
          renderTranscript(detail);
          const refreshed = detail.speakers.find(
            (item) => Number(item.id) === Number(activeSpeakerSummaryId),
          );
          if (refreshed && speakerJob.status === "completed") {
            showSpeakerSummary(refreshed);
          } else if (speakerJob.status === "failed") {
            document.querySelector("#speaker-summary-status").textContent =
              speakerJob.error_text || "Summary generation failed";
            toast(speakerJob.error_text || "Speaker summary failed.", "error");
          }
          activeSpeakerSummaryJobId = null;
        }
      }
    }
    renderPostprocess(jobs);
    const related = jobs.find((job) =>
      String(job.meeting_id) === String(meetingId) &&
      job.job_type === "transcribe" &&
      ["queued", "running", "paused"].includes(job.status));
    renderProgress(related || null);
    if (activeTranscriptionId && related) {
      await selectTranscription(activeTranscriptionId);
    }
    const terminal = jobs.filter((job) =>
      String(job.meeting_id) === String(meetingId) &&
      ["completed", "failed", "cancelled"].includes(job.status));
    const newlyTerminal = terminal.filter((job) => !terminalJobIds.has(job.uuid));
    if (!newlyTerminal.length) return;
    // One event can include both a finished summary and its newer index job.
    // Refresh each affected view before acknowledging the entire batch.
    if (newlyTerminal.some((job) => ["transcribe", "diarize"].includes(job.job_type))) {
      versions = await api(`/api/meetings/${meetingId}/transcriptions`);
      const preferred = versions.find((item) => item.is_active) || versions[0];
      if (preferred) await selectTranscription(preferred.id);
    }
    if (newlyTerminal.some((job) => job.job_type === "summarize" &&
        job.payload?.summary_scope !== "speaker")) {
      meetingSummaries = await api(`/api/meetings/${meetingId}/summaries`);
      renderSummaryPanel();
    }
    newlyTerminal.forEach((job) => {
      terminalJobIds.add(job.uuid);
      if (job.status === "completed" && job.job_type === "transcribe" &&
          !job.payload?.postprocess) {
        toast("Transcript completed locally.");
      }
      if (job.status === "failed") {
        toast(job.error_text || `${job.job_type} failed.`, "error");
      }
    });
  }

  let pendingJobSnapshot = null;
  let applyingJobSnapshot = false;
  let jobRetryTimer = null;
  async function drainJobUpdates() {
    if (applyingJobSnapshot) return;
    clearTimeout(jobRetryTimer);
    applyingJobSnapshot = true;
    try {
      while (pendingJobSnapshot) {
        const snapshot = pendingJobSnapshot;
        pendingJobSnapshot = null;
        try {
          await applyJobUpdates(snapshot);
        } catch (error) {
          // SSE sends changed snapshots only: retry a failed refresh even when
          // no further jobs finish. Newer snapshots take precedence.
          pendingJobSnapshot ||= snapshot;
          console.warn("Could not refresh meeting job results; retrying", error);
          jobRetryTimer = setTimeout(drainJobUpdates, 2000);
          break;
        }
      }
    } finally {
      applyingJobSnapshot = false;
    }
  }
  subscribeJobs((jobs) => {
    pendingJobSnapshot = jobs;
    void drainJobUpdates();
  });

  async function initializeWorkspace() {
    bindActivityLog();
    initializeLiveAssistantWidget();
    await loadWorkspace();
    syncStartAction();

    const url = new URL(window.location.href);
    if (!newMeetingRequested) return;
    url.searchParams.delete("new");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    if (captureSession) {
      toast("Stop the current live transcription before starting a new meeting.", "error");
      return;
    }
    openStartDialog();
  }

  initializeWorkspace();
})();
