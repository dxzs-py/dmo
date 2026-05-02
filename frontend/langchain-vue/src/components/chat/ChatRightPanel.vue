<script setup>
import { ref, computed, watch } from 'vue'
import { ElTag } from 'element-plus'
import { Document, Tools, MagicStick, View, Coin, Lock, Files } from '@element-plus/icons-vue'
import LxScrollArea from '../common/LxScrollArea.vue'
import CostTracker from './CostTracker.vue'
import PermissionManager from './PermissionManager.vue'
import TokenStats from './TokenStats.vue'
import AiArtifact from '../ai-elements/AiArtifact.vue'
import AiArtifactHeader from '../ai-elements/AiArtifactHeader.vue'
import AiArtifactTitle from '../ai-elements/AiArtifactTitle.vue'
import AiArtifactDescription from '../ai-elements/AiArtifactDescription.vue'
import AiArtifactActions from '../ai-elements/AiArtifactActions.vue'
import AiArtifactAction from '../ai-elements/AiArtifactAction.vue'
import AiArtifactContent from '../ai-elements/AiArtifactContent.vue'
import AiArtifactClose from '../ai-elements/AiArtifactClose.vue'
import AiPanel from '../ai-elements/AiPanel.vue'
import AiToolbar from '../ai-elements/AiToolbar.vue'

const props = defineProps({
  message: {
    type: Object,
    default: null,
  },
  visible: {
    type: Boolean,
    default: false,
  },
  costSummary: {
    type: Object,
    default: () => ({}),
  },
  sessionId: {
    type: String,
    default: null,
  },
})

const activeTab = ref('sources')

const metadata = computed(() => {
  if (!props.message || props.message.role !== 'assistant') return {}
  return {
    sources: props.message.sources || [],
    tools: (props.message.toolCalls || []).map((tc, idx) => ({
      id: tc.id || `tool-${idx}`,
      name: tc.name || tc.toolName || '未知工具',
      type: `tool-call-${tc.name || 'unknown'}`,
      state: tc.status === 'completed' ? 'output-available' : tc.status === 'error' ? 'output-error' : 'input-available',
      parameters: tc.input || tc.parameters || tc.args || {},
      result: tc.output || tc.result || null,
      error: tc.error || null,
    })),
    reasoning: props.message.reasoning?.content || null,
    chainOfThought: props.message.chainOfThought || null,
    plan: props.message.plan || null,
    context: props.message.context || null,
  }
})

const rawJson = computed(() => {
  if (!props.message) return null
  return {
    id: props.message.id,
    role: props.message.role,
    content: props.message.content,
    sources: props.message.sources,
    plan: props.message.plan,
    reasoning: props.message.reasoning,
    chainOfThought: props.message.chainOfThought,
    toolCalls: props.message.toolCalls,
    context: props.message.context,
    suggestions: props.message.suggestions,
  }
})

const hasSources = computed(() => metadata.value?.sources?.length > 0)
const hasTools = computed(() => metadata.value?.tools?.length > 0)
const hasReasoning = computed(() => !!metadata.value?.reasoning)
const hasChainOfThought = computed(() => !!metadata.value?.chainOfThought)
const hasPlan = computed(() => !!metadata.value?.plan)
const hasAnyReasoning = computed(() => hasReasoning.value || hasChainOfThought.value || hasPlan.value)

const artifacts = computed(() => {
  if (!props.message || !props.message.artifacts) return []
  return props.message.artifacts
})
const hasArtifacts = computed(() => artifacts.value.length > 0)

const tabs = computed(() => [
  { value: 'sources', label: '来源', icon: Document, count: hasSources.value ? metadata.value.sources.length : 0 },
  { value: 'artifacts', label: '产物', icon: Files, count: hasArtifacts.value ? artifacts.value.length : 0 },
  { value: 'tools', label: '工具', icon: Tools, count: hasTools.value ? metadata.value.tools.length : 0 },
  { value: 'reasoning', label: '推理', icon: MagicStick, count: 0 },
  { value: 'cost', label: '成本', icon: Coin, count: 0 },
  { value: 'permissions', label: '权限', icon: Lock, count: 0 },
  { value: 'json', label: 'JSON', icon: View, count: 0 },
])

const getToolStateType = (state) => {
  const typeMap = {
    'input-available': 'warning',
    'output-available': 'success',
    'output-error': 'danger',
    'calling': 'warning',
    'success': 'success',
    'error': 'danger',
    'pending': 'info',
    'completed': 'success',
  }
  return typeMap[state] || 'info'
}

