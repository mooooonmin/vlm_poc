"""
vLLM/Qwen 요청에 사용할 프롬프트와 응답 후처리 유틸리티입니다.

이 파일은 FastAPI 라우팅이나 job 실행 로직을 알 필요가 없습니다.
사용자 분석 요청을 질문 유형별로 분류하고, 해당 유형에 맞는 내부 출력 규격을 vLLM payload에 합성합니다.
"""

from __future__ import annotations

import json
import re
from typing import Any


# vLLM 요청의 system 메시지로 들어가는 기본 역할 지시입니다.
# system 메시지는 사용자가 입력한 질문보다 상위 지시로 취급되므로,
# "항상 한국어", "보이는 내용만 사용", "반복 금지"처럼 모든 질문에 공통으로 적용할 규칙을 둡니다.
KOREAN_SYSTEM_PROMPT = (
    "너는 영상 관제 PoC의 한국어 분석 도우미다. "
    "모든 답변은 반드시 한국어로만 작성한다. "
    "다른 언어, 번역문, 설명용 영어 문장을 섞지 않는다. "
    "영상에서 보이는 내용만 근거로 시간 순서대로 간결하게 요약한다. "
    "같은 문장을 반복하지 않는다."
)

# 사용자가 화면의 분석 요청 칸을 비워둔 경우 사용할 기본 질문입니다.
# 화면 입력값이 비어 있어도 vLLM에는 반드시 텍스트 프롬프트가 필요하므로 fallback 역할을 합니다.
DEFAULT_USER_REQUEST = "이 영상에서 발생한 주요 상황을 시간 순서대로 요약해줘."

# 모든 질문 유형에 공통으로 붙이는 안전 규칙입니다.
# 이 규칙은 모델이 샘플 프레임에 없는 내용을 만들어내거나, 사고/위반을 과하게 단정하는 것을 줄이기 위한 장치입니다.
COMMON_OUTPUT_RULES = (
    "공통 규칙:\n"
    "- 샘플 프레임에서 직접 보이는 근거만 사용\n"
    "- 아무 단서도 없을 때만 '확인 불가'라고 작성\n"
    "- 화면 제목, 자막, 표지판, 차량 움직임처럼 직접 보이는 단서가 있으면 그 단서를 기준으로 답할 것\n"
    "- 사고, 충돌, 위반은 명확한 시각 근거가 있을 때만 작성\n"
    "- 운전자 조작 오류, 과속, 신호위반, 부주의 같은 사고 원인은 화면만으로 단정하지 말 것\n"
    "- '답변:' 줄은 반드시 1개만 작성\n"
    "- '확인 불가'와 구체 답변을 동시에 쓰지 말 것\n"
    "- 내부 규칙 문장을 답변에 복사하지 말 것\n"
    "- '사용자 분석', '분석한 결과' 같은 메타 설명을 쓰지 말 것\n"
    "- 같은 문장 반복 금지, 같은 의미를 다른 표현으로 반복하지 말 것\n"
    "- 근거 프레임은 필요한 경우에만 최대 3개까지 작성\n"
    "- 전체 5줄 이내"
)

