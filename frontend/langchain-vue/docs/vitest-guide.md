# Vitest 测试指南

本项目使用 [Vitest](https://vitest.dev/) 作为前端单元测试框架，与 Vite 构建工具深度集成。

## 运行测试

| 命令 | 用途 |
|------|------|
| `npm test` | 单次运行全部测试（CI 模式） |
| `npm run test:watch` | watch 模式，文件变更时自动重跑（开发时使用） |
| `npm run test:coverage` | 运行测试并生成覆盖率报告 |
| `npm run test:ui` | 启动可视化测试界面（浏览器中查看测试结果） |

## 目录结构

测试文件放在源文件同级的 `__tests__/` 目录中：

```
src/
├── components/
│   └── common/
│       ├── ErrorBoundary.vue
│       └── __tests__/
│           └── ErrorBoundary.test.js    # 测试 ErrorBoundary.vue
├── composables/
│   ├── useStreamChat.js
│   └── __tests__/
│       └── useResearchSSE.test.js       # 测试 useResearchSSE.js
├── stores/
│   ├── session.js
│   └── __tests__/
│       └── session.test.js              # 测试 session store
└── utils/
    ├── message-operations.js
    └── __tests__/
        └── message-operations.test.js   # 测试 message-operations.js
```

## 编写测试

### 文件命名

- 测试文件：`__tests__/XxxModule.test.js`（与源文件同名 + `.test.js` 后缀）
- 测试文件放在 `__tests__/` 子目录中，与源文件同级

### 导入约定

显式从 `vitest` 导入测试函数（即使 `globals: true` 已启用，显式导入更利于 IDE 类型提示）：

```js
import { describe, it, expect, beforeEach, vi } from 'vitest'
```

### Mock 约定

屏蔽 `logger` 输出避免测试日志噪音（项目通用模式）：

```js
vi.mock('@/utils/logger', () => ({
  logger: {
    log: () => {},
    info: () => {},
    warn: () => {},
    error: () => {},
    debug: () => {},
  },
}))
```

Mock API 模块避免真实网络请求：

```js
vi.mock('@/api', () => ({
  chatAPI: {
    getSessions: vi.fn(() => Promise.resolve({ code: 200, data: { items: [] } })),
    addMessage: vi.fn(() => Promise.resolve({ code: 200, data: { data: { id: 1 } } })),
  },
}))
```

## 常见测试模式

### Pinia Store 测试

`vitest.setup.js` 已全局注册 Pinia 实例，store 测试中可直接使用 `useXxxStore()`：

```js
import { setActivePinia, createPinia } from 'pinia'
import { useSessionStore } from '@/stores/session'

beforeEach(() => {
  setActivePinia(createPinia()) // 每个 test 重置 Pinia 状态
})

describe('session store', () => {
  it('应正确添加消息', () => {
    const store = useSessionStore()
    store.addMessageToSession('session-1', { id: 'msg-1', role: 'user', content: 'hello' })
    expect(store.sessions.find(s => s.id === 'session-1').messages).toHaveLength(1)
  })
})
```

参考现有测试：[session.test.js](file:///d:/programming/langchain/langchain_xm/frontend/langchain-vue/src/stores/__tests__/session.test.js)

### 组件测试

使用 `@vue/test-utils` 的 `mount` / `shallowMount`：

```js
import { mount } from '@vue/test-utils'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'

describe('ErrorBoundary', () => {
  it('子组件渲染期抛错时显示默认兜底 UI', () => {
    const wrapper = mount(ErrorBoundary, {
      slots: {
        default: '<div>正常内容</div>',
      },
    })
    expect(wrapper.find('.error-boundary').exists()).toBe(false)
  })
})
```

参考现有测试：[ErrorBoundary.test.js](file:///d:/programming/langchain/langchain_xm/frontend/langchain-vue/src/components/common/__tests__/ErrorBoundary.test.js)

### Composable 测试

使用 `@vue/test-utils` 的 `withSetup` 或直接调用 composable 函数：

```js
import { useResearchSSE } from '@/composables/useResearchSSE'

describe('useResearchSSE', () => {
  it('应正确初始化状态', () => {
    const { isStreaming, error } = useResearchSSE()
    expect(isStreaming.value).toBe(false)
    expect(error.value).toBeNull()
  })
})
```

参考现有测试：[useResearchSSE.test.js](file:///d:/programming/langchain/langchain_xm/frontend/langchain-vue/src/composables/__tests__/useResearchSSE.test.js)

### 工具函数测试

纯函数测试最简单，直接导入调用即可：

```js
import { mergeMessageFromBackend, findToolCallInMap } from '../message-operations'

describe('mergeMessageFromBackend', () => {
  it('应合并后端消息到本地消息', () => {
    const existing = { id: 'msg-1', content: 'old' }
    const backend = { id: 100, content: 'new' }
    const result = mergeMessageFromBackend(existing, backend)
    expect(result.content).toBe('new')
    expect(result.backendId).toBe(100)
  })
})
```

参考现有测试：[message-operations.test.js](file:///d:/programming/langchain/langchain_xm/frontend/langchain-vue/src/utils/__tests__/message-operations.test.js)

## 覆盖率

### 查看报告

```bash
npm run test:coverage
```

生成报告在 `coverage/` 目录：
- `coverage/index.html` — HTML 可视化报告（浏览器打开）
- `coverage/lcov.info` — lcov 格式（CI 工具消费）
- 终端输出 text 摘要

### 覆盖率配置

配置位于 `vite.config.js` 的 `test.coverage`：

- **provider**: `v8`（使用 V8 原生覆盖率，无需 instrumentation）
- **include**: `src/**/*.{js,vue}`
- **exclude**: 测试文件自身、auto-imports.d.ts、components.d.ts、main.js、App.vue
- **thresholds**: 初始全 0（不阻断），大文件拆分后逐步提高

### CI 集成

CI pipeline（`.github/workflows/ci.yml`）的 `frontend-quality-gate` job 包含 Test 步骤：

```yaml
- name: Test (Vitest)
  run: npm run test --if-present
```

`--if-present` 防御性保留：即使 package.json 误删 test 脚本也不会导致 CI 失败。

## 配置说明

### vite.config.js test 配置

```js
test: {
  environment: 'jsdom',         // DOM 环境（组件测试需要）
  css: false,                   // 忽略 CSS 导入（EP theme-chalk/*.css）
  setupFiles: ['./vitest.setup.js'], // 全局 setup（Pinia + EP stubs）
  globals: true,                // 允许 describe/it/expect 全局使用
  server: {
    deps: {
      inline: ['element-plus', '@element-plus/icons-vue'], // inline EP 以处理 CSS
    },
  },
  coverage: { /* 见上方覆盖率配置 */ },
}
```

### vitest.setup.js

全局 setup 文件，在每个测试文件执行前运行：
- 创建并激活全局 Pinia 实例
- stub Element Plus 的 `el-message` / `el-message-box`（避免单元测试渲染真实 UI）

## 现有测试清单

| 文件 | 覆盖模块 |
|------|----------|
| `components/common/__tests__/ErrorBoundary.test.js` | ErrorBoundary 组件 |
| `composables/__tests__/useResearchSSE.test.js` | useResearchSSE composable |
| `composables/__tests__/useStreamFinalizer.test.js` | useStreamFinalizer composable |
| `stores/__tests__/approval.test.js` | approval store |
| `stores/__tests__/research.test.js` | research store |
| `stores/__tests__/session-fixes.test.js` | session store 修复 |
| `stores/__tests__/session.test.js` | session store |
| `stores/__tests__/sync.test.js` | sync store |
| `utils/__tests__/message-operations.test.js` | message-operations 工具 |
| `utils/__tests__/tool-adapters.test.js` | tool-adapters 工具 |
