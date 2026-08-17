# 研究笔记：Ollama 状态查询结果

## 子代理1 — ollama list（已安装模型）
来源：subagent_55909acc7e844e87

| NAME | ID | SIZE | MODIFIED |
|------|------|------|------|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

共 7 个模型：5 个对话/生成模型，2 个嵌入模型。

## 子代理2 — ollama ps（正在运行的模型）
来源：subagent_1914486c94f34500

| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|------|------|------|------|------|
| bge-m3:latest | 790764642607 | 664 MB | 100% GPU | 4096 | 2 minutes from now |

仅 bge-m3 嵌入模型正在运行，完全加载在 GPU，空闲约 2 分钟后自动卸载。