const getToolStateLabel = (state) => {
  const labelMap = {
    'input-available': '调用中',
    'output-available': '已完成',
    'output-error': '出错',
    'calling': '调用中',
    'success': '成功',
    'error': '失败',
    'pending': '等待中',
    'completed': '已完成',
  }
  return labelMap[state] || state
}

watch(() => props.message, () => {
  if (!hasSources.value && hasTools.value) {
    activeTab.value = 'tools'
  } else if (!hasSources.value && !hasTools.value && hasAnyReasoning.value) {
    activeTab.value = 'reasoning'
  } else if (!hasSources.value && !hasTools.value && !hasAnyReasoning.value && rawJson.value) {
    activeTab.value = 'json'
  }
})
</script>

<template>
  <Transition name="slide-right">
    <div v-if="visible && message" class="chat-right-panel">
      <div class="panel-header">
        <h3 class="panel-title">详细信息</h3>
        <span class="panel-subtitle">
          {{ message.role === 'assistant' ? 'AI 回复' : '用户消息' }}
        </span>
      </div>

      <div class="panel-tabs">
        <AiToolbar class="tabs-toolbar">
          <button
            v-for="tab in tabs"
            :key="tab.value"
            :class="['tab-trigger', { active: activeTab === tab.value }]"
            @click="activeTab = tab.value"
          >
            <el-icon class="tab-icon"><component :is="tab.icon" /></el-icon>
            <span class="tab-label">{{ tab.label }}</span>
            <span v-if="tab.count > 0" class="tab-count">{{ tab.count }}</span>
          </button>
        </AiToolbar>
      </div>

      <LxScrollArea class="panel-content" native>
        <AiPanel class="tab-panel">
          <div v-show="activeTab === 'sources'" class="tab-content">
          <div v-if="hasSources" class="sources-container">
            <div
              v-for="(source, index) in metadata.sources"
              :key="index"
              class="source-item"
              v-memo="[source.title, source.content, source.href || source.url]"
            >
              <div class="source-index">{{ index + 1 }}</div>
              <div class="source-body">
                <div class="source-header">
                  <span class="source-title">{{ source.title || `来源 ${index + 1}` }}</span>
                  <a v-if="source.href || source.url" :href="source.href || source.url" target="_blank" class="source-link">
                    ↗
                  </a>
                </div>
                <p v-if="source.content" class="source-content">
                  {{ source.content.length > 200 ? source.content.substring(0, 200) + '...' : source.content }}
                </p>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">
            <el-icon :size="32" color="var(--el-text-color-placeholder)"><Document /></el-icon>
            <span>暂无来源信息</span>
          </div>
        </div>

        <div v-show="activeTab === 'artifacts'" class="tab-content">
          <div v-if="hasArtifacts" class="artifacts-container">
            <AiArtifact v-for="(artifact, idx) in artifacts" :key="idx">
              <AiArtifactHeader>
                <AiArtifactTitle>{{ artifact.title || `产物 ${idx + 1}` }}</AiArtifactTitle>
                <AiArtifactDescription v-if="artifact.description">{{ artifact.description }}</AiArtifactDescription>
                <AiArtifactActions>
                  <AiArtifactAction
                    v-if="artifact.copyable !== false"
                    tooltip="复制内容"
                    label="复制"
                    @click="navigator.clipboard.writeText(typeof artifact.content === 'string' ? artifact.content : JSON.stringify(artifact.content))"
                  />
                  <AiArtifactClose @click="artifacts.splice(idx, 1)" />
                </AiArtifactActions>
              </AiArtifactHeader>
              <AiArtifactContent>
                <pre v-if="typeof artifact.content === 'string'" class="artifact-text">{{ artifact.content }}</pre>
                <pre v-else class="artifact-json">{{ JSON.stringify(artifact.content, null, 2) }}</pre>
              </AiArtifactContent>
            </AiArtifact>
          </div>
          <div v-else class="empty-state">
            <el-icon :size="32" color="var(--el-text-color-placeholder)"><Files /></el-icon>
            <span>暂无产物信息</span>
          </div>
        </div>

        <div v-show="activeTab === 'tools'" class="tab-content">
          <div v-if="hasTools" class="tools-container">
            <div
              v-for="tool in metadata.tools"
              :key="tool.id"
              class="tool-item"
              v-memo="[tool.name, tool.state, tool.result, tool.error]"
            >
              <div class="tool-header">
                <div class="tool-name-row">
                  <span class="tool-indicator" :class="getToolStateType(tool.state)"></span>
                  <span class="tool-name">{{ tool.name }}</span>
                </div>
                <ElTag
                  :type="getToolStateType(tool.state)"
                  size="small"
                  effect="light"
                  round
                >
                  {{ getToolStateLabel(tool.state) }}
                </ElTag>
              </div>
              <div v-if="tool.parameters && Object.keys(tool.parameters).length > 0" class="tool-section">
                <div class="section-title">输入参数</div>
                <pre class="tool-json">{{ typeof tool.parameters === 'string' ? tool.parameters : JSON.stringify(tool.parameters, null, 2) }}</pre>
              </div>
              <div v-if="tool.result" class="tool-section">
                <div class="section-title">输出结果</div>
                <pre class="tool-json">{{ typeof tool.result === 'string' ? tool.result.substring(0, 500) : JSON.stringify(tool.result, null, 2).substring(0, 500) }}</pre>
              </div>
              <div v-if="tool.error" class="tool-section">
                <div class="section-title error">错误信息</div>
                <pre class="tool-json error">{{ tool.error }}</pre>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">
            <el-icon :size="32" color="var(--el-text-color-placeholder)"><Tools /></el-icon>
            <span>暂无工具调用</span>
          </div>
        </div>

        <div v-show="activeTab === 'reasoning'" class="tab-content">
          <div v-if="hasAnyReasoning" class="reasoning-container">
            <div v-if="hasReasoning" class="reasoning-item">
              <div class="reasoning-title">推理过程</div>
              <div class="reasoning-content">{{ metadata.reasoning }}</div>
            </div>
            <div v-if="hasPlan" class="reasoning-item">
              <div class="reasoning-title">执行计划</div>
              <div class="reasoning-content">
                <div v-if="metadata.plan.title" class="plan-title">{{ metadata.plan.title }}</div>
                <div v-if="metadata.plan.description" class="plan-desc">{{ metadata.plan.description }}</div>
                <div v-if="metadata.plan.steps?.length" class="plan-steps">
                  <div v-for="(step, idx) in metadata.plan.steps" :key="idx" class="plan-step">
                    <span class="step-number">{{ idx + 1 }}</span>
                    <span class="step-text">{{ typeof step === 'string' ? step : step.text || step.title || JSON.stringify(step) }}</span>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="hasChainOfThought" class="reasoning-item">
              <div class="reasoning-title">思维链</div>
              <div class="reasoning-content">
                <template v-if="Array.isArray(metadata.chainOfThought)">
                  <div v-for="(step, idx) in metadata.chainOfThought" :key="idx" class="cot-step">
                    {{ typeof step === 'string' ? step : step.content || step.text || JSON.stringify(step) }}
                  </div>
                </template>
                <template v-else>
                  {{ typeof metadata.chainOfThought === 'string' ? metadata.chainOfThought : JSON.stringify(metadata.chainOfThought, null, 2) }}
                </template>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">
            <el-icon :size="32" color="var(--el-text-color-placeholder)"><MagicStick /></el-icon>
            <span>暂无推理信息</span>
          </div>
        </div>

        <div v-show="activeTab === 'json'" class="tab-content">
          <pre v-if="rawJson" class="json-display">{{ JSON.stringify(rawJson, null, 2) }}</pre>
          <div v-else class="empty-state">
            <el-icon :size="32" color="var(--el-text-color-placeholder)"><View /></el-icon>
            <span>暂无 JSON 数据</span>
          </div>
        </div>

        <div v-show="activeTab === 'cost'" class="tab-content">
          <TokenStats
            :prompt-tokens="costSummary.promptTokens || 0"
            :completion-tokens="costSummary.completionTokens || 0"
            :total-tokens="costSummary.totalTokens || 0"
          />
          <CostTracker :cost-summary="costSummary" />
        </div>

        <div v-show="activeTab === 'permissions'" class="tab-content">
          <PermissionManager :session-id="sessionId" />
        </div>
        </AiPanel>
      </LxScrollArea>
    </div>
  </Transition>
