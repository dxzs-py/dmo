# 研究计划：Ollama 状态查询

## 目标
并行查询本机 Ollama 服务状态：
1. `ollama list` — 列出本机已安装的模型
2. `ollama ps` — 列出当前正在运行的模型

## 步骤
1. 并行派发 2 个子代理（general-purpose）：
   - 子代理 1：执行 `ollama list`
   - 子代理 2：执行 `ollama ps`
2. 等待两个子代理完成
3. 汇总结果并生成报告

## 产出
- 报告保存至 /reports/ollama_status_report.md
