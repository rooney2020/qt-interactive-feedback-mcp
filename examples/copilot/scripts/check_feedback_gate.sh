#!/usr/bin/env bash
set -euo pipefail

payload_file="$(mktemp)"
trap 'rm -f "$payload_file"' EXIT

cat > "$payload_file"

python3 - "$payload_file" <<'PY'
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path


ALLOWED_FEEDBACK_TOOL_NAMES = {
  "mcp_interactive-f_interactive_feedback",
  "vscode_askQuestions",
}


def emit(payload: dict) -> None:
  print(json.dumps(payload, ensure_ascii=False))


def format_stop_reason(reason: str, stop_hook_active: bool) -> str:
  if stop_hook_active:
    return f"{reason} 当前 Stop 已处于续跑轮次，请立即调用 interactive_feedback，并等待用户明确选择继续或结束，避免再次直接收尾。"
  return f"{reason} 请立即调用 interactive_feedback，并等待用户明确选择继续或结束。"


def emit_stop_block(event_name: str, reason: str, stop_hook_active: bool) -> None:
  formatted_reason = format_stop_reason(reason, stop_hook_active)
  emit(
    {
      "hookSpecificOutput": {
        "hookEventName": event_name,
        "decision": "block",
        "reason": formatted_reason,
      },
      "systemMessage": formatted_reason,
    }
  )


def preview_text(text: str, limit: int = 160) -> str:
  compact = " ".join(text.split())
  if len(compact) <= limit:
    return compact
  return compact[: limit - 1] + "..."


def audit_log_path(workspace_cwd: str) -> Path:
  state_root = os.environ.get("XDG_STATE_HOME")
  if state_root:
    base_dir = Path(state_root)
  else:
    base_dir = Path.home() / ".local" / "state"
  workspace_key = hashlib.sha256(workspace_cwd.encode("utf-8", errors="ignore")).hexdigest()[:16]
  log_dir = base_dir / "copilot-feedback-hook" / workspace_key
  log_dir.mkdir(parents=True, exist_ok=True)
  return log_dir / "feedback-gate.audit.jsonl"


def append_audit_record(log_file: Path, record: dict) -> None:
  with log_file.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_transcript_lines(transcript_path: Path) -> list[dict]:
  lines = []
  for raw_line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
    stripped = raw_line.strip()
    if not stripped:
      continue
    lines.append(json.loads(stripped))
  if not lines:
    raise ValueError("transcript 没有可解析的 JSON 行")
  return lines


def is_event_stream_transcript(lines: list[dict]) -> bool:
  return any(isinstance(line, dict) and isinstance(line.get("type"), str) for line in lines[:10])


def merge_requests(lines: list[dict]) -> list[dict]:
  requests: list[dict] = []

  for line in lines:
    if line.get("kind") == 0 and isinstance(line.get("v"), dict) and isinstance(line["v"].get("requests"), list):
      requests = copy.deepcopy(line["v"]["requests"])
      continue

    path = line.get("k")
    if not isinstance(path, list) or not path or path[0] != "requests":
      continue

    if len(path) < 3 or not isinstance(path[1], int):
      continue

    idx = path[1]
    while len(requests) <= idx:
      requests.append({})
    if not isinstance(requests[idx], dict):
      requests[idx] = {}

    if len(path) == 3:
      requests[idx][path[2]] = line.get("v")

  return requests


def latest_request_text(request: dict) -> str:
  if not isinstance(request, dict):
    return ""
  message = request.get("message")
  if not isinstance(message, dict):
    return ""
  text = message.get("text")
  return text if isinstance(text, str) else ""


def walk_nodes(node):
  if isinstance(node, list):
    for item in node:
      yield from walk_nodes(item)
    return
  if isinstance(node, dict):
    yield node
    for value in node.values():
      yield from walk_nodes(value)


def response_contains_feedback(response) -> bool:
  for node in walk_nodes(response):
    tool_id = node.get("toolId")
    if isinstance(tool_id, str) and tool_id in ALLOWED_FEEDBACK_TOOL_NAMES:
      return True
  return False


def feedback_result_texts(response) -> list[str]:
  texts: list[str] = []
  for node in walk_nodes(response):
    tool_id = node.get("toolId")
    if not isinstance(tool_id, str) or tool_id not in ALLOWED_FEEDBACK_TOOL_NAMES:
      continue
    result_details = node.get("resultDetails")
    if not isinstance(result_details, dict):
      continue
    output = result_details.get("output")
    if not isinstance(output, list):
      continue
    for item in output:
      if isinstance(item, dict) and isinstance(item.get("value"), str):
        texts.append(item["value"])
  return texts


