# 🗣️ Interactive Feedback With Capture MCP

基于 [Interactive Feedback MCP](https://github.com/poliva/interactive-feedback-mcp) 的增强版本，新增**截图反馈**功能。

一个简单的 [MCP Server](https://modelcontextprotocol.io/)，用于在 [Cursor](https://www.cursor.com)、[Cline](https://cline.bot)、[Windsurf](https://windsurf.com) 等 AI 辅助开发工具中实现人机协作工作流。不仅支持文字反馈，还支持**截图反馈**，让 AI 能够直接"看到"你的屏幕内容。

> **注意：** 本服务器设计为本地运行，需要直接访问用户操作系统来显示通知窗口和截图。

## ✨ 新增功能：截图反馈

在原版纯文字反馈的基础上，新增了以下截图能力：

- **📷 全屏截图** — 点击按钮自动最小化反馈窗口，截取全屏后恢复窗口
- **📋 剪贴板粘贴** — 支持按钮粘贴或在文本框中 `Ctrl+V` 直接粘贴截图（配合 `Win+Shift+S` 截取区域后粘贴）
- **📁 浏览图片** — 支持从本地文件选择图片（PNG、JPG、BMP、GIF、WebP）
- **🖼️ 缩略图预览** — 已添加的截图显示缩略图预览，支持单独删除
- **📐 自动压缩** — 超过 1600px 的大图自动等比例缩放

截图通过 MCP Image 内容类型返回给 AI，让 AI 可以直接查看截图内容。

## ✨ 提示内容增强

- **📋 一键复制** — 提示消息区域顶部提供 Copy 按钮，一键复制完整内容到剪贴板
- **🖱️ 文字可选** — 提示内容支持鼠标选择和 `Ctrl+C` 复制
- **📜 滚动显示** — 长文本自动出现垂直滚动条，不再因内容过多而显示不全

## 💓 心跳保活机制

MCP 客户端通常配置有工具调用超时时间（如 `timeout: 600` 即 10 分钟）。当反馈窗口长时间等待用户输入时，可能因超时导致连接断开。

本项目实现了**自动心跳保活**机制解决此问题：

- **工作原理：** 服务器使用异步（`async`）工具函数，在等待用户输入期间每 **15 秒**通过 MCP 协议的 `report_progress` 通知向客户端发送进度报告
- **超时防护：** 客户端收到进度通知后重置超时计时器，避免因等待过久而断开连接
- **日志记录：** 同时通过 `ctx.info()` 记录等待时长，方便调试
- **对用户透明：** 整个过程在后台自动运行，不影响用户操作

```
反馈窗口弹出 → 15s → progress(15) → 15s → progress(30) → ... → 用户提交 → 返回结果
```

> **提示：** 配置中的 `timeout: 600` 仅为单次无响应的最大等待时间，有了心跳保活后，实际可等待时间不受此限制。

## 🖼️ 示例

![Interactive Feedback With Capture](https://raw.githubusercontent.com/dragonstylecc/Interactive-Feedback-With-Capture-MCP/refs/heads/main/.github/example.png)

## 💡 为什么使用它？

在 Cursor 等环境中，发送给 LLM 的每条提示都被视为一个独立请求，计入月度限额（如 500 次高级请求）。当你在模糊的指令上反复迭代或纠正被误解的输出时，每次后续澄清都会触发一个完整的新请求，效率很低。

本 MCP 服务器提供了一种解决方案：它允许模型在完成响应之前暂停并请求澄清。模型触发工具调用（`interactive_feedback`）打开交互式反馈窗口，你可以提供更多细节或要求更改 — 而模型在同一个请求中继续会话。

由于工具调用不计为单独的高级交互，你可以在不消耗额外请求的情况下循环多次反馈。

- **💰 减少 API 调用：** 避免在猜测的基础上浪费昂贵的 API 调用
- **✅ 减少错误：** 在行动之前澄清意味着更少的错误代码
- **⏱️ 更快的迭代：** 快速确认胜过调试错误的猜测
- **🎮 更好的协作：** 将单向指令变为对话，让你保持控制
- **📸 可视化沟通：** 截图让 AI 直接看到问题，比文字描述更直观

## 🛠️ 工具

本服务器通过 MCP 协议暴露以下工具：

- `interactive_feedback`：向用户提问并返回回答。支持预定义选项和**截图附件**。

## 📦 安装

1.  **前置要求：**
    *   Python 3.11 或更高版本
    *   [uv](https://github.com/astral-sh/uv)（Python 包管理器）：
        *   Windows: `pip install uv`
        *   Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
        *   macOS: `brew install uv`
2.  **获取代码：**
    ```bash
    git clone https://github.com/dragonstylecc/Interactive-Feedback-With-Capture-MCP.git
    ```

## ⚙️ 配置

1. 在 `claude_desktop_config.json`（Claude Desktop）或 `mcp.json`（Cursor）中添加以下配置：

**请将 `/path/to/interactive-feedback-mcp` 替换为你实际克隆仓库的路径。**

```json
{
  "mcpServers": {
    "interactive-feedback": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/interactive-feedback-mcp",
        "run",
        "server.py"
      ],
      "timeout": 600,
      "autoApprove": [
        "interactive_feedback"
      ]
    }
  }
}
```

2. 在 AI 助手的自定义规则中添加以下内容（Cursor Settings > Rules > User Rules）：
- 英文版：
> If a request or instruction is unclear, use the interactive_feedback tool to ask the user clarifying questions before proceeding. Do not make assumptions.
Provide predefined options to the user via the interactive_feedback MCP tool whenever possible to facilitate quick decision-making.
Each time you are about to complete a user request, call the interactive_feedback tool to ask for user feedback before finalizing the process. If the feedback is empty, you may end the request and must not call the tool in a loop. If the tool call fails, use the built-in AskQuestion tool.
- 中文版
> 如果要求或指令不明确，在继续操作之前使用interactive_feedback工具向用户询问澄清问题，不要做出假设。
尽可能通过interactive_feedback MCP工具向用户提供预定义的选项，以促进快速决策。
每当即将完成用户请求时，调用interactive_feedback工具在结束流程前请求用户反馈。如果反馈为空，则可以结束请求，并且不要循环调用该工具，如果该工具调用失败时，使用内置 `AskQuestion` 工具。
这将确保 AI 助手在提示不明确时以及在将任务标记为完成之前，始终使用此 MCP 服务器请求用户反馈。

## 📸 截图功能使用说明

反馈窗口底部新增了三个截图按钮：

| 按钮 | 功能 | 说明 |
|------|------|------|
| 📷 Capture Screen | 全屏截图 | 自动最小化窗口，截取整个屏幕后恢复 |
| 📋 Paste Clipboard | 粘贴剪贴板 | 粘贴已复制的截图（支持 `Win+Shift+S` 截取区域后粘贴） |
| 📁 Browse... | 浏览文件 | 从本地选择图片文件 |

**快捷操作：** 在文本输入框中按 `Ctrl+V` 可直接粘贴剪贴板中的图片。

截图以缩略图形式预览在窗口中，点击 ✕ 按钮可删除单张截图。提交反馈时，截图会通过 MCP 协议的 Image 内容类型发送给 AI，AI 可以直接查看截图内容。

## 🙏 致谢

本项目基于以下优秀项目：

- 原始项目由 Fábio Ferreira ([@fabiomlferreira](https://x.com/fabiomlferreira)) 开发
- 由 Pau Oliva ([@pof](https://x.com/pof)) 增强，灵感来自 Tommy Tong 的 [interactive-mcp](https://github.com/ttommyth/interactive-mcp)
- 截图反馈功能由 [dragonstylecc](https://github.com/dragonstylecc) 添加
