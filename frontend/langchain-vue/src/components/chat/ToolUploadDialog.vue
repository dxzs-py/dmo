<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { toolsAPI } from '@/api/tools'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  editingTool: {
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

const isEdit = computed(() => !!props.editingTool)

const dialogTitle = computed(() => isEdit.value ? '编辑自定义工具' : '上传自定义工具')

const form = ref({
  name: '',
  description: '',
  code: '',
  category: 'general',
})

const categories = ref([])
const loading = ref(false)

const codeTemplate = `from langchain_core.tools import tool

@tool
def my_custom_tool(query: str) -> str:
    """自定义工具描述"""
    return f"结果: {query}"
`

const loadCategories = async () => {
  try {
    const res = await toolsAPI.getToolMeta()
    categories.value = res.data?.data?.categories || []
  } catch {
    categories.value = []
  }
}

onMounted(loadCategories)

watch(() => props.modelValue, (val) => {
  if (val && props.editingTool) {
    const catCode = typeof props.editingTool.category === 'object'
      ? props.editingTool.category?.code || 'general'
      : props.editingTool.category || 'general'
    form.value = {
      name: props.editingTool.name || '',
      description: props.editingTool.description || '',
      code: props.editingTool.code || '',
      category: catCode,
    }
  }
})

const resetForm = () => {
  form.value = { name: '', description: '', code: '', category: 'general' }
}

const handleUseTemplate = () => {
  form.value.code = codeTemplate
}

const handleSubmit = async () => {
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入工具名称')
    return
  }
  if (!form.value.code.trim()) {
    ElMessage.warning('请输入工具代码')
    return
  }

  loading.value = true
  try {
    if (isEdit.value) {
      await toolsAPI.updateCustomTool({
        name: form.value.name.trim(),
        description: form.value.description.trim(),
        code: form.value.code,
        category: form.value.category,
      })
      ElMessage.success(`工具 "${form.value.name}" 更新成功`)
    } else {
      await toolsAPI.uploadTool({
        name: form.value.name.trim(),
        description: form.value.description.trim(),
        code: form.value.code,
        category: form.value.category,
      })
      ElMessage.success(`工具 "${form.value.name}" 上传成功`)
    }
    resetForm()
    visible.value = false
    emit('success')
  } catch (e) {
    ElMessage.error((isEdit.value ? '更新失败: ' : '上传失败: ') + (e.response?.data?.message || e.message))
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
    width="560px"
    :close-on-click-modal="false"
    append-to-body
    @close="handleClose"
  >
    <el-form label-position="top" class="tool-upload-form">
      <el-form-item label="工具名称" required>
        <el-input
          v-model="form.name"
          placeholder="例如：my_custom_tool"
          maxlength="100"
          show-word-limit
          :disabled="isEdit"
        />
      </el-form-item>

      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          placeholder="简要描述工具的功能"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>

      <el-form-item label="功能分类">
        <el-select v-model="form.category" placeholder="选择分类" style="width: 100%">
          <el-option
            v-for="cat in categories"
            :key="cat.code"
            :label="cat.name"
            :value="cat.code"
          />
        </el-select>
      </el-form-item>

      <el-form-item required>
        <template #label>
          <div class="code-label">
            <span>工具代码</span>
            <el-button size="small" link type="primary" @click="handleUseTemplate">
              使用模板
            </el-button>
          </div>
        </template>
        <el-input
          v-model="form.code"
          type="textarea"
          :rows="12"
          placeholder="粘贴 LangChain @tool 装饰器定义的工具代码"
          class="code-input"
        />
      </el-form-item>

      <div class="code-hint">
        代码需包含使用 <code>@tool</code> 装饰器定义的 LangChain BaseTool 实例
      </div>
    </el-form>

    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" :loading="loading" @click="handleSubmit">
        {{ isEdit ? '保存' : '上传' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.tool-upload-form {
  padding: 0 8px;
}

.code-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.code-input :deep(textarea) {
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.code-hint {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-top: -8px;
}

.code-hint code {
  background: var(--accent);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
