"""Plan-Execute 工作流提示词模板"""

PLAN_PROMPT = """你是一个任务规划专家。请为以下查询制定一个清晰的执行计划。

查询: {query}

对于每个步骤，请指定：
- description: 步骤的详细描述
- tool: 建议使用的工具名称（如 search/calculator/code/none）
- args: 工具调用参数（如果没有参数则为空字典）

请确保步骤合理、可执行，工具选择恰当。"""

REFLECT_PROMPT = """你是一个执行评估专家。请评估当前执行结果。

原始查询: {query}
执行计划: {plan}
当前步骤: {current_step}/{total_steps}
当前步骤描述: {step_description}
执行结果: {result}

请以 JSON 格式返回评估结果:
- "status": "continue"（继续下一步）| "revise"（需要修正计划）| "complete"（计划已完成）
- "reason": 评估理由
- "revised_plan": 仅当 status 为 "revise" 时提供修正后的计划

只返回 JSON，不要其他文本。"""

RESPOND_PROMPT = """你是一个综合回答专家。请根据以下信息生成最终回答。

原始查询: {query}
执行计划: {plan}
所有执行结果: {results}

请生成一个全面、准确、结构化的最终回答。"""
