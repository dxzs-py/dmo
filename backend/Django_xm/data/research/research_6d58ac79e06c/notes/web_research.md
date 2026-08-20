# 研究笔记：Ollama 状态检查（嵌套子代理汇总）

## 调度记录
- 主 agent 并行派发 A1、A2（均为 general-purpose）
- A1 内部派发 B1 执行 `ollama list`，A1 等待 B1 后转交结果
- A2 内部派发 B2 执行 `ollama ps`，A2 等待 B2 后转交结果
- 主 agent 用 wait_for_subagent 等待 A1、A2 完成

## A1 / B1 结果：`ollama list`（模型列表）
| NAME | ID | SIZE | MODIFIED |
|------|----|------|----------|
| ornith:latest | a75697c14589 | 5.6 GB | 5 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

共 7 个模型。命令执行成功，无错误。

## A2 / B2 结果：`ollama ps`（运行中的模型）
| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|----|------|-----------|---------|-------|
| qwen3:8b | 500a1f067a9f | 5.6 GB | 100% GPU | 4096 | 2 minutes from now |

当前仅 `qwen3:8b` 正在运行，占用 5.6 GB 显存，100% GPU，上下文 4096，约 2 分钟后自动卸载。退出码 0，无 stderr。

## 关键观察
- `qwen3:8b` 同时在 `ollama list` 与 `ollama ps` 中出现 → 该模型当前处于加载运行状态
- 其余 6 个模型处于已下载未运行状态
- 嵌套子代理调度成功：A→B 两层委派，执行均由最底层 B 完成，A 仅转交。