# 사용자 질문 유형별로 vLLM에 전달할 출력 규격입니다.
# 예를 들어 "몇 초에 나와?"와 "무슨 영상이야?"는 원하는 답변 모양이 다르므로 같은 프롬프트를 쓰면 정확도가 떨어집니다.
# 이 사전은 질문 유형마다 필요한 답변 형식과 주의사항만 좁혀서 전달하기 위해 사용합니다.
QUESTION_TYPE_PROMPTS = {
    "time": (
        "질문 유형: 시간 질문\n"
        "답변 형식:\n"
        "답변: 약 N초 또는 답변: 확인 불가\n"
        "근거: 프레임 #n, N초에서 보이는 내용\n"
        "주의: N은 샘플 프레임 시간표에 있는 가장 가까운 초를 사용"
    ),
    "video_type": (
        "질문 유형: 영상 종류 질문\n"
        "답변 형식:\n"
        "답변: ○○ 영상으로 보입니다 또는 답변: 확인 불가\n"
        "근거: 화면 제목, 자막, 장소, 사물, 행위 중 직접 보이는 단서\n"
        "주의: 화면 제목에 영상 종류가 적혀 있으면 그 텍스트를 중요한 근거로 사용\n"
        "주의: 사고/충돌 영상으로 단정하려면 충돌, 파손, 정차, 화재 등 명확한 장면이 필요"
    ),
    "object_presence": (
        "질문 유형: 객체 존재 질문\n"
        "답변 형식:\n"
        "답변: 보임 / 보이지 않음 / 확인 불가\n"
        "근거: 해당 객체가 보이는 프레임 번호와 초\n"
        "주의: 일부만 보이면 '부분적으로 보임'이라고 작성"
    ),
    "count": (
        "질문 유형: 수량 질문\n"
        "답변 형식:\n"
        "답변: 약 N개 또는 답변: 확인 불가\n"
        "근거: 가장 잘 보이는 프레임 번호와 초\n"
        "주의: 샘플 프레임 기준 추정값임을 밝힐 것"
    ),
    "incident": (
        "질문 유형: 사건/위험 판단 질문\n"
        "답변 형식:\n"
        "답변: 사고/위험으로 보임 또는 답변: 단정 불가\n"
        "근거: 충돌, 파손, 급정지, 역주행, 보행자 위험 등 직접 보이는 단서\n"
        "주의: 단순 정체나 근접 주행만으로 사고라고 단정하지 말 것"
    ),
    "location": (
        "질문 유형: 위치/장소 질문\n"
        "답변 형식:\n"
        "답변: ○○로 보입니다 또는 답변: 확인 불가\n"
        "근거: 도로, 터널, 차선, 표지판, 주변 구조물 등 직접 보이는 단서"
    ),
    "summary": (
        "질문 유형: 요약 질문\n"
        "답변 형식:\n"
        "요약: 전체 상황 1문장\n"
        "주요 장면:\n"
        "- 시간 순서대로 최대 3개\n"
        "주의: 화면 제목이나 자막이 보이면 요약에 반영\n"
        "주의: 사고 원인은 추정하지 말고 보이는 차량 움직임과 장면 변화만 요약"
    ),
    "general": (
        "질문 유형: 일반 질문\n"
        "답변 형식:\n"
        "답변: 질문에 대한 짧은 답\n"
        "근거: 관련 프레임 번호와 초\n"
        "주의: 질문에 직접 관련된 내용만 작성"
    ),
}

# 한국어 비율 검사에서 실패했을 때, 같은 멀티모달 요청을 한 번 더 보낼 때 붙이는 보강 문장입니다.
# 모델이 화면의 영어 텍스트를 그대로 답하거나 영어 설명을 섞는 경우가 있어, 재요청 때 한국어 조건을 더 강하게 줍니다.
KOREAN_RETRY_PROMPT_PREFIX = (
    "이전 응답이 한국어가 아니면 실패로 간주된다. "
    "반드시 한국어 문장으로만 답하라. "
)

# 모델이 충분한 프레임을 받고도 `답변: 확인 불가` 한 줄로 끝내는 경우가 있습니다.
# 이 재요청 문장은 "없는 사실을 만들라"가 아니라, 화면에 직접 보이는 텍스트/장면 단서를 놓치지 말라는 보정입니다.
LOW_INFORMATION_RETRY_PROMPT_PREFIX = (
    "이전 응답은 정보가 부족했다. "
    "확인 불가 한 줄로 끝내지 말고, 샘플 프레임에 직접 보이는 화면 제목, 자막, 도로, 차량, 움직임을 근거로 답하라. "
    "사고 원인은 단정하지 말되, 화면에 표시된 제목과 보이는 장면 변화는 요약하라. "
)

# 재요청 후에도 한국어 답변이 아니면, 이미 받은 모델 응답을 텍스트-only 요청으로 한국어 정리합니다.
# 이 단계는 새 영상 분석이 아니라 "기존 응답을 한국어로 정리"하는 복구 단계입니다.
KOREAN_REPAIR_PROMPT = (
    "아래 모델 원문 응답을 한국어 한 문장으로 바꿔라. "
    "원문에 보이는 텍스트나 상황만 사용하고, 새로운 사실을 만들지 마라.\n\n"
    "모델 원문 응답:\n{answer}"
)


