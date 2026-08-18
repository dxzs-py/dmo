# 深度研究计划：Ollama 运行状态与模型清单调查

## 研究目标
通过多级子代理调度架构，调查当前环境中 Ollama 的本地模型清单（ollama list）与运行中模型状态（ollama ps）。

## 调度架构
- **主 agent（调度者）**：制定计划、派发子代理、汇总报告
- **A1（general-purpose）**：并行子代理之一，内部派发 B1 执行 `ollama list`
- **A2（general-purpose）**：并行子代理之二，内部派发 B2 执行 `ollama ps`
- **B1（general-purpose）**：A1 内部子代理，执行 `ollama list` 命令
- **B2（general-purpose）**：A2 内部子代理，执行 `ollama ps` 命令

## 执行步骤
1. 主 agent 制定计划（本文件）
2. 主 agent 并行派发 A1、A2
3. A1 内部派发 B1 执行 `ollama list`，A2 内部派发 B2 执行 `ollama ps`
4. A1 用 wait_for_subagent 等待 B1，A2 用 wait_for_subagent 等待 B2
5. A1、A2 各自将内部结果返回给主 agent
6. 主 agent 用 wait_for_subagent 等待 A1、A2 完成
7. 主 agent 汇总结果，写入 /notes/，最终报告写入 /reports/

## 产出目录
- 研究计划：/plans/research_plan.md
- 研究笔记：/notes/web_research.md
- 最终报告：/reports/final_report.md
