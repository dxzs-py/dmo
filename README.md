# LangChain 智能体平台

基于 Django 5.2 + DRF + LangChain 的智能体平台，前端为 Vue 3 + Element Plus + Tailwind CSS + Pinia。核心能力：多模型对话（OpenAI / Anthropic / DeepSeek / Groq / Ollama / 千帆）、深度研究（子智能体协作）、知识库 RAG（多向量库后端）、工作流学习（出题-批改-反馈）、工具调用审批（人机协同）。前后端通过 `/api/v1/` 前缀的 REST JSON 交互，聊天回复走 SSE 流式推送，实时状态经 WebSocket 同步。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端框架 | Python 3.12、Django 5.2、Django REST Framework、drf-spectacular（OpenAPI） |
| 智能体 | LangChain、LangGraph、Deep Agents、langchain-openai/anthropic/groq 等 |
| 异步与实时 | Celery（异步任务）、Django Channels + WebSocket、SSE 流式响应 |
| 数据与缓存 | PostgreSQL（Docker 容器）+ pgvector、Redis（缓存 / Channel Layer / Celery Broker） |
| 前端框架 | Vue 3（`<script setup>` + Composition API）、Vite 7、Pinia 3、Vue Router 4 |
| 前端 UI 与工程 | Element Plus、Tailwind CSS 3、Axios、Vitest、oxlint + ESLint + Prettier |

## 目录结构总览

```text
langchain_xm/
├── backend/
│   └── Django_xm/                 # Django 后端
│       ├── manage.py              # 管理入口
│       ├── requirements.txt       # 运行依赖
│       ├── requirements-dev.txt   # 开发依赖（mypy 类型检查 / lint）
│       ├── .env.example           # 环境变量模板
│       ├── Django_xm/             # 项目包
│       │   ├── settings/          # 配置拆分：base / dev / prod / test
│       │   ├── apps/              # 业务应用（见下表）
│       │   ├── common/            # 跨应用公共模块（审批网关、SSE 工具、错误码等）
│       │   ├── tasks/             # Celery 异步任务
│       │   └── services/          # fastapi_service：FastAPI 会话执行服务
│       ├── data/                  # 运行数据（研究产物、技能、上传文件）
│       └── logs/                  # 运行日志
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
| `knowledge` | 知识库 RAG：文档解析与分块、向量库多后端（pgvector / Chroma / FAISS / Milvus）、检索、NLI 校验、索引重建 |
| `learning` | 工作流学习：规划 / 出题 / 批改 / 反馈节点与学习会话管理 |

## 快速启动

### 后端（端口 8000）

```bash
cd backend/Django_xm
conda activate langchain_xm
python manage.py migrate
python manage.py runserver
```

前置条件（conda 环境、PostgreSQL 容器、`.env` 配置）：详见 [backend/Django_xm/README.md](backend/Django_xm/README.md)。

### 前端（端口 8080）

```bash
cd frontend/langchain-vue
nvm use 20.19.5
npm install
cp .env.example .env.development
npm run dev
```

更多说明（命令表、环境变量、与后端联调）：详见 [frontend/langchain-vue/README.md](frontend/langchain-vue/README.md)。

## 测试命令总表

| 端 | 命令 | 说明 |
| --- | --- | --- |
| 后端 | `cd backend/Django_xm && conda activate langchain_xm && python manage.py test Django_xm.apps --settings=Django_xm.settings.test` | PostgreSQL 临时测试库（自动创建/销毁，不触碰开发库），需 docker PostgreSQL 运行中 |
| 前端 | `cd frontend/langchain-vue && npm run test` | Vitest 单次运行（`npm run test:watch` 进入监听模式） |
