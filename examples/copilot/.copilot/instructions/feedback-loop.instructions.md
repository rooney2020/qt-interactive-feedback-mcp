---
description: "反馈闭环硬规则：每轮必须优先 interactive_feedback，失败重试后再降级"
applyTo: "**"
---

# Feedback Loop 硬规则

## 核心要求

1. 每轮交互必须通过 `interactive_feedback` 保持闭环。
2. 未调用 `interactive_feedback` 前，不允许发送普通收尾。
3. 仅当 `interactive_feedback` 连续失败 3 次（非心跳）才可降级 `AskQuestion`。

## 执行顺序

1. 处理用户请求。
2. 需要确认、选择、结束判断时，调用 `interactive_feedback`。
3. 收到用户反馈后继续执行。
4. 循环直到用户明确说“结束”。

## 异常与重试

1. 心跳 `[心跳]`：静默重连，不计失败。
2. 超时/连接断开：立即重试，不计失败。
3. 真错误计数到 3：允许降级 `AskQuestion`，并提供“切回 Feedback”选项。

## 并发约束

1. 同一轮只允许一个活跃 feedback。
2. 用户在聊天里直接回复后，立刻放弃等待旧 feedback，以聊天内容为准。

## 选项规范

1. 结束项必须标记 `【结束会话】`。
2. 每次选项必须提供工具切换项：`切换到 AskQuestion`（或反向 `切换到 Feedback`）。