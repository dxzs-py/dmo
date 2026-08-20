# LangChain 智能体平台

基于 Django 5.2 + DRF + LangChain 的智能体平台，前端为 Vue 3 + Element Plus + Tailwind CSS + Pinia。

## 功能模块

| 模块 | 说明 |
| --- | --- |
| 智能体对话 | 多模型对话（OpenAI / Anthropic / DeepSeek / Groq / Ollama / 千帆），SSE 流式回复，WebSocket 实时状态同步 |
| 深度研究 | 子智能体协作的深度研究任务：计划 → 检索 → 撰写报告，产物落盘 `data/research/` |
| 知识库 RAG | 文档上传解析与分块、多向量库后端（pgvector / Chroma / FAISS / Milvus）、NLI 校验、来源引用 |
| 工作流学习 | 学习会话工作流：规划 → 出题 → 批改 → 反馈 |
| 工具调用审批 | 人机协同：高危工具调用需人工确认，支持批量审批、超时处理、跨浏览器状态同步 |
| 模型管理 | 多 provider 注册与切换、模型连通性测试、费用与 Token 用量统计 |

前后端通过 `/api/v1/` 前缀的 REST JSON 交互，聊天回复走 SSE 流式推送，实时状态经 WebSocket 同步。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端框架 | Python 3.12、Django 5.2、Django REST Framework、drf-spectacular（OpenAPI） |
| 智能体 | LangChain、LangGraph、Deep Agents、langchain-openai/anthropic/groq 等 |
| 异步与实时 | Celery（异步任务）、Django Channels + WebSocket、SSE 流式响应、FastAPI（会话执行服务） |
| 数据与缓存 | PostgreSQL + pgvector、Redis（缓存 / Channel Layer / Celery Broker） |
| 前端框架 | Vue 3（`<script setup>` + Composition API）、Vite 7、Pinia 3、Vue Router 4 |
| 前端 UI 与工程 | Element Plus、Tailwind CSS 3、Axios、Vitest、oxlint + ESLint + Prettier |

## 目录结构总览

```text
langchain_xm/
├── backend/
│   └── Django_xm/                 # Django 后端
│       ├── manage.py              # 管理入口
│       ├── run_fastapi.py         # FastAPI 会话执行服务入口（端口 8001）
│       ├── requirements.txt       # 运行依赖
│       ├── requirements-dev.txt   # 开发依赖（mypy 类型检查 / lint）
│       ├── .env.example           # 环境变量模板
│       ├── Django_xm/             # 项目包
│       │   ├── settings/          # 配置拆分：base / dev / prod / test
│       │   ├── apps/              # 业务应用（见下表）
│       │   ├── common/            # 跨应用公共模块（审批网关、SSE 工具、错误码等）
│       │   ├── tasks/             # Celery 异步任务
│       │   └── services/          # fastapi_service：FastAPI 会话执行服务
│       ├── data/                  # 运行数据（启动时自动创建，不入库）
│       └── logs/                  # 运行日志（启动时自动创建，不入库）
└── frontend/
    └── langchain-vue/             # Vue 3 前端
        ├── .nvmrc                 # Node 版本固定 20.19.5
        ├── .env.development       # 开发环境变量（从 .env.example 复制）
        ├── vite.config.js         # Vite 配置（dev 代理、构建分包）
        └── src/
            ├── api/               # 接口层（axios 封装 + 按业务拆分）
            ├── components/        # 组件（chat / common / layout / research / workflow / ai-elements）
            ├── composables/       # 组合式函数（流式聊天、实时同步、错误处理等）
            ├── router/            # 路由
            ├── stores/            # Pinia 状态（按领域拆分，含 sync/ 实时同步）
            ├── utils/             # 工具函数（SSE 解析、命名转换、错误处理等）
            ├── views/             # 页面级组件
            ├── config/            # 前端配置
            ├── types/             # 常量与类型定义
            └── assets/css/        # 全局样式（Tailwind、主题、动画）
```

### 后端主要应用（`backend/Django_xm/Django_xm/apps/`）

