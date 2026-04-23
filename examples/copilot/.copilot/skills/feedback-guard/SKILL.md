---
name: feedback-guard
description: "Use when: 需要严格防止漏调 interactive_feedback、建立反馈闭环、处理 feedback 重试与降级策略"
---

# Feedback Guard Skill

## 目标

防止出现“回复结束但未调用 feedback”的情况，保证会话持续闭环。

## 触发词

- feedback
- 反馈
- 漏调
- 不要停
- 继续沟通
- 结束会话确认

## 标准流程

1. 读取用户最新输入。
2. 执行任务（可调用工具）。
3. 执行反馈闸门检查：
   - 本轮是否已经调用 `interactive_feedback`？
   - 是否已有活跃 feedback 在等待？
   - 用户是否明确“结束”？
4. 若未结束且未反馈，立即调用 `interactive_feedback`。

## 失败处理

1. 心跳：静默重连。
2. 超时/断开：立即重试。
3. 连续 3 次真错误：降级 `AskQuestion`。
4. 下一次交互优先恢复 `interactive_feedback`。

## 输出要求

1. 全中文。
2. 给出简短进度说明。
3. 每次交互都保留可继续操作选项。

## 最小自检清单

- 是否调用了 feedback？
- 是否错误地提前收尾？
- 是否提供结束选项和工具切换选项？