def classify_question_type(user_request: str) -> str:
    """
    사용자의 분석 요청을 답변 목적별로 분류합니다.

    vLLM에 모든 규칙을 한 번에 보내면 모델이 질문과 상관없는 형식을 섞거나,
    "몇 초" 질문에 요약을 답하는 것처럼 초점이 흐려질 수 있습니다.
    그래서 먼저 질문 유형을 좁힌 뒤 해당 유형의 출력 규격만 payload에 넣습니다.
    """
    text = user_request.strip()

    # 분류 순서는 중요합니다.
    # "노란색 트럭은 몇 초에 나와?"처럼 객체 단어와 시간 단어가 함께 있는 질문은
    # 객체 존재 질문이 아니라 시간 질문으로 처리해야 하므로 time을 가장 먼저 검사합니다.
    if re.search(r"(몇\s*초|몇초|몇\s*분|몇분|언제|시점)", text):
        return "time"

    # "무슨 영상" 계열은 영상 전체의 종류를 묻는 질문입니다.
    # 사고/충돌 여부는 직접 보이는 근거가 없으면 단정하지 않도록 별도 출력 규격을 사용합니다.
    if re.search(r"(어떤\s*영상|무슨\s*영상|뭐.*영상|영상.*뭐|영상.*종류|영상.*내용)", text):
        return "video_type"

    # 수량 질문은 "몇 대", "몇 개"처럼 숫자 답변을 기대합니다.
    # 샘플링된 프레임만 보는 PoC라서 정확한 전체 영상 카운트가 아니라 "샘플 기준 추정"임을 답변에 남기게 합니다.
    if re.search(r"(몇\s*대|몇\s*개|몇\s*명|몇명|수량|개수|대수)", text):
        return "count"

    # 사건/위험 판단은 모델이 실제로 보이지 않는 사고를 만들어낼 위험이 큰 유형입니다.
    # 충돌, 파손, 급정지 같은 직접 근거가 없으면 단정하지 않는 규격을 적용합니다.
    if re.search(r"(사고|충돌|위험|위반|문제|고장|정체|역주행|급정거|급정지)", text):
        return "incident"

    # 위치/장소 질문은 도로, 터널, 차선, 표지판 같은 공간 단서를 중심으로 답변하게 합니다.
    if re.search(r"(어디|위치|장소|차선|터널|도로|방향)", text):
        return "location"

    # 특정 객체가 보이는지 묻는 질문입니다.
    # 트럭, 차량, 사람 같은 단어가 포함되면 해당 객체의 존재 여부와 근거 프레임을 답하게 합니다.
    if re.search(r"(보여|보이나|보이|있어|있나|나와|나오|등장|트럭|차량|자동차|사람|보행자|버스|오토바이|자전거)", text):
        return "object_presence"

    # 명시적으로 요약/정리/설명을 요구하면 시간 순서 요약 규격을 사용합니다.
    if re.search(r"(요약|정리|설명|상황|전체)", text):
        return "summary"

    # 위 규칙에 걸리지 않는 질문은 일반 질문으로 처리합니다.
    # 일반 질문은 답변을 짧게 하고, 관련 프레임 근거를 함께 쓰게 합니다.
    return "general"