| 应用 | 职责 |
| --- | --- |
| `agent_hub` | 智能体构建与执行：多类构建器、子智能体工具（spawn/wait）、审批中间件、容错调用 |
| `ai_engine` | AI 引擎：多模型 provider、LLM 工厂与缓存、护栏（guardrails）、费用/Token 追踪、子智能体运行时、plan-execute 工作流 |
| `analytics` | 使用分析：Token 与费用统计、工具使用分析、请求埋点中间件 |
| `approvals` | 工具调用审批：审批单生命周期、超时处理、批量恢复 |
| `attachments` | 附件管理：上传校验、内容提取、生命周期清理 |
| `cache_manager` | 缓存管理：会话缓存与缓存状态查询 |
| `chat` | 聊天核心：会话与消息模型、SSE 流式生成、WebSocket consumer、斜杠命令、RAG / 深度研究聊天服务 |
| `context_manager` | 上下文管理：压缩、分层记忆、知识图谱、Token 预算、检索增强 |
| `core` | 基础设施：认证、限流、公共中间件、数据库监控、任务模型 |
| `knowledge` | 知识库 RAG：文档解析与分块、向量库多后端、检索、NLI 校验、索引重建 |
| `learning` | 工作流学习：规划 / 出题 / 批改 / 反馈节点与学习会话管理 |

## 快速启动

### 前置条件

| 依赖 | 要求 |
| --- | --- |
| Python | 3.12（conda 环境名 `langchain_xm`） |
| Node.js | v20.19.5（版本固定在 `.nvmrc`） |
| PostgreSQL | 5432 端口，启用 pgvector 扩展 |
| Redis | Celery Broker / Channel Layer / 缓存（深度研究等异步功能必需） |

### 1. 后端初始化（首次）

```bash
# 创建并激活 conda 环境
conda create -n langchain_xm python=3.12
conda activate langchain_xm

cd backend/Django_xm

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（填入数据库密码、各模型 API Key 等）
cp .env.example .env

# 启动 PostgreSQL（含 pgvector）与 Redis 后，执行数据库迁移
python manage.py migrate
```

`.env` 关键变量：`DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME`（数据库）、`OPENAI_API_KEY` 等（模型 provider）、`TAVILY_API_KEY`（联网搜索）。完整清单见 `.env.example`。

### 2. 前端初始化（首次）

```bash
cd frontend/langchain-vue
nvm use 20.19.5
npm install
cp .env.example .env.development
```

### 3. 启动服务（5 个终端）

| 服务 | 端口 | 命令（均在 `backend/Django_xm` 下，需 `conda activate langchain_xm`） |
| --- | --- | --- |
| Django 主服务 | 8000 | `python manage.py runserver` |
| FastAPI 会话执行服务 | 8001 | `python run_fastapi.py` |
| Celery beat 定时调度 | - | `python -m celery -A Django_xm beat -l info` |
| Celery worker 异步任务 | - | `python -m celery -A Django_xm worker -P threads -c 4 -Q celery,rag,chat -l info` |
| 前端 Vite dev server | 8080 | `cd frontend/langchain-vue && npm run dev` |

> 说明：
> - 最小体验（普通对话）只需启动 Django + 前端；深度研究、RAG 文档处理等异步功能需完整启动 Celery（beat + worker）与 Redis。
> - `run_fastapi.py` 使用 `loop="none"` 绕过 uvicorn 默认事件循环工厂，解决 Windows 下 psycopg 异步驱动与 ProactorEventLoop 不兼容问题，不要改用 `python -m uvicorn` 启动。
> - worker 的 `-Q celery,rag,chat` 对应任务路由：默认队列 `celery`、RAG 文档处理 `rag`、聊天附件任务 `chat`。
> - `data/`、`logs/` 等运行目录由 `settings/base.py` 启动时自动创建，克隆后无需手工建立。

### 4. 访问

- 前端页面：http://localhost:8080
- 后端 API：http://127.0.0.1:8000/api/v1/
- Django admin：http://127.0.0.1:8000/admin/（首次需 `python manage.py createsuperuser`）

更多说明（环境变量明细、settings 环境切换、管理命令）：详见 [backend/Django_xm/README.md](backend/Django_xm/README.md) 与 [frontend/langchain-vue/README.md](frontend/langchain-vue/README.md)。

## 测试命令总表

| 端 | 命令 | 说明 |
| --- | --- | --- |
| 后端 | `cd backend/Django_xm && conda activate langchain_xm && python manage.py test Django_xm.apps --settings=Django_xm.settings.test` | PostgreSQL 临时测试库（自动创建/销毁，不触碰开发库），需 PostgreSQL 运行中 |
| 前端 | `cd frontend/langchain-vue && npm run test` | Vitest 单次运行（`npm run test:watch` 进入监听模式） |
