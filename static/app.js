let activeJobId = null;
let activeBatchId = null;
let jobPollTimer = null;
let batchPollTimer = null;

// 서버가 반환하는 vLLM lifecycle 값을 화면 표시용 한국어 문구로 바꿉니다.
// 실제 판정은 backend의 /api/vllm-status에서 하고, 프론트엔드는 표시만 담당합니다.
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

// 상단 런타임 카드에 필요한 상태를 한 번에 갱신합니다.
// GPU, vLLM, time-slicing, 앱 설정, worker readiness를 병렬 조회해 화면의 현재 환경 상태를 맞춥니다.
async function refreshRuntime() {
  const [gpu, vllm, timeslicing, config, workers] = await Promise.all([
    fetchJson("/api/gpu-status"),
    fetchJson("/api/vllm-status"),
    fetchJson("/api/timeslicing"),
    fetchJson("/api/config"),
    fetchJson("/api/workers/refresh", { method: "POST" }),
  ]);

  const lifecycle = lifecycleLabels[vllm.lifecycle_stage] || vllm.lifecycle_stage || "상태 불명";
  $("runtimeStatus").textContent = `GPU ${gpu.ok ? "정상" : "확인 실패"} · vLLM ${lifecycle}`;
  $("runtimeBadges").innerHTML = [
    `모델: ${config.default_model_id}`,
    `worker ${(workers.workers || []).length}개`,
    config.processing_mode,
  ].map((text) => `<span>${escapeHtml(text)}</span>`).join("");
  applyConfigToForm(config);
  renderWorkers(workers.workers || []);
  $("runtimeDetail").textContent = JSON.stringify({ gpu, vllm, timeslicing, config, workers }, null, 2);
}

// 서버 설정값을 화면 입력 범위에 반영합니다.
// MAX_SAMPLE_FRAMES를 환경변수로 바꾼 경우에도 사용자가 실제 허용 범위를 화면에서 알 수 있게 합니다.
function applyConfigToForm(config) {
  const frameInput = $("frameCount");
  if (!frameInput || !config) {
    return;
  }
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
}

