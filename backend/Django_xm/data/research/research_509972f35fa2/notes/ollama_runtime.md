# 研究笔记：Ollama 运行时状态信息采集

采集时间：2025-XX-XX（由子代理执行）
采集方式：两级代理调度（主 agent → A1/A2 → B1/B2）

## 子代理结构
- A1（general-purpose，thread: subagent_6e42e85227ae43de）
  - 内部派发 B1 执行 `ollama list`，wait_for_subagent 等待后上报 ✅
- A2（general-purpose，thread: subagent_146479ceb3664471）
  - 内部派发 B2 执行 `ollama ps`，wait_for_subagent 等待后上报 ✅

## A1 结果（ollama list —— 已安装模型）
| 模型名称 | ID（前12位） | 大小 | 修改时间 |
|---|---|---|---|
| ornith:latest | a75697c14589 | 5.6 GB | 4 周前 |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 个月前 |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 个月前 |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 个月前 |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 个月前 |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 个月前 |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 个月前 |

- 共 7 个模型，总大小约 31.4 GB
- 通用/聊天模型 5 个：ornith、lfm2.5、qwen3:8b、qwen3.5:9b、hermes3:8b
- 嵌入模型 2 个：dengcao/Qwen3-Embedding-4B、bge-m3
- 最近更新：ornith:latest（4 周前）

## A2 结果（ollama ps —— 运行中模型）
```
NAME    ID    SIZE    PROCESSOR    CONTEXT    UNTIL
```
- 仅有表头，无数据行 → 当前没有任何模型在运行
- 结论：Ollama 服务空闲，无模型加载到内存

## 关键观察
- 环境已安装模型资源充足（7 个），但运行时无任何模型处于加载状态
- 需要推理时需通过 `ollama run <模型名>` 或 API 调用触发加载
