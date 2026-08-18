# 研究笔记：Ollama 运行状态与模型清单调查

## 执行链路记录
- 主 agent 并行派发 A1（subagent_cc80cf8b312e4356）、A2（subagent_46e6d004a3b540cc）
- A1 内部派发 B1（subagent_9a3ff5f4c90c4666）执行 `ollama list`
- A2 内部派发 B2（subagent_c8547fd81ef34fc3）执行 `ollama ps`
- 均通过 wait_for_subagent 等待内部子代理完成后返回结果

## 结果一：ollama list（模型清单，共 7 个）
| NAME | ID | SIZE | MODIFIED |
|------|-----|------|----------|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

分类：对话/生成模型 5 个（ornith、lfm2.5、qwen3:8b、qwen3.5:9b、hermes3:8b）；嵌入模型 2 个（Qwen3-Embedding-4B、bge-m3）。

## 结果二：ollama ps（运行中模型，共 1 个）
| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|-----|------|-----------|---------|-------|
| bge-m3:latest | 790764642607 | 664 MB | 100% GPU | 4096 | 2 minutes from now |

结论：当前运行模型为 bge-m3:latest（嵌入模型），完全由 GPU 承载，占用约 664 MB 显存，上下文 4096 tokens。
