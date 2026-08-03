<template>
  <div class="settings-view">
    <div class="view-content">
      <!-- 通用设置 -->
      <el-card>
        <template #header>
          <div class="card-header">
            <span class="page-title">设置</span>
          </div>
        </template>
        <el-form :model="settingsForm" label-width="120px">
          <el-form-item label="后端 API 地址">
            <el-input v-model="settingsForm.apiBaseUrl" placeholder="/api/v1" />
          </el-form-item>
          <el-form-item label="主题模式">
            <el-radio-group v-model="themeForm.theme">
              <el-radio value="light">浅色</el-radio>
              <el-radio value="dark">深色</el-radio>
              <el-radio value="system">跟随系统</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="isSaving" @click="saveSettings">保存</el-button>
            <el-button @click="resetSettings">重置</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 聊天模型配置 -->
      <el-card style="margin-top: 16px;">
        <template #header>
          <div class="card-header">
            <span>聊天模型配置</span>
          </div>
        </template>

        <el-form label-width="120px">
          <!-- 默认模型 -->
          <el-form-item label="默认模型">
            <div class="model-row">
              <el-select
                v-model="chatModelForm.providerId"
                placeholder="自动选择"
                clearable
                class="model-select"
                @change="onChatProviderChange"
              >
                <el-option label="自动" value="" />
                <el-option
                  v-for="p in chatProviders"
                  :key="p.id"
                  :label="`${p.icon || ''} ${p.label}`"
                  :value="p.id"
                  :disabled="!p.available"
                />
              </el-select>
              <el-select
                v-model="chatModelForm.modelName"
                placeholder="模型"
                clearable
                class="model-select"
                :disabled="!chatModelForm.providerId"
              >
                <el-option
                  v-for="m in currentChatModels"
                  :key="typeof m === 'string' ? m : m.name"
                  :label="typeof m === 'string' ? m : m.name"
                  :value="typeof m === 'string' ? m : m.name"
                />
              </el-select>
            </div>
          </el-form-item>

          <!-- 降级模型 -->
          <el-form-item label="降级模型">
            <div class="model-row">
              <el-select
                v-model="fallbackChatForm.providerId"
                placeholder="自动"
                clearable
                class="model-select"
                @change="onFallbackChatProviderChange"
              >
                <el-option label="自动" value="" />
                <el-option
                  v-for="p in chatProviders"
                  :key="p.id"
                  :label="`${p.icon || ''} ${p.label}`"
                  :value="p.id"
                  :disabled="!p.available || p.id === chatModelForm.providerId"
                />
              </el-select>
              <el-select
                v-model="fallbackChatForm.modelName"
                placeholder="模型"
                clearable
                class="model-select"
                :disabled="!fallbackChatForm.providerId"
              >
                <el-option
                  v-for="m in currentFallbackChatModels"
                  :key="typeof m === 'string' ? m : m.name"
                  :label="typeof m === 'string' ? m : m.name"
                  :value="typeof m === 'string' ? m : m.name"
                />
              </el-select>
            </div>
            <div class="form-tip">主模型不可用时自动切换到此模型</div>
          </el-form-item>

          <!-- 辅助模型 -->
          <el-form-item label="辅助模型">
            <div class="model-row">
              <el-select
                v-model="helperForm.providerId"
                placeholder="自动"
                clearable
                class="model-select"
                @change="onHelperProviderChange"
              >
                <el-option label="自动" value="" />
                <el-option
                  v-for="p in chatProviders"
                  :key="p.id"
                  :label="`${p.icon || ''} ${p.label}`"
                  :value="p.id"
                  :disabled="!p.available"
                />
              </el-select>
              <el-select
                v-model="helperForm.modelName"
                placeholder="模型"
                clearable
                class="model-select"
                :disabled="!helperForm.providerId"
              >
                <el-option
                  v-for="m in currentHelperModels"
                  :key="m.name"
                  :label="m.name"
                  :value="m.name"
                />
              </el-select>
            </div>
            <div class="form-tip">用于循环检测、内容审核等后台任务</div>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="chatModelSaving" @click="saveChatModels">
              保存聊天模型配置
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- Embedding 配置 -->
      <el-card style="margin-top: 16px;">
        <template #header>
          <div class="card-header">
            <span>Embedding 配置</span>
            <el-tag :type="embeddingDimTagType" size="small">
              {{ embeddingDimLabel }}
            </el-tag>
          </div>
        </template>

        <el-form label-width="120px">
          <!-- 默认 Embedding -->
          <el-form-item label="默认 Embedding">
            <el-select
              v-model="embeddingForm.providerId"
              placeholder="自动选择"
              clearable
              style="width: 100%"
              @change="onEmbeddingProviderChange"
            >
              <el-option label="自动" value="" />
              <el-option
                v-for="p in embeddingProviders"
                :key="p.id"
                :label="`${p.label} (${p.dimension}维)`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>

          <!-- 降级 Embedding -->
          <el-form-item label="降级 Embedding">
            <el-select
              v-model="fallbackEmbeddingForm.providerId"
              placeholder="自动"
              clearable
              style="width: 100%"
              @change="onFallbackEmbeddingProviderChange"
            >
              <el-option label="自动" value="" />
              <el-option
                v-for="p in embeddingProviders"
                :key="p.id"
                :label="`${p.label} (${p.dimension}维)`"
                :value="p.id"
                :disabled="p.id === embeddingForm.providerId"
              />
            </el-select>
            <div class="form-tip">主 Embedding 不可用时自动切换</div>
          </el-form-item>

          <!-- 维度兼容性 -->
          <el-form-item v-if="embeddingForm.providerId" label="维度兼容性">
            <div class="dimension-info">
              <span>输出维度: <strong>{{ selectedEmbeddingDim }}</strong></span>
              <span v-if="affectedIndexes.length" class="dimension-warning">
                <el-icon><WarningFilled /></el-icon>
                {{ affectedIndexes.length }} 个索引维度不匹配
              </span>
              <span v-else class="dimension-ok">所有索引维度兼容</span>
            </div>
          </el-form-item>

          <!-- MRL 截断维度：仅当 selected embedding 支持 MRL 时显示（前端可改） -->
          <el-form-item v-if="isMrlEmbedding" label="输出维度">
            <el-input-number
              v-model="embeddingDimInput"
              :min="embeddingDimMin"
              :max="embeddingDimMax"
              :step="1"
              controls-position="right"
              style="width: 160px;"
              :disabled="!embeddingForm.providerId"
            />
            <span class="form-tip" style="margin-left: 12px;">
              支持 MRL 截断，可输入 {{ embeddingDimMin }} ~ {{ embeddingDimMax }} 之间任意整数。
              等于 {{ embeddingDimMax }} 表示不截断。
            </span>
            <el-button
              v-if="embeddingDimInputChanged"
              size="small"
              type="primary"
              plain
              style="margin-left: 12px;"
              @click="applyEmbeddingDim"
            >
              应用
            </el-button>
          </el-form-item>

          <!-- 固定维度模型：仅展示，禁止修改 -->
          <el-form-item
            v-else-if="embeddingForm.providerId && selectedEmbeddingDim !== '-'"
            label="输出维度"
          >
            <el-tag type="info" size="small">{{ selectedEmbeddingDim }} 维</el-tag>
            <span class="form-tip" style="margin-left: 12px;">
              此 Embedding 不支持 MRL 截断，维度固定。
            </span>
          </el-form-item>

          <!-- 受影响索引 -->
          <el-form-item v-if="affectedIndexes.length" label="受影响索引">
            <div class="affected-indexes">
              <el-tag
                v-for="idx in affectedIndexes"
                :key="idx.name"
                type="warning"
                size="small"
                style="margin: 2px 4px;"
              >
                {{ idx.name }} ({{ idx.embeddingDimension }}维 → {{ selectedEmbeddingDim }}维, {{ idx.numDocuments }}条)
              </el-tag>
            </div>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="embeddingSaving" @click="saveEmbedding">
              保存 Embedding 配置
            </el-button>
            <el-button
              v-if="affectedIndexes.length"
              type="warning"
              :loading="rebuilding"
              @click="rebuildAffectedIndexes"
            >
              重建不匹配索引
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 关于 -->
      <el-card style="margin-top: 16px;">
        <template #header>
          <div class="card-header">
            <span>关于</span>
          </div>
        </template>
        <p class="about-item">LC-StudyLab - 智能学习与研究助手</p>
        <p class="about-item">版本: 2.0.0</p>
        <p class="about-item">技术栈: Django 5.2 + Vue 3 + LangChain 1.2.13</p>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed, watch, onMounted } from 'vue'
