# 🗣️ Qt Interactive Feedback MCP

基于 [Interactive Feedback With Capture MCP](https://github.com/dragonstylecc/Interactive-Feedback-With-Capture-MCP) 深度定制的 MCP 反馈助手，专为中文 Linux 环境优化。

一个 [MCP Server](https://modelcontextprotocol.io/)，用于在 [Cursor](https://www.cursor.com) 等 AI 辅助开发工具中实现**人机持续对话**——支持文字反馈、截图附件和预定义选项，让 AI 在同一请求中多次与你交互。

> **注意：** 本服务器设计为本地运行，需要直接访问用户操作系统来显示 Qt 反馈窗口。

## ✨ 核心特性

### 🎨 深色主题 UI
- 完全重设计的深色主题界面（MCP 反馈助手风格）
- AI 工作摘要区域支持大面积显示（最小 20 行，最大 40 行）
- 预定义选项以复选框呈现，支持多选

### 📸 截图与图片附件
- **剪贴板粘贴** — 在文本框中 `Ctrl+V` 直接粘贴截图
- **浏览图片** — 点击「添加图片」按钮从本地选择
- **缩略图预览** — 小尺寸内联显示，悬停出现删除按钮
- **点击放大** — 点击缩略图全屏查看原图
- **自动压缩** — 超过 1600px 的大图自动缩放

### 🇨🇳 中文输入支持（Linux fcitx4）
- 内置 fcitx4 Qt6 输入法插件支持
- PySide6 6.4.3 确保与 fcitx4 Qt6 插件 ABI 兼容
- 窗口启动时自动激活输入法（`fcitx-remote -o`）

### ⏱️ 心跳保活
- 自适应 progress notification 心跳频率：0~10min 每 10s，10min~1h 每 60s，1h+ 每 5min
- 单次调用最大等待 ~12 小时（SOFT_TIMEOUT = 43000s），需配合 `mcp.json` 中 `timeout: 43200`
- 连续 3 次心跳失败自动关闭孤立窗口

### 🌐 底部快捷开关
- **使用中文**（默认勾选）：自动在反馈末尾追加中文语言提示，确保 AI 全程中文回复和思考
- **重新读取Rules**（默认不勾选）：勾选后在反馈中追加"重新读取Rules"，提醒 AI 重新加载 Cursor Rules

### 🪟 多 Agent 并行
- 基于文件锁（`fcntl`）的全局窗口 ID 管理
- 不同 Cursor 项目/会话的窗口自动分配不同编号
- 窗口关闭后编号自动释放

## 📦 安装

### 前置要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)（Python 包管理器）
- Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 克隆并安装

```bash
git clone git@github.com:rooney2020/qt-interactive-feedback-mcp.git ~/.cursor/Interactive-Feedback-MCP
cd ~/.cursor/Interactive-Feedback-MCP
uv sync
```

### Linux 中文输入配置（fcitx4 环境）

如果你使用 fcitx4 + 搜狗/百度拼音，需要额外配置 Qt6 输入法插件：

```bash
# 1. 下载 fcitx4 的 Qt6 前端插件（Ubuntu 24.04 的包）
wget -q "http://archive.ubuntu.com/ubuntu/pool/universe/f/fcitx-qt5/fcitx-frontend-qt6_1.2.7-2build13_amd64.deb" -O /tmp/fcitx-frontend-qt6.deb

# 2. 提取 .so 文件
mkdir -p /tmp/fcitx-qt6-extract
dpkg-deb -x /tmp/fcitx-frontend-qt6.deb /tmp/fcitx-qt6-extract

# 3. 找到 PySide6 插件目录并复制
PLUGINS_DIR=$(cd ~/.cursor/Interactive-Feedback-MCP && uv run python -c \
  "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))")
cp /tmp/fcitx-qt6-extract/usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitxplatforminputcontextplugin-qt6.so \
   "$PLUGINS_DIR/platforminputcontexts/"

echo "✅ fcitx4 Qt6 插件已安装"
```

## ⚙️ Cursor 配置

### 1. MCP Server 配置

在 Cursor Settings > MCP 中添加（或编辑 `.cursor/mcp.json`）：

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
      "timeout": 43200,
      "autoApprove": [
        "interactive_feedback"
      ]
    }
  }
}
```

> **将 `/path/to/interactive-feedback-mcp` 替换为实际路径。**
>
> **`timeout: 43200`**（12 小时，单位：秒）是必须的。工具的 SOFT_TIMEOUT（43000s）略小于此值，确保在 Cursor 硬超时前返回。如果 timeout 太小（如默认的 60s），Cursor 会在超时后强行取消 MCP 调用。

#### Cursor 全局超时配置（推荐）

在 Cursor `settings.json`（`Ctrl+Shift+P` → Open Settings (JSON)）中添加：

```json
"mcp.server.timeout": 43200000
```

> 单位为毫秒（43200000ms = 12h）。确保全局超时 ≥ per-server timeout。

### 2. Cursor Rules 配置

项目提供了开箱即用的 Cursor Rules 文件。将 `.cursor/rules/` 目录复制到你的项目中：

```bash
cp -r /path/to/interactive-feedback-mcp/.cursor/rules/ /your/project/.cursor/rules/
```

或者在 Cursor Settings > Rules > User Rules 中手动添加以下内容：

> 如果要求或指令不明确，在继续操作之前使用 interactive_feedback 工具向用户询问澄清问题，不要做出假设。
> 尽可能通过 interactive_feedback MCP 工具向用户提供预定义的选项，以促进快速决策。
> 每当即将完成用户请求时，调用 interactive_feedback 工具在结束流程前请求用户反馈。如果反馈为空，则可以结束请求。

### 3. 自动配置脚本（可选）

运行以下命令一键配置：

```bash
cd /path/to/interactive-feedback-mcp
bash setup.sh
```

## 🛠️ MCP 工具

| 工具 | 描述 |
|------|------|
| `interactive_feedback` | 向用户弹出 Qt 反馈窗口，支持预定义选项和截图附件 |

### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `message` | string | 显示给用户的消息/问题 |
| `predefined_options` | list | 预定义选项列表（可选） |

### 返回值

- 文字反馈：`{"interactive_feedback": "用户输入的内容"}`
- 带截图：返回包含文字和 MCP Image 对象的列表

## 📋 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Enter` | 提交反馈 |
| `Ctrl+V` | 粘贴剪贴板图片 |

## 🙏 致谢

- 原始项目：[Interactive Feedback MCP](https://github.com/poliva/interactive-feedback-mcp) by Fábio Ferreira & Pau Oliva
- 截图功能：[Interactive Feedback With Capture MCP](https://github.com/dragonstylecc/Interactive-Feedback-With-Capture-MCP) by dragonstylecc
