/**
 * dom.copyToClipboard 行为锁定测试（Spec: unify-frontend-semantics / 组 8 剪贴板统一）
 *
 * 锁定语义：优先 Clipboard API；非安全上下文（!window.isSecureContext）或 API 缺失，
 * 或 writeText 失败时 → 临时 textarea + execCommand('copy') 回退；
 * 返回成功布尔，异常内部捕获不抛出；错误提示属调用方 UI 语义。
 *
 * 覆盖场景（spec 任务书 ①-④）：
 * ① 安全上下文 + Clipboard API 成功 → true，且不触发回退
 * ② Clipboard API 抛异常 → execCommand 回退成功 → true
 * ③ 两者皆失败 → false（execCommand 返回 false / 抛异常两种形态）
 * ④ 非安全上下文且无 Clipboard API → 回退路径 → true
 *
 * 运行环境为 node（vitest.config.js 统一配置），故以 vi.stubGlobal 注入
 * window/navigator/document 全局，不依赖 jsdom。
 *
 * 运行方式（vitest）：
 *   npx vitest run
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { copyToClipboard } from '../dom.js'

let fakeDoc
let createdTextareas

/** 构造最小 document mock：createElement('textarea') / body 挂载 / execCommand */
function createFakeDocument(execCommandImpl) {
  createdTextareas = []
  const body = {
    children: [],
    appendChild(el) {
      this.children.push(el)
      el.parentNode = this
    },
    removeChild(el) {
      this.children = this.children.filter((c) => c !== el)
      el.parentNode = null
    },
  }
  fakeDoc = {
    body,
    execCommand: vi.fn(execCommandImpl),
    createElement() {
      const el = {
        value: '',
        attrs: {},
        style: {},
        parentNode: null,
        select: vi.fn(),
        setAttribute(key, val) {
          this.attrs[key] = val
        },
      }
      createdTextareas.push(el)
      return el
    },
  }
  return fakeDoc
}

/** 注入全局三件套（dom.js 仅引用 window.isSecureContext / navigator.clipboard / document） */
function stubGlobals({ isSecureContext, clipboardApi, execCommandImpl }) {
  vi.stubGlobal('window', { isSecureContext })
  vi.stubGlobal('navigator', clipboardApi ? { clipboard: clipboardApi } : {})
  vi.stubGlobal('document', createFakeDocument(execCommandImpl))
}

beforeEach(() => {
  // 默认安全上下文 + API 成功 + 回退可用，各用例按需覆盖
  stubGlobals({
    isSecureContext: true,
    clipboardApi: { writeText: vi.fn().mockResolvedValue(undefined) },
    execCommandImpl: () => true,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('copyToClipboard', () => {
  it('① 安全上下文 + Clipboard API 成功 → true，不触发 execCommand 回退', async () => {
    await expect(copyToClipboard('hello')).resolves.toBe(true)

    expect(fakeDoc.execCommand).not.toHaveBeenCalled()
    expect(createdTextareas).toHaveLength(0)
  })

  it('① Clipboard API 收到原始文本参数', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    stubGlobals({ isSecureContext: true, clipboardApi: { writeText }, execCommandImpl: () => true })

    await copyToClipboard('hello')

    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith('hello')
  })

  it('② Clipboard API 抛异常 → execCommand 回退成功 → true（textarea select 并清理）', async () => {
    stubGlobals({
      isSecureContext: true,
      clipboardApi: { writeText: vi.fn().mockRejectedValue(new Error('NotAllowedError')) },
      execCommandImpl: () => true,
    })

    await expect(copyToClipboard('fallback')).resolves.toBe(true)

    expect(fakeDoc.execCommand).toHaveBeenCalledTimes(1)
    expect(fakeDoc.execCommand).toHaveBeenCalledWith('copy')
    expect(createdTextareas[0].select).toHaveBeenCalled()
    // 回退的临时 textarea 必须被移除，无 DOM 残留
    expect(fakeDoc.body.children).toHaveLength(0)
  })

  it('③ 两者皆失败 → false（execCommand 返回 false）', async () => {
    stubGlobals({
      isSecureContext: true,
      clipboardApi: { writeText: vi.fn().mockRejectedValue(new Error('NotAllowedError')) },
      execCommandImpl: () => false,
    })

    await expect(copyToClipboard('fail')).resolves.toBe(false)
    expect(fakeDoc.body.children).toHaveLength(0)
  })

  it('③ 两者皆失败 → false（execCommand 抛异常，异常内部捕获不外抛）', async () => {
    stubGlobals({
      isSecureContext: true,
      clipboardApi: { writeText: vi.fn().mockRejectedValue(new Error('NotAllowedError')) },
      execCommandImpl: () => {
        throw new Error('execCommand unavailable')
      },
    })

    await expect(copyToClipboard('fail')).resolves.toBe(false)
    expect(fakeDoc.body.children).toHaveLength(0)
  })

  it('④ 非安全上下文且无 Clipboard API → execCommand 回退路径 → true', async () => {
    stubGlobals({ isSecureContext: false, clipboardApi: undefined, execCommandImpl: () => true })

    await expect(copyToClipboard('http-only')).resolves.toBe(true)

    expect(fakeDoc.execCommand).toHaveBeenCalledTimes(1)
    expect(fakeDoc.body.children).toHaveLength(0)
  })
})
