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
    "- 불확실하면 '확인 불가'라고 작성\n"
    "- 사고, 충돌, 위반은 명확한 시각 근거가 있을 때만 작성\n"
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
        "- 시간 순서대로 최대 3개"
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
    if re.search(r"(몇\s*초|몇초|언제|시간|시점)", text):
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
    final_prompt = f"{KOREAN_RETRY_PROMPT_PREFIX}{composed_prompt}" if strict_korean else composed_prompt

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

    # 모델이 내부 프롬프트 문장을 그대로 복사해 답변하는 경우가 있습니다.
    # 사용자는 내부 규칙을 볼 필요가 없으므로, 화면 표시 전에 이런 줄을 제거합니다.
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
    )
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(internal_rule_prefixes):
            continue
        if "사용자가 '몇 초'" in line or "사용자가 ‘몇 초’" in line:
            continue
        if "영상 종류 질문" in line:
            continue
        if "질문 유형별 출력 규격" in line:
            continue
        if "샘플 프레임에서 직접 보이는 근거만 사용" in line:
            continue
        if "사고/충돌/위반" in line:
            continue
        if "내부 출력 규격" in line:
            continue
        if "사용자 분석" in line or "사용자 分析" in line or "사용자 분析" in line:
            continue
        if "분석한 결과" in line or "分析" in line:
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
    lines = answer.splitlines()
    if not lines:
        return answer
    first_line = lines[0].strip()
    if "○○" not in first_line:
        return answer
    rest = [line for line in lines[1:] if line.strip()]
    return "\n".join(["답변: 확인 불가", *rest])


def refine_question_specific_answer(answer: str, user_request: str) -> str:
    """질문 유형별로 화면 표시용 답변 형식을 마지막으로 보정합니다."""
    refined = refine_time_question_answer(answer, user_request)
    refined = refine_video_type_answer(refined, user_request)
    return refined
