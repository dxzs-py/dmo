import { describe, it, expect, vi, beforeEach } from 'vitest'
import { effectScope } from 'vue'

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

import { ElMessage } from 'element-plus'
import { useApiTask } from '../useApiTask.js'

/** 构造手动取消类错误 */
function createCancelError(name = 'AbortError') {
  const err = new Error('The operation was aborted')
  err.name = name
  return err
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useApiTask - 正常执行', () => {
  it('run 正常执行：loading true→false 且 onSuccess 调用，返回 resolve 数据', async () => {
    const onSuccess = vi.fn()
    const { run, loading, error } = useApiTask(() => Promise.resolve({ id: 1 }), { onSuccess })

    expect(loading.value).toBe(false)
    const promise = run()
    // run 同步段即置 loading（async 函数体首个 await 前同步执行）
    expect(loading.value).toBe(true)

    const data = await promise

    expect(data).toEqual({ id: 1 })
    expect(loading.value).toBe(false)
    expect(error.value).toBeNull()
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onSuccess).toHaveBeenCalledWith({ id: 1 })
    expect(ElMessage.error).not.toHaveBeenCalled()
    // showSuccessToast 默认 false
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('run 参数原样透传给 taskFn', async () => {
    const taskFn = vi.fn((a, b) => Promise.resolve(a + b))
    const { run } = useApiTask(taskFn)

    const data = await run(1, 2)

    expect(taskFn).toHaveBeenCalledWith(1, 2)
    expect(data).toBe(3)
  })

  it('showSuccessToast=true 成功时弹出 ElMessage.success', async () => {
    const { run } = useApiTask(() => Promise.resolve('ok'), { showSuccessToast: true })

    await run()

    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('操作成功')
  })

  it('loading=false 时不托管 loading 状态', async () => {
    let resolveTask
    const { run, loading } = useApiTask(
      () => new Promise(resolve => { resolveTask = resolve }),
      { loading: false }
    )

    const promise = run()
    expect(loading.value).toBe(false)

    resolveTask()
    await promise
    expect(loading.value).toBe(false)
  })
})

describe('useApiTask - 异常处理', () => {
  it('run 抛异常：error 赋值且默认弹出 ElMessage.error，loading 复位，onError 触发', async () => {
    const err = new Error('boom')
    const onError = vi.fn()
    const { run, loading, error } = useApiTask(() => Promise.reject(err), { onError })

    const data = await run()

    expect(data).toBeUndefined()
    expect(error.value).toBe(err)
    expect(loading.value).toBe(false)
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith('boom')
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith(err)
  })

  it('taskFn 同步抛错同样被捕获处理', async () => {
    const { run, error } = useApiTask(() => { throw new Error('sync boom') })

    await run()

    expect(error.value).toBeInstanceOf(Error)
    expect(error.value.message).toBe('sync boom')
    expect(ElMessage.error).toHaveBeenCalledWith('sync boom')
  })

  it('showErrorToast=false：出错不弹消息但 error 仍赋值、onError 仍触发', async () => {
    const err = new Error('quiet failure')
    const onError = vi.fn()
    const { run, error } = useApiTask(() => Promise.reject(err), { showErrorToast: false, onError })

    await run()

    expect(error.value).toBe(err)
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledWith(err)
  })

  it('新 run 开始时清除上一轮的过期 error', async () => {
    const taskFn = vi.fn()
      .mockImplementationOnce(() => Promise.reject(new Error('first fail')))
      .mockImplementationOnce(() => Promise.resolve('ok'))
    const { run, error } = useApiTask(taskFn, { showErrorToast: false })

    await run()
    expect(error.value).not.toBeNull()

    await run()
    expect(error.value).toBeNull()
  })
})

