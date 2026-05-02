<template>
  <el-dialog
    v-model="visible"
    title="工具执行确认"
    width="480px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    @close="handleClose"
  >
    <div class="tool-confirmation">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        class="confirm-alert"
      >
        <template #title>
          工具「{{ toolName }}」需要您的确认才能执行
        </template>
      </el-alert>

      <div v-if="toolArgs && Object.keys(toolArgs).length > 0" class="tool-args">
        <div class="args-title">执行参数</div>
        <pre class="args-content">{{ formatArgs(toolArgs) }}</pre>
      </div>

      <div class="confirm-hint">
        确认后将立即执行该工具，拒绝将跳过此操作。
      </div>
    </div>

    <template #footer>
      <el-button @click="handleDeny">拒绝</el-button>
      <el-button type="primary" @click="handleApprove" :loading="loading">
        确认执行
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { chatAPI } from '@/api'
import { ElMessage } from 'element-plus'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  confirmId: {
    type: String,
    default: '',
  },
  toolName: {
    type: String,
    default: '',
  },
  toolArgs: {
    type: Object,
    default: () => ({}),
  },
})

const emit = defineEmits(['update:modelValue', 'approved', 'denied'])

const visible = ref(false)
const loading = ref(false)

watch(
  () => props.modelValue,
  (val) => { visible.value = val },
)
watch(visible, (val) => { emit('update:modelValue', val) })

function formatArgs(args) {
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

async function handleApprove() {
  if (!props.confirmId) return
  loading.value = true
  try {
    const res = await chatAPI.approveToolConfirmation(props.confirmId)
    if (res.data?.code === 200) {
      ElMessage.success('工具已执行')
      emit('approved', res.data.data)
      visible.value = false
    } else {
      ElMessage.error(res.data?.message || '执行失败')
    }
  } catch (e) {
    ElMessage.error('确认请求失败')
  } finally {
    loading.value = false
  }
}

async function handleDeny() {
  if (!props.confirmId) return
  try {
    const res = await chatAPI.denyToolConfirmation(props.confirmId)
    if (res.data?.code === 200) {
      ElMessage.info('已拒绝执行')
      emit('denied', props.confirmId)
      visible.value = false
    } else {
      ElMessage.error(res.data?.message || '拒绝失败')
    }
  } catch (e) {
    ElMessage.error('拒绝请求失败')
  }
}

function handleClose() {
  visible.value = false
}
</script>

<style scoped>
.tool-confirmation {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.confirm-alert {
  margin-bottom: 0;
}

.tool-args {
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
  padding: 12px;
}

.args-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.args-content {
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  color: var(--el-text-color-primary);
  max-height: 200px;
  overflow-y: auto;
}

.confirm-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
