# 版本履历

## 1.1.1 (2026-03-13)

### 改进
- **Tab 去重** — 基于 MCP server PID 识别同一 agent 会话，自动替换旧 tab 而非重复创建
- server.py 请求中新增 `mcp_pid` 字段，daemon 端通过 `_close_tabs_by_mcp_pid()` 清理同源旧 tab

## 1.1.0 (2026-03-13)

### 新功能
- **飞书集成** — 在反馈输入框中支持 `@` 提及飞书用户和群聊
  - 通过飞书 OAuth (OIDC) 认证获取 `user_access_token`，复用 `lark-mcp` 的 `app_id` / `app_secret`
  - 点击 `@` 按钮或在输入框中输入 `@` 均可打开搜索窗口
  - 支持联系人/群聊切换搜索，分页加载更多结果
  - 联系人显示头像，重名时自动标注区分
  - 选中的实体以整体块插入，Backspace 整块删除
  - 提交反馈时自动附带 `mentioned_entities`（包含 `open_id` / `chat_id`），Agent 可直接使用
- 飞书连接管理集成到设置页面，支持一键连接/断开，Token 自动刷新

### 文件变更
- 新增 `feishu_client.py` — 飞书 API 客户端（OAuth、Token 管理、用户/群搜索）
- 新增 `mention_completer.py` — `@` 提及 UI 组件（搜索弹窗、头像加载、分页）
- 修改 `feedback_ui.py` — 集成 `@` 检测与提及插入逻辑
- 修改 `feedback_daemon.py` — 传递 `FeishuClient` 实例到 UI
- 修改 `server.py` — 提取并返回 `mentioned_entities`
- 修改 `settings_dialog.py` — 飞书 OAuth 设置与连接管理
- 修改 `README.md` — 飞书集成文档

## 1.0.3 (2026-03-13)

### 新功能
- **自动回复倒计时** — 避免 Cursor 3600s 硬性超时，倒计时结束自动发送心跳消息
- 设置页面新增倒计时秒数配置（60~3500 秒）

## 1.0.2 (2026-03-12)

### 新功能
- **GLIBCXX 兼容性自动检测** — 针对 Ubuntu 20.04 等旧系统，自动检测 `libstdc++` 版本并通过 `LD_PRELOAD` 修复中文输入（fcitx4）

## 1.0.1 (2026-03-11)

### 改进
- Tab 关闭按钮可见化（自定义 SVG 图标）
- 设置页面复选框间距优化
- 独立更新对话框，支持进度日志和重启按钮