import { useThemeStore } from '../stores/theme'
import settings from '../config/settings'
import { updateBaseURL } from '../api/axios'
import { modelAPI } from '../api/model'
import { ElMessage, ElMessageBox } from 'element-plus'
import { WarningFilled } from '@element-plus/icons-vue'
import { logger } from '../utils/logger'

const isSaving = ref(false)
const themeStore = useThemeStore()

const settingsForm = reactive({
  apiBaseUrl: settings.apiBaseUrl,
})

const themeForm = reactive({
  theme: themeStore.currentTheme || 'light',
})

watch(() => themeForm.theme, (newTheme) => {
  themeStore.setTheme(newTheme)
})

// ===== 聊天模型 =====
const chatProviders = ref([])
const chatModelSaving = ref(false)

// 默认模型
const chatModelForm = reactive({
  providerId: '',
  modelName: '',
})

// 降级模型
const fallbackChatForm = reactive({
  providerId: '',
  modelName: '',
})

// 辅助模型
const helperForm = reactive({
  providerId: '',
  modelName: '',
})

const currentChatModels = computed(() => {
  if (!chatModelForm.providerId) return []
  const provider = chatProviders.value.find(p => p.id === chatModelForm.providerId)
  return provider?.models || []
})

const currentFallbackChatModels = computed(() => {
  if (!fallbackChatForm.providerId) return []
  const provider = chatProviders.value.find(p => p.id === fallbackChatForm.providerId)
  return provider?.models || []
})

