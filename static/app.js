let activeConversationId = null;
let conversationPollTimer = null;

// backend의 vLLM lifecycle 코드를 화면용 짧은 상태 문구로 바꿉니다.
const lifecycleLabels = {
  not_started: "vLLM 미시작",
  starting: "vLLM 시작 중",
  model_download: "모델 다운로드 중",
  model_loading: "모델 로딩 중",
  api_starting: "API 시작 중",
  api_ready: "API ready",
  failed: "실패",
};

function $(id) {
  return document.getElementById(id);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "요청이 실패했습니다.");
  }
  return data;
}

async function initializeApp() {
  bindEvents();
  await Promise.all([refreshRuntime(), loadConversations()]);
  if (!activeConversationId) {
    updateSendState(null);
  }
}

function bindEvents() {
  $("newConversationBtn").addEventListener("click", createNewConversation);
  $("videoForm").addEventListener("submit", submitConversationVideo);
  $("chatForm").addEventListener("submit", submitChatMessage);

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setActivePanel(button.dataset.panel || "analysis"));
  });

  document.querySelectorAll("[data-frame-close]").forEach((element) => {
    element.addEventListener("click", closeFramePreview);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("framePreviewModal")?.classList.contains("open")) {
      closeFramePreview();
    }
  });
}

function setActivePanel(panelName) {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.panel === panelName);
  });
  const target = panelName === "logs" ? $("logPanel") : $("runtimePanel");
  if (panelName !== "analysis") {
    target.querySelector("details")?.setAttribute("open", "");
    target.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

async function loadConversations() {
  const data = await fetchJson("/api/conversations?limit=50");
  renderConversationList(data.conversations || []);
  if (!activeConversationId && data.conversations?.length) {
    await selectConversation(data.conversations[0].conversation_id);
  }
}

async function createNewConversation() {
  const formData = new FormData();
  formData.set("title", "새 영상 분석");
  const conversation = await fetchJson("/api/conversations", { method: "POST", body: formData });
  activeConversationId = conversation.conversation_id;
  await loadConversations();
  renderConversation(conversation);
}

async function selectConversation(conversationId) {
  activeConversationId = conversationId;
  const conversation = await fetchJson(`/api/conversations/${encodeURIComponent(conversationId)}`);
  renderConversation(conversation);
  await loadConversations();
  startConversationPollingIfNeeded(conversation);
}

function getConversationState(conversation) {
  const last = conversation.last_message || (conversation.messages || []).at?.(-1);
  if (!conversation.source) {
    return { label: "영상 필요", tone: "muted" };
  }
  if (last?.status === "running" || last?.status === "queued") {
    return { label: "분석 중", tone: "working" };
  }
  if (last?.status === "failed") {
    return { label: "실패", tone: "failed" };
  }
  if (last?.status === "done") {
    return { label: "완료", tone: "done" };
  }
  return { label: "준비됨", tone: "ready" };
}

function renderConversationList(conversations) {
  $("conversationList").innerHTML = conversations.map((conversation) => {
    const sourceName = conversation.source?.name || "영상 미등록";
    const last = conversation.last_message?.content || conversation.updated_at || "";
    const state = getConversationState(conversation);
    return `
      <button type="button" class="conversation-item ${conversation.conversation_id === activeConversationId ? "active" : ""}"
        onclick="selectConversation('${escapeHtml(conversation.conversation_id)}')">
        <div class="conversation-row">
          <strong>${escapeHtml(conversation.title || "새 영상 분석")}</strong>
          <span class="state-badge ${escapeHtml(state.tone)}">${escapeHtml(state.label)}</span>
        </div>
        <span>${escapeHtml(sourceName)}</span>
        <small>${escapeHtml(last)}</small>
      </button>
    `;
  }).join("") || `<div class="empty-list">아직 대화가 없습니다.</div>`;
}

async function submitConversationVideo(event) {
  event.preventDefault();
  if (!activeConversationId) {
    await createNewConversation();
  }

  const button = $("saveVideoBtn");
  button.disabled = true;
  $("videoSourceStatus").textContent = "영상을 등록 중입니다.";
  try {
    const formData = new FormData(event.target);
    const conversation = await fetchJson(`/api/conversations/${encodeURIComponent(activeConversationId)}/video`, {
      method: "POST",
      body: formData,
    });
    renderConversation(conversation);
    await loadConversations();
    $("videoSourceStatus").textContent = "영상이 등록되었습니다.";
  } catch (error) {
    $("videoSourceStatus").textContent = String(error);
  } finally {
    button.disabled = false;
  }
}

async function submitChatMessage(event) {
  event.preventDefault();
  if (!activeConversationId) {
    await createNewConversation();
  }

  const conversation = await fetchJson(`/api/conversations/${encodeURIComponent(activeConversationId)}`);
  if (!conversation.source) {
    $("conversationMeta").textContent = "먼저 오른쪽 패널에서 영상을 등록하세요.";
    updateSendState(conversation);
    return;
  }

  const prompt = $("chatPrompt").value.trim();
  if (!prompt) {
    $("conversationMeta").textContent = "질문을 입력하세요.";
    return;
  }

  const button = $("sendMessageBtn");
  button.disabled = true;
  try {
    const formData = new FormData();
    formData.set("prompt", prompt);
    formData.set("frame_count", $("frameCount").value);
    formData.set("sampling_mode", $("samplingMode").value);
    formData.set("max_tokens", $("maxTokens").value);
    formData.set("model_id", $("modelId").value);
    formData.set("endpoint", $("endpoint").value);

    await fetchJson(`/api/conversations/${encodeURIComponent(activeConversationId)}/messages`, {
      method: "POST",
      body: formData,
    });
    $("chatPrompt").value = "";
    const updated = await fetchJson(`/api/conversations/${encodeURIComponent(activeConversationId)}`);
    renderConversation(updated);
    startConversationPollingIfNeeded(updated);
    await loadConversations();
  } catch (error) {
    $("conversationMeta").textContent = String(error);
  } finally {
    button.disabled = false;
  }
}

function startConversationPollingIfNeeded(conversation) {
  const hasRunningMessage = (conversation.messages || []).some((message) =>
    message.role === "assistant" && ["queued", "running"].includes(message.status)
  );
  if (!hasRunningMessage) {
    stopConversationPolling();
    return;
  }
  if (conversationPollTimer) {
    return;
  }
  conversationPollTimer = setInterval(async () => {
    if (!activeConversationId) {
      stopConversationPolling();
      return;
    }
    try {
      const next = await fetchJson(`/api/conversations/${encodeURIComponent(activeConversationId)}`);
      renderConversation(next);
      await loadConversations();
      const stillRunning = (next.messages || []).some((message) =>
        message.role === "assistant" && ["queued", "running"].includes(message.status)
      );
      if (!stillRunning) {
        stopConversationPolling();
      }
    } catch (error) {
      $("conversationMeta").textContent = String(error);
      stopConversationPolling();
    }
  }, 2500);
}

function stopConversationPolling() {
  if (conversationPollTimer) {
    clearInterval(conversationPollTimer);
    conversationPollTimer = null;
  }
}

function renderConversation(conversation) {
  activeConversationId = conversation.conversation_id;
  $("conversationTitle").textContent = conversation.title || "새 영상 분석";
  $("conversationMeta").textContent = formatConversationMeta(conversation);
  renderChatMessages(conversation);
  renderConversationVideo(conversation);
  renderLatestJobEvidence(conversation);
  updateSendState(conversation);
}

function updateSendState(conversation) {
  const hasSource = Boolean(conversation?.source);
  $("sendMessageBtn").disabled = !hasSource;
  $("chatPrompt").placeholder = hasSource
    ? "예: 이 영상에서 사고는 언제 발생했어?"
    : "영상을 등록한 뒤 질문할 수 있습니다.";
  $("flowHint").textContent = hasSource ? "영상 등록 완료 · 질문 가능" : "1 새 대화 · 2 영상 등록 · 3 질문";
}

function formatConversationMeta(conversation) {
  const source = conversation.source?.name || "영상 미등록";
  const count = (conversation.messages || []).length;
  return `${source} · 메시지 ${count}개`;
}

function renderChatMessages(conversation) {
  const messages = conversation.messages || [];
  if (!messages.length) {
    $("chatMessages").innerHTML = `
      <div class="empty-state">
        <strong>${conversation.source ? "질문을 입력하세요." : "영상을 먼저 등록하세요."}</strong>
        <span>${conversation.source ? "사고 시점, 주요 상황, 차량 움직임처럼 확인할 내용을 물어보세요." : "오른쪽 패널에서 파일 또는 YouTube URL을 등록하면 질문을 보낼 수 있습니다."}</span>
      </div>
    `;
    return;
  }

  const jobsById = Object.fromEntries((conversation.jobs || []).map((job) => [job.job_id, job]));
  $("chatMessages").innerHTML = messages.map((message) => {
    const job = message.job_id ? jobsById[message.job_id] : null;
    return renderMessage(message, job);
  }).join("");
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
}

function renderMessage(message, job) {
  if (message.role === "user") {
    return `
      <article class="message user">
        <div class="message-bubble">${escapeHtml(message.content || "")}</div>
      </article>
    `;
  }

  const status = message.status || job?.status || "done";
  const content = job?.status === "done" ? job.answer : message.content;
  const duration = job?.duration_ms == null ? "" : ` · ${job.duration_ms}ms`;
  const worker = job?.worker_id ? `${job.worker_id}${duration}` : status;
  return `
    <article class="message assistant">
      <div class="message-card">
        <div class="message-topline">
          <span class="message-status ${escapeHtml(status)}">${escapeHtml(status)}</span>
          <span class="message-meta">${escapeHtml(worker)}</span>
        </div>
        ${renderAnswerHtml(content || "분석 중입니다.")}
      </div>
    </article>
  `;
}

function renderConversationVideo(conversation) {
  const source = conversation.source;
  if (!source) {
    $("videoSourceStatus").textContent = "등록된 영상이 없습니다.";
    $("videoReadyBadge").textContent = "미등록";
    $("videoReadyBadge").className = "state-badge muted";
    $("videoPreview").className = "video-preview empty";
    $("videoPreview").textContent = "영상을 등록한 뒤 첫 질문을 보내면 미리보기가 표시됩니다.";
    return;
  }

  $("videoSourceStatus").textContent = source.name || "영상 등록됨";
  $("videoReadyBadge").textContent = "등록됨";
  $("videoReadyBadge").className = "state-badge ready";
  const latestJob = getLatestJobWithVideo(conversation);
  if (!latestJob) {
    $("videoPreview").className = "video-preview empty";
    $("videoPreview").textContent = "첫 질문을 보내면 영상 미리보기가 준비됩니다.";
    return;
  }
  renderVideoPreview(latestJob);
}

function renderLatestJobEvidence(conversation) {
  const jobs = conversation.jobs || [];
  const latestJob = [...jobs].reverse().find((job) => Array.isArray(job.frames) && job.frames.length) || jobs[jobs.length - 1];
  if (!latestJob) {
    $("frames").innerHTML = `<div class="hint">아직 추출된 프레임이 없습니다.</div>`;
    $("jobLogPath").textContent = "";
    return;
  }
  renderFrameCards(latestJob.frames || []);
  $("jobLogPath").textContent = latestJob.job_dir ? `로그: ${latestJob.job_dir}\\job.json` : "";
}

function getLatestJobWithVideo(conversation) {
  const jobs = conversation.jobs || [];
  return [...jobs].reverse().find((job) => job.source?.path);
}

async function refreshRuntime() {
  const [gpu, vllm, timeslicing, config, workers] = await Promise.all([
    fetchJson("/api/gpu-status"),
    fetchJson("/api/vllm-status"),
    fetchJson("/api/timeslicing"),
    fetchJson("/api/config"),
    fetchJson("/api/workers/refresh", { method: "POST" }),
  ]);

  const lifecycle = lifecycleLabels[vllm.lifecycle_stage] || vllm.lifecycle_stage || "상태 불명";
  $("runtimeStatus").textContent = `GPU ${gpu.ok ? "확인됨" : "확인 실패"} · vLLM ${lifecycle}`;
  $("runtimeBadges").innerHTML = [
    `모델 ${config.default_model_id}`,
    `worker ${(workers.workers || []).length}개`,
    config.processing_mode,
  ].map((text) => `<span>${escapeHtml(text)}</span>`).join("");
  applyConfigToForm(config);
  renderWorkers(workers.workers || []);
  $("runtimeDetail").textContent = JSON.stringify({ gpu, vllm, timeslicing, config, workers }, null, 2);
}

function applyConfigToForm(config) {
  const frameInput = $("frameCount");
  if (config.max_sample_frames) {
    frameInput.max = String(config.max_sample_frames);
    const note = frameInput.closest(".field")?.querySelector(".range-note");
    if (note) {
      note.textContent = `1~${config.max_sample_frames}`;
    }
  }
  if (config.default_frame_count && !frameInput.dataset.configApplied) {
    frameInput.value = String(config.default_frame_count);
    frameInput.dataset.configApplied = "1";
  }
  if (config.default_sampling_mode && !$("samplingMode").dataset.configApplied) {
    $("samplingMode").value = String(config.default_sampling_mode);
    $("samplingMode").dataset.configApplied = "1";
  }
  if (config.default_max_tokens && !$("maxTokens").dataset.configApplied) {
    $("maxTokens").value = String(config.default_max_tokens);
    $("maxTokens").dataset.configApplied = "1";
  }
}

async function startVllm() {
  $("runtimeStatus").textContent = "vLLM 시작 요청 중입니다.";
  const data = await fetchJson("/api/start-vllm", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  await waitForVllmReady();
}

async function waitForVllmReady() {
  const maxAttempts = 120;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const vllm = await fetchJson("/api/vllm-status");
    const lifecycle = lifecycleLabels[vllm.lifecycle_stage] || vllm.lifecycle_stage || "상태 불명";
    $("runtimeStatus").textContent = `vLLM 준비 확인 중 (${attempt}/${maxAttempts}) · ${lifecycle}`;
    if (vllm.running) {
      await refreshRuntime();
      return;
    }
  }
  $("runtimeStatus").textContent = "vLLM 준비 확인 시간이 초과되었습니다. vLLM 로그를 확인하세요.";
}

async function stopVllm() {
  $("runtimeStatus").textContent = "vLLM 종료 중입니다.";
  const data = await fetchJson("/api/stop-vllm", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  await refreshRuntime();
}

async function loadVllmLogs() {
  const data = await fetchJson("/api/vllm/logs?lines=160");
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  renderVllmLogs(data);
  $("runtimeStatus").textContent = "vLLM 로그 조회 완료";
}

async function collectTimeslicingLogs() {
  $("runtimeStatus").textContent = "Time-slicing 로그 수집 중입니다.";
  const data = await fetchJson("/api/timeslicing/logs", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  renderTimeslicingResult(data);
  $("runtimeStatus").textContent = `Time-slicing 로그 수집 완료: ${data.overall_status || "상태 없음"}`;
}

async function cleanupTmpFiles() {
  const confirmed = window.confirm("완료/실패한 job, 추출 프레임, 자동 생성 로그를 정리할까요? 진행 중인 작업은 삭제하지 않습니다.");
  if (!confirmed) {
    return;
  }
  const result = await fetchJson("/api/tmp/cleanup", { method: "POST" });
  $("conversationMeta").textContent =
    `임시파일 정리 완료 · job ${result.deleted_job_count}개 · 프레임 ${result.deleted_frame_file_count}개`;
  await loadConversations();
}

function renderWorkers(workers) {
  $("workerPanel").innerHTML = `
    <div class="worker-strip">
      ${workers.map((worker) => `
        <div class="worker-chip">
          <span class="check-status ${escapeHtml(worker.status || "unknown")}">${escapeHtml(worker.status || "-")}</span>
          <b>${escapeHtml(worker.worker_id || "-")}</b>
          <code>${escapeHtml(worker.endpoint || "-")}</code>
        </div>
      `).join("") || `<span class="hint">등록된 worker가 없습니다.</span>`}
    </div>
  `;
}

function renderVllmLogs(data) {
  const stdout = data.stdout || "(stdout 없음)";
  const stderr = data.stderr || "(stderr 없음)";
  $("vllmLogPanel").innerHTML = `
    <div class="log-header">
      <strong>vLLM 로그</strong>
      <span>returncode: ${escapeHtml(String(data.returncode ?? "-"))}</span>
    </div>
    <div class="log-grid">
      <div>
        <label>stdout</label>
        <pre class="log-pre">${escapeHtml(stdout)}</pre>
      </div>
      <div>
        <label>stderr</label>
        <pre class="log-pre">${escapeHtml(stderr)}</pre>
      </div>
    </div>
  `;
}

function renderTimeslicingResult(data) {
  const checks = data.checks || [];
  $("timeslicingResult").innerHTML = `
    <div class="check-header">
      <strong>Time-slicing: ${escapeHtml(data.overall_status || "unknown")}</strong>
    </div>
    <div class="check-table-wrap">
      <table class="check-table">
        <thead>
          <tr>
            <th>상태</th>
            <th>항목</th>
            <th>원인</th>
            <th>요약</th>
          </tr>
        </thead>
        <tbody>
          ${checks.map((check) => `
            <tr>
              <td><span class="check-status ${escapeHtml(check.status || "unknown")}">${escapeHtml(check.status || "-")}</span></td>
              <td>${escapeHtml(check.label || check.id || "-")}</td>
              <td><code>${escapeHtml(check.reason_code || "-")}</code></td>
              <td>${escapeHtml(check.summary || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderVideoPreview(job) {
  const preview = $("videoPreview");
  if (!job.source?.path) {
    preview.className = "video-preview empty";
    preview.textContent = "영상 파일을 준비하는 중입니다.";
    return;
  }
  const videoUrl = `/api/jobs/${encodeURIComponent(job.job_id)}/video`;
  const duration = job.video_info?.duration_sec == null ? "" : `<span>${Number(job.video_info.duration_sec).toFixed(2)}초</span>`;
  preview.className = "video-preview";
  preview.innerHTML = `
    <video controls preload="metadata" src="${videoUrl}"></video>
    <div class="video-meta">
      <span>${escapeHtml(job.source?.name || job.job_id)}</span>
      ${duration}
    </div>
  `;
}

function renderFrameCards(frames) {
  const frameRoot = $("frames");
  frameRoot.innerHTML = frames.map((frame) => {
    const caption = `#${frame.index} / ${Number(frame.timestamp_sec || 0).toFixed(2)}초`;
    return `
      <button type="button" class="frame-card" data-frame-url="${escapeHtml(frame.preview_url)}" data-frame-caption="${escapeHtml(caption)}">
        <img src="${escapeHtml(frame.preview_url)}" alt="${escapeHtml(`sample frame ${frame.index}`)}" />
        <span>${escapeHtml(caption)}</span>
      </button>
    `;
  }).join("") || `<div class="hint">아직 추출된 프레임이 없습니다.</div>`;

  frameRoot.querySelectorAll(".frame-card").forEach((button) => {
    button.addEventListener("click", () => {
      openFramePreview(button.dataset.frameUrl || "", button.dataset.frameCaption || "");
    });
  });
}

function renderAnswerHtml(answerText) {
  const parsed = parseAnswerSections(answerText || "");
  return `
    <div class="answer-summary">
      <span>${escapeHtml(parsed.primaryLabel)}</span>
      <strong>${escapeHtml(parsed.primaryText || "-")}</strong>
    </div>
    ${parsed.sections.map((section) => `
      <div class="answer-section">
        <span>${escapeHtml(section.label)}</span>
        <p>${escapeHtml(section.text)}</p>
      </div>
    `).join("")}
  `;
}

function parseAnswerSections(answerText) {
  const lines = String(answerText).split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const primary = lines.find((line) => /^(답변|요약|실패):/.test(line)) || lines[0] || "";
  const primaryLabel = (primary.match(/^(답변|요약|실패):/) || ["답변"])[0].replace(":", "");
  const primaryText = primary.replace(/^(답변|요약|실패):\s*/, "");
  const sections = [];

  for (const line of lines) {
    if (line === primary) {
      continue;
    }
    const match = line.match(/^(근거|주요 장면|주의|비고):\s*(.*)$/);
    if (match) {
      sections.push({ label: match[1], text: tidyAnswerSection(match[2]) });
    } else {
      sections.push({ label: "상세", text: tidyAnswerSection(line) });
    }
  }
  return { primaryLabel, primaryText: tidyAnswerSection(primaryText), sections };
}

function tidyAnswerSection(text) {
  return String(text).replace(/\s+/g, " ").trim();
}

function openFramePreview(imageUrl, caption) {
  if (!imageUrl) {
    return;
  }
  $("framePreviewImage").src = imageUrl;
  $("framePreviewCaption").textContent = caption;
  $("framePreviewModal").classList.add("open");
  $("framePreviewModal").setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
}

function closeFramePreview() {
  const modal = $("framePreviewModal");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  $("framePreviewImage").src = "";
  $("framePreviewCaption").textContent = "";
  document.body.classList.remove("modal-open");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#039;");
}

initializeApp().catch((error) => {
  $("conversationMeta").textContent = String(error);
});