</template>

<style scoped>
.chat-right-panel {
  width: 340px;
  min-width: 340px;
  border-left: 1px solid var(--el-border-color-lighter);
  background-color: var(--el-bg-color);
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.slide-right-enter-active,
.slide-right-leave-active {
  transition: all 0.3s ease;
}

.slide-right-enter-from,
.slide-right-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

.panel-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  color: var(--el-text-color-primary);
}

.panel-subtitle {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.panel-tabs {
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding: 0 4px;
  position: relative;
}

.tabs-toolbar {
  position: static;
  transform: none;
  margin-bottom: 0;
  display: flex;
  border: none;
  background: transparent;
  padding: 0;
  gap: 0;
}

.tab-trigger {
  position: relative;
  flex: 1;
  padding: 10px 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.tab-trigger:hover {
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-lighter);
}

.tab-trigger.active {
  color: var(--el-color-primary);
  border-bottom-color: var(--el-color-primary);
}

.tab-icon {
  font-size: 14px;
}

.tab-label {
  font-size: 13px;
}

.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 9px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.panel-content {
  flex: 1;
  overflow: hidden;
}

.tab-panel {
  border: none;
  border-radius: 0;
  margin: 0;
  padding: 0;
  background: transparent;
}

.tab-content {
  padding: 16px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 128px;
  font-size: 13px;
  color: var(--el-text-color-placeholder);
}

.sources-container,
.tools-container,
.reasoning-container,
.artifacts-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.artifact-text,
.artifact-json {
  margin: 0;
  font-size: 13px;
  font-family: 'Consolas', 'Monaco', monospace;
  white-space: pre-wrap;
  word-break: break-all;
}

.artifact-json {
  color: var(--el-text-color-secondary);
}

.source-item {
  display: flex;
  gap: 10px;
  padding: 12px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 8px;
  border: 1px solid var(--el-border-color-extra-light);
  transition: border-color 0.2s;
}

.source-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.source-index {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.source-body {
  flex: 1;
  min-width: 0;
}

.source-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.source-title {
  font-weight: 500;
  font-size: 13px;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-link {
  text-decoration: none;
  font-size: 14px;
  color: var(--el-color-primary);
  flex-shrink: 0;
  margin-left: 4px;
  transition: opacity 0.2s;
}

.source-link:hover {
  opacity: 0.7;
}

.source-content {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 0;
  line-height: 1.6;
}

.tool-item {
  padding: 12px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 8px;
  border: 1px solid var(--el-border-color-extra-light);
}

.tool-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.tool-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tool-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.tool-indicator.success,
.tool-indicator.output-available {
  background: var(--el-color-success);
}

.tool-indicator.warning,
.tool-indicator.input-available,
.tool-indicator.calling {
  background: var(--el-color-warning);
  animation: pulse 1.5s infinite;
}

.tool-indicator.danger,
.tool-indicator.error,
.tool-indicator.output-error {
  background: var(--el-color-danger);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.tool-name {
  font-weight: 500;
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.tool-section {
  margin-bottom: 10px;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.section-title.error {
  color: var(--el-color-danger);
}

.tool-json {
  background-color: var(--el-bg-color-page);
  padding: 10px;
  border-radius: 6px;
  font-size: 12px;
  overflow-x: auto;
  margin: 0;
  line-height: 1.5;
  color: var(--el-text-color-primary);
  max-height: 200px;
  overflow-y: auto;
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
}

.tool-json.error {
  color: var(--el-color-danger);
}

.reasoning-item {
  padding: 12px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 8px;
  border: 1px solid var(--el-border-color-extra-light);
}

.reasoning-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--el-text-color-primary);
  margin-bottom: 8px;
}

.reasoning-content {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.plan-title {
  font-weight: 500;
  color: var(--el-text-color-primary);
  margin-bottom: 4px;
}

.plan-desc {
  font-size: 12px;
  margin-bottom: 8px;
}

.plan-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.plan-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.step-number {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.step-text {
  font-size: 12px;
  line-height: 20px;
}

.cot-step {
  padding: 6px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  font-size: 12px;
}

.cot-step:last-child {
  border-bottom: none;
}

.json-display {
  background-color: var(--el-fill-color-lighter);
  padding: 14px;
  border-radius: 8px;
  font-size: 12px;
  overflow-x: auto;
  margin: 0;
  line-height: 1.5;
  color: var(--el-text-color-primary);
  max-height: 500px;
  overflow-y: auto;
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
}
</style>