def summarize_response_tail(response) -> tuple[bool, str, list[str]]:
  last_preview = ""
  last_tools: list[str] = []
  last_has_feedback = False

  for node in walk_nodes(response):
    tool_id = node.get("toolId")
    if isinstance(tool_id, str):
      if tool_id not in last_tools:
        last_tools.append(tool_id)
      if tool_id in ALLOWED_FEEDBACK_TOOL_NAMES:
        last_has_feedback = True

    text = node.get("text")
    if isinstance(text, str) and text.strip():
      last_preview = preview_text(text)

  return last_has_feedback, last_preview, last_tools


END_PATTERNS = [
  r"(^|\b)(结束|结束会话|可以了|先这样|不用了|完成了)(\b|$)",
  r"【结束会话】",
]


def user_explicitly_wants_to_end(text: str) -> bool:
  normalized = " ".join((text or "").split())
  if not normalized:
    return False
  return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in END_PATTERNS)


def derive_chat_session_path(transcript_path: Path) -> Path | None:
  try:
    workspace_storage_dir = transcript_path.parents[2]
  except IndexError:
    return None

  candidate = workspace_storage_dir / "chatSessions" / f"{transcript_path.stem}.jsonl"
  if candidate.exists():
    return candidate
  return None


def extract_state_from_event_stream(lines: list[dict]) -> tuple[object, str, bool, bool, str, list[str], str]:
  latest_request_id = None
  latest_text = ""
  request_feedback_called = False
  last_assistant_had_feedback = False
  last_assistant_preview = ""
  last_assistant_tool_names: list[str] = []
  last_assistant_state_source = "none"

  for line in lines:
    if not isinstance(line, dict):
      continue

    event_type = line.get("type")
    data = line.get("data")
    if not isinstance(data, dict):
      continue

    if event_type == "user.message":
      latest_request_id = line.get("id")
      latest_text = data.get("content") if isinstance(data.get("content"), str) else ""
      request_feedback_called = False
      last_assistant_had_feedback = False
      last_assistant_preview = ""
      last_assistant_tool_names = []
      continue

    if event_type != "assistant.message":
      continue

    tool_requests = data.get("toolRequests")
    tool_names = [
      tool_request.get("name")
      for tool_request in tool_requests
      if isinstance(tool_request, dict) and isinstance(tool_request.get("name"), str)
    ] if isinstance(tool_requests, list) else []
    message_has_feedback = any(tool_name in ALLOWED_FEEDBACK_TOOL_NAMES for tool_name in tool_names)
    if message_has_feedback:
      request_feedback_called = True

    content = data.get("content")
    message_preview = preview_text(content) if isinstance(content, str) and content.strip() else ""
    if tool_names or message_preview:
      last_assistant_had_feedback = message_has_feedback
      last_assistant_preview = message_preview
      last_assistant_tool_names = tool_names
      last_assistant_state_source = "transcript"

  return (
    latest_request_id,
    latest_text,
    request_feedback_called,
    last_assistant_had_feedback,
    last_assistant_preview,
    last_assistant_tool_names,
    last_assistant_state_source,
  )


