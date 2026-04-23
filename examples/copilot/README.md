# GitHub Copilot 示例资产

这组文件用于把 Interactive Feedback MCP 接入 GitHub Copilot 工作流。

## 文件说明

- `mcp.json`：MCP server 注册示例，记得把仓库路径改成实际路径。
- `.github/instructions/feedback-loop.instructions.md`：始终优先使用 `interactive_feedback` 的规则模板。
- `.github/skills/feedback-guard/SKILL.md`：严格 feedback 闭环技能模板。
- `.github/hooks/feedback-stop.json`：在 `Stop` / `sessionEnd` 触发 feedback 闸门检查的 hook 示例。
- `scripts/check_feedback_gate.sh`：Stop hook 脚本，按最新一轮 request 检查 feedback 是否真的完成闭环。
- `scripts/query_feedback_gate_log.py`：查询 hook 审计日志的辅助脚本。

## 推荐用法

1. 把 `mcp.json` 合并到你的 MCP 配置文件。
2. 把 `.github/` 目录复制到你的项目根目录。
3. 把 `scripts/` 目录复制到你的项目根目录。
4. 如果使用用户级 hooks，把 `feedback-stop.json` 的 `bash` 路径改成你的真实项目脚本路径后，再放到 `~/.copilot/hooks/`。

## 注意

- 这些文件是示例模板，不会自动在当前仓库启用。
- `check_feedback_gate.sh` 会读取最新 transcript / chatSession 记录，因此要求宿主编辑器能够提供标准的会话记录文件。