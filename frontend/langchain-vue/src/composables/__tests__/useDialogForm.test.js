import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref, nextTick, effectScope } from 'vue'

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

import { ElMessage } from 'element-plus'
import { useDialogForm } from '../useDialogForm.js'

beforeEach(() => {
  vi.clearAllMocks()
})

/**
 * 在 effectScope 中构造宿主上下文（composable 内 watch/computed 需要响应式作用域），
 * visible 用可写 ref 模拟组件侧 defineModel 的双向绑定。
 */
function setupDialogForm(options) {
  const visible = ref(false)
  const scope = effectScope()
  const api = scope.run(() => useDialogForm({ visible, ...options }))
  return { visible, scope, ...api }
}

describe('useDialogForm - 打开重置与编辑态', () => {
  it('打开时重置表单为 defaultsFactory 结果并触发 onReset', async () => {
    const onReset = vi.fn()
    const { visible, form } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({ name: '', steps: [] }),
      onReset,
    })

    form.value.name = '草稿'
    visible.value = true
    await nextTick()

    expect(form.value).toEqual({ name: '', steps: [] })
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('关闭不影响表单状态（重置仅在打开时与 handleClose/提交成功路径发生）', async () => {
    const onReset = vi.fn()
    const { visible, form } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({ name: '' }),
      onReset,
    })

    visible.value = true
    await nextTick()
    expect(onReset).toHaveBeenCalledTimes(1)

    form.value.name = '编辑中'
    visible.value = false
    await nextTick()
    expect(onReset).toHaveBeenCalledTimes(1)
    expect(form.value).toEqual({ name: '编辑中' })
  })

  it('isEdit 推断编辑态并生成对应 dialogTitle', async () => {
    const editSource = ref(null)
    const { isEdit, dialogTitle } = setupDialogForm({
      isEditSource: () => editSource.value,
      titles: { edit: '编辑技能', create: '添加技能' },
      defaultsFactory: () => ({}),
    })

    expect(isEdit.value).toBe(false)
    expect(dialogTitle.value).toBe('添加技能')

    editSource.value = { name: 'skill_demo' }
    await nextTick()
    expect(isEdit.value).toBe(true)
    expect(dialogTitle.value).toBe('编辑技能')
  })
})

describe('useDialogForm - 提交状态机', () => {
  it('validate 不通过：弹 warning、不调 submit、保持打开、submitting 复位', async () => {
    const submit = vi.fn()
    const onSuccess = vi.fn()
    const { visible, handleSubmit, submitting } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({}),
      validate: () => ({ ok: false, message: '请输入技能名称' }),
      submit,
      successToast: '成功',
      onSuccess,
    })

    visible.value = true
    await nextTick()
    await handleSubmit()

    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
    expect(ElMessage.warning).toHaveBeenCalledWith('请输入技能名称')
    expect(submit).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(visible.value).toBe(true)
    expect(onSuccess).not.toHaveBeenCalled()
    expect(submitting.value).toBe(false)
  })

  it('validate 不通过且无 message：静默中断（回调内已自行提示形态）', async () => {
    const submit = vi.fn()
    const { handleSubmit } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({}),
      validate: () => ({ ok: false }),
      submit,
    })

    await handleSubmit()

    expect(ElMessage.warning).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
  })

  it('提交成功：successToast 函数求值弹出、表单重置、关闭并触发 onSuccess', async () => {
    const submit = vi.fn(async () => true)
    const onSuccess = vi.fn()
    const onReset = vi.fn()
    const { visible, form, handleSubmit, submitting } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({ name: '占位' }),
      validate: () => ({ ok: true }),
      submit,
      successToast: () => `技能 "${form.value.name}" 创建成功`,
      onReset,
      onSuccess,
    })

    form.value.name = 'demo'
    await handleSubmit()

    expect(submit).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('技能 "demo" 创建成功')
    expect(form.value).toEqual({ name: '占位' })
    expect(visible.value).toBe(false)
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onReset).toHaveBeenCalledTimes(1)
    expect(submitting.value).toBe(false)
  })

  it('successToast 传字符串时原样弹出', async () => {
    const submit = vi.fn(async () => true)
    const { handleSubmit } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({}),
      validate: () => ({ ok: true }),
      submit,
      successToast: '技能包上传成功',
    })

    await handleSubmit()

    expect(ElMessage.success).toHaveBeenCalledWith('技能包上传成功')
  })

  it('submit 返回 false：失败保持打开，无成功 toast、不触发 onSuccess', async () => {
    const submit = vi.fn(async () => false)
    const onSuccess = vi.fn()
    const { visible, handleSubmit, submitting } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({}),
      validate: () => ({ ok: true }),
      submit,
      successToast: '成功',
      onSuccess,
    })

    visible.value = true
    await nextTick()
    await handleSubmit()

    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(visible.value).toBe(true)
    expect(onSuccess).not.toHaveBeenCalled()
    expect(submitting.value).toBe(false)
  })

  it('submitting 在提交期间为 true，结束后复位', async () => {
    let resolveSubmit
    const { handleSubmit, submitting } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({}),
      validate: () => ({ ok: true }),
      submit: () => new Promise((resolve) => { resolveSubmit = resolve }),
      successToast: 'ok',
    })

    const pending = handleSubmit()
    // await validate() 引入一次微任务轮转后 submitting 才置 true
    await Promise.resolve()
    expect(submitting.value).toBe(true)

    resolveSubmit()
    await pending
    expect(submitting.value).toBe(false)
  })
})

describe('useDialogForm - 关闭', () => {
  it('handleClose：重置表单与域状态并关闭', () => {
    const onReset = vi.fn()
    const { visible, form, handleClose } = setupDialogForm({
      titles: { edit: '编辑', create: '新增' },
      defaultsFactory: () => ({ name: '默认' }),
      onReset,
    })

    form.value.name = '改动'
    handleClose()

    expect(form.value).toEqual({ name: '默认' })
    expect(onReset).toHaveBeenCalledTimes(1)
    expect(visible.value).toBe(false)
  })
})