def build_vllm_payload(
    model_id: str,
    prompt: str,
    sampled_frames: list[dict[str, Any]],
    max_tokens: int,
    strict_korean: bool = False,
    low_information_retry: bool = False,
) -> dict[str, Any]:
    """추출 프레임들을 vLLM OpenAI 호환 멀티이미지 요청 형식으로 변환합니다."""
    user_request = prompt.strip() or DEFAULT_USER_REQUEST

    # 샘플 프레임의 번호와 시간을 텍스트로 함께 보냅니다.
    # 모델은 이미지 자체만 보면 "몇 초"인지 알 수 없으므로, 시간 질문 정확도를 위해 이 표가 필요합니다.
    frame_timeline = "\n".join(
        f"- 프레임 #{frame['index']}: {float(frame.get('timestamp_sec') or 0):.2f}초"
        for frame in sampled_frames
    )

    # 사용자 질문을 먼저 분류한 뒤 해당 유형의 규격만 선택합니다.
    # 모든 규격을 한꺼번에 넣으면 모델이 질문과 관계없는 섹션을 섞어 답하는 문제가 생길 수 있습니다.
    question_type = classify_question_type(user_request)
    type_prompt = QUESTION_TYPE_PROMPTS.get(question_type, QUESTION_TYPE_PROMPTS["general"])

    # vLLM에는 최종적으로 하나의 텍스트 프롬프트와 여러 장의 이미지가 함께 전달됩니다.
    # 이 텍스트에는 사용자 요청, 프레임 시간표, 질문 유형별 출력 규격, 공통 안전 규칙이 포함됩니다.
    composed_prompt = (
        f"사용자 분석 요청:\n{user_request}\n\n"
        f"질문 유형:\n{question_type}\n\n"
        f"샘플 프레임 시간표:\n{frame_timeline}\n\n"
        f"질문 유형별 출력 규격:\n{type_prompt}\n\n"
        f"{COMMON_OUTPUT_RULES}"
    )

    # strict_korean은 한국어 검사 실패 후 재요청할 때만 True로 들어옵니다.
    # 같은 질문이라도 두 번째 요청에서는 한국어 조건을 더 앞에 붙여 모델의 출력 언어를 강하게 제한합니다.
    prefixes = []
    if strict_korean:
        prefixes.append(KOREAN_RETRY_PROMPT_PREFIX)
    if low_information_retry:
        prefixes.append(LOW_INFORMATION_RETRY_PROMPT_PREFIX)
    final_prompt = "".join(prefixes) + composed_prompt

    # OpenAI 호환 chat/completions 멀티모달 형식입니다.
    # 첫 content는 텍스트 지시, 이후 content들은 base64 data URL 이미지입니다.
    content: list[dict[str, Any]] = [{"type": "text", "text": final_prompt}]
    for frame in sampled_frames:
        content.append({"type": "image_url", "image_url": {"url": frame["data_url"]}})

    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": KOREAN_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "frequency_penalty": 0.4,
        "presence_penalty": 0.1,
    }


def build_text_only_payload(model_id: str, prompt: str, max_tokens: int) -> dict[str, Any]:
    """이미지 없이 vLLM에 텍스트 정리/번역 요청을 보낼 때 사용하는 payload입니다."""
    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": KOREAN_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "frequency_penalty": 0.4,
        "presence_penalty": 0.1,
    }


def extract_answer(vllm_response: dict[str, Any]) -> str:
    """vLLM 응답에서 화면에 표시할 assistant 메시지를 추출합니다."""
    try:
        content = vllm_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, indent=2)


def normalize_answer_text(answer: str) -> str:
    """
    화면에 표시할 VLM 응답에서 반복 줄만 제거합니다.

    모델이 같은 문장을 번호만 바꿔 반복하는 경우가 있어, 번호 접두사를 제외한 본문이 같은 줄은 1번만 남깁니다.
    새로운 사실을 만들지 않고 중복 줄만 제거합니다.
    """
    cleaned_lines = []
    seen_normalized = set()

    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if is_internal_prompt_artifact(line):
            continue

        # "1. 같은 문장", "2. 같은 문장"처럼 번호만 달라진 반복을 제거하기 위해
        # 번호/불릿 접두사를 뺀 본문을 기준으로 중복 여부를 판단합니다.
        normalized = re.sub(r"^\s*[-*\d.)]+\s*", "", line)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = collapse_repeated_phrases(normalized)
        if normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        if re.match(r"^\s*\d+[.)]\s+", line):
            cleaned_lines.append(f"- {normalized}")
        else:
            cleaned_lines.append(collapse_repeated_phrases(line))
    return "\n".join(cleaned_lines[:6]) if cleaned_lines else answer.strip()


