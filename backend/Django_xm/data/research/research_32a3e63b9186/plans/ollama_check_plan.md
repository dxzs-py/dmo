# Ollama 状态检查研究计划

## 目标
通过并行执行 `ollama list`（已安装模型列表）和 `ollama ps`（当前运行模型）命令，获取 Ollama 服务的模型清单与运行状态，并将结果汇总保存。

## 任务拆分
| 子任务 | 子代理类型 | 命令 | 期望产出 |
|--------|-----------|------|---------|
| 子代理 1 | general-purpose | `ollama list` | 已安装模型列表（名称、大小、修改时间等） |
| 子代理 2 | general-purpose | `ollama ps` | 当前正在运行的模型进程信息 |

## 执行步骤
1. 制定研究计划（本文件）
2. 并行派发 2 个子代理执行上述命令
3. 等待两个子代理全部完成并获取结果
4. 汇总两个子代理结果
5. 将汇总结果保存到 `D:\programming\langchain\langchain_xm\test_approval.txt`
