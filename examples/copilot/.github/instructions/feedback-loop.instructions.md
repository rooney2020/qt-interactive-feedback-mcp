---
description: 每次对话优先使用 interactive_feedback 建立反馈闭环，只有连续失败 3 次才降级 AskQuestion
applyTo: "**"
---

# Feedback Loop

## 核心要求

1. 每轮交互优先使用 `interactive_feedback` 与用户保持闭环。
2. 只有 `interactive_feedback` 连续失败 3 次，才允许降级到 `AskQuestion`。
3. 用户暂未响应、收到自动回复或等待超时，都不等于用户明确结束会话。

## 执行顺序

1. 先处理用户请求。
2. 需要确认、选择、澄清或结束判断时，调用 `interactive_feedback`。
3. 如果返回 `[心跳]`，静默重连，不输出额外说明。
4. 收到用户明确的结束意图前，不要直接收尾。

## 失败处理

1. 心跳 `[心跳]`：静默重连，不计失败。
2. 超时或连接断开：立即重试，不计失败。
3. 真错误累计到 3 次：允许降级 `AskQuestion`，并在选项中提供“切换到 Feedback”。

## 选项规范

1. 结束类选项必须标注 `【结束会话】`。
2. 每次提供预定义选项时，都要包含工具切换项：`切换到 AskQuestion` 或 `切换到 Feedback`。