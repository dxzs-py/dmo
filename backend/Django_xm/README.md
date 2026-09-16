# 后端（backend/Django_xm）

Django 5.2 + DRF + LangChain 智能体平台后端。API 统一前缀 `/api/v1/`，REST JSON 交互，聊天回复 SSE 流式推送，实时状态经 WebSocket 同步。

## 环境前置

- Python 3.12，conda 环境名 `langchain_xm`：

```bash
# 首次创建（如环境已存在可跳过）
conda create -n langchain_xm python=3.12
conda activate langchain_xm
```

- PostgreSQL：运行在 Docker 容器中，默认连接 `localhost:5432`（可通过 `.env` 中 `DB_HOST` / `DB_PORT` 调整）。
- Redis（开发期可选）：`dev.py` 启动时探测 Redis（`REDIS_URL`，默认值见 `app_cfg`），可用则作为 Channel Layer / 缓存 / Celery Broker；不可用自动回退进程内实现（`InMemoryChannelLayer`）并在日志中给出警告。

## 环境变量配置

复制模板生成本地配置：

```bash
cp .env.example .env
```

`.env.example` 内置变量大类（完整清单与默认值直接参见该文件，此处不重复）：


| 大类        | 关键变量                                                                                 | 说明                       |
| ----------- | ---------------------------------------------------------------------------------------- | -------------------------- |
| Django 核心 | `SECRET_KEY`、`DEBUG`、`ALLOWED_HOSTS`                                                   | 基础安全配置               |
| 数据库      | `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`                                | PostgreSQL 连接            |
| OpenAI API  | `OPENAI_API_KEY`、`OPENAI_API_BASE`、`OPENAI_MODEL` 等                                   | 默认 LLM 提供方            |
| Tavily 搜索 | `TAVILY_API_KEY`、`TAVILY_MAX_RESULTS`                                                   | 联网搜索工具               |
| 高德地图    | `AMAP_KEY`                                                                               | 地图工具                   |
| 服务器      | `SERVER_HOST`、`SERVER_PORT`、`SERVER_RELOAD`                                            | 服务监听配置               |
| 日志        | `LOG_LEVEL`、`LOG_FILE`、`LOG_ROTATION`、`LOG_RETENTION`                                 | 日志级别与轮转             |
| CORS        | `CORS_ALLOWED_ORIGINS`                                                                   | 允许的前端来源（逗号分隔） |
| 向量存储    | `VECTOR_STORE_PATH`、`VECTOR_STORE_TYPE`、`EMBEDDING_MODEL`、`EMBEDDING_BATCH_SIZE`      | 向量库后端与嵌入模型       |
| 文本分块    | `CHUNK_SIZE`、`CHUNK_OVERLAP`                                                            | 文档切分参数               |
| 检索器      | `RETRIEVER_SEARCH_TYPE`、`RETRIEVER_K`、`RETRIEVER_SCORE_THRESHOLD`、`RETRIEVER_FETCH_K` | RAG 检索参数               |
| Agent       | `AGENT_MAX_ITERATIONS`、`AGENT_MAX_EXECUTION_TIME`                                       | 智能体迭代限制             |
| RAG Agent   | `RAG_AGENT_MAX_ITERATIONS`、`RAG_AGENT_RETURN_SOURCE_DOCUMENTS`                          | RAG 智能体行为             |

## settings 环境说明

配置按环境拆分在 `Django_xm/settings/`，由环境变量 `DJANGO_ENV` 决定加载哪个模块：


| `DJANGO_ENV` 取值                            | 加载模块  | 用途                       |
| -------------------------------------------- | --------- | -------------------------- |
| 缺省 /`development` / 其他                   | `dev.py`  | 开发环境（默认，无需设置） |
| `production` / `prod`                        | `prod.py` | 生产环境                   |
| 显式指定`--settings=Django_xm.settings.test` | `test.py` | 测试专用（见下文）         |

## 依赖安装

```bash
conda activate langchain_xm
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 可选：mypy 类型检查与 lint 相关依赖
```

## 启动

```bash
conda activate langchain_xm
python manage.py migrate
python manage.py runserver
```

默认监听 `http://127.0.0.1:8000`。

## 测试

```bash
conda activate langchain_xm
python manage.py test Django_xm.apps --settings=Django_xm.settings.test
```

测试配置 `Django_xm.settings.test` 的数据库直接继承 base.py 的 PostgreSQL 配置（贴合实际运行环境）：Django 测试运行器自动创建 `test_<DB_NAME>` 临时库并在测试结束后销毁，**不会触碰开发库数据**，但要求 docker 容器中的 PostgreSQL 处于运行状态。缓存使用 LocMemCache、Channel Layer 使用 InMemoryChannelLayer、Celery 以 EAGER 模式同步执行，因此跑测试不需要启动 Redis。

## 常用管理命令

以下子命令来自 `python manage.py help` 的真实输出。

```bash
conda activate langchain_xm

python manage.py createsuperuser                 # 创建管理员账号
python manage.py makemigrations                  # 生成迁移文件
python manage.py migrate                         # 应用数据库迁移
python manage.py showmigrations                   # 查看迁移状态
python manage.py check                           # 项目自检
python manage.py shell                           # 交互式 Python shell
python manage.py dbshell                         # 进入数据库命令行
python manage.py collectstatic                   # 收集静态文件
python manage.py dumpdata <app_label> > data.json  # 导出数据
python manage.py loaddata data.json              # 导入数据
```

项目自定义命令：

```bash
python manage.py manage_attachments              # 附件管理（attachments 应用）
python manage.py manage_chat_backups             # 聊天备份管理（chat 应用）
python manage.py rebuild_index                   # 知识库索引重建（knowledge 应用）
```

其他可用子命令（节选，完整列表运行 `python manage.py help`）：`spectacular`（drf-spectacular 生成 OpenAPI schema）、`flushexpiredtokens`（清理过期 JWT token）、`clearsessions`（清理过期会话）、`runworker`（Channels worker）。
