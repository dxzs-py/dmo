# Ollama 运行时状态信息采集报告

## 摘要
本报告基于两级代理调度架构完成：主调度 agent 并行派发 A1、A2 两个 general-purpose 子代理，A1 内部再派发 B1 执行 `ollama list`（已安装模型列表），A2 内部再派发 B2 执行 `ollama ps`（运行中模型状态）。两条命令均执行成功。结果显示：当前环境共安装 7 个 Ollama 模型（约 31.4 GB），但当前没有任何模型处于运行/加载状态，Ollama 服务处于空闲状态。

## 1. 调度执行流程
| 层级 | 角色 | 任务 | 状态 |
|---|---|---|---|
| 主 agent | 调度者 | 制定计划、并行派发 A1/A2、等待结果、汇总报告 | ✅ |
| A1 | general-purpose | 内部派发 B1 执行 `ollama list`，等待后上报 | ✅ |
| B1 | general-purpose | 执行 `ollama list` | ✅ |
| A2 | general-purpose | 内部派发 B2 执行 `ollama ps`，等待后上报 | ✅ |
| B2 | general-purpose | 执行 `ollama ps` | ✅ |

## 2. 已安装模型（ollama list）
| # | 模型名称 | ID（前12位） | 大小 | 修改时间 |
|---|---|---|---|---|
| 1 | ornith:latest | a75697c14589 | 5.6 GB | 4 周前 |
| 2 | lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 个月前 |
| 3 | dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 个月前 |
| 4 | bge-m3:latest | 790764642607 | 1.2 GB | 2 个月前 |
| 5 | qwen3:8b | 500a1f067a9f | 5.2 GB | 2 个月前 |
| 6 | qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 个月前 |
| 7 | hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 个月前 |

**要点**：
- 共 7 个模型，总大小约 31.4 GB。
- 通用/聊天模型 5 个：ornith、lfm2.5、qwen3:8b、qwen3.5:9b、hermes3:8b。
- 嵌入模型 2 个：dengcao/Qwen3-Embedding-4B、bge-m3。
- 最近更新：ornith:latest（4 周前）。

## 3. 运行中模型（ollama ps）
```
NAME    ID    SIZE    PROCESSOR    CONTEXT    UNTIL
```
- 输出仅含表头、无数据行：**当前没有任何模型正在运行**。
- 无进程 ID、无显存占用、无运行时长信息。

## 4. 结论
1. 环境具备丰富的本地模型资源（7 个模型，覆盖通用对话与嵌入场景），可满足推理需求。
2. 当前 Ollama 运行时为空闲状态，未加载任何模型；首次调用某模型时需经历加载过程（将占用显存/内存）。
3. 调度链路（主 agent → A1/A2 → B1/B2，两层嵌套 + 逐层 wait_for_subagent）执行顺畅，所有命令均成功返回。

## 5. 建议
- 若需立即使用某个模型，可通过 `ollama run <模型名>` 或调用 Ollama API（如 `POST /api/generate`）触发模型加载。
- 为优化响应延迟，可预先加载高频模型（如 ornith、qwen3:8b）并保持常驻。
- 日常可结合 `ollama ps` 监控运行态，避免过多模型同时驻留占用显存。

## 6. 参考文献 / 数据来源
- 子代理 A1（thread: subagent_6e42e85227ae43de）→ B1 执行 `ollama list` 的原始输出（见 /notes/ollama_runtime.md）。
- 子代理 A2（thread: subagent_146479ceb3664471）→ B2 执行 `ollama ps` 的原始输出（见 /notes/ollama_runtime.md）。
- 研究计划：/plans/research_plan.md。
- 研究笔记：/notes/ollama_runtime.md。
