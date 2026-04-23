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

EXPLICIT_END_PHRASES = {
  "结束",
  "结束会话",
  "结束当前会话",
  "结束会话吧",
  "结束当前会话吧",
  "结束吧",
  "先这样结束",
  "先这样结束会话",
  "先这样结束当前会话",
  "先这样吧结束",
  "先这样吧结束会话",
  "先这样吧结束当前会话",
}

FEEDBACK_REMINDER_PREFIXES = (
  "必须完全使用中文",
  "请继续使用feedback和我沟通",
)


def emit(payload: dict) -> None:
  print(json.dumps(payload, ensure_ascii=False))


def emit_block(reason: str) -> None:
  emit(
    {
      "continue": False,
      "stopReason": reason,
      "systemMessage": reason,
    }
  )


def preview_text(text: str, limit: int = 160) -> str:
  compact = " ".join(text.split())
  if len(compact) <= limit:
    return compact
  return compact[: limit - 1] + "..."


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


def extract_event_stream_state(lines: list[dict]) -> tuple[object, str, bool, bool, str, list[str], str]:
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
    # Even an empty assistant.message is the newest assistant state and must
    # clear any earlier feedback-only state from the same turn.
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


def derive_chat_session_path(transcript_path: Path) -> Path | None:
  try:
    workspace_storage_dir = transcript_path.parents[2]
  except IndexError:
    return None

  candidate = workspace_storage_dir / "chatSessions" / f"{transcript_path.stem}.jsonl"
  if candidate.exists():
    return candidate
  return None


