# 研究计划：嵌套子代理调度实验

## 目标
通过嵌套子代理调度，验证子代理 A（web-researcher）内部再派发子代理 B（general-purpose）执行 `ollama list` 命令，并等待 B 完成后汇总结果。

## 步骤
1. 主 agent 制定计划（本文件）
2. 主 agent 派发子代理 A（web-researcher）
   - A 内部再派发子代理 B（general-purpose）执行 `ollama list`
   - A 使用 wait_for_subagent 等待 B 完成后汇总
3. 主 agent 使用 wait_for_subagent 等待 A 完成并获取结果
4. 将汇总结果保存到 `D:\programming\langchain\langchain_xm\test_nested_deepresearch.txt`

## 产出
- 汇总结果文件
