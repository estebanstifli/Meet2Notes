(() => {
  "use strict";
  const { api, escapeHTML, toast } = window.Meet2Notes;
  const form = document.querySelector("#prompt-form");
  const question = document.querySelector("#prompt-question");
  const meeting = document.querySelector("#prompt-meeting");
  const useRag = document.querySelector("#prompt-use-rag");
  const messages = document.querySelector("#prompt-messages");
  const submit = document.querySelector("#prompt-submit");
  const note = document.querySelector("#prompt-composer-note");
  const history = [];

  function sourceTime(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function appendMessage(role, content, sources = []) {
    messages.querySelector(".prompt-welcome")?.remove();
    const article = document.createElement("article");
    article.className = `prompt-message ${role}`;
    const body = role === "assistant"
      ? escapeHTML(content).replaceAll(/\n/g, "<br>")
      : escapeHTML(content);
    article.innerHTML = `<div class="prompt-message-label">${role === "assistant" ? "Meet2Notes AI" : "You"}</div><div class="prompt-message-body">${body}</div>`;
    if (sources.length) {
      const details = document.createElement("details");
      details.className = "prompt-sources";
      details.innerHTML = `<summary>${sources.length} retrieved source${sources.length === 1 ? "" : "s"}</summary><div>${sources.map((source, index) => `
        <a href="/meetings/${source.meeting_id}/transcript" target="_blank" rel="noopener">
          <strong>[R${index + 1}] ${escapeHTML(source.meeting_title)}</strong>
          <span>${escapeHTML(String(source.meeting_date || "").slice(0, 16))} · ${sourceTime(source.start_ms)} · score ${Number(source.score).toFixed(3)}</span>
          <small>${escapeHTML(source.text).slice(0, 280)}${source.text.length > 280 ? "…" : ""}</small>
        </a>`).join("")}</div>`;
      article.append(details);
    }
    messages.append(article);
    messages.scrollTop = messages.scrollHeight;
  }

  function setRagCopy() {
    note.textContent = useRag.checked
      ? "RAG will embed this question before retrieving and ranking context."
      : (meeting.value ? "The complete selected transcript will be sent as context." : "No meeting context will be supplied.");
  }

  async function loadStatus() {
    const status = document.querySelector("#prompt-rag-status");
    try {
      const data = await api("/api/rag/status");
      status.classList.toggle("ready", data.provider.available);
      status.querySelector("span").textContent = `${data.chunks} chunks · ${data.meetings} meetings · ${data.vector_acceleration}`;
    } catch (error) {
      status.querySelector("span").textContent = error.message;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = question.value.trim();
    if (!value || form.dataset.busy) return;
    appendMessage("user", value);
    question.value = "";
    form.dataset.busy = "true";
    submit.disabled = true;
    submit.querySelector("span").textContent = useRag.checked ? "Retrieving…" : "Thinking…";
    const pending = document.createElement("article");
    pending.className = "prompt-message assistant pending";
    pending.innerHTML = '<div class="prompt-message-label">Meet2Notes AI</div><div class="prompt-thinking"><i></i><i></i><i></i><span>Searching local meeting context…</span></div>';
    messages.append(pending);
    messages.scrollTop = messages.scrollHeight;
    try {
      const result = await api("/api/prompt", {
        method: "POST",
        body: JSON.stringify({
          question: value,
          meeting_id: meeting.value ? Number(meeting.value) : null,
          use_rag: useRag.checked,
          history: history.slice(-8),
        }),
      });
      pending.remove();
      appendMessage("assistant", result.answer, result.sources || []);
      history.push({ role: "user", content: value }, { role: "assistant", content: result.answer });
      loadStatus();
    } catch (error) {
      pending.remove();
      appendMessage("assistant", `I could not complete that request: ${error.message}`);
      toast(error.message, "error");
    } finally {
      delete form.dataset.busy;
      submit.disabled = false;
      submit.querySelector("span").textContent = "Ask AI";
      question.focus();
    }
  });

  document.querySelectorAll("[data-prompt-suggestion]").forEach((button) => {
    button.addEventListener("click", () => {
      question.value = button.dataset.promptSuggestion;
      question.focus();
    });
  });
  useRag.addEventListener("change", setRagCopy);
  meeting.addEventListener("change", setRagCopy);
  question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  setRagCopy();
  loadStatus();
})();