def is_internal_prompt_artifact(line: str) -> bool:
    """
    모델이 내부 프롬프트 규칙을 복사한 줄인지 판정합니다.

    Qwen 계열 VLM은 가끔 답변 대신 `답변 형식`, `주의`, `공통 규칙` 같은 지시문을 그대로 출력합니다.
    이런 줄은 분석 결과가 아니므로 화면에 표시하지 않고, 실제 관찰 문장만 남깁니다.
    """
    stripped = line.strip()
    internal_rule_prefixes = (
        "시간 질문 대응:",
        "영상 종류 질문 대응:",
        "시간 질문 전용 지시:",
        "영상 종류 질문 전용 지시:",
        "질문 유형:",
        "질문 유형별 출력 규격:",
        "공통 규칙:",
        "답변 형식:",
        "규칙:",
        "내부 출력 규격:",
        "사용자 분석 요청:",
        "주의:",
    )
    if stripped.startswith(internal_rule_prefixes):
        return True

    artifact_phrases = (
        "○○",
        "또는 답변:",
        "보임 / 보이지 않음 / 확인 불가",
        "화면 제목, 자막, 장소, 사물, 행위 중 직접 보이는 단서",
        "샘플 프레임에서 직접 보이는 근거만 사용",
        "아무 단서도 없을 때만",
        "직접 보이는 단서가 있으면",
        "사고/충돌 영상으로 단정",
        "사고, 충돌, 위반은 명확한 시각 근거",
        "운전자 조작 오류, 과속, 신호위반",
        "'답변:' 줄은 반드시",
        "'확인 불가'와 구체 답변",
        "내부 규칙 문장을 답변에 복사",
        "사용자 분석",
        "분석한 결과",
        "같은 문장 반복 금지",
        "근거 프레임은 필요한 경우",
        "전체 5줄 이내",
    )
    return any(phrase in stripped for phrase in artifact_phrases)


def collapse_repeated_phrases(text: str) -> str:
    """
    한 줄 안에서 같은 구절이 반복되는 응답을 줄입니다.

    줄 단위 중복 제거만으로는 "A 후, A 후, A 후"처럼 한 문장 안에서 반복되는 구절을 제거할 수 없습니다.
    이 함수는 새 정보를 만들지 않고, 같은 쉼표 단위 구절이 연속해서 반복될 때 첫 구절만 남깁니다.
    """
    parts = [part.strip() for part in re.split(r"(,|，|、)", text) if part.strip() and part not in {",", "，", "、"}]
    if len(parts) < 3:
        return text

    compact_parts: list[str] = []
    last_normalized = ""
    for part in parts:
        normalized = re.sub(r"\s+", " ", part).strip()
        normalized = re.sub(r"[.。]$", "", normalized)
        if normalized == last_normalized:
            continue
        compact_parts.append(part)
        last_normalized = normalized

    if len(compact_parts) == len(parts):
        return text
    return ", ".join(compact_parts)


def refine_time_question_answer(answer: str, user_request: str) -> str:
    """
    시간 질문 답변의 첫 줄을 보정합니다.

    모델이 본문에는 "프레임 #2: 1.00초에 ..."처럼 시간을 적고도 첫 줄에 "확인 불가"라고 쓰는 경우가 있습니다.
    이 함수는 모델 응답 안에 이미 존재하는 시간 근거만 사용해 첫 줄을 `답변: 약 N초`로 맞춥니다.
    """
    if classify_question_type(user_request) != "time":
        return answer

    stopwords = {"몇초", "몇", "초", "초에", "나와", "나오", "보여", "보이", "언제", "시간", "시점", "텍스트"}
    keywords = []
    for token in re.findall(r"[가-힣A-Za-z0-9-]{2,}", user_request):
        cleaned = re.sub(r"(은|는|이|가|을|를|에|에서)$", "", token)
        if cleaned and cleaned not in stopwords:
            keywords.append(cleaned)
    lines = answer.splitlines()
    selected_time = None
    for line in lines:
        if len(keywords) >= 2 and not all(keyword in line for keyword in keywords):
            continue
        if len(keywords) == 1 and keywords[0] not in line:
            continue
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*초", line)
        if match:
            selected_time = match.group(1)
            break

    if not selected_time:
        fallback_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*초", answer)
        if fallback_match:
            selected_time = fallback_match.group(1)

    if not selected_time:
        return answer

    first_line = f"답변: 약 {selected_time}초"
    rest = []
    for line in lines:
        if line.startswith("답변:"):
            continue
        if selected_time in line and not line.startswith("근거:"):
            rest.append(f"근거: {line}")
        else:
            rest.append(line)
    return "\n".join([first_line, *rest])


