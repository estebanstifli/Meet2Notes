(() => {
  "use strict";

  const {
    api,
    escapeHTML,
    formatBytes,
    formatDate,
    formatDuration,
    renderJobCard,
    subscribeJobs,
    toast,
  } = window.Meet2Notes;

  const page = document.querySelector(".meeting-page");
  const meetingId = page.dataset.meetingId;
  let meeting = null;

  async function loadMeeting() {
    try {
      const [meetingData, recordings, jobs] = await Promise.all([
        api(`/api/meetings/${meetingId}`),
        api(`/api/meetings/${meetingId}/recordings`),
        api(`/api/jobs?meeting_id=${meetingId}`),
      ]);
      meeting = meetingData;
      renderMeeting(meeting);
      renderRecordings(recordings);
      renderJobs(jobs);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderMeeting(item) {
    document.querySelector("#meeting-title-display").textContent = item.title;
    document.querySelector("#meeting-description-display").textContent = item.description || "No description yet.";
    document.querySelector("#meeting-date").textContent = formatDate(item.created_at);
    document.querySelector("#detail-created").textContent = formatDate(item.created_at);
    document.querySelector("#detail-duration").textContent = formatDuration(item.duration_ms);
    document.querySelector("#detail-status").textContent = item.status;
    const badge = document.querySelector(".meeting-kicker .status-badge");
    badge.className = `status-badge status-${item.status}`;
    badge.textContent = item.status;
    document.title = `${item.title} · Meet2Notes`;
  }

  function renderRecordings(items) {
    const container = document.querySelector("#recording-list");
    if (!items.length) {
      container.innerHTML = `
        <div class="recording-empty">
          <span class="recording-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0L7 9m5-5 5 5M5 14v5h14v-5"/></svg>
          </span>
          <div><strong>No recording attached</strong><p>Add audio or video when you are ready.</p></div>
          <button class="button secondary" data-recording-import>Add media</button>
        </div>`;
      container.querySelector("[data-recording-import]")?.addEventListener("click", () =>
        document.querySelector("[data-open-import]")?.click());
      return;
    }
    container.innerHTML = items.map((recording) => {
      const hasVideo = Boolean(recording.metadata?.has_video);
      const stream = (recording.metadata?.streams || []).find((item) => item.codec_type === "audio");
      return `
        <div class="recording-item">
          <span class="recording-icon ${hasVideo ? "video" : ""}">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              ${hasVideo
                ? '<rect x="3" y="5" width="14" height="14" rx="2"/><path d="m17 10 4-2v8l-4-2"/>'
                : '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8"/>'}
            </svg>
          </span>
          <div class="recording-name">
            <strong title="${escapeHTML(recording.original_filename)}">${escapeHTML(recording.original_filename || "Recording")}</strong>
            <span>${hasVideo ? "Video source" : "Audio source"} · original preserved</span>
          </div>
          <div class="recording-stat"><strong>${formatDuration(recording.duration_ms)}</strong><span>Duration</span></div>
          <div class="recording-stat"><strong>${formatBytes(recording.size_bytes)}</strong><span>File size</span></div>
          <div class="recording-stat"><strong>${escapeHTML(stream?.codec_name?.toUpperCase() || "Pending")}</strong><span>Codec</span></div>
        </div>`;
    }).join("");
  }

  function renderJobs(items) {
    document.querySelector("#meeting-job-list").innerHTML =
      items.slice(0, 6).map((job) => renderJobCard(job)).join("");
  }

  const editDialog = document.querySelector("#edit-dialog");
  document.querySelector("#edit-meeting")?.addEventListener("click", () => {
    document.querySelector("#edit-title").value = meeting.title;
    document.querySelector("#edit-description").value = meeting.description || "";
    editDialog.showModal();
  });
  document.querySelectorAll("[data-close-edit]").forEach((button) =>
    button.addEventListener("click", () => editDialog.close()));
  document.querySelector("#edit-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      meeting = await api(`/api/meetings/${meetingId}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: document.querySelector("#edit-title").value.trim(),
          description: document.querySelector("#edit-description").value.trim() || null,
        }),
      });
      renderMeeting(meeting);
      editDialog.close();
      toast("Meeting details saved.");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  const deleteDialog = document.querySelector("#delete-dialog");
  const confirmation = document.querySelector("#delete-confirmation");
  const confirmButton = document.querySelector("#delete-confirm-button");
  document.querySelector("#delete-meeting")?.addEventListener("click", () => {
    confirmation.value = "";
    confirmButton.disabled = true;
    deleteDialog.showModal();
  });
  document.querySelectorAll("[data-close-delete]").forEach((button) =>
    button.addEventListener("click", () => deleteDialog.close()));
  confirmation.addEventListener("input", () => {
    confirmButton.disabled = confirmation.value !== "DELETE";
  });
  document.querySelector("#delete-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (confirmation.value !== "DELETE") return;
    confirmButton.disabled = true;
    try {
      await api(`/api/meetings/${meetingId}`, { method: "DELETE" });
      window.location.href = "/";
    } catch (error) {
      toast(error.message, "error");
      confirmButton.disabled = false;
    }
  });

  subscribeJobs((jobs) => {
    const related = jobs.filter((job) => String(job.meeting_id) === String(meetingId));
    renderJobs(related);
    if (related.some((job) => ["completed", "failed", "cancelled"].includes(job.status))) {
      Promise.all([
        api(`/api/meetings/${meetingId}`),
        api(`/api/meetings/${meetingId}/recordings`),
      ]).then(([meetingData, recordings]) => {
        meeting = meetingData;
        renderMeeting(meeting);
        renderRecordings(recordings);
      }).catch(() => {});
    }
  });

  loadMeeting();
})();