const currentHelperModels = computed(() => {
  if (!helperForm.providerId) return []
  const provider = chatProviders.value.find(p => p.id === helperForm.providerId)
  return provider?.models || []
})

const onChatProviderChange = (providerId) => {
  chatModelForm.modelName = ''
  if (providerId) {
    const provider = chatProviders.value.find(p => p.id === providerId)
    chatModelForm.modelName = provider?.defaultModel || ''
  }
}

const onFallbackChatProviderChange = (providerId) => {
  fallbackChatForm.modelName = ''
  if (providerId) {
    const provider = chatProviders.value.find(p => p.id === providerId)
    fallbackChatForm.modelName = provider?.defaultModel || ''
  }
}

const onHelperProviderChange = (providerId) => {
  helperForm.modelName = ''
  if (providerId) {
    const provider = chatProviders.value.find(p => p.id === providerId)
    helperForm.modelName = provider?.defaultModel || ''
  }
}

const saveChatModels = async () => {
  chatModelSaving.value = true
  try {
    await modelAPI.updateAISettings({
      defaultChatModel: {
        providerId: chatModelForm.providerId,
        modelName: chatModelForm.modelName,
      },
      fallbackChatModel: {
        providerId: fallbackChatForm.providerId,
        modelName: fallbackChatForm.modelName,
      },
      helperModel: {
        providerId: helperForm.providerId,
        modelName: helperForm.modelName,
      },
    })
    ElMessage.success('聊天模型配置已保存')
  } catch (e) {
    logger.error('保存聊天模型配置失败:', e)
    ElMessage.error('保存失败')
  } finally {
    chatModelSaving.value = false
  }
}

// ===== Embedding =====
const embeddingProviders = ref([])
const embeddingForm = reactive({ providerId: '' })
const fallbackEmbeddingForm = reactive({ providerId: '' })
// 用户在前端调整的 MRL 截断维度（与 SystemConfig.embedding_provider.dimension 同步）
const embeddingDimInput = ref(null)
const embeddingSaving = ref(false)
const rebuilding = ref(false)
const affectedIndexes = ref([])
const indexDimensions = ref([])

const selectedEmbeddingDim = computed(() => {
  if (!embeddingForm.providerId) return '-'
  const p = embeddingProviders.value.find(p => p.id === embeddingForm.providerId)
  return p ? p.dimension : '-'
})

const selectedEmbeddingMinDim = computed(() => {
  if (!embeddingForm.providerId) return 0
  const p = embeddingProviders.value.find(p => p.id === embeddingForm.providerId)
  return p ? (p.minDimension || 0) : 0
})

const selectedEmbeddingMaxDim = computed(() => {
  if (!embeddingForm.providerId) return 0
  const p = embeddingProviders.value.find(p => p.id === embeddingForm.providerId)
  return p ? (p.nativeMaxDimension || 0) : 0
})

// 是否 MRL 模型：min_dimension > 0 且 native_max_dimension > 0
const isMrlEmbedding = computed(() => selectedEmbeddingMinDim.value > 0 && selectedEmbeddingMaxDim.value > 0)

// 输入框范围：min_dimension ~ native_max_dimension
const embeddingDimMin = computed(() => {
  if (!isMrlEmbedding.value) return 1
  return selectedEmbeddingMinDim.value
})
const embeddingDimMax = computed(() => selectedEmbeddingMaxDim.value)