def refine_video_type_answer(answer: str, user_request: str) -> str:
    """
    영상 종류 질문에서 모델이 예시 placeholder를 그대로 복사한 경우를 보수적으로 보정합니다.

    `○○ 영상`은 프롬프트의 형식 예시일 뿐 실제 관찰 결과가 아니므로, 그대로 화면에 보여주면
    사용자가 모델이 영상을 분류했다고 오해할 수 있습니다. 이 경우 새 사실을 만들지 않고 첫 줄만
    `답변: 확인 불가`로 바꿉니다.
    """
    if classify_question_type(user_request) != "video_type":
        return answer
    lines = consolidate_answer_lines(answer).splitlines()
    if not lines:
        return answer
    if not any(line.strip().startswith("답변:") for line in lines):
        inferred = infer_video_type_from_evidence("\n".join(lines))
        if inferred:
            return "\n".join([f"답변: {inferred} 영상으로 보입니다.", *lines])
    first_line = lines[0].strip()
    if first_line.startswith("답변:") and "확인 불가" not in first_line and "영상" not in first_line:
        answer_text = first_line.split(":", 1)[1].strip()
        rest = [line for line in lines[1:] if line.strip()]
        return "\n".join([f"답변: {answer_text} 영상으로 보입니다.", *rest])
    if first_line.startswith("답변:") and "사고 의심" in first_line:
        rest_text = "\n".join(lines[1:])
        if any(phrase in rest_text for phrase in ("명확한 시각적 증거는 없음", "명확한 장면은 없음", "일반적인 교통 상황")):
            rest = [line for line in lines[1:] if line.strip()]
            return "\n".join(["답변: 고속도로 교통 상황 영상으로 보입니다.", *rest])
    if "○○" not in first_line:
        return "\n".join(lines)
    rest = [line for line in lines[1:] if line.strip()]
    return "\n".join(["답변: 확인 불가", *rest])


def infer_video_type_from_evidence(text: str) -> str:
    """
    영상 종류 질문에서 모델이 `답변:` 없이 근거만 쓴 경우 보수적인 유형명을 만듭니다.

    새 사실을 만들지 않기 위해 근거 문장에 이미 들어 있는 장소/객체 단어만 사용합니다.
    """
    no_clear_incident = any(phrase in text for phrase in ("명확한 시각적 증거는 없음", "명확한 장면은 없음", "일반적인 교통 상황"))
    if "고속도로" in text and ("충돌" in text or "사고" in text) and not no_clear_incident:
        return "고속도로 차량 사고 의심"
    if "교차로" in text and ("충돌" in text or "사고" in text) and not no_clear_incident:
        return "교차로 차량 사고 의심"
    if "고속도로" in text:
        return "고속도로 교통 상황"
    if "교차로" in text:
        return "교차로 교통 상황"
    if "터널" in text:
        return "터널 교통 상황"
    if "트럭" in text or "차량" in text:
        return "차량 주행 상황"
    return ""