async function startVllm() {
  $("runtimeStatus").textContent = "vLLM 시작 중";
  const data = await fetchJson("/api/start-vllm", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  await waitForVllmReady();
}

// vLLM 컨테이너 시작은 Docker image pull, 모델 다운로드, 모델 로딩 때문에 오래 걸릴 수 있습니다.
// 그래서 버튼 클릭 요청은 바로 반환하고, 화면은 3초마다 /api/vllm-status를 polling해 API ready 여부를 확인합니다.
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
  $("runtimeStatus").textContent = "vLLM 종료 중";
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

async function refreshWorkers() {
  const data = await fetchJson("/api/workers/refresh", { method: "POST" });
  renderWorkers(data.workers || []);
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  $("runtimeStatus").textContent = "Worker 상태 갱신 완료";
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

// vLLM 로그는 JSON 안에 stdout/stderr가 들어가면 읽기 어렵습니다.
// 화면에는 사람이 바로 볼 수 있도록 stdout, stderr, 실행 명령을 분리해서 표시하고 원본 JSON은 아래 상세에 남깁니다.
function renderVllmLogs(data) {
  const stdout = data.stdout || "(stdout 없음)";
  const stderr = data.stderr || "(stderr 없음)";
  const command = Array.isArray(data.command) ? data.command.join(" ") : "-";
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
    <details class="log-command">
      <summary>실행 명령</summary>
      <code>${escapeHtml(command)}</code>
    </details>
  `;
}

async function collectTimeslicingLogs() {
  $("runtimeStatus").textContent = "Time-slicing 로그 수집 중";
  const data = await fetchJson("/api/timeslicing/logs", { method: "POST" });
  $("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  renderTimeslicingResult(data);
  $("runtimeStatus").textContent =
    `Time-slicing 로그 수집 완료: ${data.overall_status || "상태 없음"}`;
}

// time-slicing 검증은 로컬 Windows에서 실제 적용이 아니라 "검증 가능 여부와 실패 원인 기록"이 목적입니다.
// backend가 만든 checks 배열을 표로 보여주고, 원본 JSON은 runtime 상세 영역에 남깁니다.
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

// 분석 요청 제출 흐름입니다.
// 화면에는 영상 입력 슬롯이 3개 있지만, backend는 실제 입력된 슬롯만 job으로 만들고 하나의 batch_id로 묶습니다.
async function submitAnalysis(event) {
  event.preventDefault();
  const button = $("analyzeBtn");
  button.disabled = true;
  $("analyzeStatus").textContent = "분석 요청 중";
  clearResult();

  try {
    const formData = new FormData(event.target);
    const batch = await fetchJson("/api/jobs/video-batch", { method: "POST", body: formData });
    activeBatchId = batch.batch_id;
    activeJobId = batch.jobs?.[0]?.job_id || batch.created_jobs?.[0]?.job_id || null;
    $("analyzeStatus").textContent = `분석 시작: ${batch.total}개`;
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
    $("analyzeStatus").textContent = "분석 요청 실패";
    $("answer").textContent = String(error);
  } finally {
    button.disabled = false;
  }
}

// batch polling은 "한 번에 여러 영상"을 요청했을 때 전체 진행률을 갱신하는 루프입니다.
// batch 안의 각 job은 독립적으로 queued/running/done/failed가 되므로, 선택된 job 결과와 batch 요약을 함께 갱신합니다.
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

// 단일 job polling은 사용자가 최근 작업 목록에서 특정 job만 선택했을 때 사용합니다.
// batch polling과 동시에 돌면 화면 상태가 엇갈릴 수 있어 batch polling 시작 시 job polling을 중지합니다.
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

// 최근 job 목록과 통계를 갱신합니다.
// 사용자는 여기서 성공/실패 수, 평균 처리시간, worker별 처리 건수, 실패 원인을 빠르게 볼 수 있습니다.
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

// evaluation runner가 만든 summary.json 목록을 화면에 표시합니다.
// 실제 테스트 결과의 상세 근거는 docs/TEST_RESULTS.md와 logs/evaluation/{run_id} 파일을 기준으로 관리합니다.
async function refreshEvaluations() {
  const data = await fetchJson("/api/evaluations?limit=10");
  renderEvaluations(data.evaluations || []);
}

// 완료/실패한 job의 업로드/다운로드 영상, 추출 프레임, job.json, 자동 생성 로그를 정리합니다.
// backend는 queued/running job을 건너뛰므로 분석 중인 작업을 실수로 삭제하지 않습니다.
async function cleanupTmpFiles() {
  const confirmed = window.confirm("완료/실패한 작업의 임시 영상, 프레임, job 로그, 평가/time-slicing 로그를 정리할까요? 진행 중인 작업은 삭제하지 않습니다.");
  if (!confirmed) {
    return;
  }

  $("jobStatus").textContent = "임시파일 정리 중";
  try {
    if (batchPollTimer) {
      clearInterval(batchPollTimer);
      batchPollTimer = null;
    }
    if (jobPollTimer) {
      clearInterval(jobPollTimer);
      jobPollTimer = null;
    }
    const result = await fetchJson("/api/tmp/cleanup", { method: "POST" });
    activeJobId = null;
    activeBatchId = null;
    clearResult();
    await refreshJobs();
    const extraCount = Number(result.deleted_orphan_job_dir_count || 0)
      + Number(result.deleted_extra_dir_count || 0)
      + Number(result.deleted_extra_file_count || 0)
      + Number(result.deleted_log_dir_count || 0);
    $("jobStatus").textContent =
      `임시파일 정리 완료 · job ${result.deleted_job_count}개 · 프레임 ${result.deleted_frame_file_count}개 · 로그/기타 ${extraCount}개 · ${(Number(result.freed_bytes || 0) / 1024 / 1024).toFixed(1)}MB`;
  } catch (error) {
    $("jobStatus").textContent = `임시파일 정리 실패: ${String(error)}`;
  }
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
      <div><strong>${Number(stats.total || 0)}</strong><span>최근</span></div>
      <div><strong>${Number(status.done || 0)}</strong><span>성공</span></div>
      <div><strong>${Number(status.failed || 0)}</strong><span>실패</span></div>
      <div><strong>${stats.average_duration_ms == null ? "-" : `${stats.average_duration_ms}ms`}</strong><span>평균</span></div>
    </div>
    <details>
      <summary>상세 통계</summary>
      <div class="hint">worker: ${escapeHtml(workerText)}</div>
      <div class="hint">실패: ${escapeHtml(failureText)}</div>
      <div class="hint">한국어 통과 ${Number(stats.korean_ok_count || 0)} · 재요청 ${Number(stats.korean_retry_count || 0)} · fallback ${Number(stats.korean_fallback_count || 0)}</div>
    </details>
  `;
}

// 최근 작업 목록에서 job 하나를 클릭했을 때 호출됩니다.
// 진행 중인 job이면 polling을 다시 시작해 결과 화면이 자동으로 갱신되게 합니다.
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

// batch 요약 렌더링입니다.
// 여러 영상 요청에서는 사용자가 어떤 영상 job의 결과를 보고 있는지 알아야 하므로, 선택된 job을 active로 표시합니다.
function renderBatch(batch) {
  const counts = batch.status_counts || {};
  $("batchPanel").innerHTML = `
    <div class="batch-summary">
      <strong>${Number(batch.finished || 0)} / ${Number(batch.total || 0)} 완료</strong>
      <span>${escapeHtml(batch.status || "-")}</span>
      <small>성공 ${Number(counts.done || 0)} · 진행 ${Number(counts.running || 0)} · 대기 ${Number(counts.queued || 0)} · 실패 ${Number(counts.failed || 0)}</small>
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

// 단일 job 결과 렌더링입니다.
// 프레임 미리보기, 정리된 VLM 응답, job.json 로그 경로만 화면에 보여주고 원본 vLLM JSON은 파일에만 둡니다.
function renderJob(job) {
  const sampledCount = job.video_info?.sampled_frame_count ?? 0;
  const workerText = job.worker_id ? ` / worker: ${job.worker_id}` : "";
  const durationText = job.duration_ms == null ? "" : ` / ${job.duration_ms}ms`;
  const samplingText = formatSamplingSummary(job.video_info);
  $("jobStatus").textContent = `${job.source?.name || job.job_id} · ${job.status}${workerText}${durationText}${samplingText}`;
  $("analyzeStatus").textContent = `현재: ${job.status}`;
  renderVideoPreview(job);
  $("frames").innerHTML = (job.frames || []).map((frame) => `
    <div class="frame-card">
      <img src="${frame.preview_url}" alt="sample frame ${frame.index}" />
      <div>#${frame.index} / ${Number(frame.timestamp_sec || 0).toFixed(2)}초</div>
    </div>
  `).join("");
  if (job.status === "done") {
    $("answer").textContent = job.answer || "(응답 텍스트 없음)";
  } else if (job.status === "failed") {
    $("answer").textContent = `실패: ${job.failure_reason || job.error?.message || job.message || "분석 실패"}`;
  } else {
    $("answer").textContent = `분석 중 · 프레임 ${sampledCount}개`;
  }
  $("jobLogPath").textContent = job.job_dir ? `로그: ${job.job_dir}\\job.json` : "";
}

// 프레임 추출 방식은 분석 정확도와 직결되므로 결과 상단에 짧게 표시합니다.
// sampling_limited=true이면 긴 영상에서 사용자가 지정한 최대 프레임 수까지만 추출됐다는 뜻입니다.
function formatSamplingSummary(videoInfo) {
  if (!videoInfo?.sampling_strategy) {
    return "";
  }
  const count = videoInfo.sampled_frame_count ?? 0;
  const limit = videoInfo.requested_max_frames ?? "-";
  const limited = videoInfo.sampling_limited ? " / 상한 도달" : "";
  return ` / 1fps ${count}/${limit}장${limited}`;
}

// 업로드 파일이나 URL에서 다운로드된 원본 영상을 화면에서 바로 확인합니다.
// URL 영상은 다운로드가 끝난 뒤 source.path가 생기므로, queued/running 초반에는 안내 문구만 표시됩니다.
function renderVideoPreview(job) {
  const preview = $("videoPreview");
  if (!job.source?.path) {
    preview.className = "video-preview empty";
    preview.textContent = "영상 파일을 준비하는 중입니다.";
    return;
  }

  const videoUrl = `/api/jobs/${encodeURIComponent(job.job_id)}/video`;
  const sourceType = job.source?.type === "upload" ? "업로드 파일" : "URL 다운로드";
  const duration = job.video_info?.duration_sec == null ? "" : `<span>${Number(job.video_info.duration_sec).toFixed(2)}초</span>`;
  preview.className = "video-preview";
  preview.innerHTML = `
    <video controls preload="metadata" src="${videoUrl}"></video>
    <div class="video-meta">
      <span>${escapeHtml(sourceType)}</span>
      <span>${escapeHtml(job.source?.name || job.job_id)}</span>
      ${duration}
    </div>
  `;
}

// job.json에는 반복 테스트용 점검 항목이 저장됩니다.
// 현재 화면에서는 직접 표시하지 않지만, 디버깅 화면이나 향후 상세 패널에서 사용할 수 있도록 문자열 포맷을 유지합니다.
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

// job 단계별 처리시간을 사람이 읽기 쉬운 한 줄 문자열로 만듭니다.
// 전체 시간과 vLLM 요청 시간을 분리하면 지연 원인이 프레임 추출인지 모델 응답인지 구분할 수 있습니다.
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
  $("videoPreview").className = "video-preview empty";
  $("videoPreview").textContent = "아직 미리보기 가능한 영상이 없습니다.";
  $("answer").textContent = "";
  $("jobLogPath").textContent = "";
  $("frames").innerHTML = "";
}

// 서버 응답값을 HTML 문자열에 넣기 전에 escape합니다.
// 영상 URL, 모델 응답, 로그 문자열은 외부 입력일 수 있으므로 XSS 위험을 줄이기 위해 반드시 거칩니다.
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
