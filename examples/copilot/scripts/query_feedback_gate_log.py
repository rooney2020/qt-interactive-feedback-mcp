#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def audit_log_path(workspace_cwd: str) -> Path:
  state_root = os.environ.get("XDG_STATE_HOME")
  if state_root:
    base_dir = Path(state_root)
  else:
    base_dir = Path.home() / ".local" / "state"
  workspace_key = hashlib.sha256(workspace_cwd.encode("utf-8", errors="ignore")).hexdigest()[:16]
  return base_dir / "copilot-feedback-hook" / workspace_key / "feedback-gate.audit.jsonl"


def main() -> int:
  parser = argparse.ArgumentParser(description="查询 feedback gate 审计日志")
  parser.add_argument("--workspace-cwd", default=os.getcwd(), help="工作区目录，默认取当前目录")
  parser.add_argument("--limit", type=int, default=10, help="输出最近多少条记录")
  parser.add_argument("--json", action="store_true", help="原样输出 JSON 行")
  parser.add_argument("--event", choices=["Stop", "sessionEnd"], help="仅输出指定事件")
  parser.add_argument("--decision", choices=["allow", "block"], help="仅输出指定决策")
  parser.add_argument("--request-id", help="仅输出指定 requestId")
  parser.add_argument("--contains", help="仅输出原因、用户消息预览或最后回复预览包含指定文本的记录")
  args = parser.parse_args()

  log_path = audit_log_path(args.workspace_cwd)
  if not log_path.exists():
    print(f"日志不存在: {log_path}")
    return 1

  records = [json.loads(line) for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
  filtered_records = records
  if args.event:
    filtered_records = [record for record in filtered_records if record.get("event") == args.event]
  if args.decision:
    filtered_records = [record for record in filtered_records if record.get("decision") == args.decision]
  if args.request_id:
    filtered_records = [record for record in filtered_records if record.get("requestId") == args.request_id]
  if args.contains:
    keyword = args.contains.casefold()
    filtered_records = [
      record
      for record in filtered_records
      if keyword in str(record.get("reason", "")).casefold()
      or keyword in str(record.get("userTextPreview", "")).casefold()
      or keyword in str(record.get("lastAssistantPreview", "")).casefold()
    ]

  tail_records = filtered_records[-args.limit :]

  if args.json:
    for record in tail_records:
      print(json.dumps(record, ensure_ascii=False))
    return 0

  print(f"日志文件: {log_path}")
  print(f"匹配记录数: {len(filtered_records)}")
  for record in tail_records:
    print("-" * 80)
    print(f"时间: {record.get('timestamp', '')}")
    print(f"事件: {record.get('event', '')}")
    print(f"决策: {record.get('decision', '')}")
    print(f"原因: {record.get('reason', '')}")
    print(f"请求: {record.get('requestId', '')}")
    print(f"用户消息预览: {record.get('userTextPreview', '')}")
    print(f"最后回复预览: {record.get('lastAssistantPreview', '')}")
    print(f"最后回复工具: {', '.join(record.get('lastAssistantToolNames', []))}")
    print(f"最后回复状态来源: {record.get('lastAssistantStateSource', '')}")
    print(f"本请求曾调 feedback: {record.get('requestFeedbackCalled', False)}")
    print(f"最后回复带 feedback: {record.get('lastAssistantHadFeedback', False)}")
    print(f"已确认结束: {record.get('endConfirmed', False)}")
    print(f"transcript 路径: {record.get('transcriptPath', '')}")

  return 0


if __name__ == "__main__":
  raise SystemExit(main())