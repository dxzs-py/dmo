# 研究计划：并行子代理嵌套执行 ollama list / ollama ps

## 目标
通过两层子代理结构并行获取本机 Ollama 运行时状态信息：
- 第一层：主 agent 并行派发 A1、A2 两个 general-purpose 子代理
- 第二层：A1 内部派发 B1 执行 `ollama list`；A2 内部派发 B2 执行 `ollama ps`
- A1/A2 均使用 wait_for_subagent 等待内部子代理完成后返回结果

## 执行步骤
1. [进行中] 主 agent 使用 write_todos 制定待办清单
2. [进行中] 主 agent 并行 spawn A1（内部 B1 执行 `ollama list`）、A2（内部 B2 执行 `ollama ps`）
3. [待办] 主 agent 使用 wait_for_subagent 等待 A1、A2 完成
4. [待办] 整理子代理返回结果写入 /notes/ 研究笔记
5. [待办] 汇总结果撰写最终报告写入 /reports/ 目录

## 职责边界
- 主 agent 不直接执行 shell 命令，业务执行全部由子代理完成
- A1 不直接执行 `ollama list`，交由 B1 执行
- A2 不直接执行 `ollama ps`，交由 B2 执行

## 产出目录
- 计划：/plans/research_plan.md
- 笔记：/notes/subagent_ollama_research.md
- 报告：/reports/final_report_ollama_status.md
