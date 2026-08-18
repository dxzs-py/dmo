# Ollama 运行状态研究计划

## 目标
并行派发两个子代理，分别执行 ollama 相关命令，汇总 ollama 模型的运行状态与列表信息。

## 子任务拆分
1. **子代理 1（general-purpose）**：运行 `ollama list`，并允许第一个模型被询问"你是谁"，以获取模型的自我识别信息。
2. **子代理 2（general-purpose）**：运行 `ollama ps`，获取当前正在运行的模型进程状态。

## 执行方式
- 两个子任务相互独立，并行派发。
- 使用 wait_for_subagent 等待两个子代理全部完成。

## 产出
- 研究笔记写入 /notes/
- 汇总报告写入 /reports/
