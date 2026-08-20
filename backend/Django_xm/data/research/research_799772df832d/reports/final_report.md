# 最终报告：Ollama 运行状态检查

## 摘要
本报告汇总了通过两级子代理调度（主 agent → A1/A2 → B1/B2）执行的 Ollama 状态检查结果。其中 `ollama list` 展示了本地已安装的 7 个模型，`ollama ps` 展示了当前正在运行的 2 个模型。所有命令均执行成功，无错误输出。

## 执行架构
本次任务验证了"多级子代理调度"模式：
1. 主 agent 并行派发 2 个 general-purpose 子代理 A1、A2。
2. A1 内部再派发子代理 B1 执行 `ollama list`；A2 内部再派发子代理 B2 执行 `ollama ps`。
3. A1、A2 各自通过 wait_for_subagent 等待内部子代理完成后，将结果返回主 agent。
4. 主 agent 用 wait_for_subagent 汇总 A1、A2 结果。

## 一、已安装模型（ollama list）
`ollama list` 命令执行成功，共列出 **7 个**本地模型：

| NAME | ID | SIZE | MODIFIED |
|------|----|------|----------|
| ornith:latest | a75697c14589 | 5.6 GB | 5 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

## 二、当前运行中的模型（ollama ps）
`ollama ps` 命令执行成功（退出码 0），当前有 **2 个模型**正在运行：

| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|----|------|-----------|---------|-------|
| bge-m3:latest | 790764642607 | 664 MB | 100% GPU | 4096 | 4 minutes from now |
| qwen3:8b | 500a1f067a9f | 5.6 GB | 100% GPU | 4096 | 3 minutes from now |

## 结论与建议
- **结论**：Ollama 服务运行正常。本地共安装 7 个模型，当前 bge-m3:latest 和 qwen3:8b 两个模型正被加载运行，均分配 100% GPU 资源。
- **建议**：
  - qwen3:8b（5.6 GB，运行中）与 bge-m3（1.2 GB 存储 / 664 MB 运行）占用 GPU 内存，若需释放资源可在任务结束后执行 `ollama stop` 或等待其自动到期。
  - 若需长期保留，建议定期清理不再使用的模型以释放磁盘空间。

## 参考文献 / 来源
- 子代理 A1（内部 B1）执行 `ollama list` 的原始命令输出。
- 子代理 A2（内部 B2）执行 `ollama ps` 的原始命令输出。
- 研究笔记：/notes/ollama_research.md
