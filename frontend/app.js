"use strict";

(function () {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("message-input");
  const submitButton = document.getElementById("submit-button");
  const formStatus = document.getElementById("form-status");
  const messageList = document.getElementById("message-list");

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
    appendUserMessage(message);
    setSubmitting(true);
    updateFormState("正在发送消息...", true);

    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message }),
      });

      if (!response.ok) {
        throw new Error("消息发送失败");
      }

      const payload = await response.json();
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
      updateFormState("消息发送失败。", false);
      setSubmitting(false);
    }
  });

  async function bootstrapSession() {
    updateFormState("正在创建会话...", true);

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
      submitButton.disabled = false;
      updateFormState(`会话已创建：${sessionId}`, false);
    } catch (error) {
      submitButton.disabled = true;
      updateFormState(getErrorMessage(error), false);
    }
  }

  function openStream(currentSessionId, runId) {
    const source = new EventSource(
      `/api/sessions/${encodeURIComponent(currentSessionId)}/runs/${encodeURIComponent(runId)}/stream`
    );
    activeSource = source;

    source.onmessage = (event) => {
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

      appendAssistantMessage({
        title: "Stream Error",
        body: "事件流已中断，请稍后重试。",
        tone: "error",
      });
      finalizeStream(source);
      updateFormState("事件流已中断，请稍后重试。", false);
    };
  }

  function renderPayload(payload) {
    const type = payload.type;

    if (type === "assistant") {
      appendAssistantMessage({
        title: "Assistant",
        body: payload.message || "",
        tone: "assistant",
      });
      updateFormState(payload.message || "等待下一条消息。", false);
      return;
    }

    if (type === "tool_status") {
      appendAssistantMessage({
        title: "Status",
        body: payload.message || "工具执行中。",
        tone: "status",
      });
      updateFormState(payload.message || "工具执行中。", true);
      return;
    }

    if (type === "artifact") {
      const resultMessage = appendAssistantMessage({
        title: "Artifact",
        body: payload.summary || "结果已生成。",
        tone: "result",
      });

      if (payload.download_url) {
        resultMessage.appendChild(buildDownloadLink(payload.download_url, payload.filename));
      }

      if (Array.isArray(payload.preview_columns) && Array.isArray(payload.preview_rows)) {
        resultMessage.appendChild(buildPreviewTable(payload.preview_columns, payload.preview_rows));
      }
      return;
    }

    if (type === "error") {
      appendAssistantMessage({
        title: "Error",
        body: payload.message || "任务执行失败。",
        tone: "error",
      });
      updateFormState(payload.message || "任务执行失败。", false);
      return;
    }

    if (type === "done") {
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

    item.scrollIntoView({ behavior: "smooth", block: "end" });
    return card;
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
        const cell = document.createElement("td");
        cell.textContent = value == null ? "" : String(value);
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

  function parsePayload(raw) {
    try {
      return JSON.parse(raw);
    } catch (_error) {
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
