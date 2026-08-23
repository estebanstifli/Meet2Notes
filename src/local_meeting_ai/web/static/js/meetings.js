(() => {
  "use strict";

  const { api, toast } = window.Meet2Notes;
  const rows = [...document.querySelectorAll(".meeting-library-row")];
  const search = document.querySelector("#meeting-search");
  const empty = document.querySelector("#meetings-search-empty");
  const count = document.querySelector("#visible-meeting-count");
  const countLabel = document.querySelector("#visible-meeting-label");

  document.querySelectorAll("[data-local-date]").forEach((element) => {
    const date = new Date(element.dataset.localDate);
    if (!Number.isNaN(date.getTime())) {
      element.textContent = date.toLocaleDateString(Meet2Notes.currentLanguage, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    }
  });

  document.querySelectorAll("[data-local-time]").forEach((element) => {
    const date = new Date(element.dataset.localTime);
    if (!Number.isNaN(date.getTime())) {
      element.textContent = date.toLocaleTimeString(Meet2Notes.currentLanguage, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
  });

  document.querySelectorAll("[data-duration-ms]").forEach((element) => {
    const totalSeconds = Math.max(0, Math.round(Number(element.dataset.durationMs) / 1000));
    if (!totalSeconds) return;
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    element.textContent = hours
      ? `${hours}h ${String(minutes).padStart(2, "0")}m`
      : `${minutes}:${String(seconds).padStart(2, "0")}`;
  });

  function applySearch() {
    const query = search.value.trim().toLocaleLowerCase(Meet2Notes.currentLanguage);
    let visible = 0;
    rows.forEach((row) => {
      const match = !query || row.dataset.meetingSearch.includes(query);
      row.classList.toggle("hidden", !match);
      if (match) visible += 1;
    });
    count.textContent = String(visible);
    countLabel.textContent = Meet2Notes.t("meetings.saved_count", { count: visible }, visible);
    empty.classList.toggle("hidden", visible > 0 || rows.length === 0);
  }

  search?.addEventListener("input", applySearch);
  document.addEventListener("localmeet:languagechange", applySearch);
  applySearch();
  document.querySelectorAll("[data-delete-meeting-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const title = button.dataset.deleteMeetingTitle || "this meeting";
      const confirmed = window.confirm(
        `Delete “${title}” permanently? This removes its audio, transcript, speakers, AI notes, RAG index data and all files. This cannot be undone.`,
      );
      if (!confirmed) return;
      button.disabled = true;
      try {
        await api(`/api/meetings/${encodeURIComponent(button.dataset.deleteMeetingId)}`, {
          method: "DELETE",
        });
        const row = button.closest(".meeting-library-row");
        const index = rows.indexOf(row);
        if (index >= 0) rows.splice(index, 1);
        row?.remove();
        if (!rows.length) {
          window.location.reload();
          return;
        }
        applySearch();
        toast("Meeting and all of its local data were deleted.");
      } catch (error) {
        button.disabled = false;
        toast(error.message, "error");
      }
    });
  });
})();
