# 前端（frontend/langchain-vue）

Vue 3 + Element Plus + Tailwind CSS + Pinia 的智能体平台前端。开发模式由 Vite 提供 dev server（默认端口 8080），API 经代理转发至后端（默认 `http://127.0.0.1:8000`）。

## 环境要求

- Node v20.19.5（版本已固定在 `.nvmrc`）：

```bash
nvm use 20.19.5
```

- 依赖使用 npm 管理：

```bash
npm install
```

- 环境变量：复制 `.env.example` 为 `.env.development`（或参考 `.env.example` 手动配置）：

```bash
cp .env.example .env.development
```

## npm 命令

| 命令 | 用途 |
| --- | --- |
| `npm run dev` | 启动 Vite 开发服务器（默认 `http://localhost:8080`） |
| `npm run build` | 生产构建（输出至 `dist/`，terser 压缩 + gzip 预压缩） |
| `npm run preview` | 本地预览生产构建产物 |
| `npm run test` | Vitest 单次运行全部单元测试 |
| `npm run test:watch` | Vitest 监听模式，文件变更自动重跑 |
| `npm run lint` | 依次执行 oxlint 与 ESLint 检查并自动修复 |
| `npm run format` | Prettier 格式化 `src/` |

## 环境变量说明

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `VITE_DEV_HOST` | `localhost` | dev server 绑定的主机名 |
| `VITE_DEV_PORT` | `8080` | dev server 监听端口 |
| `VITE_DEV_OPEN` | `true` | 是否在启动后自动打开浏览器（仅 `true` 时开启） |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | 开发代理指向的后端地址，解决跨域 |

## 与后端联调

开发模式下 Vite 将以下路径代理到 `VITE_API_PROXY_TARGET`（见 `vite.config.js`）：

- `/api`（REST 接口，含 SSE 流式响应）
- `/ws`（WebSocket 实时同步，已开启 `ws: true`）
- `/health`、`/admin`、`/media`

后端默认运行在 `http://127.0.0.1:8000`，保持 `VITE_API_PROXY_TARGET` 指向该地址即可，前端无需处理跨域。后端启动方式见仓库根目录 README 或 `backend/Django_xm/README.md`。

## 目录结构简述

```text
src/
├── api/           # 接口层：axios.js 封装实例 + 按业务拆分（chat / research / workflow 等）
├── components/    # 组件：chat / common / layout / research / workflow / ai-elements
├── composables/   # 组合式函数：流式聊天、实时同步、错误处理、键盘快捷键等
├── config/        # 前端应用配置
├── router/        # Vue Router 路由定义
├── stores/        # Pinia 状态（user / session / chat / theme 等，sync/ 为实时同步逻辑）
├── types/         # 常量与类型定义
├── utils/         # 工具函数：sse.js（流式解析）、sessionTransformers.js（命名转换边界）等
├── views/         # 页面级组件（ChatView / RagView / WorkflowView 等）
├── assets/css/    # 全局样式：tailwind.css / theme.css / reset.css / animations.css
├── App.vue        # 根组件
└── main.js        # 入口文件
```
