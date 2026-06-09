"""
채팅형 영상 분석 세션을 파일로 저장하는 간단한 저장소입니다.

이 프로젝트는 PoC 단계이므로 운영 DB를 사용하지 않습니다. 대신 대화 1개를
`tmp/conversations/{conversation_id}/conversation.json` 파일 1개로 저장합니다.
서버가 재시작되어도 최근 대화 목록을 다시 읽을 수 있고, 테스트 중 문제가 생기면
해당 JSON 파일만 열어 대화, 영상 source, 연결된 job_id를 확인할 수 있습니다.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


STORE_LOCK = threading.Lock()
CONVERSATIONS: dict[str, dict[str, Any]] = {}


def now_text() -> str:
    """화면과 로그에서 읽기 쉬운 현재 시각 문자열을 반환합니다."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def create_conversation(base_dir: Path, title: str | None = None) -> dict[str, Any]:
    """
    새 채팅 세션을 생성합니다.

    conversation_id는 URL/API에서 사용할 고유 ID입니다. 한 대화 세션에는 영상 1개만
    연결하고, 사용자는 그 영상에 대해 여러 질문을 이어서 보낼 수 있습니다.
    """
    conversation_id = f"conv_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    conversation_dir = _conversation_dir(base_dir, conversation_id)
    conversation_dir.mkdir(parents=True, exist_ok=True)

    conversation = {
        "conversation_id": conversation_id,
        "title": title.strip() if title else "새 영상 분석",
        "created_at": now_text(),
        "updated_at": now_text(),
        "conversation_dir": str(conversation_dir),
        "source": None,
        "job_ids": [],
        "messages": [],
    }
    _save_conversation(conversation)
    return dict(conversation)


def list_conversations(limit: int = 50) -> list[dict[str, Any]]:
    """최근 수정된 대화 목록을 반환합니다."""
    with STORE_LOCK:
        conversations = sorted(
            CONVERSATIONS.values(),
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )
        return [_summarize_conversation(item) for item in conversations[:limit]]


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    """conversation_id로 대화 전체 내용을 조회합니다."""
    with STORE_LOCK:
        conversation = CONVERSATIONS.get(conversation_id)
        return dict(conversation) if conversation else None


def set_conversation_video(conversation_id: str, source: dict[str, Any]) -> dict[str, Any]:
    """
    대화 세션에 분석 대상 영상을 연결합니다.

    source에는 업로드 파일 경로 또는 URL을 저장합니다. 실제 프레임 추출과 vLLM 호출은
    질문 메시지를 보낼 때 생성되는 job에서 수행합니다.
    """
    return update_conversation(conversation_id, source=source)


def append_job_id(conversation_id: str, job_id: str) -> dict[str, Any]:
    """대화와 내부 분석 job을 연결합니다."""
    with STORE_LOCK:
        conversation = _require_conversation(conversation_id)
        job_ids = list(conversation.get("job_ids") or [])
        if job_id not in job_ids:
            job_ids.append(job_id)
        conversation["job_ids"] = job_ids
        conversation["updated_at"] = now_text()
        _write_conversation_file(conversation)
        return dict(conversation)


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    status: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """
    대화에 메시지를 추가합니다.

    assistant 메시지는 job_id를 가질 수 있습니다. 이 경우 화면은 메시지를 polling하면서
    연결된 job 상태가 done/failed가 될 때까지 `분석 중` 상태로 표시합니다.
    """
    if role not in {"user", "assistant", "system"}:
        raise ValueError(f"지원하지 않는 메시지 role입니다: {role}")

    message = {
        "message_id": f"msg_{int(time.time())}_{uuid.uuid4().hex[:8]}",
        "role": role,
        "content": content,
        "status": status,
        "job_id": job_id,
        "created_at": now_text(),
        "updated_at": now_text(),
    }
    with STORE_LOCK:
        conversation = _require_conversation(conversation_id)
        messages = list(conversation.get("messages") or [])
        messages.append(message)
        conversation["messages"] = messages
        conversation["updated_at"] = now_text()
        if role == "user" and content.strip() and conversation.get("title") == "새 영상 분석":
            conversation["title"] = _title_from_question(content)
        _write_conversation_file(conversation)
        return dict(message)


def update_message(conversation_id: str, message_id: str, **changes: Any) -> dict[str, Any]:
    """기존 메시지의 상태, 답변 내용, 연결 job 정보를 갱신합니다."""
    with STORE_LOCK:
        conversation = _require_conversation(conversation_id)
        messages = list(conversation.get("messages") or [])
        for index, message in enumerate(messages):
            if message.get("message_id") == message_id:
                updated = dict(message)
                updated.update(changes)
                updated["updated_at"] = now_text()
                messages[index] = updated
                conversation["messages"] = messages
                conversation["updated_at"] = now_text()
                _write_conversation_file(conversation)
                return dict(updated)
    raise KeyError(f"없는 message_id입니다: {message_id}")


def update_conversation(conversation_id: str, **changes: Any) -> dict[str, Any]:
    """대화의 제목, 영상 source 같은 상위 필드를 갱신합니다."""
    with STORE_LOCK:
        conversation = _require_conversation(conversation_id)
        conversation.update(changes)
        conversation["updated_at"] = now_text()
        _write_conversation_file(conversation)
        return dict(conversation)


def load_existing_conversations(base_dir: Path) -> None:
    """서버 재시작 후 기존 conversation.json 파일을 메모리에 다시 로드합니다."""
    conversations_root = base_dir / "conversations"
    if not conversations_root.exists():
        return
    for conversation_file in conversations_root.glob("*/conversation.json"):
        try:
            conversation = json.loads(conversation_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(conversation, dict) and conversation.get("conversation_id"):
            with STORE_LOCK:
                CONVERSATIONS[str(conversation["conversation_id"])] = conversation


def _conversation_dir(base_dir: Path, conversation_id: str) -> Path:
    """대화 JSON과 업로드 영상을 저장할 폴더 경로를 만듭니다."""
    return base_dir / "conversations" / conversation_id


def _save_conversation(conversation: dict[str, Any]) -> None:
    """새 대화를 메모리와 파일에 동시에 저장합니다."""
    with STORE_LOCK:
        CONVERSATIONS[conversation["conversation_id"]] = conversation
        _write_conversation_file(conversation)


def _write_conversation_file(conversation: dict[str, Any]) -> None:
    """conversation.json을 UTF-8 JSON으로 저장합니다."""
    conversation_dir = Path(str(conversation["conversation_dir"]))
    conversation_dir.mkdir(parents=True, exist_ok=True)
    (conversation_dir / "conversation.json").write_text(
        json.dumps(conversation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _require_conversation(conversation_id: str) -> dict[str, Any]:
    """없는 conversation_id일 때 명확한 예외를 발생시킵니다."""
    if conversation_id not in CONVERSATIONS:
        raise KeyError(f"없는 conversation_id입니다: {conversation_id}")
    return CONVERSATIONS[conversation_id]


def _summarize_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    """목록 화면에 필요한 핵심 필드만 반환합니다."""
    messages = conversation.get("messages") or []
    last_message = messages[-1] if messages else None
    return {
        "conversation_id": conversation.get("conversation_id"),
        "title": conversation.get("title"),
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "source": conversation.get("source"),
        "job_count": len(conversation.get("job_ids") or []),
        "message_count": len(messages),
        "last_message": last_message,
    }


def _title_from_question(content: str) -> str:
    """첫 질문을 대화 제목으로 짧게 축약합니다."""
    title = " ".join(content.strip().split())
    if len(title) > 28:
        return f"{title[:28]}..."
    return title or "새 영상 분석"
