# 研究计划：Ollama 状态检查

## 目标
并行派发 2 个子代理 A1、A2（general-purpose），分别执行 ollama 相关命令：
- A1 内部派发子代理 B1 执行 `ollama list`
- A2 内部派发子代理 B2 执行 `ollama ps`
两个子代理均用 wait_for_subagent 等待其内部子代理完成后返回结果，最后汇总。

## 步骤
1. 制定计划（本文件）
2. 并行派发 A1、A2（每个内部再派发 B1/B2）
3. 等待 A1、A2 完成
4. 整合结果，写入 /notes/ 与 /reports/

## 产出
- /notes/ollama_research.md（研究笔记）
- /reports/final_report.md（最终报告）
