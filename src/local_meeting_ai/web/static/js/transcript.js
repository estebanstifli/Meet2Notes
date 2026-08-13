(() => {
  "use strict";

  const {
    api,
    escapeHTML,
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
  const speakerSummaryDialog = document.querySelector("#speaker-summary-dialog");
  const newMeetingRequested = new URL(window.location.href).searchParams.get("new") === "1";

  let meetingId = page.dataset.meetingId || null;
  let draftTitle = page.dataset.defaultTitle || "New Transcription";
  let audioSources = [];
  let captureCapability = {};
  let recordings = [];
  let versions = [];
  let meetingSummaries = [];
  let engineCapabilities = {};
  let preferences = {};
  let activeTranscriptionId = null;
  let activeJob = null;
  let captureSession = null;
  let capturePollTimer = null;
  let capturePollBusy = false;
  let lastInsightPollAt = 0;
  let lastLiveSegmentCount = -1;
  let lastDetail = null;
  let terminalJobIds = new Set();
  let sourcePreviewTimer = null;
  let sourcePreviewBusy = false;
  let latestActivityId = 0;
  let postprocessMeetingId = null;
  let workflowVisible = false;
  let workflowDismissed = false;
  let workflowCompleted = false;
  let startActionAvailable = newMeetingRequested;
  let audioStopAtSeconds = null;
  let speakerPlaybackRanges = [];
  let speakerPlaybackIndex = -1;
  let activeSpeakerSummaryJobId = null;
  let activeSpeakerSummaryId = null;
  let speakerSummaryDismissed = false;
  let pendingPostprocessKind = null;
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

  function resetPostprocessLog(message) {
    postprocessLogLines.length = 0;
    postprocessJobSnapshots.clear();
    const time = new Date().toLocaleTimeString([], { hour12: false });
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
        : timestamp.toLocaleTimeString([], { hour12: false });
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
    const mobile = window.matchMedia("(max-width: 600px)").matches;
    const maximum = mobile
      ? Math.min(220, Math.max(150, transcriptionWorkspace.clientHeight - 420))
      : Math.max(180, transcriptionWorkspace.clientHeight - 320);
    const height = Math.round(Math.min(maximum, Math.max(110, requestedHeight)));
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
      if (event.key === "Home") return setActivityLogHeight(110);
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
      ];
      const meetingRequests = meetingId
        ? [
            api(`/api/meetings/${meetingId}/recordings`),
            api(`/api/meetings/${meetingId}/transcriptions`),
            api(`/api/jobs?meeting_id=${meetingId}`),
            api(`/api/meetings/${meetingId}/summaries`),
          ]
        : [
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
        recordingData,
        versionData,
        jobs,
        summaryData,
      ] = await Promise.all([...commonRequests, ...meetingRequests]);

      engineCapabilities = capabilities;
      preferences = preferenceData;
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

      if (currentCapture) {
        meetingId = String(currentCapture.meeting_id);
        page.dataset.meetingId = meetingId;
        activeTranscriptionId = Number(currentCapture.transcription_id);
        setLiveState(currentCapture);
        await refreshLiveTranscript(currentCapture, true);
        await refreshWebhookInsights(true);
      } else if (meetingId && !preferred) {
        // Imported or otherwise saved meetings still expose the complete
        // workspace, even when they do not have a transcript version yet.
        document.querySelector("#meeting-tabs").classList.remove("hidden");
      }
      if (capabilities.features.transcription !== "available") {
        document.querySelector("#start-transcription").dataset.engineUnavailable = "true";
      }
      if (meetingId && !currentCapture) await refreshWebhookInsights(true);
    } catch (error) {
      toast(error.message, "error");
      renderLoadError(error.message);
    }
  }

  async function loadMeetingWorkspace() {
    if (!meetingId) return;
    const [recordingData, versionData, jobs, summaryData] = await Promise.all([
      api(`/api/meetings/${meetingId}/recordings`),
      api(`/api/meetings/${meetingId}/transcriptions`),
      api(`/api/jobs?meeting_id=${meetingId}`),
      api(`/api/meetings/${meetingId}/summaries`),
    ]);
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

  function configureAudio() {
    const original = recordings.find((item) => item.role === "original");
    const row = document.querySelector("#audio-row");
    if (!original) {
      row.classList.add("hidden");
      audio.removeAttribute("src");
      return;
    }
    row.classList.remove("hidden");
    document.querySelector("#audio-filename").textContent =
      original.original_filename || "Original recording";
    audio.src = `/api/recordings/${original.id}/media`;
  }

  async function refreshSources() {
    const list = document.querySelector("#native-source-list");
    list.innerHTML = '<div class="source-loading"><i class="mini-spinner"></i> Scanning audio devices…</div>';
    try {
      const data = await api("/api/audio/sources");
      captureCapability = data.capability || {};
      audioSources = data.sources || [];
      renderSources();
    } catch (error) {
      list.innerHTML = `<div class="source-empty">${escapeHTML(error.message)}</div>`;
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
    const mode = selectedSourceMode();
    const nativePanel = document.querySelector("#native-source-panel");
    const filePanel = document.querySelector("#file-source-panel");
    nativePanel.classList.toggle("hidden", mode === "file");
    filePanel.classList.toggle("hidden", mode !== "file");
    const submitCopy = document.querySelector("#transcription-submit span");
    submitCopy.textContent = mode === "file"
      ? "Import and transcribe"
      : "Start live transcription";
    if (mode === "file") return;

    const candidates = audioSources.filter((source) =>
      mode === "system"
        ? source.kind === "system"
        : source.kind === "microphone" || source.kind === "interface");
    document.querySelector("#source-picker-title").textContent =
      mode === "system" ? "System audio sources" : "Available microphones and inputs";
    const list = document.querySelector("#native-source-list");
    if (!candidates.length) {
      list.innerHTML = `
        <div class="source-empty">
          ${mode === "system"
            ? "No system-audio source is currently available."
            : "No microphone or audio input was found."}
        </div>`;
    } else {
      list.innerHTML = candidates.map((source, index) => `
        <label class="native-source-option">
          <input type="radio" name="native-source" value="${escapeHTML(source.id)}"
            ${source.is_default || (!candidates.some((item) => item.is_default) && index === 0) ? "checked" : ""}>
          <span>
            <strong>${escapeHTML(source.name)}</strong>
            <small>${escapeHTML(source.host_api)} · ${source.channels} ch · ${source.sample_rate / 1000} kHz</small>
          </span>
          <span class="source-level-preview" data-source-meter="${escapeHTML(source.id)}" aria-label="Live input level">
            <i></i>
          </span>
          ${source.is_default ? '<span class="source-default-badge">Default</span>' : ""}
        </label>`).join("");
    }
    const guidance = document.querySelector("#source-guidance");
    const note = mode === "system" ? captureCapability.system_audio_note : null;
    guidance.textContent = note || "";
    guidance.classList.toggle("hidden", !note);
    scheduleSourcePreview();
  }

  function selectedSourceMode() {
    return document.querySelector('input[name="source-mode"]:checked')?.value || "microphone";
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
    const selected = document.querySelector('input[name="native-source"]:checked');
    if (!selected) return;
    const meter = document.querySelector(
      `[data-source-meter="${CSS.escape(selected.value)}"]`,
    );
    document.querySelectorAll(".source-level-preview").forEach((item) =>
      item.classList.toggle("active", item === meter));
    sourcePreviewBusy = true;
    try {
      const result = await api(
        `/api/audio/sources/${encodeURIComponent(selected.value)}/level`,
      );
      if (meter) {
        meter.classList.remove("unavailable");
        meter.querySelector("i").style.width =
          `${Math.max(2, Math.round(Number(result.level || 0) * 100))}%`;
      }
    } catch {
      meter?.classList.add("unavailable");
    } finally {
      sourcePreviewBusy = false;
      scheduleSourcePreview(180);
    }
  }

  async function selectTranscription(transcriptionId) {
    activeTranscriptionId = Number(transcriptionId);
    try {
      const detail = await api(`/api/transcriptions/${activeTranscriptionId}`);
      renderTranscript(detail);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderTranscript(detail) {
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
      `${transcription.model} · ${transcription.language || "detecting language"} · ${segments.length} shown / ${detail.segments.length} segments${captureSession ? " · Live" : ""}`;
    if (!detail.segments.length) {
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
      segmentContainer.innerHTML = `
        <div class="result-empty">
          <strong>No transcript segments for this speaker</strong>
          <span>Choose All speakers or another participant.</span>
        </div>`;
      return;
    }
    segmentContainer.innerHTML = segments.map((segment) => {
      const rawSpeaker = Number(segment.speaker_id);
      const hasSpeaker = segment.speaker_id !== null && Number.isFinite(rawSpeaker);
      const speakerNumber = hasSpeaker ? speakerNumbers.get(rawSpeaker) : null;
      const speakerColor = hasSpeaker ? Math.abs(speakerNumber - 1) % 6 : null;
      const provisional = !segment.is_final;
      return `
        <article class="segment-row ${hasSpeaker ? `speaker-color-${speakerColor}` : "speaker-pending"} ${provisional ? "live-segment" : ""}" data-segment-id="${segment.id}">
          <div class="segment-cue">
            ${hasSpeaker ? `<span class="segment-speaker"><i></i>${escapeHTML(speakerNames.get(rawSpeaker) || t("speaker", { number: speakerNumber }))}</span>` : ""}
            ${provisional ? '<span class="live-segment-badge"><i></i> Live</span>' : ""}
            <button class="timestamp-button" data-seek-ms="${segment.start_ms}" title="Play from this timestamp">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>
              ${formatTimestamp(segment.start_ms)}
            </button>
          </div>
          <textarea class="segment-editor" rows="2" aria-label="Transcript segment ${segment.segment_index + 1}" ${provisional ? "readonly" : ""}>${escapeHTML(segment.text)}</textarea>
          <button class="segment-save ${provisional ? "hidden" : ""}" data-save-segment="${segment.id}">Save</button>
        </article>`;
    }).join("");
    applySearch();
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
    renderSpeakerPanel(detail);
    renderSummaryPanel();
  }

  function renderSpeakerPanel(detail = lastDetail || {}) {
    const container = document.querySelector("#speaker-results");
    const status = document.querySelector("#speaker-result-status");
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
    const colors = ["#176bff", "#7a5af8", "#079455", "#e04f16", "#c11574", "#0891b2"];
    const totalTalkTime = speakers.reduce((total, speaker) => total + speaker.talk_time_ms, 0);
    const cards = speakers.map((speaker, index) => {
      const share = totalTalkTime > 0
        ? Math.round((speaker.talk_time_ms / totalTalkTime) * 100)
        : 0;
      return `
        <article class="speaker-card" style="--speaker-color:${colors[index % colors.length]}">
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
            <button type="button" class="text-button speaker-card-action" data-play-speaker="${speaker.id}" title="Play all fragments from this speaker">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg> Play all
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
            <a class="text-button" href="/api/transcriptions/${activeTranscriptionId}/speakers/${speaker.id}/audio?format=wav">Export WAV</a>
            <a class="text-button" href="/api/transcriptions/${activeTranscriptionId}/speakers/${speaker.id}/audio?format=mp3">Export MP3</a>
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
        <article class="speaker-line" style="--speaker-color:${colors[colorIndex % colors.length]}">
          <button class="speaker-fragment-play" data-play-range-start="${turn.start_ms}" data-play-range-end="${turn.end_ms}" title="Play this audio fragment">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>
          </button>
          <time>${formatTimestamp(turn.start_ms)}–${formatTimestamp(turn.end_ms)}<small>${formatTimestamp(turn.end_ms - turn.start_ms)}</small></time>
          <div><strong>${escapeHTML(speaker?.display_name || "Unidentified")}</strong><p>${escapeHTML(transcriptText || "Diarized audio turn")}</p></div>
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

  async function playAllSpeakerFragments(speakerId) {
    if (!audio.getAttribute("src")) {
      toast("The meeting audio is not available.", "error");
      return;
    }
    speakerPlaybackRanges = (lastDetail?.speaker_turns || [])
      .filter((turn) => Number(turn.speaker_id) === Number(speakerId))
      .sort((left, right) => left.start_ms - right.start_ms);
    if (!speakerPlaybackRanges.length) {
      toast("This speaker has no audio fragments.", "error");
      return;
    }
    speakerPlaybackIndex = 0;
    const range = speakerPlaybackRanges[0];
    audio.currentTime = range.start_ms / 1000;
    audioStopAtSeconds = range.end_ms / 1000;
    await audio.play().catch(() => toast("The speaker audio could not be played.", "error"));
  }

  function renderSummaryPanel() {
    const container = document.querySelector("#ai-report-content");
    const status = document.querySelector("#ai-result-status");
    const summary = meetingSummaries.find(
      (item) => Number(item.transcription_id) === Number(activeTranscriptionId),
    ) || meetingSummaries[0];
    const configured = preferences.summary_engine || {};
    const engineName = configured.provider === "local"
      ? (engineCapabilities.summaries?.display_name || configured.model_file || "Local AI")
      : (configured.model || configured.provider || "Selected AI engine");
    document.querySelector("#ai-result-engine").textContent = `Generated by ${engineName}.`;
    if (!summary) {
      status.textContent = "Not processed";
      status.classList.remove("ready");
      container.innerHTML = `
        <div class="result-empty">
          <strong>No AI report yet</strong>
          <span>The summary, decisions and action items will appear here.</span>
        </div>`;
      return;
    }
    status.textContent = summary.status === "completed" ? "Ready" : summary.status;
    status.classList.toggle("ready", summary.status === "completed");
    container.innerHTML = summary.content_markdown
      ? escapeHTML(summary.content_markdown).replace(/\n/g, "<br>")
      : `<div class="result-empty"><strong>AI analysis is ${escapeHTML(summary.status)}</strong><span>The report will appear automatically.</span></div>`;
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
    workflowDismissed = false;
    workflowVisible = true;
    workflowCompleted = false;
    pendingPostprocessKind = null;
    document.querySelector("#postprocess-options").classList.add("hidden");
    document.querySelector("#postprocess-progress-view").classList.remove("hidden");
    resetPostprocessLog("Final processing started");
    document.querySelector("#postprocess-title").textContent =
      "Turning the conversation into useful notes";
    document.querySelector("#postprocess-description").textContent = description;
    document.querySelector("#postprocess-background").classList.remove("hidden");
    document.querySelector("#postprocess-results").classList.add("hidden");
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

  function selectedPostprocessOptions() {
    const diarization = document.querySelector("#postprocess-diarization").checked;
    const known = document.querySelector('input[name="postprocess-speaker-mode"]:checked')?.value === "known";
    const requestedCount = Number(document.querySelector("#postprocess-speaker-count").value);
    return {
      diarization,
      speaker_count: diarization && known && Number.isInteger(requestedCount)
        && requestedCount >= 1 && requestedCount <= 20
        ? requestedCount
        : null,
      summary: document.querySelector("#postprocess-summary").checked,
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
    document.querySelector('input[name="postprocess-speaker-mode"][value="auto"]').checked = true;
    document.querySelector("#postprocess-speaker-count").value = "2";
    document.querySelector("#postprocess-options").classList.remove("hidden");
    document.querySelector("#postprocess-progress-view").classList.add("hidden");
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
    const snapshot = `${job.status}:${progressPercent}:${job.message || ""}:${job.error_text || ""}`;
    if (postprocessJobSnapshots.get(job.uuid) !== snapshot) {
      postprocessJobSnapshots.set(job.uuid, snapshot);
      const time = new Date().toLocaleTimeString([], { hour12: false });
      const detail = job.error_text || job.message || job.status;
      appendPostprocessLog(
        `[${time}] ${job.status === "failed" ? "ERROR  " : "INFO   "} ${name} · ${progressPercent}% · ${detail}`,
      );
    }
    if (["queued", "running", "paused"].includes(job.status)) {
      const progress = Number(job.progress || 0);
      const state = job.status === "queued"
        ? "Queued"
        : `${Math.max(1, Math.round(progress * 100))}%`;
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
    const time = new Date().toLocaleTimeString([], { hour12: false });
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
    document.querySelector("#postprocess-progress").value = overall;
    document.querySelector("#postprocess-percent").textContent = `${overall}%`;

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
    if (mode !== "file" && !document.querySelector('input[name="native-source"]:checked')) {
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
      toast(error.message, "error");
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
    const selected = document.querySelector('input[name="native-source"]:checked');
    if (!selected) throw new Error("Choose an available audio source.");
    const session = await api("/api/capture/sessions", {
      method: "POST",
      body: JSON.stringify({
        source_id: selected.value,
        ...transcriptionOptions(),
      }),
    });
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
    strip.classList.toggle("transcription-error", session.realtime_status === "error");
    document.querySelector("#live-source-name").textContent = session.source.name;
    document.querySelector("#live-capture-state").textContent =
      paused ? "Capture paused" : session.realtime_message || "Transcribing locally";
    document.querySelector("#capture-elapsed").textContent = formatTimestamp(session.elapsed_ms);
    document.querySelector("#capture-level").style.width = `${Math.round((session.level || 0) * 100)}%`;
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
        <div><p>${escapeHTML(item.text)}</p><small>${escapeHTML(item.endpoint_name)} · ${escapeHTML(item.kind)}${item.confidence == null ? "" : ` · ${Math.round(Number(item.confidence) * 100)}% confidence`}</small></div>
        <div class="live-agent-insight-actions">${item.status === "new" ? `<button class="text-button" type="button" data-insight-status="accepted" data-insight-id="${escapeHTML(item.id)}">Accept</button>` : ""}<button class="text-button" type="button" data-insight-status="dismissed" data-insight-id="${escapeHTML(item.id)}">Dismiss</button></div>
      </article>`).join("");
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
      window.history.replaceState({}, "", `/?meeting=${meetingId}`);
      openPostprocessDialog({
        initialLabel: finalTranscription ? "Preparing final pass…" : "Saving live transcript…",
        description: "Meet2Notes is processing the selected local steps.",
      });
      await loadMeetingWorkspace();
    } catch (error) {
      if (postprocessDialog.open) postprocessDialog.close();
      workflowVisible = false;
      toast(error.message, "error");
    } finally {
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
    document.querySelector("#transcription-progress-value").textContent = `${value}%`;
    document.querySelector("#transcription-progress-bar").value = value;
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
    input.addEventListener("change", renderSourceMode));
  document.querySelector("#native-source-list").addEventListener("change", (event) => {
    if (event.target.matches('input[name="native-source"]')) scheduleSourcePreview(20);
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
  document.querySelector("#postprocess-diarization").addEventListener("change", syncPostprocessSpeakerControls);
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
    button.addEventListener("click", () => setActiveMeetingTab(button.dataset.meetingTab)));
  document.querySelector("#copy-transcript").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(transcriptAsText());
      toast("Transcript copied to the clipboard.");
    } catch {
      toast("The transcript could not be copied.", "error");
    }
  });
  document.querySelector("#download-transcript").addEventListener("click", () => {
    downloadFile(exportFilename("txt"), transcriptAsText(), "text/plain;charset=utf-8");
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
      await playAllSpeakerFragments(playSpeaker.dataset.playSpeaker);
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
      speakerPlaybackRanges = [];
      speakerPlaybackIndex = -1;
      audio.currentTime = Number(range.dataset.playRangeStart) / 1000;
      audioStopAtSeconds = Number(range.dataset.playRangeEnd) / 1000;
      await audio.play().catch(() => toast("The audio fragment could not be played.", "error"));
      return;
    }
    const seek = event.target.closest("[data-seek-ms]");
    if (seek) {
      speakerPlaybackRanges = [];
      speakerPlaybackIndex = -1;
      audioStopAtSeconds = null;
      audio.currentTime = Number(seek.dataset.seekMs) / 1000;
      await audio.play().catch(() => {});
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
    audio.pause();
    if (speakerPlaybackIndex >= 0 && speakerPlaybackIndex + 1 < speakerPlaybackRanges.length) {
      speakerPlaybackIndex += 1;
      const next = speakerPlaybackRanges[speakerPlaybackIndex];
      audio.currentTime = next.start_ms / 1000;
      audioStopAtSeconds = next.end_ms / 1000;
      audio.play().catch(() => {
        speakerPlaybackRanges = [];
        speakerPlaybackIndex = -1;
        audioStopAtSeconds = null;
        toast("Speaker playback could not continue.", "error");
      });
      return;
    }
    speakerPlaybackRanges = [];
    speakerPlaybackIndex = -1;
    audioStopAtSeconds = null;
  });

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

  subscribeJobs(async (jobs) => {
    if (!meetingId) return;
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
    const newlyTerminal = terminal.find((job) => !terminalJobIds.has(job.uuid));
    terminal.forEach((job) => terminalJobIds.add(job.uuid));
    if (!newlyTerminal) return;
    if (["transcribe", "diarize"].includes(newlyTerminal.job_type)) {
      versions = await api(`/api/meetings/${meetingId}/transcriptions`);
      const preferred = versions.find((item) => item.is_active) || versions[0];
      if (preferred) await selectTranscription(preferred.id);
    }
    if (newlyTerminal.job_type === "summarize" &&
        newlyTerminal.payload?.summary_scope !== "speaker") {
      meetingSummaries = await api(`/api/meetings/${meetingId}/summaries`);
      renderSummaryPanel();
    }
    if (newlyTerminal.status === "completed" && newlyTerminal.job_type === "transcribe" &&
        !newlyTerminal.payload?.postprocess) {
      toast("Transcript completed locally.");
    }
    if (newlyTerminal.status === "failed") {
      toast(newlyTerminal.error_text || `${newlyTerminal.job_type} failed.`, "error");
    }
  });

  async function initializeWorkspace() {
    bindActivityLog();
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
