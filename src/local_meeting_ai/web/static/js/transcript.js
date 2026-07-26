(() => {
  "use strict";

  const {
    api,
    escapeHTML,
    formatBytes,
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

  let meetingId = page.dataset.meetingId || null;
  let draftTitle = page.dataset.defaultTitle || "New Transcription";
  let audioSources = [];
  let captureCapability = {};
  let recordings = [];
  let versions = [];
  let activeTranscriptionId = null;
  let activeJob = null;
  let captureSession = null;
  let capturePollTimer = null;
  let capturePollBusy = false;
  let lastLiveSegmentCount = -1;
  let lastDetail = null;
  let terminalJobIds = new Set();
  let sourcePreviewTimer = null;
  let sourcePreviewBusy = false;

  async function loadWorkspace() {
    try {
      const commonRequests = [
        api("/api/capabilities"),
        api("/api/audio/sources"),
        api("/api/capture/session"),
      ];
      const meetingRequests = meetingId
        ? [
            api(`/api/meetings/${meetingId}/recordings`),
            api(`/api/meetings/${meetingId}/transcriptions`),
            api(`/api/jobs?meeting_id=${meetingId}`),
          ]
        : [Promise.resolve([]), Promise.resolve([]), Promise.resolve([])];
      const [
        capabilities,
        sourceData,
        currentCapture,
        recordingData,
        versionData,
        jobs,
      ] = await Promise.all([...commonRequests, ...meetingRequests]);

      captureCapability = sourceData.capability || {};
      audioSources = sourceData.sources || [];
      recordings = recordingData;
      versions = versionData;
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
      }
      if (capabilities.features.transcription !== "available") {
        document.querySelector("#start-transcription").dataset.engineUnavailable = "true";
      }
    } catch (error) {
      toast(error.message, "error");
      renderLoadError(error.message);
    }
  }

  async function loadMeetingWorkspace() {
    if (!meetingId) return;
    const [recordingData, versionData, jobs] = await Promise.all([
      api(`/api/meetings/${meetingId}/recordings`),
      api(`/api/meetings/${meetingId}/transcriptions`),
      api(`/api/jobs?meeting_id=${meetingId}`),
    ]);
    recordings = recordingData;
    versions = versionData;
    configureAudio();
    activeJob = jobs.find((job) =>
      job.job_type === "transcribe" &&
      ["queued", "running", "paused"].includes(job.status)) || null;
    renderProgress(activeJob);
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
    const segments = detail.segments;
    setTitle(transcription.title);
    document.querySelector("#editor-meta").textContent =
      `${transcription.model} · ${transcription.language || "detecting language"} · ${segments.length} segments${captureSession ? " · Live" : ""}`;
    if (!segments.length) {
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
    segmentContainer.innerHTML = segments.map((segment) => {
      const rawSpeaker = Number(segment.speaker_id);
      const speakerNumber = segment.speaker_id === null || !Number.isFinite(rawSpeaker)
        ? 1
        : Math.max(1, rawSpeaker);
      const speakerColor = Math.abs(speakerNumber - 1) % 6;
      const provisional = !segment.is_final;
      return `
        <article class="segment-row speaker-color-${speakerColor} ${provisional ? "live-segment" : ""}" data-segment-id="${segment.id}">
          <div class="segment-cue">
            <span class="segment-speaker"><i></i>${escapeHTML(t("speaker", { number: speakerNumber }))}</span>
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
          <button type="button" class="button primary" data-empty-start>Start transcription</button>
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
    stopSourcePreview();
    submit.disabled = true;
    document.querySelector("#transcription-form").dataset.busy = "true";
    try {
      if (mode === "file") {
        await startFileTranscription();
      } else {
        await startNativeCapture();
      }
    } catch (error) {
      toast(error.message, "error");
      submit.disabled = false;
      delete document.querySelector("#transcription-form").dataset.busy;
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
    startDialog.close();
    delete document.querySelector("#transcription-form").dataset.busy;
    document.querySelector("#transcription-submit").disabled = false;
    window.history.replaceState({}, "", `/?meeting=${meetingId}&live=${session.session_id}`);
    setLiveState(session);
    await selectTranscription(activeTranscriptionId);
    toast("Real-time local transcription started.");
  }

  async function startFileTranscription() {
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
      body: JSON.stringify(transcriptionOptions()),
    });
    startDialog.close();
    toast("Media imported. Local transcription has started.");
    window.location.href = `/?meeting=${meeting.id}&transcription=${response.transcription.id}`;
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
    document.querySelector("#start-transcription").classList.add("hidden");
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
    document.querySelector("#start-transcription").classList.remove("hidden");
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
    const stop = document.querySelector("#stop-capture");
    const pause = document.querySelector("#pause-capture");
    stop.disabled = true;
    pause.disabled = true;
    try {
      const response = await api(
        `/api/capture/sessions/${captureSession.session_id}/stop`,
        { method: "POST" },
      );
      meetingId = String(response.session.meeting_id);
      page.dataset.meetingId = meetingId;
      activeTranscriptionId = response.transcription.id;
      activeJob = response.transcription_job;
      setTitle(response.transcription.title);
      clearLiveState();
      window.history.replaceState({}, "", `/?meeting=${meetingId}`);
      await loadMeetingWorkspace();
      toast("Capture stopped. Refining the complete transcript locally.");
    } catch (error) {
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

  document.addEventListener("click", async (event) => {
    if (event.target.closest("[data-empty-start]")) {
      openStartDialog();
      return;
    }
    const seek = event.target.closest("[data-seek-ms]");
    if (seek) {
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

  segmentContainer.addEventListener("input", (event) => {
    if (!event.target.matches(".segment-editor")) return;
    event.target.closest(".segment-row").classList.add("dirty");
    setSaveState(true, "Unsaved changes");
    applySearch();
  });

  document.addEventListener("localmeet:languagechange", () => {
    if (lastDetail) renderTranscript(lastDetail);
  });

  subscribeJobs(async (jobs) => {
    if (!meetingId) return;
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
      job.job_type === "transcribe" &&
      ["completed", "failed", "cancelled"].includes(job.status));
    const newlyTerminal = terminal.find((job) => !terminalJobIds.has(job.uuid));
    terminal.forEach((job) => terminalJobIds.add(job.uuid));
    if (!newlyTerminal) return;
    versions = await api(`/api/meetings/${meetingId}/transcriptions`);
    const preferred = versions.find((item) => item.is_active) || versions[0];
    if (preferred) await selectTranscription(preferred.id);
    if (newlyTerminal.status === "completed") toast("Transcript completed locally.");
    if (newlyTerminal.status === "failed") {
      toast(newlyTerminal.error_text || "Transcription failed.", "error");
    }
  });

  loadWorkspace();
})();
