# 研究笔记：ollama list 与 ollama ps 执行结果

## 子代理 1：ollama list（已安装模型列表）
命令执行成功，共 7 个已安装模型：

| 模型名称 | ID | 大小 | 修改时间 |
|---|---|---|---|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

## 子代理 2：ollama ps（运行中的模型状态）
命令执行成功，仅返回表头，无任何运行中的模型记录。
结论：Ollama 服务正常运行，但当前没有任何模型被加载到内存中运行。

## 综合观察
- 模型涵盖聊天/生成模型（qwen3、qwen3.5、hermes3、ornith、lfm2.5）和嵌入模型（bge-m3、Qwen3-Embedding-4B）
- 体积最大：qwen3.5:9b（6.6 GB）；体积最小：bge-m3（1.2 GB）
- 最近更新：ornith:latest（4 周前）
- 当前无活动负载，所有模型均处于空闲未加载状态