def enrich_event_stream_state_from_chat_session(
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

  latest_request = requests[-1]
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
      if user_explicitly_wants_to_end(extract_feedback_user_text(result_text)):
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


def merge_requests(lines: list[dict]) -> list[dict]:
  requests: list[dict] = []

  for line in lines:
    if line.get("kind") == 0 and isinstance(line.get("v"), dict) and isinstance(line["v"].get("requests"), list):
      requests = copy.deepcopy(line["v"]["requests"])
      continue

    path = line.get("k")
    if not isinstance(path, list) or not path or path[0] != "requests":
      continue

    value = line.get("v")
    if len(path) == 1 and isinstance(value, list):
      for item in value:
        if not isinstance(item, dict):
          continue
        request_id = item.get("requestId")
        replaced = False
        if request_id is not None:
          for index, existing in enumerate(requests):
            if isinstance(existing, dict) and existing.get("requestId") == request_id:
              requests[index] = copy.deepcopy(item)
              replaced = True
              break
        if not replaced:
          requests.append(copy.deepcopy(item))
      continue

    if len(path) >= 3 and isinstance(path[1], int):
      request_index = path[1]
      while len(requests) <= request_index:
        requests.append({})
      if not isinstance(requests[request_index], dict):
        requests[request_index] = {}
      requests[request_index][path[2]] = copy.deepcopy(value)

  if not requests:
    raise ValueError("transcript 中没有 requests")
  return requests


def latest_request_text(request: dict) -> str:
  message = request.get("message")
  if isinstance(message, dict) and isinstance(message.get("text"), str):
    return message["text"]
  return ""


def response_contains_feedback(response_items: object) -> bool:
  if isinstance(response_items, dict):
    if response_items.get("toolId") in ALLOWED_FEEDBACK_TOOL_NAMES:
      return True
    return any(response_contains_feedback(value) for value in response_items.values())

  if isinstance(response_items, list):
    return any(response_contains_feedback(item) for item in response_items)

  return False


def feedback_result_texts(response_items: object) -> list[str]:
  texts: list[str] = []

  if isinstance(response_items, list):
    for item in response_items:
      texts.extend(feedback_result_texts(item))
    return texts

  if not isinstance(response_items, dict):
    return texts

  if response_items.get("toolId") in ALLOWED_FEEDBACK_TOOL_NAMES:
    result_details = response_items.get("resultDetails")
    if isinstance(result_details, dict):
      outputs = result_details.get("output")
      if isinstance(outputs, list):
        for output in outputs:
          if isinstance(output, dict) and isinstance(output.get("value"), str):
            texts.append(output["value"])
    return texts

  for value in response_items.values():
    texts.extend(feedback_result_texts(value))
  return texts


def extract_feedback_user_text(result_text: str) -> str:
  raw_text = result_text

  try:
    payload = json.loads(result_text)
  except Exception:
    payload = None

  if isinstance(payload, dict):
    preferred_keys = ("interactive_feedback", "text", "message", "response")
    for key in preferred_keys:
      value = payload.get(key)
      if isinstance(value, str) and value.strip():
        raw_text = value
        break

  lines = []
  for line in raw_text.splitlines():
    stripped = line.strip()
    if not stripped:
      continue
    if any(stripped.startswith(prefix) for prefix in FEEDBACK_REMINDER_PREFIXES):
      continue
    lines.append(stripped)

  return lines[0] if lines else ""


def latest_feedback_user_text(response_items: object) -> str:
  for result_text in reversed(feedback_result_texts(response_items)):
    user_text = extract_feedback_user_text(result_text)
    if user_text:
      return user_text
  return ""


def summarize_response_tail(response_items: object) -> tuple[bool, str, list[str]]:
  last_assistant_had_feedback = False
  last_assistant_preview = ""
  last_assistant_tool_names: list[str] = []

  def walk(node: object) -> None:
    nonlocal last_assistant_had_feedback, last_assistant_preview, last_assistant_tool_names

    if isinstance(node, list):
      for item in node:
        walk(item)
      return

    if not isinstance(node, dict):
      return

    tool_id = node.get("toolId")
    if isinstance(tool_id, str):
      last_assistant_tool_names = [tool_id]
      last_assistant_had_feedback = tool_id in ALLOWED_FEEDBACK_TOOL_NAMES
      result_details = node.get("resultDetails")
      if isinstance(result_details, dict):
        outputs = result_details.get("output")
        if isinstance(outputs, list):
          for output in outputs:
            if isinstance(output, dict) and isinstance(output.get("value"), str) and output.get("value").strip():
              last_assistant_preview = preview_text(output["value"])
              break

    text = node.get("text")
    if isinstance(text, str) and text.strip():
      last_assistant_had_feedback = False
      last_assistant_preview = preview_text(text)
      last_assistant_tool_names = []

    for value in node.values():
      walk(value)

  walk(response_items)
  return last_assistant_had_feedback, last_assistant_preview, last_assistant_tool_names


def user_explicitly_wants_to_end(text: str) -> bool:
  if not text:
    return False

  if "【结束会话】" in text:
    return True

  normalized = re.sub(r"\s+", "", text).strip("。！？!?，,；;：: ")
  return normalized in EXPLICIT_END_PHRASES


def latest_feedback_user_text_from_transcript(transcript_path: Path) -> str:
  chat_session_path = derive_chat_session_path(transcript_path)
  if chat_session_path is None:
    return ""

  try:
    chat_session_lines = load_transcript_lines(chat_session_path)
    requests = merge_requests(chat_session_lines)
  except Exception:
    return ""

  latest_request = requests[-1]
  latest_response = latest_request.get("response", []) if isinstance(latest_request, dict) else []
  return latest_feedback_user_text(latest_response)


payload_path = Path(sys.argv[1])

try:
  hook_input = json.loads(payload_path.read_text(encoding="utf-8"))
except Exception as exc:  # pragma: no cover - hook fallback path
  emit_block(f"feedback gate 读取 Hook 输入失败: {exc}")
  sys.exit(0)

event_name = str(hook_input.get("hookEventName") or hook_input.get("hook_event_name") or "")
if event_name not in {"Stop", "sessionEnd"}:
  emit({"continue": True})
  sys.exit(0)

stop_hook_active = bool(hook_input.get("stop_hook_active"))
workspace_cwd = str(hook_input.get("cwd", ""))
log_file = audit_log_path(workspace_cwd)
transcript_path_value = hook_input.get("transcript_path")
if not isinstance(transcript_path_value, str) or not transcript_path_value:
  reason = "结束前无法读取 transcript，不能确认 feedback 与结束意图。请先调用 interactive_feedback；如不可用，可降级为 vscode_askQuestions。"
  append_audit_record(
    log_file,
    {
      "timestamp": hook_input.get("timestamp"),
      "event": event_name,
      "decision": "block",
      "reason": reason,
      "requestId": None,
      "feedbackCalled": False,
      "requestFeedbackCalled": False,
      "lastAssistantHadFeedback": False,
      "lastAssistantPreview": "",
      "lastAssistantToolNames": [],
      "lastAssistantStateSource": "none",
      "userTextPreview": "",
      "endConfirmed": False,
      "transcriptPath": None,
    },
  )
  emit_stop_block(event_name, reason, stop_hook_active)
  sys.exit(0)

try:
  transcript_path = Path(transcript_path_value)
  transcript_lines = load_transcript_lines(transcript_path)
  latest_feedback_reply_text = ""
  if is_event_stream_transcript(transcript_lines):
    (
      latest_request_id,
      latest_text,
      request_feedback_called,
      last_assistant_had_feedback,
      last_assistant_preview,
      last_assistant_tool_names,
      last_assistant_state_source,
    ) = extract_event_stream_state(transcript_lines)
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
    ) = enrich_event_stream_state_from_chat_session(
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
    latest_feedback_reply_text = latest_feedback_user_text_from_transcript(transcript_path)
  else:
    requests = merge_requests(transcript_lines)
    latest_request = requests[-1]
    latest_request_id = latest_request.get("requestId") if isinstance(latest_request, dict) else None
    latest_text = latest_request_text(latest_request)
    latest_response = latest_request.get("response", [])
    latest_feedback_reply_text = latest_feedback_user_text(latest_response)
    request_feedback_called = response_contains_feedback(latest_response)
    last_assistant_had_feedback, last_assistant_preview, last_assistant_tool_names = summarize_response_tail(latest_response)
    last_assistant_state_source = "chatSession" if (last_assistant_preview or last_assistant_tool_names) else "none"
    end_confirmed = user_explicitly_wants_to_end(latest_text)
    if not end_confirmed:
      for result_text in feedback_result_texts(latest_response):
        if user_explicitly_wants_to_end(extract_feedback_user_text(result_text)):
          end_confirmed = True
          break
except Exception as exc:
  reason = f"结束前解析 transcript 失败：{exc}。请先调用 interactive_feedback；如不可用，可降级为 vscode_askQuestions。"
  append_audit_record(
    log_file,
    {
      "timestamp": hook_input.get("timestamp"),
      "event": event_name,
      "decision": "block",
      "reason": reason,
      "requestId": None,
      "feedbackCalled": False,
      "requestFeedbackCalled": False,
      "lastAssistantHadFeedback": False,
      "lastAssistantPreview": "",
      "lastAssistantToolNames": [],
      "lastAssistantStateSource": "none",
      "userTextPreview": "",
      "endConfirmed": False,
      "transcriptPath": transcript_path_value,
    },
  )
  emit_stop_block(event_name, reason, stop_hook_active)
  sys.exit(0)

requires_end_confirmation = event_name == "sessionEnd"
feedback_gate_satisfied = request_feedback_called and last_assistant_had_feedback
feedback_reply_requires_follow_up = (
  event_name == "Stop"
  and last_assistant_had_feedback
  and bool(latest_feedback_reply_text)
  and not end_confirmed
)

if feedback_gate_satisfied and (end_confirmed or not requires_end_confirmation) and not feedback_reply_requires_follow_up:
  append_audit_record(
    log_file,
    {
      "timestamp": hook_input.get("timestamp"),
      "event": event_name,
      "decision": "allow",
      "reason": "本轮已完成 feedback 闭环。" if not requires_end_confirmation else "本轮已完成 feedback 闭环且用户明确要求结束。",
      "requestId": latest_request_id,
      "feedbackCalled": True,
      "requestFeedbackCalled": request_feedback_called,
      "lastAssistantHadFeedback": last_assistant_had_feedback,
      "lastAssistantPreview": last_assistant_preview,
      "lastAssistantToolNames": last_assistant_tool_names,
      "lastAssistantStateSource": last_assistant_state_source,
      "userTextPreview": preview_text(latest_text) if latest_text else "",
      "endConfirmed": end_confirmed,
      "transcriptPath": transcript_path_value,
    },
  )
  emit({"continue": True})
  sys.exit(0)

reasons = []
if feedback_reply_requires_follow_up:
  reasons.append(
    f"本轮 feedback 已收到回复“{preview_text(latest_feedback_reply_text)}”，assistant 尚未继续处理，不能直接结束当前 turn"
  )
elif not request_feedback_called:
  reasons.append("本轮结束前未调用 interactive_feedback；如不可用，可降级为 vscode_askQuestions")
elif not last_assistant_had_feedback:
  reasons.append("Stop 前最后一条 assistant.message 未携带 interactive_feedback，说明出现了先 feedback 后直接收尾的漏拦截路径")
if requires_end_confirmation and not end_confirmed:
  reasons.append("用户本轮尚未明确表达结束会话")

reason_text = "；".join(reasons) + "。"
append_audit_record(
  log_file,
  {
    "timestamp": hook_input.get("timestamp"),
    "event": event_name,
    "decision": "block",
    "reason": reason_text,
    "requestId": latest_request_id,
    "feedbackCalled": request_feedback_called,
    "requestFeedbackCalled": request_feedback_called,
    "lastAssistantHadFeedback": last_assistant_had_feedback,
    "lastAssistantPreview": last_assistant_preview,
    "lastAssistantToolNames": last_assistant_tool_names,
    "lastAssistantStateSource": last_assistant_state_source,
    "userTextPreview": preview_text(latest_text) if latest_text else "",
    "endConfirmed": end_confirmed,
    "transcriptPath": transcript_path_value,
  },
)

emit(
  {
    "hookSpecificOutput": {
      "hookEventName": event_name,
      "decision": "block",
      "reason": format_stop_reason(reason_text, stop_hook_active),
    },
    "systemMessage": format_stop_reason(reason_text, stop_hook_active),
  }
)
PY
