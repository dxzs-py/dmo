# 研究计划：Ollama 运行时状态信息采集

## 目标
并行采集 Ollama 的模型列表（ollama list）与运行中模型状态（ollama ps），并汇总输出。

## 调度结构（两级代理）
- 主 agent（调度者）：制定计划、派发 A1/A2、等待结果、汇总报告。
- A1（general-purpose）：内部再派发 B1 执行 `ollama list`，用 wait_for_subagent 等待 B1 返回，再上报结果。
- A2（general-purpose）：内部再派发 B2 执行 `ollama ps`，用 wait_for_subagent 等待 B2 返回，再上报结果。

## 执行步骤
1. 制定计划（本文件）。
2. 并行 spawn A1、A2（均指定为 general-purpose）。
3. 主 agent 用 wait_for_subagent 等待 A1、A2 全部完成。
4. 将子代理返回结果整理为研究笔记写入 /notes/。
5. 汇总生成最终报告写入 /reports/。

## 产出目录规范
- 计划：/plans/research_plan.md
- 笔记：/notes/ollama_runtime.md
- 报告：/reports/ollama_runtime_report.md