describe('useApiTask - AbortError 手动取消', () => {
  it('AbortError 不弹错误 toast、不置 error、不触发 onError、loading 复位', async () => {
    const onError = vi.fn()
    const { run, loading, error } = useApiTask(
      () => Promise.reject(createCancelError('AbortError')),
      { onError }
    )

    const data = await run()

    expect(data).toBeUndefined()
    expect(error.value).toBeNull()
    expect(loading.value).toBe(false)
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('CancellationError 同样按取消处理', async () => {
    const { run, error } = useApiTask(
      () => Promise.reject(createCancelError('CancellationError')),
      { showSuccessToast: false }
    )

    await run()

    expect(error.value).toBeNull()
    expect(ElMessage.error).not.toHaveBeenCalled()
  })
})

describe('useApiTask - 重复调用防护（代际 token）', () => {
  it('新 run 使旧任务作废：旧任务后完成不弹 toast、不覆盖 loading、onSuccess 不触发', async () => {
    let resolveFirst
    let resolveSecond
    const taskFn = vi.fn()
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveSecond = resolve }))
    const onSuccess = vi.fn()
    const { run, loading } = useApiTask(taskFn, { onSuccess })

    const p1 = run()
    const p2 = run()
    expect(loading.value).toBe(true)

    resolveFirst('first')
    expect(await p1).toBeUndefined() // 旧任务结果丢弃
    expect(loading.value).toBe(true) // 新任务仍在途，loading 仍持有
    expect(onSuccess).not.toHaveBeenCalled()

    resolveSecond('second')
    expect(await p2).toBe('second')
    expect(loading.value).toBe(false)
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onSuccess).toHaveBeenCalledWith('second')
  })

  it('旧任务后失败：不弹错误 toast、不置 error、不触发 onError', async () => {
    let rejectFirst
    const taskFn = vi.fn()
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectFirst = reject }))
      .mockImplementationOnce(() => Promise.resolve('second'))
    const onError = vi.fn()
    const { run, error } = useApiTask(taskFn, { onError })

    const p1 = run()
    const p2 = run()

    rejectFirst(new Error('stale failure'))
    await p1

    expect(error.value).toBeNull()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()

    await p2
    expect(error.value).toBeNull()
  })
})

describe('useApiTask - autoAbortOnUnmount（effectScope 模拟组件卸载）', () => {
  it('scope dispose 时 pending 任务被作废：loading 复位、resolve 后不弹 toast 不触发 onSuccess', async () => {
    let resolveTask
    const onSuccess = vi.fn()
    const scope = effectScope()
    let ctx
    scope.run(() => {
      ctx = useApiTask(
        () => new Promise(resolve => { resolveTask = resolve }),
        { onSuccess, showSuccessToast: true }
      )
    })

    const promise = ctx.run()
    expect(ctx.loading.value).toBe(true)

    scope.stop() // 模拟组件 unmount
    expect(ctx.loading.value).toBe(false)

    resolveTask('late result')
    await promise

    expect(onSuccess).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('scope dispose 后 pending 任务 reject 也不弹错误 toast、不置 error', async () => {
    let rejectTask
    const scope = effectScope()
    let ctx
    scope.run(() => {
      ctx = useApiTask(() => new Promise((_, reject) => { rejectTask = reject }))
    })

    const promise = ctx.run()
    scope.stop()

    rejectTask(new Error('late failure'))
    await promise

    expect(ctx.error.value).toBeNull()
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('dispose 后再次 run 静默跳过：不执行 taskFn、不弹 toast', async () => {
    const taskFn = vi.fn(() => Promise.resolve('x'))
    const scope = effectScope()
    let ctx
    scope.run(() => { ctx = useApiTask(taskFn) })
    scope.stop()

    const result = await ctx.run()

    expect(result).toBeUndefined()
    expect(taskFn).not.toHaveBeenCalled()
    expect(ctx.loading.value).toBe(false)
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('autoAbortOnUnmount=false 时 scope dispose 不影响在途任务', async () => {
    let resolveTask
    const onSuccess = vi.fn()
    const scope = effectScope()
    let ctx
    scope.run(() => {
      ctx = useApiTask(
        () => new Promise(resolve => { resolveTask = resolve }),
        { autoAbortOnUnmount: false, onSuccess }
      )
    })

    const promise = ctx.run()
    scope.stop()
    // 未托管清理：loading 保持、任务继续有效
    expect(ctx.loading.value).toBe(true)

    resolveTask('ok')
    const data = await promise

    expect(data).toBe('ok')
    expect(onSuccess).toHaveBeenCalledWith('ok')
    expect(ctx.loading.value).toBe(false)
  })
})
