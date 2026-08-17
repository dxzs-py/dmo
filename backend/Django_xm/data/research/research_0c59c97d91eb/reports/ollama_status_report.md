# Ollama 状态查询报告

## 摘要
本报告汇总了两个并行子代理在本机执行的 Ollama 状态查询结果：`ollama list`（已安装模型）与 `ollama ps`（正在运行的模型）。查询显示本机共安装 7 个模型，当前仅有 1 个模型（bge-m3）正在 GPU 上运行。

## 一、已安装模型（ollama list）
本机共安装 **7 个模型**，总占用约 31.4 GB：

| NAME | ID | SIZE | MODIFIED |
|------|------|------|----------|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

**模型分类：**
- 对话/生成模型（5 个）：ornith、lfm2.5、qwen3:8b、qwen3.5:9b、hermes3:8b
- 嵌入模型（2 个）：dengcao/Qwen3-Embedding-4B、bge-m3

## 二、正在运行的模型（ollama ps）
当前仅有 **1 个模型**正在运行：

| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|------|------|-----------|---------|-------|
| bge-m3:latest | 790764642607 | 664 MB | 100% GPU | 4096 | 2 minutes from now |

- bge-m3 嵌入模型完全加载在 GPU 上运行（100% GPU）
- 上下文窗口长度：4096 tokens
- 由于空闲，预计约 2 分钟后自动卸载

## 三、结论与建议
1. 本机 Ollama 服务运行正常，命令执行无报错。
2. 已安装模型以对话生成模型为主，辅以 2 个嵌入模型；其中 qwen3.5:9b 为最大模型（6.6 GB）。
3. 当前仅 bge-m3 处于运行态且占用 GPU，适合作为嵌入用途；如需释放显存可执行 `ollama stop bge-m3` 或等待其空闲自动卸载。
4. 若需进一步了解某模型细节，可执行 `ollama show <模型名>`。

## 参考文献
- 子代理 1（general-purpose）：`ollama list` 执行输出（thread: subagent_55909acc7e844e87）
- 子代理 2（general-purpose）：`ollama ps` 执行输出（thread: subagent_1914486c94f34500）
- Ollama 官方文档：https://github.com/ollama/ollama
