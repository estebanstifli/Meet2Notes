(() => {
  const api = (...args) => window.Meet2Notes.api(...args);
  const list = document.querySelector("#speaker-profile-list"), options = document.querySelector("#speaker-filter-options"), results = document.querySelector("#speaker-meeting-results"), dialog = document.querySelector("#speaker-profile-dialog");
  let profiles = [];
  const escape = (v) => String(v).replace(/[&<>'"]/g, (x) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" }[x]));
  async function load() { profiles = await api("/api/speaker-profiles"); render(); }
  function render() {
    list.innerHTML = profiles.length ? profiles.map(p => `<article class="saved-voice"><span class="saved-voice-mark">${escape(p.name[0]?.toUpperCase() || "V")}</span><div><strong>${escape(p.name)}</strong><small>${p.meeting_count} meeting${p.meeting_count === 1 ? "" : "s"} recognized</small></div><button class="text-button" data-rename="${p.id}">Rename</button><button class="text-button danger" data-delete="${p.id}">Delete</button></article>`).join("") : `<div class="result-empty"><strong>No saved voices yet</strong><span>Remember a speaker from a meeting or add a voice sample.</span></div>`;
    options.innerHTML = profiles.length ? profiles.map(p => `<label><input type="checkbox" value="${p.id}"><span>${escape(p.name)}</span></label>`).join("") : `<span class="muted">Save a voice first to filter meetings.</span>`;
    results.innerHTML = `<div class="result-empty"><strong>Select speakers</strong><span>Choose one or more people above to search their meetings.</span></div>`;
  }
  document.querySelectorAll("[data-speaker-tab]").forEach(button => button.addEventListener("click", () => { document.querySelectorAll("[data-speaker-tab]").forEach(x => x.classList.toggle("active", x === button)); document.querySelectorAll("[data-speaker-panel]").forEach(x => x.hidden = x.dataset.speakerPanel !== button.dataset.speakerTab); }));
  document.querySelector("#add-speaker-profile").onclick = () => dialog.showModal();
  document.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  document.querySelector("#speaker-profile-form").onsubmit = async (event) => { event.preventDefault(); const form = new FormData(event.target); try { await api("/api/speaker-profiles", { method: "POST", body: form }); dialog.close(); event.target.reset(); await load(); } catch (e) { alert(e.message); } };
  document.addEventListener("click", async (event) => { const rename = event.target.closest("[data-rename]"), del = event.target.closest("[data-delete]"); if (rename) { const p = profiles.find(x => x.id === +rename.dataset.rename), name = prompt("Speaker name", p?.name); if (name?.trim()) { try { await api(`/api/speaker-profiles/${p.id}`, { method:"PATCH", body: JSON.stringify({ name }) }); await load(); } catch (e) { alert(e.message); } } } if (del && confirm("Delete this saved voice? Existing meetings are kept.")) { await api(`/api/speaker-profiles/${del.dataset.delete}`, { method:"DELETE" }); await load(); } });
  options.addEventListener("change", async () => { const ids = [...options.querySelectorAll("input:checked")].map(x => x.value); if (!ids.length) return render(); const meetings = await api(`/api/speaker-profiles/meetings?${ids.map(id => `profile_ids=${id}`).join("&")}`); results.innerHTML = meetings.length ? meetings.map(m => `<a class="speaker-meeting-row" href="/?meeting=${m.id}"><strong>${escape(m.title)}</strong><span>${new Date(m.started_at || m.created_at).toLocaleString()}</span></a>`).join("") : `<div class="result-empty"><strong>No matching meetings</strong><span>No saved meeting contains every selected speaker yet.</span></div>`; });
  load().catch(e => { list.textContent = e.message; });
})();
