<script setup>
import { ref, computed } from 'vue'
import { ElBadge, ElTag } from 'element-plus'
import LxScrollArea from '../LxScrollArea.vue'

const props = defineProps({
  metadata: {
    type: Object,
    default: () => ({})
  },
  rawJson: {
    type: Object,
    default: null
  }
})

const activeTab = ref('sources')

const hasSources = computed(() => props.metadata?.sources && props.metadata.sources.length > 0)
const hasTools = computed(() => props.metadata?.tools && props.metadata.tools.length > 0)
const hasReasoning = computed(() => !!props.metadata?.reasoning)
const hasChainOfThought = computed(() => !!props.metadata?.chainOfThought)

const tabs = [
  { value: 'sources', label: '来源', showBadge: hasSources },
  { value: 'tools', label: '工具', showBadge: hasTools },
  { value: 'reasoning', label: '推理' },
  { value: 'json', label: 'JSON' }
]

const getToolStateType = (state) => {
  const typeMap = {
    'calling': 'warning',
    'success': 'success',
    'error': 'danger',
    'pending': 'info'
  }
  return typeMap[state] || 'info'
}
</script>

<template>
  <div class="chat-right-panel">
    <div class="panel-header">
      <h3 class="panel-title">详细信息</h3>
    </div>

    <div class="panel-tabs">
      <div class="tabs-list">
        <button
          v-for="tab in tabs"
          :key="tab.value"
          :class="['tab-trigger', { active: activeTab === tab.value }]"
          @click="activeTab = tab.value"
        >
          {{ tab.label }}
          <ElBadge
            v-if="tab.showBadge"
            :value="tab.value === 'sources' ? metadata.sources?.length : metadata.tools?.length"
            type="info"
            class="tab-badge"
          />
        </button>
      </div>
    </div>

    <LxScrollArea class="panel-content">
      <div v-show="activeTab === 'sources'" class="tab-content">
        <div v-if="hasSources" class="sources-container">
          <div
            v-for="(source, index) in metadata.sources"
            :key="index"
            class="source-item"
          >
            <div class="source-header">
              <span class="source-title">{{ source.title || `来源 ${index + 1}` }}</span>
              <a v-if="source.href" :href="source.href" target="_blank" class="source-link">
                🔗
              </a>
            </div>
            <p v-if="source.content" class="source-content">
              {{ source.content }}
            </p>
          </div>
        </div>
        <div v-else class="empty-state">
          暂无来源信息
        </div>
      </div>

      <div v-show="activeTab === 'tools'" class="tab-content">
        <div v-if="hasTools" class="tools-container">
          <div
            v-for="tool in metadata.tools"
            :key="tool.id"
            class="tool-item"
          >
            <div class="tool-header">
              <span class="tool-name">{{ tool.name }}</span>
              <ElTag
                :type="getToolStateType(tool.state)"
                size="small"
                class="tool-state-tag"
              >
                {{ tool.state }}
              </ElTag>
            </div>
            <div v-if="tool.parameters" class="tool-section">
              <div class="section-title">输入参数</div>
              <pre class="tool-json">{{ JSON.stringify(tool.parameters, null, 2) }}</pre>
            </div>
            <div v-if="tool.result" class="tool-section">
              <div class="section-title">输出结果</div>
              <pre class="tool-json">{{ JSON.stringify(tool.result, null, 2) }}</pre>
            </div>
            <div v-if="tool.error" class="tool-section">
              <div class="section-title error">错误</div>
              <pre class="tool-json error">{{ tool.error }}</pre>
            </div>
          </div>
        </div>
        <div v-else class="empty-state">
          暂无工具调用
        </div>
      </div>

      <div v-show="activeTab === 'reasoning'" class="tab-content">
        <div v-if="hasReasoning || hasChainOfThought" class="reasoning-container">
          <div v-if="hasReasoning" class="reasoning-item">
            <div class="reasoning-title">推理过程</div>
            <div class="reasoning-content">{{ metadata.reasoning }}</div>
          </div>
          <div v-if="hasChainOfThought" class="reasoning-item">
            <div class="reasoning-title">思维链</div>
            <div class="reasoning-content">{{ metadata.chainOfThought }}</div>
          </div>
        </div>
        <div v-else class="empty-state">
          暂无推理信息
        </div>
      </div>

      <div v-show="activeTab === 'json'" class="tab-content">
        <pre v-if="rawJson" class="json-display">{{ JSON.stringify(rawJson, null, 2) }}</pre>
        <div v-else class="empty-state">
          暂无 JSON 数据
        </div>
      </div>
    </LxScrollArea>
  </div>
</template>

<style scoped>
.chat-right-panel {
  width: 320px;
  border-left: 1px solid var(--el-border-color);
  background-color: var(--el-bg-color);
  display: flex;
  flex-direction: column;
  height: 100%;
}

.panel-header {
  padding: 16px;
  border-bottom: 1px solid var(--el-border-color);
}

.panel-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  color: var(--el-text-color-primary);
}

.panel-tabs {
  border-bottom: 1px solid var(--el-border-color);
}

.tabs-list {
  display: flex;
  padding: 0 8px;
}

.tab-trigger {
  position: relative;
  flex: 1;
  padding: 12px 8px;
  font-size: 14px;
  color: var(--el-text-color-secondary);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.tab-trigger:hover {
  color: var(--el-text-color-primary);
}

.tab-trigger.active {
  color: var(--el-color-primary);
  border-bottom: 2px solid var(--el-color-primary);
  margin-bottom: -1px;
}

.tab-badge {
  font-size: 10px;
}

.panel-content {
  flex: 1;
  overflow: hidden;
}

.tab-content {
  padding: 16px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 128px;
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.sources-container,
.tools-container,
.reasoning-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.source-item {
  padding: 12px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.source-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.source-title {
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.source-link {
  text-decoration: none;
  font-size: 14px;
}

.source-content {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0;
  line-height: 1.6;
}

.tool-item {
  padding: 12px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.tool-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.tool-name {
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.tool-section {
  margin-bottom: 12px;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.section-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
  text-transform: uppercase;
}

.section-title.error {
  color: var(--el-color-danger);
}

.tool-json {
  background-color: var(--el-bg-color-page);
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  overflow-x: auto;
  margin: 0;
  line-height: 1.5;
  color: var(--el-text-color-primary);
}

.tool-json.error {
  color: var(--el-color-danger);
}

.reasoning-item {
  padding: 12px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.reasoning-title {
  font-weight: 500;
  color: var(--el-text-color-primary);
  margin-bottom: 8px;
}

.reasoning-content {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.json-display {
  background-color: var(--el-fill-color-lighter);
  padding: 16px;
  border-radius: 8px;
  font-size: 12px;
  overflow-x: auto;
  margin: 0;
  line-height: 1.5;
  color: var(--el-text-color-primary);
}
</style>
