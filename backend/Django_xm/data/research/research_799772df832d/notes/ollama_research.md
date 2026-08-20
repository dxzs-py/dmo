# 研究笔记：Ollama 状态检查（ollama list / ollama ps）

## 执行结构
- 主 agent 并行派发 A1、A2（general-purpose）
- A1 内部派发 B1 执行 `ollama list`
- A2 内部派发 B2 执行 `ollama ps`
- A1/A2 各自用 wait_for_subagent 等待内部子代理完成后返回

## A1 结果（B1 执行 ollama list）
退出状态：成功，共 7 个本地模型：
| NAME | ID | SIZE | MODIFIED |
|------|----|------|----------|
| ornith:latest | a75697c14589 | 5.6 GB | 5 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

## A2 结果（B2 执行 ollama ps）
退出码 0，当前 2 个模型正在运行：
| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|----|------|-----------|---------|-------|
| bge-m3:latest | 790764642607 | 664 MB | 100% GPU | 4096 | 4 minutes from now |
| qwen3:8b | 500a1f067a9f | 5.6 GB | 100% GPU | 4096 | 3 minutes from now |

## 观察
- 已安装 7 个模型，其中 bge-m3:latest 与 qwen3:8b 当前正被加载运行（100% GPU）。