def enrich_state_from_chat_session(
  transcript_path: Path,
  latest_request_id: object,
  latest_text: str,
  request_feedback_called: bool,
  end_confirmed: bool,
  last_assistant_had_feedback: bool,
  last_assistant_preview: str,
  last_assistant_tool_names: list[str],
  last_assistant_state_source: str,
) -> tuple[object, str, bool, bool, bool, str, list[str], str]:
  chat_session_path = derive_chat_session_path(transcript_path)
  if chat_session_path is None:
    return (
      latest_request_id,
      latest_text,
      request_feedback_called,
      end_confirmed,
      last_assistant_had_feedback,
      last_assistant_preview,
      last_assistant_tool_names,
      last_assistant_state_source,
    )

  try:
    chat_session_lines = load_transcript_lines(chat_session_path)
    requests = merge_requests(chat_session_lines)
    latest_request = requests[-1]
  except Exception:
    return (
      latest_request_id,
      latest_text,
      request_feedback_called,
      end_confirmed,
      last_assistant_had_feedback,
      last_assistant_preview,
      last_assistant_tool_names,
      last_assistant_state_source,
    )

  latest_request_id = latest_request.get("requestId") if isinstance(latest_request, dict) else latest_request_id
  chat_session_text = latest_request_text(latest_request)
  latest_response = latest_request.get("response", []) if isinstance(latest_request, dict) else []

  if chat_session_text:
    latest_text = chat_session_text

  request_feedback_called = request_feedback_called or response_contains_feedback(latest_response)
  response_last_assistant_had_feedback, response_last_assistant_preview, response_last_assistant_tool_names = summarize_response_tail(
    latest_response
  )
  if (not last_assistant_preview and not last_assistant_tool_names) and (
    response_last_assistant_preview or response_last_assistant_tool_names
  ):
    last_assistant_had_feedback = response_last_assistant_had_feedback
    last_assistant_preview = response_last_assistant_preview
    last_assistant_tool_names = response_last_assistant_tool_names
    last_assistant_state_source = "chatSession"

  end_confirmed = end_confirmed or user_explicitly_wants_to_end(chat_session_text)
  if not end_confirmed:
    for result_text in feedback_result_texts(latest_response):
      if user_explicitly_wants_to_end(result_text):
        end_confirmed = True
        break

  return (
    latest_request_id,
    latest_text,
    request_feedback_called,
    end_confirmed,
    last_assistant_had_feedback,
    last_assistant_preview,
    last_assistant_tool_names,
    last_assistant_state_source,
  )


def main() -> int:
  payload_path = Path(sys.argv[1])
  payload = json.loads(payload_path.read_text(encoding="utf-8"))

  event_name = payload.get("hookEventName", "Stop")
  workspace_cwd = payload.get("cwd") or os.getcwd()
  transcript_path_str = payload.get("transcriptPath")
  stop_hook_active = bool(payload.get("stop_hook_active"))

  if not transcript_path_str:
    emit({"continue": True})
    return 0

  transcript_path = Path(transcript_path_str)
  if not transcript_path.exists():
    emit({"continue": True})
    return 0

  lines = load_transcript_lines(transcript_path)
  if not is_event_stream_transcript(lines):
    emit({"continue": True})
    return 0

  (
    latest_request_id,
    latest_text,
    request_feedback_called,
    last_assistant_had_feedback,
    last_assistant_preview,
    last_assistant_tool_names,
    last_assistant_state_source,
  ) = extract_state_from_event_stream(lines)

  end_confirmed = user_explicitly_wants_to_end(latest_text)
  (
    latest_request_id,
    latest_text,
    request_feedback_called,
    end_confirmed,
    last_assistant_had_feedback,
    last_assistant_preview,
    last_assistant_tool_names,
    last_assistant_state_source,
  ) = enrich_state_from_chat_session(
    transcript_path,
    latest_request_id,
    latest_text,
    request_feedback_called,
    end_confirmed,
    last_assistant_had_feedback,
    last_assistant_preview,
    last_assistant_tool_names,
    last_assistant_state_source,
  )

  allow = request_feedback_called and end_confirmed
  if allow:
    result = {"continue": True}
    decision = "allow"
    reason = "本轮已完成 feedback 闭环，且用户明确要求结束。"
  else:
    decision = "block"
    if not request_feedback_called:
      reason = "本轮尚未调用 interactive_feedback 或 AskQuestion。"
    elif not end_confirmed:
      reason = "用户尚未明确要求结束，本轮不能直接收尾。"
    else:
      reason = "反馈闸门检查未通过。"
    emit_stop_block(event_name, reason, stop_hook_active)
    result = None

  record = {
    "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    "event": event_name,
    "decision": decision,
    "reason": reason,
    "requestId": latest_request_id,
    "requestFeedbackCalled": request_feedback_called,
    "lastAssistantHadFeedback": last_assistant_had_feedback,
    "lastAssistantPreview": last_assistant_preview,
    "lastAssistantToolNames": last_assistant_tool_names,
    "lastAssistantStateSource": last_assistant_state_source,
    "endConfirmed": end_confirmed,
    "userTextPreview": preview_text(latest_text),
    "transcriptPath": str(transcript_path),
  }
  append_audit_record(audit_log_path(workspace_cwd), record)

  if result is not None:
    emit(result)

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
PY