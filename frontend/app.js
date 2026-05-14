"use strict";

(function () {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("message-input");
  const submitButton = document.getElementById("submit-button");
  const formStatus = document.getElementById("form-status");
  const conversationPanel = document.getElementById("conversation-panel");
  const messageList = document.getElementById("message-list");
  const processingIndicator = document.getElementById("processing-indicator");
  const processingText = document.getElementById("processing-text");

  const DEFAULT_PROCESSING_TEXT = "正在整理任务...";

  let activeSource = null;
  let sessionId = null;

  submitButton.disabled = true;
  bootstrapSession();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const message = input.value.trim();
    if (!message) {
      updateFormState("请输入对话内容。", false);
      input.focus();
      return;
    }

    if (!sessionId) {
      updateFormState("会话尚未创建完成。", true);
      return;
    }

    closeActiveSource();
    hideProcessingIndicator();
    appendUserMessage(message);
    setSubmitting(true);
    updateFormState("正在发送消息...", true);
    showProcessingIndicator("Agent 正在读取请求...");

    try {
      const payload = await postMessageWithSessionRecovery(message);
      if (!payload.session_id || !payload.run_id) {
        throw new Error("响应缺少会话或运行标识");
      }

      sessionId = payload.session_id;
      input.value = "";
      openStream(payload.session_id, payload.run_id);
    } catch (error) {
      appendAssistantMessage({
        title: "Error",
        body: getErrorMessage(error),
        tone: "error",
      });
      hideProcessingIndicator();
      updateFormState("消息发送失败。", false);
      setSubmitting(false);
    }
  });

  async function bootstrapSession() {
    return bootstrapSessionWithOptions();
  }

  async function bootstrapSessionWithOptions(options = {}) {
    const silent = Boolean(options.silent);

    if (!silent) {
      updateFormState("正在创建会话...", true);
    }

    try {
      const response = await fetch("/api/sessions", {
        method: "POST",
      });

      if (!response.ok) {
        throw new Error("会话创建失败");
      }

      const payload = await response.json();
      if (!payload.session_id) {
        throw new Error("响应缺少 session_id");
      }

      sessionId = payload.session_id;
      if (!silent) {
        submitButton.disabled = false;
        updateFormState(`会话已创建：${sessionId}`, false);
      }
      return sessionId;
    } catch (error) {
      if (!silent) {
        submitButton.disabled = true;
        updateFormState(getErrorMessage(error), false);
      }
      throw error;
    }
  }

  async function postMessage(message) {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });

    return response;
  }

  async function postMessageWithSessionRecovery(message) {
    let response = await postMessage(message);
    if (response.status === 404) {
      await bootstrapSessionWithOptions({ silent: true });
      updateFormState("会话已过期，正在重建并重试...", true);
      response = await postMessage(message);
    }

    if (!response.ok) {
      throw new Error("消息发送失败");
    }

    return response.json();
  }

  function openStream(currentSessionId, runId) {
    const source = new EventSource(
      `/api/sessions/${encodeURIComponent(currentSessionId)}/runs/${encodeURIComponent(runId)}/stream`
    );
    activeSource = source;

    source.onmessage = (event) => {
      if (activeSource !== source) {
        return;
      }

      const payload = parsePayload(event.data);
      if (!payload) {
        return;
      }

      renderPayload(payload);

      if (payload.type === "done") {
        finalizeStream(source);
      }
    };

    source.onerror = () => {
      if (activeSource !== source) {
        return;
      }

      if (source.readyState === EventSource.CLOSED) {
        updateFormState("事件流连接已关闭，请刷新页面后重试。", true);
        return;
      }

      updateFormState("事件流连接中断，正在尝试重连...", true);
    };
  }

  function renderPayload(payload) {
    const type = payload.type;

    if (type === "assistant") {
      hideProcessingIndicator();
      appendAssistantMessage({
        title: "Assistant",
        body: payload.message || "",
        tone: "assistant",
      });
      updateFormState(payload.message || "等待下一条消息。", false);
      return;
    }

    if (type === "tool_status") {
      showProcessingIndicator(payload.message || "工具执行中。");
      updateFormState(payload.message || "工具执行中。", true);
      return;
    }

    if (type === "artifact") {
      hideProcessingIndicator();
      const resultMessage = appendAssistantMessage({
        title: "Artifact",
        body: payload.summary || "结果已生成。",
        tone: "result",
      });

      if (payload.download_url) {
        resultMessage.appendChild(buildDownloadLink(payload.download_url, payload.filename));
      }

      if (Array.isArray(payload.preview_columns) && Array.isArray(payload.preview_rows)) {
        const preview = normalizePreviewData(payload.preview_columns, payload.preview_rows);
        resultMessage.appendChild(buildPreviewTable(preview.columns, preview.rows));
      }
      return;
    }

    if (type === "error") {
      hideProcessingIndicator();
      appendAssistantMessage({
        title: "Error",
        body: payload.message || "任务执行失败。",
        tone: "error",
      });
      updateFormState(payload.message || "任务执行失败。", false);
      return;
    }

    if (type === "done") {
      hideProcessingIndicator();
      updateFormState("等待下一条消息。", false);
      return;
    }
  }

  function appendUserMessage(text) {
    appendMessage({
      role: "user",
      title: "You",
      body: text,
      tone: "user",
    });
  }

  function appendAssistantMessage({ title, body, tone }) {
    return appendMessage({
      role: "assistant",
      title,
      body,
      tone,
    });
  }

  function appendMessage({ role, title, body, tone }) {
    const item = document.createElement("li");
    item.className = `message ${role} ${tone || ""}`.trim();

    const card = document.createElement("article");
    card.className = "message-card";

    const meta = document.createElement("div");
    meta.className = "message-meta";

    const roleBadge = document.createElement("span");
    roleBadge.className = "message-role";
    roleBadge.textContent = role === "user" ? "User" : "Assistant";

    const titleNode = document.createElement("h3");
    titleNode.className = "message-title";
    titleNode.textContent = title;

    meta.appendChild(roleBadge);
    meta.appendChild(titleNode);

    const bodyNode = document.createElement("p");
    bodyNode.className = "message-body";
    bodyNode.textContent = body;

    card.appendChild(meta);
    card.appendChild(bodyNode);
    item.appendChild(card);
    messageList.appendChild(item);
    scrollConversationToBottom();
    return card;
  }

  function scrollConversationToBottom() {
    if (conversationPanel) {
      conversationPanel.scrollTop = conversationPanel.scrollHeight;
      return;
    }

    messageList.scrollTop = messageList.scrollHeight;
  }

  function showProcessingIndicator(message) {
    if (!processingIndicator) {
      return;
    }

    processingIndicator.hidden = false;
    processingIndicator.dataset.state = "active";
    if (processingText) {
      processingText.textContent = message || DEFAULT_PROCESSING_TEXT;
    }
    scrollConversationToBottom();
  }

  function hideProcessingIndicator() {
    if (!processingIndicator) {
      return;
    }

    processingIndicator.hidden = true;
    processingIndicator.dataset.state = "idle";
    if (processingText) {
      processingText.textContent = DEFAULT_PROCESSING_TEXT;
    }
  }

  function buildDownloadLink(url, filename) {
    const wrapper = document.createElement("p");
    wrapper.className = "download-wrap";

    const link = document.createElement("a");
    link.className = "download-link";
    link.href = url;
    link.textContent = filename ? `下载 ${filename}` : "下载 CSV";

    wrapper.appendChild(link);
    return wrapper;
  }

  function buildPreviewTable(columns, rows) {
    const section = document.createElement("section");
    section.className = "preview-section";

    const heading = document.createElement("h4");
    heading.className = "preview-title";
    heading.textContent = "CSV Preview";
    section.appendChild(heading);

    const scroller = document.createElement("div");
    scroller.className = "table-scroller";

    const table = document.createElement("table");
    table.className = "preview-table";

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = String(column);
      headRow.appendChild(cell);
    });
    thead.appendChild(headRow);

    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const rowNode = document.createElement("tr");
      row.forEach((value) => {
        const cell = buildPreviewCell(value);
        rowNode.appendChild(cell);
      });
      tbody.appendChild(rowNode);
    });

    table.appendChild(thead);
    table.appendChild(tbody);
    scroller.appendChild(table);
    section.appendChild(scroller);

    return section;
  }

  function buildPreviewCell(value) {
    const cell = document.createElement("td");
    const text = value == null ? "" : String(value);

    if (!text) {
      return cell;
    }

    const toggle = document.createElement("div");
    toggle.className = "preview-cell-toggle";
    toggle.dataset.expanded = "false";
    toggle.textContent = text;
    toggle.addEventListener("click", () => {
      const isExpanded = toggle.dataset.expanded === "true";
      toggle.dataset.expanded = isExpanded ? "false" : "true";
    });

    cell.appendChild(toggle);
    return cell;
  }

  function normalizePreviewData(columns, rows) {
    const cols = Array.isArray(columns) ? columns.map((c) => String(c)) : [];
    const normalizedRows = Array.isArray(rows)
      ? rows.map((row) => (Array.isArray(row) ? row.map((v) => (v == null ? "" : String(v))) : []))
      : [];
    return { columns: cols, rows: normalizedRows };
  }

  function parsePayload(raw) {
    try {
      return JSON.parse(raw);
    } catch (_error) {
      hideProcessingIndicator();
      appendAssistantMessage({
        title: "Parse Error",
        body: "收到无法解析的事件数据。",
        tone: "error",
      });
      return null;
    }
  }

  function closeActiveSource() {
    if (activeSource) {
      activeSource.close();
      activeSource = null;
    }
    hideProcessingIndicator();
  }

  function finalizeStream(source) {
    if (activeSource === source) {
      source.close();
      activeSource = null;
    }
    setSubmitting(false);
  }

  function setSubmitting(isSubmitting) {
    submitButton.disabled = isSubmitting;
    input.disabled = isSubmitting;
  }

  function updateFormState(message, isBusy) {
    formStatus.textContent = message;
    formStatus.dataset.busy = isBusy ? "true" : "false";
  }

  function getErrorMessage(error) {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return "发生未知错误。";
  }
})();
