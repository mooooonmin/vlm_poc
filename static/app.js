async function refreshRuntime() {
  const [gpu, vllm, timeslicing] = await Promise.all([
    fetch("/api/gpu-status").then((response) => response.json()),
    fetch("/api/vllm-status").then((response) => response.json()),
    fetch("/api/timeslicing").then((response) => response.json()),
  ]);
  const vllmState = vllm.running
    ? "실행 중 - Qwen 모델이 GPU 메모리를 점유하고 있습니다. 테스트가 끝나면 vLLM 종료 / GPU 해제를 누르세요."
    : "중지됨 - GPU 모델 서버가 떠 있지 않습니다.";
  document.getElementById("runtimeStatus").textContent =
    `GPU: ${gpu.ok ? "확인됨" : "확인 실패"} / vLLM: ${vllmState} ${vllm.message || ""}`;
  document.getElementById("runtimeDetail").textContent =
    JSON.stringify({ gpu, vllm, timeslicing }, null, 2);
}

async function startVllm() {
  document.getElementById("runtimeStatus").textContent =
    "vLLM 컨테이너 시작 요청 중... 모델이 GPU에 로드되면 VRAM을 계속 점유합니다.";
  const response = await fetch("/api/start-vllm", { method: "POST" });
  const data = await response.json();
  document.getElementById("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  if (!data.ok) {
    document.getElementById("runtimeStatus").textContent = data.message || "vLLM 시작 실패";
    return;
  }
  document.getElementById("runtimeStatus").textContent =
    "vLLM 컨테이너 시작 완료. 모델 다운로드/로딩 상태를 자동 확인합니다...";
  await waitForVllmReady();
}

async function waitForVllmReady() {
  const maxAttempts = 120;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const [gpu, vllm, timeslicing] = await Promise.all([
      fetch("/api/gpu-status").then((response) => response.json()),
      fetch("/api/vllm-status").then((response) => response.json()),
      fetch("/api/timeslicing").then((response) => response.json()),
    ]);
    document.getElementById("runtimeDetail").textContent =
      JSON.stringify({ gpu, vllm, timeslicing }, null, 2);
    if (vllm.running) {
      document.getElementById("runtimeStatus").textContent =
        "vLLM 준비 완료 - Qwen 모델이 GPU 메모리를 점유하고 있습니다. 이제 영상 분석을 실행할 수 있습니다.";
      return;
    }
    document.getElementById("runtimeStatus").textContent =
      `vLLM 로딩 대기 중 (${attempt}/${maxAttempts}) - 첫 실행은 이미지/모델 다운로드 때문에 오래 걸릴 수 있습니다.`;
  }
  document.getElementById("runtimeStatus").textContent =
    "vLLM 준비 확인 시간이 초과되었습니다. CUDA / vLLM 상세의 Docker logs를 확인하세요.";
}

async function stopVllm() {
  document.getElementById("runtimeStatus").textContent =
    "vLLM 컨테이너 종료 요청 중... 완료되면 Qwen 모델이 내려가고 GPU 메모리가 반환됩니다.";
  const response = await fetch("/api/stop-vllm", { method: "POST" });
  const data = await response.json();
  document.getElementById("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  await refreshRuntime();
}

async function collectTimeslicingLogs() {
  document.getElementById("runtimeStatus").textContent =
    "time-slicing 관련 Kubernetes/GPU 로그를 수집 중입니다...";
  const response = await fetch("/api/timeslicing/logs", { method: "POST" });
  const data = await response.json();
  document.getElementById("runtimeDetail").textContent = JSON.stringify(data, null, 2);
  document.getElementById("runtimeStatus").textContent =
    `time-slicing 로그 수집 완료: ${data.log_dir || "로그 경로 없음"}`;
}

document.getElementById("analyzeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("analyzeBtn");
  const status = document.getElementById("analyzeStatus");
  const answer = document.getElementById("answer");
  const rawJson = document.getElementById("rawJson");
  const frames = document.getElementById("frames");

  button.disabled = true;
  status.textContent = "영상 저장, 프레임 추출, vLLM 분석 요청을 진행 중입니다...";
  answer.textContent = "";
  rawJson.textContent = "";
  frames.innerHTML = "";

  try {
    const formData = new FormData(event.target);
    const response = await fetch("/api/analyze-video", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || data.error || "분석 요청 실패");
    }
    status.textContent = `완료: ${data.video_info.sampled_frame_count}개 프레임 분석`;
    answer.textContent = data.answer || "(응답 텍스트 없음)";
    rawJson.textContent = JSON.stringify(data, null, 2);
    frames.innerHTML = data.frames.map((frame) => `
      <div class="frame-card">
        <img src="${frame.preview_url}" alt="sample frame ${frame.index}" />
        <div>#${frame.index} / ${frame.timestamp_sec.toFixed(2)}초</div>
      </div>
    `).join("");
  } catch (error) {
    status.textContent = "오류 발생";
    answer.textContent = String(error);
  } finally {
    button.disabled = false;
  }
});

refreshRuntime();
