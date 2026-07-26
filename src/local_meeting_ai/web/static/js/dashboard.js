(() => {
  "use strict";

  const {
    api,
    escapeHTML,
    formatDate,
    formatDuration,
    renderJobCard,
    subscribeJobs,
    toast,
  } = window.Meet2Notes;

  let meetings = [];
  let jobs = [];
  const list = document.querySelector("#meeting-list");
  const jobList = document.querySelector("#job-list");

  async function loadDashboard() {
    try {
      const [meetingData, jobData, capabilities] = await Promise.all([
        api("/api/meetings"),
        api("/api/jobs?limit=50"),
        api("/api/capabilities"),
      ]);
      meetings = meetingData;
      jobs = jobData;
      renderMeetings(meetings);
      renderJobs(jobs);
      renderMetrics();
      renderCapabilities(capabilities);
    } catch (error) {
      list.innerHTML = `<div class="empty-library"><h3>Could not load the workspace</h3><p>${escapeHTML(error.message)}</p></div>`;
      toast(error.message, "error");
    }
  }

  function renderMetrics() {
    document.querySelector("#metric-meetings").textContent = meetings.length;
    document.querySelector("#metric-meetings-note").textContent =
      meetings.length === 1 ? "1 private conversation" : `${meetings.length} private conversations`;
    const totalMilliseconds = meetings.reduce((total, meeting) => total + (meeting.duration_ms || 0), 0);
    document.querySelector("#metric-hours").textContent =
      totalMilliseconds ? (totalMilliseconds / 3600000).toFixed(totalMilliseconds >= 36000000 ? 0 : 1) : "0";
    const active = jobs.filter((job) => ["queued", "running", "paused"].includes(job.status));
    document.querySelector("#metric-jobs").textContent = active.length;
    document.querySelector("#metric-jobs-note").textContent =
      active.length ? "Processing on this computer" : "Queue is clear";
  }

  function renderMeetings(items) {
    if (!items.length) {
      list.innerHTML = `
        <div class="empty-library">
          <span class="empty-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z"/></svg>
          </span>
          <h3>Your meeting library is ready</h3>
          <p>Import a recording or create a meeting to begin organizing conversations locally.</p>
          <button class="button primary" data-open-import-empty>Import your first recording</button>
        </div>`;
      list.querySelector("[data-open-import-empty]")?.addEventListener("click", () => {
        document.querySelector("[data-open-import]")?.click();
      });
      return;
    }
    list.innerHTML = items.map((meeting) => `
      <a class="meeting-row" href="/meetings/${meeting.id}">
        <div class="meeting-name">
          <span class="meeting-thumb">${escapeHTML(meeting.title.slice(0, 1).toUpperCase())}</span>
          <div>
            <strong>${escapeHTML(meeting.title)}</strong>
            <span>${escapeHTML(meeting.description || `${meeting.recording_count} recording${meeting.recording_count === 1 ? "" : "s"}`)}</span>
          </div>
        </div>
        <div class="meeting-meta">
          <strong>${formatDate(meeting.created_at, "short")}</strong>
          <span>Created</span>
        </div>
        <div class="meeting-meta">
          <span class="status-badge status-${meeting.status}">${escapeHTML(meeting.status)}</span>
          <span>${formatDuration(meeting.duration_ms)}</span>
        </div>
        <span class="row-arrow">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
        </span>
      </a>`).join("");
  }

  function renderJobs(items) {
    const recent = items.slice(0, 4);
    if (!recent.length) {
      jobList.innerHTML = `
        <div class="activity-empty">
          <span class="pulse-orbit"><i></i></span>
          <strong>Everything is quiet</strong>
          <p>Imports and future AI tasks will appear here.</p>
        </div>`;
      return;
    }
    jobList.innerHTML = recent.map((job) => renderJobCard(job)).join("");
  }

  function renderCapabilities(data) {
    const element = document.querySelector("#ffmpeg-status");
    const available = data.ffmpeg.available;
    element.classList.add(available ? "available" : "unavailable");
    element.querySelector("strong").textContent = available ? "FFmpeg ready" : "FFmpeg not found";
    element.querySelector(":scope > div > span").textContent = available
      ? "Media inspection available"
      : "Configure it in the environment";

    const transcription = document.querySelector("#transcription-status");
    const engineAvailable = data.features.transcription === "available";
    transcription.classList.add(engineAvailable ? "available" : "unavailable");
    transcription.querySelector("strong").textContent = engineAvailable
      ? "Faster Whisper ready"
      : "Transcription is optional";
    transcription.querySelector(":scope > div > span").textContent = engineAvailable
      ? `${data.transcription.cuda_available ? "CUDA" : "CPU"} processing available`
      : "Install when you need it";
  }

  document.querySelector("#meeting-search")?.addEventListener("input", (event) => {
    const query = event.target.value.trim().toLowerCase();
    const filtered = meetings.filter((meeting) =>
      meeting.title.toLowerCase().includes(query) ||
      (meeting.description || "").toLowerCase().includes(query));
    renderMeetings(filtered);
  });

  const meetingDialog = document.querySelector("#meeting-dialog");
  document.querySelector("#new-meeting-button")?.addEventListener("click", () => meetingDialog.showModal());
  document.querySelectorAll("[data-close-meeting]").forEach((button) =>
    button.addEventListener("click", () => meetingDialog.close()));
  document.querySelector("#meeting-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const meeting = await api("/api/meetings", {
        method: "POST",
        body: JSON.stringify({
          title: document.querySelector("#meeting-title").value.trim(),
          description: document.querySelector("#meeting-description").value.trim() || null,
        }),
      });
      window.location.href = `/meetings/${meeting.id}`;
    } catch (error) {
      toast(error.message, "error");
      submit.disabled = false;
    }
  });

  subscribeJobs((updatedJobs) => {
    jobs = updatedJobs;
    renderJobs(jobs);
    renderMetrics();
    if (jobs.some((job) => job.status === "completed")) {
      api("/api/meetings").then((data) => {
        meetings = data;
        renderMeetings(meetings);
        renderMetrics();
      }).catch(() => {});
    }
  });

  loadDashboard();
})();
