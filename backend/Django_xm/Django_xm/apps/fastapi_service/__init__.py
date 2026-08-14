"""FastAPI Agent 执行服务（仅代码归类，不注册 Django App）。

深度研究长任务执行层，独立进程运行（端口 8001），
与 Django Web 进程共享 PostgreSQL + Redis。
"""
