# 研究计划：Ollama 状态检查（嵌套子代理调度）

## 目标
通过嵌套子代理架构并行收集 Ollama 的模型列表与运行状态：
- A1（general-purpose）内部派发 B1 执行 `ollama list`
- A2（general-purpose）内部派发 B2 执行 `ollama ps`

## 调度架构
```
主 agent
 ├─ spawn A1 (general-purpose)
 │    └─ spawn B1 → 执行 `ollama list` → wait_for_subagent(B1)
 └─ spawn A2 (general-purpose)
      └─ spawn B2 → 执行 `ollama ps` → wait_for_subagent(B2)
主 agent → wait_for_subagent(A1, A2) → 汇总
```

## 步骤
1. 制定计划（本文件）
2. 并行派发 A1、A2
3. 等待 A1、A2 完成
4. 汇总写入 /notes/ 与 /reports/
