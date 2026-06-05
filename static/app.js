let activeJobId = null;
let jobPollTimer = null;

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

async function refreshRuntime() {
  const [gpu, vllm, timeslicing, config] = await Promise.all([
    fetchJson("/api/gpu-status"),
    fetchJson("/api/vllm-status"),
    fetchJson("/api/timeslicing"),
    fetchJson("/api/config"),
  ]);

  const lifecycle = lifecycleLabels[vllm.lifecycle_stage] || vllm.lifecycle_stage || "상태 불명";
  const vllmState = vllm.running
    ? "vLLM API가 응답 중입니다. 테스트가 끝나면 vLLM 종료 / GPU 해제 버튼으로 컨테이너를 내리세요."
    : "vLLM API가 아직 응답하지 않습니다. 시작 직후라면 모델 다운로드/로딩이 끝날 때까지 기다리세요.";

  $("runtimeStatus").textContent = `GPU: ${gpu.ok ? "확인됨" : "확인 실패"} / vLLM: ${lifecycle} - ${vllmState}`;
  $("runtimeBadges").innerHTML = [
    `모델: ${config.default_model_id}`,
    `MAX_MODEL_LEN: ${config.max_model_len}`,
    `GPU_MEMORY_UTILIZATION: ${config.gpu_memory_utilization}`,
    `HF_TOKEN: ${config.hf_token_configured ? "설정됨" : "미설정"}`,
    `분석 처리: ${config.processing_mode}`,
  ].map((text) => `<span>${escapeHtml(text)}</span>`).join("");
  $("runtimeDetail").textContent = JSON.stringify({ gpu, vllm, timeslicing, config }, null, 2);
}

async function startVllm() {
  $("runtimeStatus").textContent = "vLLM 컨테이너 시작 요청 중입니다. Docker 이미지 pull 또는 모델 로딩은 오래 걸릴 수 있습니다.";
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
    $("runtimeDetail").textContent = JSON.stringify({ vllm }, null, 2);
    $("runtimeStatus").textContent = `vLLM 준비 확인 중 (${attempt}/${maxAttempts}) - ${lifecycle}`;
    if (vllm.running) {
      await refreshRuntime();
      return;
    }
  }
  $("runtimeStatus").textContent = "vLLM 준비 확인 시간이 초과됐습니다. vLLM 로그 버튼으로 Docker 로그를 확인하세요.";
}

async function stopVllm() {
  $("runtimeStatus").textContent = "vLLM 컨테이너 종료 요청 중입니다. 완료되면 GPU 메모리가 반환됩니다.";
  const data = await fetchJson("/api/stop-vllm", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  await refreshRuntime();
}

async function loadVllmLogs() {
  const data = await fetchJson("/api/vllm/logs?lines=160");
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  $("runtimeStatus").textContent = "vLLM 컨테이너 로그 tail을 불러왔습니다.";
}

async function collectTimeslicingLogs() {
  $("runtimeStatus").textContent = "time-slicing 관련 Kubernetes/GPU 로그를 수집 중입니다.";
  const data = await fetchJson("/api/timeslicing/logs", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  $("runtimeStatus").textContent =
    `time-slicing 로그 수집 완료: ${data.log_dir || "로그 경로 없음"}. 로컬 Windows에서는 실제 적용이 아니라 검증 근거 수집입니다.`;
}

async function submitAnalysis(event) {
  event.preventDefault();
  const button = $("analyzeBtn");
  button.disabled = true;
  $("analyzeStatus").textContent = "영상 분석 작업을 생성하는 중입니다.";
  clearResult();

  try {
    const formData = new FormData(event.target);
    const job = await fetchJson("/api/jobs/video", { method: "POST", body: formData });
    activeJobId = job.job_id;
    $("analyzeStatus").textContent = `작업 생성 완료: ${activeJobId}`;
    renderJob(job);
    await refreshJobs();
    startJobPolling(activeJobId);
  } catch (error) {
    $("analyzeStatus").textContent = "작업 생성 실패";
    $("answer").textContent = String(error);
  } finally {
    button.disabled = false;
  }
}

function startJobPolling(jobId) {
  if (jobPollTimer) {
    clearInterval(jobPollTimer);
  }
  jobPollTimer = setInterval(async () => {
    try {
      const job = await fetchJson(`/api/jobs/${encodeURIComponent(jobId)}`);
      renderJob(job);
      await refreshJobs();
      if (job.status === "done" || job.status === "failed") {
        clearInterval(jobPollTimer);
        jobPollTimer = null;
      }
    } catch (error) {
      $("jobStatus").textContent = String(error);
    }
  }, 2500);
}

async function refreshJobs() {
  const data = await fetchJson("/api/jobs?limit=10");
  $("jobList").innerHTML = data.jobs.map((job) => `
    <button type="button" class="job-item ${job.job_id === activeJobId ? "active" : ""}" onclick="selectJob('${job.job_id}')">
      <strong>${escapeHtml(job.status)}</strong>
      <span>${escapeHtml(job.source?.name || job.job_id)}</span>
      <small>${escapeHtml(job.updated_at || "")}</small>
    </button>
  `).join("") || "<div class=\"hint\">최근 작업이 없습니다.</div>";
}

async function selectJob(jobId) {
  activeJobId = jobId;
  const job = await fetchJson(`/api/jobs/${encodeURIComponent(jobId)}`);
  renderJob(job);
  if (job.status === "queued" || job.status === "running") {
    startJobPolling(jobId);
  }
  await refreshJobs();
}

function renderJob(job) {
  const sampledCount = job.video_info?.sampled_frame_count ?? 0;
  $("jobStatus").textContent = `작업 ${job.job_id} / 상태: ${job.status} / ${job.message || ""}`;
  $("analyzeStatus").textContent = `현재 작업: ${job.job_id} (${job.status})`;
  $("frames").innerHTML = (job.frames || []).map((frame) => `
    <div class="frame-card">
      <img src="${frame.preview_url}" alt="sample frame ${frame.index}" />
      <div>#${frame.index} / ${Number(frame.timestamp_sec || 0).toFixed(2)}초</div>
    </div>
  `).join("");
  if (job.status === "done") {
    $("answer").textContent = job.answer || "(응답 텍스트 없음)";
  } else if (job.status === "failed") {
    $("answer").textContent = job.error?.message || job.message || "분석 실패";
  } else {
    $("answer").textContent = `분석 진행 중입니다. 추출된 프레임: ${sampledCount}개`;
  }
  $("rawJson").textContent = JSON.stringify(job, null, 2);
}

function clearResult() {
  $("jobStatus").textContent = "작업을 준비 중입니다.";
  $("answer").textContent = "";
  $("rawJson").textContent = "";
  $("frames").innerHTML = "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#039;");
}

$("analyzeForm").addEventListener("submit", submitAnalysis);
refreshRuntime();
refreshJobs();
