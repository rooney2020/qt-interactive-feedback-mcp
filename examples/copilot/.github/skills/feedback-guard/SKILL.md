---
name: feedback-guard
description: "Use when: 需要严格防止漏调 interactive_feedback、保持 feedback 闭环、处理超时与重试策略"
---

# Feedback Guard Skill

## 目标

防止出现“回复结束但没有调用 feedback”的情况，保证 agent 始终通过 `interactive_feedback` 与用户保持闭环。

## 适用场景

- feedback
- 反馈闭环
- 漏调 interactive_feedback
- 用户超时未回复但会话不能自动结束

## 标准流程

1. 读取用户最新输入。
2. 执行当前任务。
3. 在准备收尾前检查：
   - 本轮是否调用过 `interactive_feedback` 或降级使用了 `AskQuestion`。
   - 用户是否明确表达“结束”。
4. 如果没有结束确认，继续通过 `interactive_feedback` 沟通。

## 自检清单

- 是否错误地把超时自动回复当成“用户确认结束”？
- 是否在未调用 feedback 的情况下直接输出最终结果？
- 是否为结束项添加了 `【结束会话】` 标记？