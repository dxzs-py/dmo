import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'

/**
 * 对话框表单状态机骨架（spec 组9：三上传对话框同构收敛）
 *
 * 只负责骨架：visible 双向、编辑态推断、标题生成、打开时重置表单、
 * 提交 loading、成功 toast + 重置 + 关闭 + 成功回调。
 * 校验逻辑整体下沉：validate 回调返回 { ok, message? }，内部校验
 * 顺序/文案/细节由各对话框原样保留，本 composable 不拆校验步骤。
 *
 * 提交失败约定：submit 回调内部自行弹错误 toast（文案为域逻辑），
 * 失败时返回 false——骨架保持对话框打开、不发成功 toast、不触发 onSuccess。
 * validate 返回 { ok: false } 且无 message 时视为回调内已自行提示
 * （如 buildValidatedSteps 情形），骨架静默中断。
 *
 * @param {Object} options
 * @param {import('vue').Ref<boolean>} options.visible 对话框开闭状态（组件经 defineModel 构造，双向读写）
 * @param {() => Object|null} [options.isEditSource] 编辑态数据源（editingSkill/editingTool/editingServer）
 * @param {{ edit: string, create: string }} options.titles 编辑/新增标题文案
 * @param {() => Object} options.defaultsFactory 表单默认值工厂（resetForm 时求值）
 * @param {() => { ok: boolean, message?: string }} [options.validate] 校验回调（同步或异步）
 * @param {() => Promise<boolean|void>} options.submit 提交回调，失败返回 false（错误 toast 已在回调内弹出）
 * @param {string | (() => string)} options.successToast 成功提示文案；含表单值时传函数（提交成功后求值）
 * @param {() => void} [options.onReset] 附加域状态重置（如 skillMode/skillFile/mode 等表单外状态）
 * @param {() => void} [options.onSuccess] 提交成功回调（组件内 emit('success')）
 * @returns {{
 *   visible: import('vue').Ref<boolean>,
 *   isEdit: import('vue').ComputedRef<boolean>,
 *   dialogTitle: import('vue').ComputedRef<string>,
 *   form: import('vue').Ref<Object>,
 *   resetForm: () => void,
 *   handleClose: () => void,
 *   handleSubmit: () => Promise<void>,
 *   submitting: import('vue').Ref<boolean>,
 * }}
 */
export function useDialogForm({
  visible,
  isEditSource,
  titles,
  defaultsFactory,
  validate,
  submit,
  successToast,
  onReset,
  onSuccess,
}) {
  const isEdit = computed(() => !!isEditSource?.())
  const dialogTitle = computed(() => (isEdit.value ? titles.edit : titles.create))
  const submitting = ref(false)
  const form = ref(defaultsFactory())

  const resetForm = () => {
    form.value = defaultsFactory()
    onReset?.()
  }

  // 打开时统一重置为默认表单；编辑回填 watch 由组件在调用本函数之后注册，
  // 触发顺序晚于本 watch，回填覆盖本次重置结果
  watch(visible, (val) => {
    if (val) resetForm()
  })

  const handleClose = () => {
    resetForm()
    visible.value = false
  }

  const handleSubmit = async () => {
    const check = validate ? await validate() : { ok: true }
    if (!check?.ok) {
      if (check?.message) ElMessage.warning(check.message)
      return
    }
    submitting.value = true
    try {
      const result = await submit()
      if (result === false) return
      ElMessage.success(typeof successToast === 'function' ? successToast() : successToast)
      resetForm()
      visible.value = false
      onSuccess?.()
    } finally {
      submitting.value = false
    }
  }

  // resetForm 为模块内部状态机步骤（handleSubmit/onReset 使用），不对外导出
  return { visible, isEdit, dialogTitle, form, handleClose, handleSubmit, submitting }
}
