<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Delete, UploadFilled } from '@element-plus/icons-vue'
import { toolsAPI } from '@/api/tools'
import { useToolsStore } from '../../stores/tools'

const toolsStore = useToolsStore()

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  editingSkill: {
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

const isEdit = computed(() => !!props.editingSkill)

const dialogTitle = computed(() => isEdit.value ? '编辑技能' : '添加技能')

const form = ref({
  name: '',
  description: '',
  steps: [
    { toolName: '', argsTemplate: '', condition: '' },
  ],
})

const loading = ref(false)
const availableTools = computed(() => toolsStore.langchainTools.map(t => t.name))
const toolsLoading = computed(() => toolsStore.langchainLoading)
const mode = ref('package')
const skillFile = ref(null)
const skillMode = ref('pipeline')

watch(() => props.modelValue, (val) => {
  if (val && props.editingSkill) {
    const skill = props.editingSkill
    const rawName = (skill.name || '').replace(/^skill_/, '')
    const steps = (skill.steps || []).map(s => ({
      toolName: s.toolName || '',
      argsTemplate: typeof s.argsTemplate === 'object' ? JSON.stringify(s.argsTemplate) : (s.argsTemplate || ''),
      condition: s.condition || '',
    }))
    form.value = {
      name: rawName,
      description: skill.description || '',
      steps: steps.length > 0 ? steps : [{ toolName: '', argsTemplate: '', condition: '' }],
    }
    skillMode.value = skill.mode || 'pipeline'
    mode.value = 'pipeline'
  }
})

watch(visible, (val) => {
  if (val) toolsStore.loadLangchainTools()
})

const handleFileChange = (file) => {
  skillFile.value = file.raw
}

const addStep = () => {
  form.value.steps.push({ toolName: '', argsTemplate: '', condition: '' })
}

const removeStep = (index) => {
  if (form.value.steps.length <= 1) {
    ElMessage.warning('至少需要一个执行步骤')
    return
  }
  form.value.steps.splice(index, 1)
}

const resetForm = () => {
  form.value = {
    name: '',
    description: '',
    steps: [{ toolName: '', argsTemplate: '', condition: '' }],
  }
  skillMode.value = 'pipeline'
  skillFile.value = null
  mode.value = 'package'
}

const handleSubmit = async () => {
  if (isEdit.value) {
    if (!form.value.name.trim()) {
      ElMessage.warning('请输入技能名称')
      return
    }

    const validSteps = form.value.steps.filter(s => s.toolName.trim())
    if (validSteps.length === 0) {
      ElMessage.warning('至少需要一个执行步骤，且工具名不能为空')
      return
    }

    const steps = validSteps.map(s => {
      const step = {
        toolName: s.toolName.trim(),
        argsTemplate: {},
        condition: null,
      }
      if (s.argsTemplate.trim()) {
        try {
          step.argsTemplate = JSON.parse(s.argsTemplate.trim())
        } catch {
          step.argsTemplate = {}
        }
      }
      if (s.condition.trim()) {
        step.condition = s.condition.trim()
      }
      return step
    })

    loading.value = true
    try {
      await toolsAPI.updateSkill({
        name: form.value.name.trim(),
        description: form.value.description.trim(),
        mode: skillMode.value,
        steps,
      })
      ElMessage.success(`技能 "${form.value.name}" 更新成功`)
      resetForm()
      visible.value = false
      emit('success')
    } catch (e) {
      const detail = e.response?.data?.data?.data
        ? JSON.stringify(e.response.data.data.data)
        : (e.response?.data?.message || e.message)
      ElMessage.error('更新失败: ' + detail)
    } finally {
      loading.value = false
    }
    return
  }

  if (mode.value === 'package') {
    if (!skillFile.value) {
      ElMessage.warning('请选择 ZIP 文件')
      return
    }
    loading.value = true
    try {
      const formData = new FormData()
      formData.append('file', skillFile.value)
      await toolsAPI.uploadSkillPackage(formData)
      ElMessage.success('技能包上传成功')
      resetForm()
      visible.value = false
      emit('success')
    } catch (e) {
      ElMessage.error('上传失败: ' + (e.response?.data?.message || e.message))
    } finally {
      loading.value = false
    }
    return
  }

  if (!form.value.name.trim()) {
    ElMessage.warning('请输入技能名称')
    return
  }
  if (form.value.name.trim().startsWith('skill_')) {
    ElMessage.warning('技能名称不能以 skill_ 开头')
    return
  }

  const validSteps = form.value.steps.filter(s => s.toolName.trim())
  if (validSteps.length === 0) {
    ElMessage.warning('至少需要一个执行步骤，且工具名不能为空')
    return
  }

  const steps = validSteps.map(s => {
    const step = {
      toolName: s.toolName.trim(),
      argsTemplate: {},
      condition: null,
    }
    if (s.argsTemplate.trim()) {
      try {
        step.argsTemplate = JSON.parse(s.argsTemplate.trim())
      } catch {
        step.argsTemplate = {}
      }
    }
    if (s.condition.trim()) {
      step.condition = s.condition.trim()
    }
    return step
  })

  loading.value = true
  try {
    await toolsAPI.createSkill({
      name: form.value.name.trim(),
      description: form.value.description.trim(),
      mode: skillMode.value,
      steps,
    })
    ElMessage.success(`技能 "${form.value.name}" 创建成功`)
    resetForm()
    visible.value = false
    emit('success')
  } catch (e) {
    const detail = e.response?.data?.data?.data
      ? JSON.stringify(e.response.data.data.data)
      : (e.response?.data?.message || e.message)
    ElMessage.error('创建失败: ' + detail)
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
    <template v-if="!isEdit">
      <el-radio-group v-model="mode" class="mb-4">
        <el-radio-button value="package">技能包 (ZIP)</el-radio-button>
        <el-radio-button value="pipeline">工具管道</el-radio-button>
      </el-radio-group>
    </template>

    <!-- 技能包上传模式（仅新增） -->
    <div v-if="!isEdit && mode === 'package'">
      <el-upload
        ref="uploadRef"
        :auto-upload="false"
        :limit="1"
        accept=".zip"
        :on-change="handleFileChange"
        drag
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽 ZIP 文件到此处，或 <em>点击上传</em></div>
        <template #tip>
          <div class="el-upload__tip">
            遵循 Agent Skills 规范的 ZIP 包，需包含 SKILL.md 文件
          </div>
        </template>
      </el-upload>
    </div>

    <!-- 工具管道模式（编辑 + 新增） -->
    <div v-if="isEdit || mode === 'pipeline'">
    <el-form label-position="top" class="skill-upload-form">
      <el-form-item label="技能名称" required>
        <el-input
          v-model="form.name"
          placeholder="例如：data_analysis"
          maxlength="100"
          show-word-limit
          :disabled="isEdit"
        />
      </el-form-item>

      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          placeholder="简要描述此技能的功能"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>

      <el-form-item label="执行模式">
        <el-radio-group v-model="skillMode" size="small">
          <el-radio-button value="pipeline">管线模式</el-radio-button>
          <el-radio-button value="advisor">顾问模式</el-radio-button>
          <el-radio-button value="hybrid">混合模式</el-radio-button>
        </el-radio-group>
        <div class="mode-hint">
          <span v-if="skillMode === 'pipeline'">自动按步骤链执行子工具</span>
          <span v-else-if="skillMode === 'advisor'">返回 SKILL.md 指令，由 Agent 自主决策</span>
          <span v-else>先加载指令注入上下文，再执行步骤链</span>
        </div>
      </el-form-item>

      <div v-if="skillMode === 'pipeline' || skillMode === 'hybrid'">
      <el-form-item required>
        <template #label>
          <div class="steps-label">
            <span>执行步骤</span>
            <el-button
              size="small"
              link
              type="primary"
              :icon="Plus"
              @click="addStep"
            >
              添加步骤
            </el-button>
          </div>
        </template>

        <div class="steps-list">
          <div
            v-for="(step, index) in form.steps"
            :key="index"
            class="step-item"
          >
            <div class="step-header">
              <span class="step-index">步骤 {{ index + 1 }}</span>
              <el-button
                v-if="form.steps.length > 1"
                :icon="Delete"
                size="small"
                circle
                text
                type="danger"
                title="删除步骤"
                @click="removeStep(index)"
              />
            </div>

            <el-form-item label="工具名称" required class="step-field">
              <el-select
                v-model="step.toolName"
                placeholder="选择或输入工具名"
                filterable
                allow-create
                :loading="toolsLoading"
                style="width: 100%"
              >
                <el-option
                  v-for="name in availableTools"
                  :key="name"
                  :label="name"
                  :value="name"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="参数模板 (JSON)" class="step-field">
              <el-input
                v-model="step.argsTemplate"
                placeholder='例如：{"query": "{{input}}"}'
              />
            </el-form-item>

            <el-form-item label="执行条件 (可选)" class="step-field">
              <el-input
                v-model="step.condition"
                placeholder="例如：input.length > 0"
              />
            </el-form-item>
          </div>
        </div>
      </el-form-item>

      <div class="steps-hint">
        每个步骤选择一个工具并配置参数模板，参数模板为 JSON 格式，支持 <code v-text="'{{input}}'"></code> 占位符
      </div>
      </div>
    </el-form>
    </div>

    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" :loading="loading" @click="handleSubmit">
        {{ isEdit ? '保存' : (mode === 'package' ? '上传' : '创建') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.skill-upload-form {
  padding: 0 8px;
}

.steps-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.steps-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.step-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  background: var(--accent);
}

.step-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.step-index {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted-foreground);
}

.step-field {
  margin-bottom: 8px;
}

.step-field:last-child {
  margin-bottom: 0;
}

.steps-hint {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-top: -8px;
}

.steps-hint code {
  background: var(--accent);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.mode-hint {
  font-size: 11px;
  color: var(--muted-foreground);
  margin-top: 4px;
}
</style>
