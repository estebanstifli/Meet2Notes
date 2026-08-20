(() => {
  const api = (...args) => window.Meet2Notes.api(...args);
  const list = document.querySelector("#speaker-profile-list"), options = document.querySelector("#speaker-filter-options"), results = document.querySelector("#speaker-meeting-results"), dialog = document.querySelector("#speaker-profile-dialog");
  let profiles = [];
  const voiceAudio = new Audio();
  let playingProfileId = null;
  const escape = (v) => String(v).replace(/[&<>'"]/g, (x) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" }[x]));
  const icon = (name) => ({
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>',
    stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="6" width="10" height="12" rx="1"/></svg>',
    rename: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16-.5 4.5L8 20l11-11-4-4L4 16Z"/><path d="m13 7 4 4"/></svg>',
    delete: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/></svg>',
  })[name];
  async function load() { profiles = await api("/api/speaker-profiles"); render(); }
  function render() {
    list.innerHTML = profiles.length ? profiles.map(p => `<article class="saved-voice"><span class="saved-voice-mark">${escape(p.name[0]?.toUpperCase() || "V")}</span><div><strong>${escape(p.name)}</strong><small>${p.meeting_count} meeting${p.meeting_count === 1 ? "" : "s"} recognized</small></div><div class="saved-voice-actions"><button class="text-button" data-play-profile="${p.id}" aria-pressed="${p.id === playingProfileId}" title="${p.id === playingProfileId ? "Stop" : "Play saved voice"}">${icon(p.id === playingProfileId ? "stop" : "play")} ${p.id === playingProfileId ? "Stop" : "Play"}</button><button class="text-button" data-rename="${p.id}">${icon("rename")} Rename</button><button class="text-button danger" data-delete="${p.id}">${icon("delete")} Delete</button></div></article>`).join("") : `<div class="result-empty"><strong>No saved voices yet</strong><span>Remember a speaker from a meeting or add a voice sample.</span></div>`;
    options.innerHTML = profiles.length ? profiles.map(p => `<label><input type="checkbox" value="${p.id}"><span>${escape(p.name)}</span></label>`).join("") : `<span class="muted">Save a voice first to filter meetings.</span>`;
    results.innerHTML = `<div class="result-empty"><strong>Select speakers</strong><span>Choose one or more people above to search their meetings.</span></div>`;
  }
  document.querySelectorAll("[data-speaker-tab]").forEach(button => button.addEventListener("click", () => { document.querySelectorAll("[data-speaker-tab]").forEach(x => x.classList.toggle("active", x === button)); document.querySelectorAll("[data-speaker-panel]").forEach(x => x.hidden = x.dataset.speakerPanel !== button.dataset.speakerTab); }));
  document.querySelector("#add-speaker-profile").onclick = () => dialog.showModal();
  document.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  document.querySelector("#speaker-profile-form").onsubmit = async (event) => { event.preventDefault(); const form = new FormData(event.target); try { await api("/api/speaker-profiles", { method: "POST", body: form }); dialog.close(); event.target.reset(); await load(); } catch (e) { alert(e.message); } };
  function syncPlaybackControls() { list.querySelectorAll("[data-play-profile]").forEach(button => { const active = Number(button.dataset.playProfile) === playingProfileId; button.setAttribute("aria-pressed", String(active)); button.title = active ? "Stop" : "Play saved voice"; button.innerHTML = `${icon(active ? "stop" : "play")} ${active ? "Stop" : "Play"}`; }); }
  function stopVoice() { voiceAudio.pause(); voiceAudio.currentTime = 0; playingProfileId = null; syncPlaybackControls(); }
  async function toggleVoice(profileId) { if (playingProfileId === profileId && !voiceAudio.paused) { stopVoice(); return; } voiceAudio.pause(); voiceAudio.src = `/api/speaker-profiles/${profileId}/audio`; playingProfileId = profileId; try { await voiceAudio.play(); syncPlaybackControls(); } catch { playingProfileId = null; syncPlaybackControls(); alert("The saved voice could not be played."); } }
  voiceAudio.addEventListener("ended", () => { playingProfileId = null; syncPlaybackControls(); });
  voiceAudio.addEventListener("error", () => { if (playingProfileId !== null) { playingProfileId = null; syncPlaybackControls(); } });
  document.addEventListener("click", async (event) => { const play = event.target.closest("[data-play-profile]"), rename = event.target.closest("[data-rename]"), del = event.target.closest("[data-delete]"); if (play) { await toggleVoice(Number(play.dataset.playProfile)); return; } if (rename) { const p = profiles.find(x => x.id === +rename.dataset.rename), name = prompt("Speaker name", p?.name); if (name?.trim()) { try { await api(`/api/speaker-profiles/${p.id}`, { method:"PATCH", body: JSON.stringify({ name }) }); await load(); } catch (e) { alert(e.message); } } return; } if (del && confirm("Delete this saved voice? Existing meetings are kept.")) { if (playingProfileId === Number(del.dataset.delete)) stopVoice(); await api(`/api/speaker-profiles/${del.dataset.delete}`, { method:"DELETE" }); await load(); } });
  options.addEventListener("change", async () => { const ids = [...options.querySelectorAll("input:checked")].map(x => x.value); if (!ids.length) return render(); const meetings = await api(`/api/speaker-profiles/meetings?${ids.map(id => `profile_ids=${id}`).join("&")}`); results.innerHTML = meetings.length ? meetings.map(m => `<a class="speaker-meeting-row" href="/?meeting=${m.id}"><strong>${escape(m.title)}</strong><span>${new Date(m.started_at || m.created_at).toLocaleString()}</span></a>`).join("") : `<div class="result-empty"><strong>No matching meetings</strong><span>No saved meeting contains every selected speaker yet.</span></div>`; });
  load().catch(e => { list.textContent = e.message; });
})();