def remove_unsupported_cause_claims(answer: str) -> str:
    """
    프레임만으로 확인하기 어려운 사고 원인 추정 절을 제거합니다.

    vLLM은 "운전자 조작 오류", "사고 원인으로 판단"처럼 영상만으로 단정하기 어려운 문구를
    요약에 섞을 수 있습니다. 화면에는 관찰 가능한 장면을 우선 보여줘야 하므로,
    원인/책임을 판단하는 절만 제거하고 나머지 장면 설명은 유지합니다.
    """
    cleaned_lines: list[str] = []
    unsupported_terms = r"(운전자|조작\s*오류|과속|신호\s*위반|부주의|사고\s*원인|원인으로\s*판단)"
    removed_summary = False
    for line in answer.splitlines():
        cleaned = re.sub(rf",?\s*[^.\n。]*{unsupported_terms}[^.\n。]*(?:[.。]|$)", "", line).strip()
        cleaned = re.sub(r"(발생한 것으로|나타난 것으로)$", r"\1 보입니다.", cleaned)
        cleaned = re.sub(r"일반적인\s+교통\s+상$", "일반적인 교통 상황으로 보입니다.", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            cleaned_lines.append(cleaned)
        elif line.strip().startswith("요약:"):
            removed_summary = True
    if removed_summary and cleaned_lines:
        cleaned_lines.insert(0, "요약: 프레임에서 확인되는 주요 장면 변화는 아래와 같습니다.")
    return "\n".join(cleaned_lines)


def remove_conflicting_incident_claims(answer: str) -> str:
    """
    같은 답변 안에서 사고 단정과 사고 증거 없음이 동시에 나온 경우 단정 문구를 낮춥니다.

    예: "충돌하는 장면이 나타남"과 "충돌이나 사고의 명확한 시각적 증거는 없음"이 함께 있으면
    앞의 단정 문구를 제거하고, 보수적인 증거 없음 문장만 남깁니다.
    """
    if not any(phrase in answer for phrase in ("명확한 시각적 증거는 없음", "명확한 장면은 없음")):
        return answer

    cleaned_lines: list[str] = []
    for line in answer.splitlines():
        cleaned = re.sub(r"또한\s*[^.\n。]*(충돌|사고)[^.\n。]*(나타나고 있다|보인다|발생)[^.\n。]*(?:[.。]|$)", "", line).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)


def consolidate_answer_lines(answer: str) -> str:
    """
    모델이 `답변:` 줄을 여러 개 출력한 경우 하나만 남깁니다.

    실제 응답에서 `답변: 확인 불가` 다음 줄에 `답변: 교통사고`처럼 서로 충돌하는 결과가 함께
    나오는 경우가 있습니다. 사용자는 최종 판단 하나만 봐야 하므로, 구체 답변이 있으면
    `확인 불가`보다 구체 답변을 우선하고 나머지 `답변:` 줄은 제거합니다.
    """
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    answer_lines = [line for line in lines if line.startswith("답변:")]
    if len(answer_lines) <= 1:
        return "\n".join(lines)

    specific_answers = [line for line in answer_lines if "확인 불가" not in line and "단정 불가" not in line]
    selected_answer = specific_answers[0] if specific_answers else answer_lines[0]
    output = [selected_answer]
    output.extend(line for line in lines if not line.startswith("답변:"))
    return "\n".join(output)


def refine_question_specific_answer(answer: str, user_request: str) -> str:
    """질문 유형별로 화면 표시용 답변 형식을 마지막으로 보정합니다."""
    refined = consolidate_answer_lines(answer)
    refined = refine_time_question_answer(refined, user_request)
    refined = refine_video_type_answer(refined, user_request)
    refined = remove_unsupported_cause_claims(refined)
    refined = remove_conflicting_incident_claims(refined)
    return refined


def should_retry_low_information_answer(answer: str, user_request: str) -> bool:
    """
    `확인 불가` 한 줄로 끝난 응답을 같은 프레임으로 한 번 더 물어볼지 판단합니다.

    시간 질문처럼 대상 시점이 정말 안 보일 수 있는 질문은 재요청하지 않습니다.
    반대로 요약/영상 종류/일반 질문은 화면 제목이나 장면 단서만으로도 최소 설명이 가능할 수 있어
    1회 재요청 대상이 됩니다.
    """
    question_type = classify_question_type(user_request)
    if question_type not in {"summary", "video_type", "general", "incident", "location"}:
        return False
    compact = re.sub(r"\s+", "", answer)
    return compact in {"답변:확인불가", "확인불가", "답변:단정불가", "단정불가"}
