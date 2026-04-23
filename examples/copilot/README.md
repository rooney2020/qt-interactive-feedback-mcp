# GitHub Copilot 示例资产

这组文件用于把 Interactive Feedback MCP 接入 GitHub Copilot 工作流，并且全部按**用户级**目录组织，而不是项目级 `.github/` 模板。

## 文件说明

- `mcp.json`：MCP server 注册示例，记得把仓库路径改成实际路径。
- `.copilot/hooks/feedback_guard.json`：当前用户级 hooks 示例，直接对应 `~/.copilot/hooks/feedback_guard.json`。
- `.copilot/hooks/check_feedback_gate.sh`：当前用户级 Stop/sessionEnd hook 脚本。
- `.copilot/hooks/query_feedback_gate_log.py`：查询 hook 审计日志的辅助脚本。
- `.copilot/instructions/feedback-loop.instructions.md`：当前用户级 feedback instruction。
- `.copilot/skills/feedback/SKILL.md`：当前用户级 feedback skill。
- `.copilot/skills/feedback-guard/SKILL.md`：当前用户级 feedback-guard skill。
- `vscode-user-prompts/feedback-guard.instructions.md`：当前 VS Code 用户级 instruction。
- `vscode-user-prompts/global-mcp-feedback.instructions.md`：当前 VS Code 用户级 instruction。

## 推荐用法

1. 把 `mcp.json` 合并到你的 MCP 配置文件。
2. 把 `.copilot/` 目录复制到 `~/.copilot/`。
3. 把 `vscode-user-prompts/` 下的文件复制到 `~/.config/Code/User/prompts/`。
4. 如果你的用户名或路径不同，修改 `.copilot/hooks/feedback_guard.json` 里的绝对脚本路径。

## 注意

- 这里复制的是我当前真实在用的用户级 assets，已经不再是项目级 `.github` 模板。
- `check_feedback_gate.sh` 会读取最新 transcript / chatSession 记录，因此要求宿主编辑器能够提供标准的会话记录文件。