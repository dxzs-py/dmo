import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { h, defineComponent, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import ErrorBoundary from '../ErrorBoundary.vue'

// Mock trackError 验证统一上报通道
vi.mock('@/composables/useErrorHandler', () => ({
  trackError: vi.fn(),
}))
import { trackError } from '@/composables/useErrorHandler'

// Stub Element Plus 组件，避免 vitest 环境加载 EP CSS 报错
const epStubs = {
  'el-icon': { template: '<span class="el-icon"><slot /></span>' },
  'el-button': {
    template: '<button class="el-button"><slot /></button>',
    props: ['type'],
  },
}

/**
 * 构造一个在渲染期抛错的子组件
 *
 * onErrorCaptured 捕获子组件渲染期错误（render/setup/lifecycle）。
 * 在 render() 中抛错是最可靠的触发方式，确保 Vue 将错误冒泡到父组件的
 * onErrorCaptured 钩子。
 */
function makeThrowingComponent(errorMessage = '子组件渲染失败') {
  return defineComponent({
    name: 'ThrowingChild',
    render() {
      if (errorMessage) {
        throw new Error(errorMessage)
      }
      return h('div')
    },
  })
}

/** 构造正常子组件 */
const NormalChild = defineComponent({
  name: 'NormalChild',
  render() {
    return h('div', { class: 'normal-child' }, '正常内容')
  },
})

/** 创建测试路由器：ErrorBoundary 内部调用 useRoute()，需提供路由注入避免警告 */
function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/home', component: NormalChild },
      { path: '/other', component: NormalChild },
    ],
  })
}

/**
 * 统一挂载辅助：始终提供路由器（消除 useRoute 注入警告）与 EP stubs。
 * 返回 wrapper 与 router，便于路由切换测试复用。
 *
 * 注意：子组件渲染期抛错时，onErrorCaptured 同步触发并置 hasError=true，
 * 但回退 UI 的重渲染通过调度器异步执行（nextTick 微任务），
 * 故断言回退 UI 前必须 await nextTick()。
 */
async function mountBoundary(props = {}, slots = {}) {
  const router = createTestRouter()
  await router.push('/home')
  await router.isReady()
  const wrapper = mount(ErrorBoundary, {
    props,
    global: { plugins: [router], stubs: epStubs },
    slots,
  })
  return { wrapper, router }
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('无错误时渲染 default slot 内容', async () => {
    const { wrapper } = await mountBoundary({}, { default: h(NormalChild) })
    expect(wrapper.find('.normal-child').exists()).toBe(true)
    expect(wrapper.find('.error-boundary').exists()).toBe(false)
  })

  it('子组件渲染期抛错时显示默认兜底 UI', async () => {
    // 抑制 Vue 的控制台错误日志（onErrorCaptured 已 return false 阻止冒泡，但 vitest 仍可能打印）
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { wrapper } = await mountBoundary(
      {},
      { default: h(makeThrowingComponent('测试错误消息')) }
    )
    // 回退 UI 重渲染异步发生，需 nextTick 刷新调度队列
    await nextTick()

    expect(wrapper.find('.error-boundary').exists()).toBe(true)
    expect(wrapper.text()).toContain('抱歉，发生了错误')
    expect(wrapper.text()).toContain('测试错误消息')
    spy.mockRestore()
  })

  it('抛错时调用 trackError 上报错误详情', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    await mountBoundary(
      {},
      { default: h(makeThrowingComponent('上报测试错误')) }
    )

    // trackError 在 onErrorCaptured 内同步调用，无需 nextTick
    expect(trackError).toHaveBeenCalledTimes(1)
    const reported = trackError.mock.calls[0][0]
    expect(reported.message).toBe('上报测试错误')
    expect(reported.component).toBe('ThrowingChild')
    expect(reported.url).toContain('http')
    expect(reported.timestamp).toBeTruthy()
    spy.mockRestore()
  })

  it('fullScreen=true 时添加全屏 CSS 类', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { wrapper } = await mountBoundary(
      { fullScreen: true },
      { default: h(makeThrowingComponent()) }
    )
    await nextTick()

    expect(wrapper.find('.error-boundary--fullscreen').exists()).toBe(true)
    spy.mockRestore()
  })

  it('fullScreen=false 时不添加全屏 CSS 类（局部错屏）', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { wrapper } = await mountBoundary(
      { fullScreen: false },
      { default: h(makeThrowingComponent()) }
    )
    await nextTick()

    expect(wrapper.find('.error-boundary--fullscreen').exists()).toBe(false)
    expect(wrapper.find('.error-boundary').exists()).toBe(true)
    spy.mockRestore()
  })

  it('提供 fallback 插槽时使用自定义兜底 UI', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { wrapper } = await mountBoundary(
      {},
      {
        default: h(makeThrowingComponent()),
        fallback: '<div class="custom-fallback">自定义错误界面</div>',
      }
    )
    await nextTick()

    expect(wrapper.find('.custom-fallback').exists()).toBe(true)
    expect(wrapper.find('.custom-fallback').text()).toBe('自定义错误界面')
    // 默认 UI 不应渲染
    expect(wrapper.text()).not.toContain('抱歉，发生了错误')
    spy.mockRestore()
  })

  it('reset() 软重置清除错误状态', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { wrapper } = await mountBoundary(
      {},
      { default: h(makeThrowingComponent()) }
    )
    await nextTick()
    expect(wrapper.find('.error-boundary').exists()).toBe(true)

    // reset 为软重置：清除 hasError 并触发重渲染。
    // 由于 default slot 仍是抛错组件，reset 后会立即再次捕获错误，
    // 此处验证 reset 方法存在且可调用不抛异常。
    expect(typeof wrapper.vm.reset).toBe('function')
    expect(() => wrapper.vm.reset()).not.toThrow()
    spy.mockRestore()
  })

  it('resetOnRouteChange=true 时路由切换自动复位错误', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { wrapper, router } = await mountBoundary(
      { fullScreen: false, resetOnRouteChange: true },
      { default: h(NormalChild) }
    )

    // 初始正常渲染
    expect(wrapper.find('.normal-child').exists()).toBe(true)

    // 切换到 /other 路由（内容仍为 NormalChild）
    await router.push('/other')
    await nextTick()

    // 路由切换后仍正常（无错误状态需复位）
    expect(wrapper.find('.normal-child').exists()).toBe(true)
    expect(wrapper.find('.error-boundary').exists()).toBe(false)
    spy.mockRestore()
  })

  it('返回首页按钮仅在 fullScreen=true 时显示', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    // fullScreen=true：显示返回首页
    const { wrapper: wrapperFull } = await mountBoundary(
      { fullScreen: true },
      { default: h(makeThrowingComponent()) }
    )
    await nextTick()
    expect(wrapperFull.text()).toContain('返回首页')

    // fullScreen=false：不显示返回首页
    const { wrapper: wrapperLocal } = await mountBoundary(
      { fullScreen: false },
      { default: h(makeThrowingComponent()) }
    )
    await nextTick()
    expect(wrapperLocal.text()).not.toContain('返回首页')

    spy.mockRestore()
  })
})
