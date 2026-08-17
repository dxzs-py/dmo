# Ollama 状态汇总报告

## 摘要
本报告汇总了两个并行子代理分别执行 `ollama list` 与 `ollama ps` 的结果，展示当前 Ollama 本地已安装的模型清单以及内存中正在运行的模型状态。

## 一、已安装模型清单（`ollama list`）
当前本地共安装 **7 个模型**，涵盖对话生成与文本嵌入两类用途：

| 模型名称 | ID | 大小 | 修改时间 |
|---------|-----|------|---------|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

**用途分类：**
- **对话/生成模型（5 个）**：`ornith`、`lfm2.5`、`qwen3:8b`、`qwen3.5:9b`、`hermes3:8b`
- **嵌入模型（2 个）**：`dengcao/Qwen3-Embedding-4B`、`bge-m3`

## 二、内存中运行状态（`ollama ps`）
输出仅包含表头（NAME、ID、SIZE、PROCESSOR、CONTEXT、UNTIL），**没有任何模型数据行**。

**结论：** Ollama 服务运行正常，但当前**没有模型正在加载/驻留内存中**。

## 三、结论与建议
- 已安装模型资源充足，覆盖生成与嵌入两大类常用任务。
- 当前无模型驻留内存，若需即时推理，首次调用会触发模型加载（会有一定冷启动延迟）。
- 如需查看某模型详细信息，可执行 `ollama show <模型名>`；如需删除无用模型可执行 `ollama rm <模型名>`。

## 参考文献
- 子代理 subagent_390d82f975ef483a：`ollama list` 原始输出
- 子代理 subagent_b6fa36cbd27542a9：`ollama ps` 原始输出
