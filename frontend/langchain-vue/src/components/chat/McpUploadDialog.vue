<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { toolsAPI } from '../../api'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  editingServer: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits({
  'update:modelValue': (val) => typeof val === 'boolean',
  success: () => true,
})

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const isEdit = computed(() => !!props.editingServer)

const dialogTitle = computed(() => isEdit.value ? '编辑 MCP 服务器' : '添加 MCP 服务器')

const form = ref({
  name: '',
  transport: 'sse',
  url: '',
  command: '',
  args: '',
  description: '',
  headers: '',
  authToken: '',
})

const loading = ref(false)

const isStdio = computed(() => form.value.transport === 'stdio')

const transportOptions = [
  { label: 'SSE', value: 'sse', desc: 'Server-Sent Events' },
  { label: 'HTTP', value: 'http', desc: 'HTTP Streamable' },
  { label: 'WebSocket', value: 'websocket', desc: 'WebSocket 连接' },
  { label: 'Stdio', value: 'stdio', desc: '本地进程通信' },
]

const resetForm = () => {
  form.value = {
    name: '',
    transport: 'sse',
    url: '',
    command: '',
    args: '',
    description: '',
    headers: '',
    authToken: '',
  }
}

watch(() => props.modelValue, (val) => {
  if (val && props.editingServer) {
    const srv = props.editingServer
    form.value = {
      name: srv.name || '',
      transport: srv.transport || 'sse',
      url: srv.url || '',
      command: srv.command || '',
      args: Array.isArray(srv.args) ? srv.args.join(' ') : (srv.args || ''),
      description: srv.description || '',
      headers: srv.headers && typeof srv.headers === 'object' ? JSON.stringify(srv.headers, null, 2) : '',
      authToken: srv.authToken || '',
    }
  } else if (val) {
    resetForm()
  }
})

const buildPayload = () => {
  const data = {
    name: form.value.name.trim(),
    transport: form.value.transport,
    description: form.value.description.trim(),
  }

  if (isStdio.value) {
    data.command = form.value.command.trim()
    data.args = form.value.args.trim() ? form.value.args.trim().split(/\s+/) : []
  } else {
    data.url = form.value.url.trim()
    if (form.value.headers.trim()) {
      try {
        data.headers = JSON.parse(form.value.headers.trim())
      } catch {
        data.headers = {}
      }
    }
    if (form.value.authToken.trim()) {
      data.authToken = form.value.authToken.trim()
    }
  }

  return data
}

const handleSubmit = async () => {
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入服务器名称')
    return
  }

  if (isStdio.value && !form.value.command.trim()) {
    ElMessage.warning('stdio 传输必须提供命令')
    return
  }

  if (!isStdio.value && !form.value.url.trim()) {
    ElMessage.warning('远程传输必须提供 URL')
    return
  }

  loading.value = true
  try {
    const data = buildPayload()

    if (isEdit.value) {
      await toolsAPI.updateMcpServer(data)
      ElMessage.success(`MCP Server "${form.value.name}" 更新成功`)
    } else {
      await toolsAPI.addMcpServer(data)
      ElMessage.success(`MCP Server "${form.value.name}" 添加成功`)
    }

    resetForm()
    visible.value = false
    emit('success')
  } catch (e) {
    const label = isEdit.value ? '更新' : '添加'
    ElMessage.error(`${label}失败: ` + (e.response?.data?.message || e.message))
  } finally {
    loading.value = false
  }
}

const handleClose = () => {
  resetForm()
  visible.value = false
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    width="480px"
    :close-on-click-modal="false"
    append-to-body
    @close="handleClose"
  >
    <el-form label-position="top" class="mcp-upload-form">
      <el-form-item label="服务器名称" required>
        <el-input
          v-model="form.name"
          placeholder="例如：my-server"
          maxlength="100"
          show-word-limit
          :disabled="isEdit"
        />
      </el-form-item>

      <el-form-item label="传输协议" required>
        <el-radio-group v-model="form.transport" :disabled="isEdit">
          <el-radio-button
            v-for="opt in transportOptions"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </el-radio-button>
        </el-radio-group>
        <div class="transport-hint">
          {{ transportOptions.find(o => o.value === form.transport)?.desc }}
        </div>
      </el-form-item>

      <el-form-item v-if="!isStdio" label="服务器 URL" required>
        <el-input
          v-model="form.url"
          placeholder="例如：https://mcp.example.com/sse"
        />
      </el-form-item>

      <el-form-item v-if="isStdio" label="命令" required>
        <el-input
          v-model="form.command"
          placeholder="例如：npx 或 python"
        />
      </el-form-item>

      <el-form-item v-if="isStdio" label="参数">
        <el-input
          v-model="form.args"
          placeholder="空格分隔，例如：-y @modelcontextprotocol/server-xxx"
        />
      </el-form-item>

      <el-form-item v-if="!isStdio" label="Headers (JSON)">
        <el-input
          v-model="form.headers"
          type="textarea"
          :rows="2"
          placeholder='例如：{"Authorization": "Bearer xxx"}'
        />
      </el-form-item>

      <el-form-item v-if="!isStdio" label="Auth Token">
        <el-input
          v-model="form.authToken"
          type="password"
          show-password
          placeholder="可选的认证令牌"
        />
      </el-form-item>

      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="简要描述此 MCP 服务器的功能"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" :loading="loading" @click="handleSubmit">
        {{ isEdit ? '保存' : '添加' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.mcp-upload-form {
  padding: 0 8px;
}

.transport-hint {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-top: 4px;
}
</style>
