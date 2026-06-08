let activeJobId = null;
let activeBatchId = null;
let jobPollTimer = null;
let batchPollTimer = null;

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
  const [gpu, vllm, timeslicing, config, workers] = await Promise.all([
    fetchJson("/api/gpu-status"),
    fetchJson("/api/vllm-status"),
    fetchJson("/api/timeslicing"),
    fetchJson("/api/config"),
    fetchJson("/api/workers/refresh", { method: "POST" }),
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
    `vLLM worker: ${(workers.workers || []).length}개`,
  ].map((text) => `<span>${escapeHtml(text)}</span>`).join("");
  renderWorkers(workers.workers || []);
  $("runtimeDetail").textContent = JSON.stringify({ gpu, vllm, timeslicing, config, workers }, null, 2);
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

async function refreshWorkers() {
  const data = await fetchJson("/api/workers/refresh", { method: "POST" });
  renderWorkers(data.workers || []);
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  $("runtimeStatus").textContent = "vLLM worker 상태를 새로고침했습니다.";
}

function renderWorkers(workers) {
  $("workerPanel").innerHTML = `
    <div class="worker-strip">
      <strong>vLLM Worker</strong>
      ${workers.map((worker) => `
        <div class="worker-chip">
          <span class="check-status ${escapeHtml(worker.status || "unknown")}">${escapeHtml(worker.status || "-")}</span>
          <b>${escapeHtml(worker.worker_id || "-")}</b>
          <code>${escapeHtml(worker.endpoint || "-")}</code>
          <span>job: ${escapeHtml(worker.active_job_id || "-")}</span>
          <span>오류: ${escapeHtml(worker.last_error || "-")}</span>
        </div>
      `).join("") || `<span class="hint">등록된 worker가 없습니다.</span>`}
    </div>
  `;
}

async function collectTimeslicingLogs() {
  $("runtimeStatus").textContent = "time-slicing 관련 Kubernetes/GPU 로그를 수집 중입니다.";
  const data = await fetchJson("/api/timeslicing/logs", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  renderTimeslicingResult(data);
  $("runtimeStatus").textContent =
    `time-slicing 로그 수집 완료: ${data.overall_status || "상태 없음"} / ${data.log_dir || "로그 경로 없음"}`;
}

function renderTimeslicingResult(data) {
  const checks = data.checks || [];
  $("timeslicingResult").innerHTML = `
    <div class="check-header">
      <strong>Time-slicing 검증 결과: ${escapeHtml(data.overall_status || "unknown")}</strong>
      <span>run_id: ${escapeHtml(data.run_id || "-")}</span>
    </div>
    <div class="hint">summary.md: ${escapeHtml(data.summary_md_path || "-")}</div>
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

async function submitAnalysis(event) {
  event.preventDefault();
  const button = $("analyzeBtn");
  button.disabled = true;
  $("analyzeStatus").textContent = "영상 분석 batch를 생성하는 중입니다.";
  clearResult();

  try {
    const formData = new FormData(event.target);
    const batch = await fetchJson("/api/jobs/video-batch", { method: "POST", body: formData });
    activeBatchId = batch.batch_id;
    activeJobId = batch.jobs?.[0]?.job_id || batch.created_jobs?.[0]?.job_id || null;
    $("analyzeStatus").textContent = `batch 생성 완료: ${activeBatchId}`;
    renderBatch(batch);
    if (activeJobId) {
      const firstJob = (batch.jobs || batch.created_jobs || []).find((job) => job.job_id === activeJobId);
      if (firstJob) {
        renderJob(firstJob);
      }
    }
    await refreshJobs();
    startBatchPolling(activeBatchId);
  } catch (error) {
    $("analyzeStatus").textContent = "batch 생성 실패";
    $("answer").textContent = String(error);
  } finally {
    button.disabled = false;
  }
}

function startBatchPolling(batchId) {
  if (!batchId) {
    return;
  }
  if (batchPollTimer) {
    clearInterval(batchPollTimer);
  }
  if (jobPollTimer) {
    clearInterval(jobPollTimer);
    jobPollTimer = null;
  }
  batchPollTimer = setInterval(async () => {
    try {
      const batch = await fetchJson(`/api/batches/${encodeURIComponent(batchId)}`);
      renderBatch(batch);
      const selectedJob = (batch.jobs || []).find((job) => job.job_id === activeJobId) || batch.jobs?.[0];
      if (selectedJob) {
        activeJobId = selectedJob.job_id;
        renderJob(selectedJob);
      }
      await refreshJobs();
      if (batch.status === "done" || batch.status === "failed") {
        clearInterval(batchPollTimer);
        batchPollTimer = null;
      }
    } catch (error) {
      $("jobStatus").textContent = String(error);
    }
  }, 2500);
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
  const [data, stats] = await Promise.all([
    fetchJson("/api/jobs?limit=10"),
    fetchJson("/api/jobs/stats?limit=50"),
  ]);
  renderJobStats(stats);
  $("jobList").innerHTML = data.jobs.map((job) => `
    <button type="button" class="job-item ${job.job_id === activeJobId ? "active" : ""}" onclick="selectJob('${job.job_id}')">
      <strong>${escapeHtml(job.status)}</strong>
      <span>${escapeHtml(job.source?.name || job.job_id)}</span>
      <small>${escapeHtml(job.updated_at || "")}</small>
    </button>
  `).join("") || "<div class=\"hint\">최근 작업이 없습니다.</div>";
}

async function refreshEvaluations() {
  const data = await fetchJson("/api/evaluations?limit=10");
  renderEvaluations(data.evaluations || []);
}

function renderEvaluations(evaluations) {
  $("evaluationList").innerHTML = evaluations.map((evaluation) => {
    const fallbackRate = evaluation.korean_fallback_rate != null
      ? `${Math.round(Number(evaluation.korean_fallback_rate) * 1000) / 10}%`
      : evaluation.sample_count
      ? `${Math.round((Number(evaluation.korean_fallback_count || 0) / Number(evaluation.sample_count)) * 1000) / 10}%`
      : "-";
    return `
      <div class="evaluation-item">
        <div>
          <strong>${escapeHtml(evaluation.run_id || "-")}</strong>
          <span>${escapeHtml(evaluation.started_at || "-")} - ${escapeHtml(evaluation.finished_at || "-")}</span>
        </div>
        <div class="stat-grid compact">
          <div><strong>${Number(evaluation.sample_count || 0)}</strong><span>샘플</span></div>
          <div><strong>${Number(evaluation.success_count || 0)}</strong><span>성공</span></div>
          <div><strong>${evaluation.success_rate == null ? "-" : `${Math.round(Number(evaluation.success_rate) * 1000) / 10}%`}</strong><span>성공률</span></div>
          <div><strong>${evaluation.average_duration_ms == null ? "-" : `${evaluation.average_duration_ms}ms`}</strong><span>평균</span></div>
          <div><strong>${fallbackRate}</strong><span>fallback</span></div>
        </div>
        <details>
          <summary>리포트 경로와 원본 JSON</summary>
          <pre>${escapeHtml(JSON.stringify(evaluation, null, 2))}</pre>
        </details>
      </div>
    `;
  }).join("") || "<div class=\"hint\">저장된 평가 리포트가 없습니다.</div>";
}

function renderJobStats(stats) {
  const status = stats.status_counts || {};
  const workers = stats.worker_counts || {};
  const failures = stats.failure_counts || {};
  const workerText = Object.entries(workers)
    .map(([worker, count]) => `${worker}: ${count}`)
    .join(" / ") || "-";
  const failureText = Object.entries(failures)
    .map(([reason, count]) => `${reason}: ${count}`)
    .join(" / ") || "-";

  $("jobStats").innerHTML = `
    <div class="stat-grid">
      <div><strong>${Number(stats.total || 0)}</strong><span>최근 job</span></div>
      <div><strong>${Number(status.done || 0)}</strong><span>성공</span></div>
      <div><strong>${Number(status.failed || 0)}</strong><span>실패</span></div>
      <div><strong>${stats.average_duration_ms == null ? "-" : `${stats.average_duration_ms}ms`}</strong><span>평균 처리시간</span></div>
      <div><strong>${Number(stats.korean_ok_count || 0)}</strong><span>한국어 통과</span></div>
      <div><strong>${Number(stats.korean_retry_count || 0)}</strong><span>한국어 재요청</span></div>
      <div><strong>${Number(stats.korean_repair_count || 0)}</strong><span>한국어 복구</span></div>
      <div><strong>${Number(stats.korean_fallback_count || 0)}</strong><span>한국어 fallback</span></div>
      <div><strong>${Number(stats.gpu_snapshot_job_count || 0)}</strong><span>GPU 로그 job</span></div>
    </div>
    <div class="hint">worker별 처리: ${escapeHtml(workerText)}</div>
    <div class="hint">실패 원인: ${escapeHtml(failureText)}</div>
  `;
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

async function selectBatchJob(jobId) {
  await selectJob(jobId);
  if (activeBatchId) {
    const batch = await fetchJson(`/api/batches/${encodeURIComponent(activeBatchId)}`);
    renderBatch(batch);
  }
}

function renderBatch(batch) {
  const counts = batch.status_counts || {};
  $("batchPanel").innerHTML = `
    <div class="batch-summary">
      <strong>Batch ${escapeHtml(batch.batch_id || "-")}</strong>
      <div>${Number(batch.finished || 0)} / ${Number(batch.total || 0)} 완료 · 상태: ${escapeHtml(batch.status || "-")}</div>
      <div class="hint">done ${Number(counts.done || 0)} / running ${Number(counts.running || 0)} / queued ${Number(counts.queued || 0)} / failed ${Number(counts.failed || 0)}</div>
    </div>
    <div class="batch-jobs">
      ${(batch.jobs || []).map((job) => `
        <button type="button" class="batch-job ${job.job_id === activeJobId ? "active" : ""}" onclick="selectBatchJob('${job.job_id}')">
          <strong>${escapeHtml(job.batch_index || "-")}. ${escapeHtml(job.status || "-")}</strong>
          <span>${escapeHtml(job.source?.name || job.job_id)}</span>
          <small>${escapeHtml(job.worker_id ? ` / ${job.worker_id}` : "")}</small>
        </button>
      `).join("")}
    </div>
  `;
}

function renderJob(job) {
  const sampledCount = job.video_info?.sampled_frame_count ?? 0;
  const workerText = job.worker_id ? ` / worker: ${job.worker_id}` : "";
  const durationText = job.duration_ms == null ? "" : ` / ${job.duration_ms}ms`;
  $("jobStatus").textContent = `작업 ${job.job_id} / 상태: ${job.status}${workerText}${durationText} / ${job.message || ""}`;
  $("analyzeStatus").textContent = `현재 작업: ${job.job_id} (${job.status})`;
  $("frames").innerHTML = (job.frames || []).map((frame) => `
    <div class="frame-card">
      <img src="${frame.preview_url}" alt="sample frame ${frame.index}" />
      <div>#${frame.index} / ${Number(frame.timestamp_sec || 0).toFixed(2)}초</div>
    </div>
  `).join("");
  if (job.status === "done") {
    $("answer").textContent = `${job.worker_id ? `[${job.worker_id}] ${job.worker_endpoint || ""}\n` : ""}${formatLoopChecks(job)}\n${formatJobTiming(job)}\n${job.answer || "(응답 텍스트 없음)"}`;
  } else if (job.status === "failed") {
    $("answer").textContent = `${formatLoopChecks(job)}\n${formatJobTiming(job)}\n실패 단계: ${job.failure_stage || "-"}\n실패 원인: ${job.failure_reason || "-"}\n${job.error?.message || job.message || "분석 실패"}`;
  } else {
    $("answer").textContent = `분석 진행 중입니다. 추출된 프레임: ${sampledCount}개${job.worker_id ? `\n배정 worker: ${job.worker_id}` : ""}`;
  }
  $("rawJson").textContent = JSON.stringify(job, null, 2);
}

function formatLoopChecks(job) {
  const checks = job.loop_checks || {};
  const korean = job.korean_check || {};
  const gpuCount = Array.isArray(job.gpu_snapshots) ? job.gpu_snapshots.length : 0;
  const retry = job.korean_retry_used ? "사용" : "미사용";
  const repair = job.korean_repair_used ? "사용" : "미사용";
  const fallback = job.korean_fallback_used ? "사용" : "미사용";
  return [
    "[루프 점검]",
    `1 한국어 응답: ${checks["1_korean_response"] || "-"} (한글 ${korean.hangul_count ?? "-"}자, 비율 ${korean.hangul_ratio ?? "-"})`,
    `2 실제 영상/통계: ${checks["2_real_video_stats"] || "-"}`,
    `3 GPU 스냅샷: ${checks["3_gpu_snapshot"] || "-"} (${gpuCount}개)`,
    `4 Worker 배정: ${checks["4_worker_assignment"] || "-"} (${job.worker_id || "-"})`,
    `한국어 재요청: ${retry}`,
    `한국어 복구: ${repair}`,
    `한국어 fallback: ${fallback}`,
  ].join("\n");
}

function formatJobTiming(job) {
  const parts = [];
  if (job.duration_ms != null) {
    parts.push(`전체: ${job.duration_ms}ms`);
  }
  if (job.frame_extract_duration_ms != null) {
    parts.push(`프레임 추출: ${job.frame_extract_duration_ms}ms`);
  }
  if (job.vllm_duration_ms != null) {
    parts.push(`vLLM 요청: ${job.vllm_duration_ms}ms`);
  }
  return parts.length ? `[처리시간] ${parts.join(" / ")}` : "[처리시간] 아직 기록 없음";
}

function clearResult() {
  $("jobStatus").textContent = "작업을 준비 중입니다.";
  $("batchPanel").innerHTML = "";
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
refreshEvaluations();
