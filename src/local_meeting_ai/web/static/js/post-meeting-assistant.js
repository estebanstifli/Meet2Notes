(() => {
  "use strict";

  const widget = document.querySelector("#post-meeting-assistant");
  if (!widget) return;

  const { api, escapeHTML, t, toast } = window.Meet2Notes;
  const form = document.querySelector("#post-meeting-assistant-form");
  const question = document.querySelector("#post-meeting-assistant-question");
  const send = document.querySelector("#post-meeting-assistant-send");
  const messages = document.querySelector("#post-meeting-assistant-messages");
  const meetingSelect = document.querySelector("#post-meeting-assistant-meeting");
  const toggle = document.querySelector("#post-meeting-assistant-toggle");
  const dragHandle = document.querySelector("#post-meeting-assistant-drag-handle");
  const resizeHandle = document.querySelector("#post-meeting-assistant-resize");
  const ragStatus = document.querySelector("#post-meeting-assistant-rag");
  const contextToggle = document.querySelector("#post-meeting-assistant-context-toggle");
  const contextPanel = document.querySelector("#post-meeting-assistant-context-panel");
  const contextClose = document.querySelector("#post-meeting-assistant-context-close");
  const contextDocuments = document.querySelector("#post-meeting-assistant-context-documents");
  const contextChips = document.querySelector("#post-meeting-assistant-context-chips");
  const contextBudget = document.querySelector("#post-meeting-assistant-context-budget");
  const defaultMeetingId = widget.dataset.defaultMeetingId || "";
  const embedded = widget.dataset.layout === "embedded" && Boolean(widget.closest(".prompt-assistant"));
  const history = [];
  const selectedAttachments = new Map();
  const storageKey = "meet2notes.postMeetingAssistant.v2";

  function sourceTime(milliseconds) {
    const seconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000));
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function setRagState(state, text) {
    ragStatus.dataset.state = state;
    ragStatus.querySelector("span").textContent = text;
  }

  function estimatedTokens(text) {
    return text ? Math.max(1, Math.ceil(text.length / 3)) : 0;
  }

  function attachmentKey(kind, id) {
    return `${kind}:${id}`;
  }

  function renderContextChips() {
    contextChips.innerHTML = [...selectedAttachments.values()].map((item) => `
      <button class="post-meeting-assistant-context-chip" type="button" data-remove-attachment="${attachmentKey(item.kind, item.id)}" title="Remove ${escapeHTML(item.label)}">
        <span>${escapeHTML(item.label)}</span><i>×</i>
      </button>`).join("");
    const tokens = [...selectedAttachments.values()].reduce(
      (total, item) => total + Number(item.estimatedTokens || 0), 0,
    );
    contextBudget.textContent = tokens
      ? t("post_assistant.tokens", { count: tokens.toLocaleString() })
      : "RAG only";
    contextChips.querySelectorAll("[data-remove-attachment]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedAttachments.delete(button.dataset.removeAttachment);
        const checkbox = contextDocuments.querySelector(
          `[data-attachment-key="${CSS.escape(button.dataset.removeAttachment)}"]`,
        );
        if (checkbox) checkbox.checked = false;
        renderContextChips();
      });
    });
  }

  async function selectAttachment(input) {
    const key = input.dataset.attachmentKey;
    if (!input.checked) {
      selectedAttachments.delete(key);
      renderContextChips();
      return;
    }
    input.disabled = true;
    try {
      let tokens = Number(input.dataset.estimatedTokens || 0);
      if (input.dataset.attachmentKind === "transcription" && !tokens) {
        const detail = await api(`/api/transcriptions/${Number(input.dataset.attachmentId)}`);
        tokens = estimatedTokens((detail.segments || []).map((segment) => segment.text).join("\n"));
        input.dataset.estimatedTokens = String(tokens);
        input.closest("label").querySelector("small").textContent += ` · ${t("post_assistant.tokens", { count: tokens.toLocaleString() })}`;
      }
      selectedAttachments.set(key, {
        kind: input.dataset.attachmentKind,
        id: Number(input.dataset.attachmentId),
        label: input.dataset.attachmentLabel,
        estimatedTokens: tokens,
      });
      renderContextChips();
    } catch (error) {
      input.checked = false;
      toast(error.message, "error");
    } finally {
      input.disabled = false;
    }
  }

  async function loadContextDocuments() {
    selectedAttachments.clear();
    renderContextChips();
    const meetingId = Number(meetingSelect.value || 0);
    contextToggle.disabled = !meetingId;
    contextPanel.classList.add("hidden");
    contextToggle.setAttribute("aria-expanded", "false");
    if (!meetingId) {
      contextDocuments.innerHTML = "<span>Select one meeting as scope first.</span>";
      return;
    }
      contextDocuments.innerHTML = `<span>${t("post_assistant.loading_documents")}</span>`;
    try {
      const [transcriptions, summaries] = await Promise.all([
        api(`/api/meetings/${meetingId}/transcriptions`),
        api(`/api/meetings/${meetingId}/summaries`),
      ]);
      const documents = [
        ...transcriptions.filter((item) => item.status === "completed").map((item) => ({
          kind: "transcription",
          id: item.id,
          label: item.title,
          detail: `${t(item.is_active ? "post_assistant.active_transcript" : "post_assistant.transcript_version")} · ${item.model} · ${String(item.completed_at || item.created_at).slice(0, 16)}`,
          estimatedTokens: 0,
        })),
        ...summaries.filter((item) => item.status === "completed" && item.content_markdown).map((item) => ({
          kind: "summary",
          id: item.id,
          label: t("post_assistant.ai_notes", { id: item.id }),
          detail: `${item.model} · ${String(item.completed_at || item.created_at).slice(0, 16)}`,
          estimatedTokens: estimatedTokens(item.content_markdown),
        })),
      ];
      contextDocuments.innerHTML = documents.length ? documents.map((item) => `
        <label class="post-meeting-assistant-context-document">
          <input type="checkbox"
            data-attachment-key="${attachmentKey(item.kind, item.id)}"
            data-attachment-kind="${item.kind}"
            data-attachment-id="${item.id}"
            data-attachment-label="${escapeHTML(item.label)}"
            data-estimated-tokens="${item.estimatedTokens}">
          <span><strong>${escapeHTML(item.label)}</strong><small>${escapeHTML(item.detail)}${item.estimatedTokens ? ` · ${t("post_assistant.tokens", { count: item.estimatedTokens.toLocaleString() })}` : ""}</small></span>
        </label>`).join("") : `<span>${t("post_assistant.no_documents")}</span>`;
      contextDocuments.querySelectorAll("[data-attachment-key]").forEach((input) => {
        input.addEventListener("change", () => selectAttachment(input));
      });
    } catch (error) {
      contextDocuments.innerHTML = `<span>${escapeHTML(error.message)}</span>`;
    }
  }

  function appendMessage(role, content, sources = []) {
    document.querySelector("#post-meeting-assistant-welcome")?.remove();
    const article = document.createElement("article");
    article.className = `post-meeting-assistant-message ${role}`;
    const label = role === "assistant" ? "Meet2Notes AI" : "You";
    const body = role === "assistant"
      ? escapeHTML(content).replaceAll(/\n/g, "<br>")
      : escapeHTML(content);
    article.innerHTML = `<div class="post-meeting-assistant-message-label">${label}</div><div class="post-meeting-assistant-message-body">${body}</div>`;
    if (sources.length) {
      const details = document.createElement("details");
      details.className = "post-meeting-assistant-sources";
      details.innerHTML = `<summary>${t("post_assistant.sources", { count: sources.length }, sources.length)}</summary><div>${sources.map((source) => `
        <a href="/?meeting=${Number(source.meeting_id)}">
          <strong>${escapeHTML(source.meeting_title)}</strong>
          <small>${escapeHTML(String(source.meeting_date || "").slice(0, 16))} · ${sourceTime(source.start_ms)}</small>
        </a>`).join("")}</div>`;
      article.append(details);
    }
    messages.append(article);
    messages.scrollTop = messages.scrollHeight;
    return article;
  }

  function appendPending() {
    const pending = appendMessage("assistant", "Searching local meeting context...");
    pending.classList.add("pending");
    return pending;
  }

  function liveMeetingIsActive() {
    const liveActions = document.querySelector("#live-actions");
    const liveWidget = document.querySelector("#live-ai-assistant");
    return Boolean(
      (liveActions && !liveActions.classList.contains("hidden"))
      || (liveWidget && !liveWidget.classList.contains("hidden")),
    );
  }

  function syncVisibility() {
    const belongsOnPage = embedded || widget.dataset.context === "library" || Boolean(defaultMeetingId);
    widget.classList.toggle("hidden", !belongsOnPage || liveMeetingIsActive());
  }

  function saveState() {
    if (embedded) return;
    if (widget.dataset.collapsed === "true") {
      localStorage.setItem(storageKey, JSON.stringify({ collapsed: true }));
      return;
    }
    localStorage.setItem(storageKey, JSON.stringify({
      collapsed: false,
      width: Math.round(widget.getBoundingClientRect().width),
      height: Math.round(widget.getBoundingClientRect().height),
    }));
  }

  function restoreState() {
    if (embedded) {
      widget.dataset.collapsed = "false";
      return;
    }
    try {
      const state = JSON.parse(localStorage.getItem(storageKey) || "{}");
      if (Number.isFinite(state.width)) widget.style.width = `${Math.max(300, state.width)}px`;
      if (Number.isFinite(state.height)) widget.style.height = `${Math.max(300, state.height)}px`;
      setCollapsed(Boolean(state.collapsed), false);
    } catch {
      setCollapsed(false, false);
    }
  }

  function constrainWidget() {
    if (widget.dataset.collapsed === "true") return;
    const rect = widget.getBoundingClientRect();
    const margin = 8;
    const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
    const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
    if (widget.style.left || widget.style.top) {
      widget.style.left = `${Math.min(Math.max(rect.left, margin), maxLeft)}px`;
      widget.style.top = `${Math.min(Math.max(rect.top, margin), maxTop)}px`;
      widget.style.right = "auto";
      widget.style.bottom = "auto";
    }
  }

  function setCollapsed(collapsed, persist = true) {
    widget.dataset.collapsed = String(collapsed);
    toggle.setAttribute("aria-label", collapsed ? "Expand Meeting Assistant" : "Minimize Meeting Assistant");
    toggle.title = collapsed ? "Expand Meeting Assistant" : "Minimize Meeting Assistant";
    if (!collapsed) {
      widget.style.left = "auto";
      widget.style.top = "auto";
      widget.style.right = "24px";
      widget.style.bottom = "24px";
    }
    if (persist) saveState();
  }

  async function loadMeetings() {
    try {
      const meetings = await api("/api/meetings?limit=500");
      meetings.filter((meeting) => meeting.status === "ready").forEach((meeting) => {
        const option = document.createElement("option");
        option.value = String(meeting.id);
        option.textContent = meeting.title;
        meetingSelect.append(option);
      });
      if (defaultMeetingId && [...meetingSelect.options].some((option) => option.value === defaultMeetingId)) {
        meetingSelect.value = defaultMeetingId;
      }
      loadContextDocuments();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function loadRagStatus() {
    try {
      const data = await api("/api/rag/status");
      if (!data.enabled) {
        setRagState("error", "RAG disabled");
        return;
      }
      setRagState(data.provider?.available ? "ready" : "error", "Local RAG");
    } catch {
      setRagState("error", "RAG unavailable");
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = question.value.trim();
    if (!value || form.dataset.busy) return;
    appendMessage("user", value);
    question.value = "";
    question.style.height = "auto";
    form.dataset.busy = "true";
    send.disabled = true;
    setRagState("busy", "Searching...");
    const pending = appendPending();
    try {
      const result = await api("/api/prompt", {
        method: "POST",
        body: JSON.stringify({
          question: value,
          meeting_id: meetingSelect.value ? Number(meetingSelect.value) : null,
          use_rag: true,
          history: history.slice(-8),
          attachments: [...selectedAttachments.values()].map(({ kind, id }) => ({ kind, id })),
        }),
      });
      pending.remove();
      appendMessage("assistant", result.answer, result.sources || []);
      history.push({ role: "user", content: value }, { role: "assistant", content: result.answer });
      const usage = result.context_usage || {};
      const used = Number(usage.estimated_total_input_tokens || 0);
      const capacity = Number(usage.context_window_tokens || 0);
      contextBudget.textContent = used && capacity
        ? t("post_assistant.token_budget", { used: used.toLocaleString(), capacity: capacity.toLocaleString() })
        : contextBudget.textContent;
      setRagState("ready", "Local RAG");
    } catch (error) {
      pending.remove();
      appendMessage("assistant", t("post_assistant.error", { message: error.message }));
      setRagState("error", "RAG error");
      toast(error.message, "error");
    } finally {
      delete form.dataset.busy;
      send.disabled = !question.value.trim();
      question.focus();
    }
  });

  question.addEventListener("input", () => {
    send.disabled = Boolean(form.dataset.busy) || !question.value.trim();
    question.style.height = "auto";
    question.style.height = `${Math.min(question.scrollHeight, 92)}px`;
  });
  question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  meetingSelect.addEventListener("change", loadContextDocuments);
  contextToggle.addEventListener("click", () => {
    const open = contextPanel.classList.contains("hidden");
    contextPanel.classList.toggle("hidden", !open);
    contextToggle.setAttribute("aria-expanded", String(open));
  });
  contextClose.addEventListener("click", () => {
    contextPanel.classList.add("hidden");
    contextToggle.setAttribute("aria-expanded", "false");
  });

  toggle.addEventListener("click", () => {
    if (embedded) return;
    const collapsed = widget.dataset.collapsed !== "true";
    setCollapsed(collapsed);
    if (!collapsed) question.focus();
  });

  document.querySelectorAll("[data-open-post-meeting-assistant]").forEach((button) => {
    button.addEventListener("click", () => {
      setCollapsed(false);
      syncVisibility();
      question.focus();
    });
  });

  dragHandle.addEventListener("pointerdown", (event) => {
    if (embedded || widget.dataset.collapsed === "true" || event.target.closest("button")) return;
    event.preventDefault();
    const rect = widget.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    dragHandle.setPointerCapture(event.pointerId);
    const move = (moveEvent) => {
      widget.style.left = `${moveEvent.clientX - offsetX}px`;
      widget.style.top = `${moveEvent.clientY - offsetY}px`;
      widget.style.right = "auto";
      widget.style.bottom = "auto";
      constrainWidget();
    };
    const finish = () => {
      dragHandle.removeEventListener("pointermove", move);
      dragHandle.removeEventListener("pointerup", finish);
      dragHandle.removeEventListener("pointercancel", finish);
    };
    dragHandle.addEventListener("pointermove", move);
    dragHandle.addEventListener("pointerup", finish);
    dragHandle.addEventListener("pointercancel", finish);
  });

  resizeHandle.addEventListener("pointerdown", (event) => {
    if (embedded || widget.dataset.collapsed === "true") return;
    event.preventDefault();
    const rect = widget.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    resizeHandle.setPointerCapture(event.pointerId);
    const move = (moveEvent) => {
      widget.style.width = `${Math.min(window.innerWidth - 16, Math.max(300, rect.width + moveEvent.clientX - startX))}px`;
      widget.style.height = `${Math.min(window.innerHeight - 16, Math.max(300, rect.height + moveEvent.clientY - startY))}px`;
      constrainWidget();
    };
    const finish = () => {
      resizeHandle.removeEventListener("pointermove", move);
      resizeHandle.removeEventListener("pointerup", finish);
      resizeHandle.removeEventListener("pointercancel", finish);
      saveState();
    };
    resizeHandle.addEventListener("pointermove", move);
    resizeHandle.addEventListener("pointerup", finish);
    resizeHandle.addEventListener("pointercancel", finish);
  });

  resizeHandle.addEventListener("keydown", (event) => {
    if (embedded) return;
    const delta = event.shiftKey ? 50 : 20;
    const widthDelta = event.key === "ArrowRight" ? delta : event.key === "ArrowLeft" ? -delta : 0;
    const heightDelta = event.key === "ArrowDown" ? delta : event.key === "ArrowUp" ? -delta : 0;
    if (!widthDelta && !heightDelta) return;
    event.preventDefault();
    const rect = widget.getBoundingClientRect();
    widget.style.width = `${Math.max(300, rect.width + widthDelta)}px`;
    widget.style.height = `${Math.max(300, rect.height + heightDelta)}px`;
    constrainWidget();
    saveState();
  });

  const visibilityObserver = new MutationObserver(syncVisibility);
  [document.querySelector("#live-actions"), document.querySelector("#live-ai-assistant")]
    .filter(Boolean)
    .forEach((element) => visibilityObserver.observe(element, { attributes: true, attributeFilter: ["class"] }));

  window.addEventListener("resize", constrainWidget);
  restoreState();
  syncVisibility();
  loadMeetings();
  loadRagStatus();
})();