// 当前输入框相对 provider 默认 dimension 是否变化
const embeddingDimInputChanged = computed(() => {
  if (!isMrlEmbedding.value) return false
  if (embeddingDimInput.value === null || embeddingDimInput.value === undefined) return false
  return embeddingDimInput.value !== Number(selectedEmbeddingDim.value)
})

const embeddingDimLabel = computed(() => {
  if (!embeddingForm.providerId) return '未选择'
  return `${selectedEmbeddingDim.value}维`
})

const embeddingDimTagType = computed(() => {
  if (!embeddingForm.providerId) return 'info'
  return affectedIndexes.value.length > 0 ? 'warning' : 'success'
})

// 切换 provider 时，重置输入框为当前生效维度
watch(() => embeddingForm.providerId, () => {
  const p = embeddingProviders.value.find(p => p.id === embeddingForm.providerId)
  embeddingDimInput.value = p ? Number(p.dimension) : null
  onEmbeddingProviderChange()
})

const onEmbeddingProviderChange = () => {
  const newDim = selectedEmbeddingDim.value
  if (!newDim || newDim === '-') {
    affectedIndexes.value = []
    return
  }
  affectedIndexes.value = indexDimensions.value.filter(
    idx => idx.embeddingDimension && idx.embeddingDimension !== newDim
  )
}

const onFallbackEmbeddingProviderChange = () => {
  // 降级 Embedding 切换时无需额外操作
}

const applyEmbeddingDim = async () => {
  if (!isMrlEmbedding.value) {
    ElMessage.warning('此 Embedding 不支持 MRL 截断，无法调整维度')
    return
  }
  const dim = Number(embeddingDimInput.value)
  if (!Number.isInteger(dim) || dim < embeddingDimMin.value || dim > embeddingDimMax.value) {
    ElMessage.error(`维度必须是 ${embeddingDimMin.value} ~ ${embeddingDimMax.value} 之间的整数`)
    return
  }
  embeddingSaving.value = true
  try {
    const res = await modelAPI.updateAISettings({
      embeddingProvider: {
        providerId: embeddingForm.providerId,
        dimension: dim,
      },
    })
    const data = res.data?.data
    if (data?.dimensionInfo?.dimensionChanged) {
      affectedIndexes.value = data.dimensionInfo.affectedIndexes || []
      if (affectedIndexes.value.length > 0) {
        ElMessageBox.confirm(
          `Embedding 维度已变化，${affectedIndexes.value.length} 个索引需要重建才能正常使用 RAG 检索。是否立即重建？`,
          '维度变化提示',
          { confirmButtonText: '立即重建', cancelButtonText: '稍后重建', type: 'warning' }
        ).then(() => {
          rebuildAffectedIndexes()
        }).catch(() => {
          ElMessage.info('配置已保存。索引未重建，RAG 检索可能受影响。')
        })
      } else {
        ElMessage.success('Embedding 维度已更新')
      }
    } else {
      ElMessage.success('Embedding 维度已更新')
    }
  } catch (e) {
    logger.error('应用 Embedding 维度失败:', e)
    ElMessage.error('保存失败')
  } finally {
    embeddingSaving.value = false
  }
}

const saveEmbedding = async () => {
  embeddingSaving.value = true
  try {
    const payload = {
      embeddingProvider: {
        providerId: embeddingForm.providerId,
      },
      fallbackEmbeddingProvider: {
        providerId: fallbackEmbeddingForm.providerId,
      },
    }
    // 如果是 MRL 模型且输入框有值，把当前 dimension 一并保存
    if (isMrlEmbedding.value && embeddingDimInput.value) {
      payload.embeddingProvider.dimension = Number(embeddingDimInput.value)
    }
    const res = await modelAPI.updateAISettings(payload)
    const data = res.data?.data
    if (data?.dimensionInfo?.dimensionChanged) {
      affectedIndexes.value = data.dimensionInfo.affectedIndexes || []
      if (affectedIndexes.value.length > 0) {
        ElMessageBox.confirm(
          `Embedding 维度已变化，${affectedIndexes.value.length} 个索引需要重建才能正常使用 RAG 检索。是否立即重建？`,
          '维度变化提示',
          { confirmButtonText: '立即重建', cancelButtonText: '稍后重建', type: 'warning' }
        ).then(() => {
          rebuildAffectedIndexes()
        }).catch(() => {
          ElMessage.info('配置已保存。索引未重建，RAG 检索可能受影响。')
        })
      }
    } else {
      affectedIndexes.value = []
      ElMessage.success('Embedding 配置已保存')
    }
  } catch (e) {
    logger.error('保存 Embedding 设置失败:', e)
    ElMessage.error('保存失败')
  } finally {
    embeddingSaving.value = false
  }
}

