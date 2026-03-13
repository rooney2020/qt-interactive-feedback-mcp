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

### 3. Daemon 进程存在但 UI 无响应（heartbeat 停止）

**症状：** `pgrep -f feedback_daemon.py` 返回 PID，但窗口不处理新请求，日志中没有新的 heartbeat。

**历史根因（已修复）：** Cursor 启动 daemon 时使用 `stderr=PIPE`。当 server.py 被杀后管道断裂，`_log()` 中的 `print(file=sys.stderr)` 抛出 `BrokenPipeError`，导致日志停止写入、close_queue 处理异常。

**已修复：**
- `_log()` 中 stderr 写入已加 `try/except(BrokenPipeError)`
- server.py 启动 daemon 时 stderr 改为 `DEVNULL`
- `_poll_requests` 已用 `try...except` 包裹
- 新增 60 秒间隔的 `_watchdog_timer`，自动检测并重启卡死的 `_poll_timer`

**如果仍然出现：**

```bash
# 查看日志中是否有 CRITICAL 或 WATCHDOG 关键字
grep -E "CRITICAL|WATCHDOG|ERROR|BrokenPipe" /tmp/mcp_feedback_daemon.log | tail -20

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
echo $QT_IM_MODULE  # 应该是 fcitx 或 fcitx5
echo $XMODIFIERS     # 应该是 @im=fcitx 或 @im=fcitx5

# 确认 fcitx Qt6 插件已安装（参见 README.md）
ls $(cd ~/.cursor/Interactive-Feedback-With-Capture-MCP && uv run python -c \
  "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))")/platforminputcontexts/

# 检查 .im_config.json 是否存在
cat ~/.cursor/Interactive-Feedback-With-Capture-MCP/.im_config.json
```

### 6. GLIBCXX 版本不兼容（Ubuntu 20.04 等旧版系统）

**症状：** fcitx Qt6 插件已安装到 PySide6 目录，但中文输入仍然不工作。Daemon 日志中可能出现类似 `undefined symbol: _ZNSt7__...` 或 `GLIBCXX_3.4.29 not found` 的错误。

**原因：** 从 Ubuntu 24.04 软件包提取的 fcitx Qt6 插件编译时链接了较新的 `libstdc++`，需要 `GLIBCXX_3.4.29`，而 Ubuntu 20.04 系统的 `libstdc++` 只提供到 `GLIBCXX_3.4.28`。

**诊断步骤：**

```bash
# 1. 检查系统 GLIBCXX 最高版本
strings /usr/lib/x86_64-linux-gnu/libstdc++.so.6 | grep GLIBCXX | sort -V | tail -5

# 2. 检查 fcitx 插件需要的 GLIBCXX 版本
PLUGINS_DIR=$(cd ~/.cursor/Interactive-Feedback-With-Capture-MCP && uv run python -c \
  "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))")
strings "$PLUGINS_DIR/platforminputcontexts/libfcitxplatforminputcontextplugin-qt6.so" | grep GLIBCXX | sort -V | tail -5

# 3. 检查 .im_config.json 中的 ld_preload 配置
cat ~/.cursor/Interactive-Feedback-With-Capture-MCP/.im_config.json

# 4. 如果 ld_preload 有值，验证该 libstdc++ 是否可用
# （将路径替换为 .im_config.json 中的实际路径）
strings /path/to/libstdc++.so.6 | grep GLIBCXX_3.4.29

# 5. 检查 daemon 日志中的 LD_PRELOAD 相关信息
grep -i "ld_preload\|re-executing\|libstdc" /tmp/mcp_feedback_daemon.log | tail -10
grep -i "ld_preload" /tmp/mcp_feedback_server.log | tail -10
```

**修复方案：**

**方案 A：重新运行 setup.sh（推荐）**

```bash
cd ~/.cursor/Interactive-Feedback-With-Capture-MCP
bash setup.sh
```

`setup.sh` 会自动检测 GLIBCXX 兼容性，搜索 Conda 等环境中的兼容 `libstdc++`，并写入 `.im_config.json`。程序启动时会自动应用 `LD_PRELOAD`。

**方案 B：手动创建 `.im_config.json`**

```bash
# 找一个提供 GLIBCXX_3.4.29 的 libstdc++
# 常见来源：Conda (miniconda3/miniforge3/anaconda3)、较新的 GCC
for lib in ~/miniconda3/lib/libstdc++.so.6 ~/miniforge3/lib/libstdc++.so.6 ~/anaconda3/lib/libstdc++.so.6; do
  if [[ -f "$lib" ]] && strings "$lib" | grep -q "GLIBCXX_3.4.29"; then
    echo "✅ Found: $lib -> $(readlink -f "$lib")"
  fi
done

# 创建配置（替换为实际路径）
cat > ~/.cursor/Interactive-Feedback-With-Capture-MCP/.im_config.json << 'EOF'
{
    "im_module": "fcitx",
    "ld_preload": "/home/用户名/miniconda3/lib/libstdc++.so.6.0.34"
}
EOF
```

**方案 C：安装较新的 libstdc++**

```bash
# 通过 Conda（推荐，不影响系统）
conda install -c conda-forge libstdcxx-ng

# 或者安装较新的 GCC（会装很多依赖）
sudo apt install g++-11
```

安装后重新运行 `bash setup.sh`。

**方案 D：手动使用 LD_PRELOAD 启动 daemon（临时调试）**

```bash
LD_PRELOAD=/path/to/libstdc++.so.6 \
  QT_IM_MODULE=fcitx XMODIFIERS=@im=fcitx \
  uv run python -u feedback_daemon.py
```

**验证修复：**

```bash
# 1. 停止旧 daemon
pkill -f "feedback_daemon.py"
rm -f /tmp/mcp_feedback_daemon.sock /tmp/mcp_feedback_daemon.lock

# 2. 重启 Cursor MCP 或等待下次调用自动拉起

# 3. 查看日志确认 LD_PRELOAD 生效
grep "LD_PRELOAD\|Re-executing" /tmp/mcp_feedback_daemon.log | tail -5
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
