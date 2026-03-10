# 故障排除手册

## 日志文件位置

| 文件 | 路径 | 说明 |
|------|------|------|
| Daemon 日志 | `/tmp/mcp_feedback_daemon.log` | feedback_daemon.py 的运行日志，包含 UI、socket、tab 操作 |
| Server 日志 | `/tmp/mcp_feedback_server.log` | server.py（MCP 侧）的运行日志 |
| Session 日志 | `/tmp/mcp_feedback_sessions.log` | 每个会话的生命周期摘要（开始、结束、超时、取消） |

查看实时日志：

```bash
tail -f /tmp/mcp_feedback_daemon.log
```

## 常见问题

### 1. 界面不显示（Cursor 调用了 interactive_feedback 但窗口不弹出）

**症状：** Cursor 已调用 `interactive_feedback`，但看不到反馈窗口。

**诊断步骤：**

```bash
# 1. 检查 daemon 进程是否存在
pgrep -f "feedback_daemon.py"

# 2. 检查 socket 文件是否存在
ls -la /tmp/mcp_feedback_daemon.sock

# 3. 查看 daemon 日志最后几行
tail -20 /tmp/mcp_feedback_daemon.log

# 4. 查看 server 日志
tail -20 /tmp/mcp_feedback_server.log
```

**手动修复：**

```bash
# 停止旧进程
pkill -f "feedback_daemon.py"

# 清理残留文件
rm -f /tmp/mcp_feedback_daemon.sock /tmp/mcp_feedback_daemon.lock

# 手动启动 daemon（用于调试，看输出）
cd ~/.cursor/Interactive-Feedback-With-Capture-MCP
QT_IM_MODULE=fcitx XMODIFIERS=@im=fcitx uv run python -u feedback_daemon.py
```

启动后，下次 Cursor 调用 `interactive_feedback` 时会自动连接到新 daemon。

**一键修复脚本：**

```bash
pkill -f "feedback_daemon.py"; rm -f /tmp/mcp_feedback_daemon.sock /tmp/mcp_feedback_daemon.lock
```

执行后无需手动启动 daemon——Cursor 下次调用 `interactive_feedback` 时会自动拉起。

### 2. 出现重复的 Tab 页

**症状：** 同一个会话出现了多个 Tab 页。

**原因：** server.py 在取消或超时时未正确关闭到 daemon 的 socket 连接，导致 daemon 侧残留了"孤儿"连接。

**已修复：** `server.py` 的 `_send_to_daemon` 函数 `finally` 块中已添加 `writer.close()` + `await writer.wait_closed()`，确保连接被正确关闭。

**如果仍然出现（daemon 未更新）：**

```bash
# 重启 daemon 以加载最新代码
pkill -f "feedback_daemon.py"
rm -f /tmp/mcp_feedback_daemon.sock /tmp/mcp_feedback_daemon.lock
```

### 3. Daemon 进程存在但 UI 无响应

**症状：** `pgrep -f feedback_daemon.py` 返回 PID，但窗口无反应或不处理新请求。

**原因：** Qt 事件循环的 `_poll_timer` 可能因未捕获异常而停止。

**已修复：** 
- `_poll_requests` 已用 `try...except` 包裹，异常不再导致 timer 停止
- 新增 60 秒间隔的 `_watchdog_timer`，自动检测并重启卡死的 `_poll_timer`

**如果 watchdog 也无效：**

```bash
# 查看日志中是否有 CRITICAL 或 WATCHDOG 关键字
grep -E "CRITICAL|WATCHDOG|ERROR" /tmp/mcp_feedback_daemon.log | tail -20

# 强制重启
pkill -f "feedback_daemon.py"
rm -f /tmp/mcp_feedback_daemon.sock /tmp/mcp_feedback_daemon.lock
```

### 4. Socket 连接被拒绝

**症状：** server.py 日志出现 `ConnectionRefusedError`。

**原因：** daemon 进程崩溃但 socket 文件或 lock 文件残留。

```bash
# 清理并让 Cursor 自动重启
rm -f /tmp/mcp_feedback_daemon.sock /tmp/mcp_feedback_daemon.lock
```

### 5. 中文输入法不工作

```bash
# 确认环境变量
echo $QT_IM_MODULE  # 应该是 fcitx
echo $XMODIFIERS     # 应该是 @im=fcitx

# 确认 fcitx4 Qt6 插件已安装（参见 README.md）
ls $(cd ~/.cursor/Interactive-Feedback-With-Capture-MCP && uv run python -c \
  "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))")/platforminputcontexts/
```

## 让 Cursor 自动修复

当 `interactive_feedback` 工具出现界面不显示的问题时，你可以在 Cursor 聊天中发送以下指令，让 AI 帮你修复：

> **简短版：**
> 
> `feedback 工具界面不显示了，帮我重启 daemon 进程修复。`

> **详细版（包含诊断）：**
>
> ```
> interactive_feedback 工具有问题，界面不弹出。请按以下步骤排查和修复：
> 1. 检查 /tmp/mcp_feedback_daemon.log 最后 30 行日志
> 2. 检查 daemon 进程是否存在（pgrep -f feedback_daemon.py）
> 3. 如果进程存在但无响应，kill 掉并清理 /tmp/mcp_feedback_daemon.sock 和 .lock 文件
> 4. 如果日志有 CRITICAL/ERROR/WATCHDOG 关键字，分析根因
> 5. 重启后调用 interactive_feedback 验证是否恢复
> ```

> **如果怀疑是代码 bug：**
>
> ```
> feedback daemon 反复出问题。请：
> 1. 查看 /tmp/mcp_feedback_daemon.log 中的 CRITICAL/ERROR 日志
> 2. 查看 /tmp/mcp_feedback_server.log 中的异常
> 3. 查看 /tmp/mcp_feedback_sessions.log 中最近会话的状态
> 4. 根据日志分析根因，修复 ~/.cursor/Interactive-Feedback-With-Capture-MCP/ 中的代码
> 5. 重启 daemon 并验证
> ```

## 架构说明

```
Cursor ──stdio──> server.py (FastMCP)
                    │
                    │  Unix domain socket
                    │  /tmp/mcp_feedback_daemon.sock
                    ▼
              feedback_daemon.py
                    │
                    ├── Socket Server Thread（接收请求）
                    ├── Qt Event Loop（主线程，UI 渲染）
                    │     ├── _poll_timer (100ms) 从 queue 取请求/关闭
                    │     └── _watchdog_timer (60s) 监控 poll_timer
                    └── Client Handler Threads（每连接一个线程）
```

- `server.py`：MCP 标准 IO 服务端，接收 Cursor 的工具调用，通过 Unix socket 转发给 daemon
- `feedback_daemon.py`：长驻进程，管理 Qt 窗口和多 Tab 页，通过 socket 接收请求、返回用户反馈
- 通信协议：JSON + `\n` 分隔，通过 Unix domain socket