const rebuildAffectedIndexes = async () => {
  if (!affectedIndexes.value.length) return
  rebuilding.value = true
  try {
    const indexNames = affectedIndexes.value.map(idx => idx.name)
    const res = await modelAPI.rebuildIndexes(embeddingForm.providerId, indexNames)
    const data = res.data?.data
    if (data?.total > 0) {
      ElMessage.success(`重建完成: ${data.total} 个索引成功`)
      affectedIndexes.value = []
    } else {
      ElMessage.warning('无需重建的索引')
    }
  } catch (e) {
    logger.error('重建索引失败:', e)
    ElMessage.error('重建索引失败')
  } finally {
    rebuilding.value = false
  }
}

// ===== 通用设置 =====
const saveSettings = async () => {
  if (!settingsForm.apiBaseUrl.trim()) {
    ElMessage.warning('请输入 API 地址')
    return
  }
  isSaving.value = true
  try {
    localStorage.setItem('lc-studylab-api-url', settingsForm.apiBaseUrl)
    settings.apiBaseUrl = settingsForm.apiBaseUrl
    updateBaseURL(settingsForm.apiBaseUrl)
    ElMessage.success('设置已保存')
  } catch (error) {
    logger.error('保存设置失败:', error)
    ElMessage.error('保存设置失败')
  } finally {
    isSaving.value = false
  }
}

const resetSettings = () => {
  settingsForm.apiBaseUrl = '/api/v1'
  ElMessage.info('已重置为默认值')
}

// ===== 初始化 =====
onMounted(async () => {
  try {
    const res = await modelAPI.getAISettings()
    const data = res.data?.data
    if (data) {
      chatProviders.value = data.providers || []
      embeddingProviders.value = data.embeddingProviders || []
      indexDimensions.value = data.indexes || []

      const current = data.current || {}

      if (current.defaultChatModel) {
        chatModelForm.providerId = current.defaultChatModel?.providerId || ''
        chatModelForm.modelName = current.defaultChatModel?.modelName || ''
      }
      if (current.fallbackChatModel) {
        fallbackChatForm.providerId = current.fallbackChatModel?.providerId || ''
        fallbackChatForm.modelName = current.fallbackChatModel?.modelName || ''
      }
      if (current.embeddingProvider) {
        embeddingForm.providerId = current.embeddingProvider?.providerId || ''
        // 同步当前生效的 MRL 截断维度到输入框
        if (current.embeddingProvider?.dimension) {
          embeddingDimInput.value = Number(current.embeddingProvider?.dimension)
        } else {
          const p = embeddingProviders.value.find(p => p.id === embeddingForm.providerId)
          embeddingDimInput.value = p ? Number(p.dimension) : null
        }
      }
      if (current.fallbackEmbeddingProvider) {
        fallbackEmbeddingForm.providerId = current.fallbackEmbeddingProvider?.providerId || ''
      }
      if (current.helperModel) {
        helperForm.providerId = current.helperModel?.providerId || ''
        helperForm.modelName = current.helperModel?.modelName || ''
      }

      onEmbeddingProviderChange()
    }
  } catch (e) {
    logger.warn('加载 AI 设置失败:', e)
  }
})
</script>

<style scoped>
.settings-view {
  height: 100%;
  padding: 24px;
  overflow-y: auto;
}

.view-content {
  max-width: 900px;
  margin: 0 auto;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.about-item {
  margin: 8px 0;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

.model-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.model-select {
  flex: 1;
}

.form-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
  line-height: 1.4;
}

.dimension-info {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 14px;
}

.dimension-warning {
  color: var(--el-color-warning);
  display: flex;
  align-items: center;
  gap: 4px;
}

.dimension-ok {
  color: var(--el-color-success);
}

.dimension-hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}
.dimension-hint .hint-text {
  margin-right: 4px;
}
.dimension-hint code {
  background: var(--el-fill-color-light);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
  color: var(--el-color-primary);
}

.affected-indexes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

@media (max-width: 768px) {
  .settings-view { padding: 12px; }
  .view-content { max-width: 100%; }
  .model-row { flex-direction: column; }
}

@media (max-width: 480px) {
  .settings-view { padding: 8px; }
}
</style>
