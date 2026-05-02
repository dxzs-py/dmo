<script setup>
import { computed } from 'vue'
import { InfoFilled, Tools, FolderOpened } from '@element-plus/icons-vue'
import ModelSelector from '../common/ModelSelector.vue'
import { useSessionStore } from '../../stores/session'

const props = defineProps({
  title: {
    type: String,
    default: '智能聊天',
  },
  selectedModel: {
    type: String,
    default: 'deepseek-chat',
  },
  currentMode: {
    type: String,
    required: true,
  },
  availableModes: {
    type: Object,
    required: true,
  },
  showDebug: {
    type: Boolean,
    default: false,
  },
  showRightPanel: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits({
  'update:selectedModel': (model) => typeof model === 'string',
  'update:currentMode': (mode) => typeof mode === 'string',
  toggleDebug: () => true,
  toggleRightPanel: () => true,
})

const sessionStore = useSessionStore()

const selectedKnowledgeBaseId = computed({
  get: () => sessionStore.selectedKnowledgeBase?.id || null,
  set: (val) => {
    sessionStore.setSelectedKnowledgeBase(val)
  }
})

const modeConfig = {
  'basic-agent': { icon: '🤖', label: '基础代理', desc: '日常问答与任务执行' },
  'deep-thinking': { icon: '🧠', label: '深度思考', desc: '深度分析与推理' },
  'rag': { icon: '📚', label: 'RAG 检索', desc: '基于知识库的检索增强' },
  'workflow': { icon: '🔄', label: '学习工作流', desc: '计划-练习-反馈学习' },
  'deep-research': { icon: '🔬', label: '深度研究', desc: '多步骤深度研究分析' },
  'guarded': { icon: '🛡️', label: '安全代理', desc: '带安全防护的对话' },
}

function getModeConfig(mode) {
  return modeConfig[mode] || { icon: '💬', label: mode, desc: '' }
}
</script>

<template>
  <div class="chat-header">
    <div class="header-left">
      <span class="page-title">{{ title }}</span>
    </div>
    <div class="header-right">
      <el-tooltip :content="showRightPanel ? '隐藏详情面板' : '显示详情面板'">
        <el-button
          :type="showRightPanel ? 'primary' : 'default'"
          size="small"
          circle
          @click="emit('toggleRightPanel')"
        >
          <el-icon><InfoFilled /></el-icon>
        </el-button>
      </el-tooltip>
      <el-tooltip :content="showDebug ? '关闭调试模式' : '开启调试模式'">
        <el-button
          :type="showDebug ? 'primary' : 'default'"
          size="small"
          circle
          @click="emit('toggleDebug')"
        >
          <el-icon><Tools /></el-icon>
        </el-button>
      </el-tooltip>
      <ModelSelector
        :model-value="selectedModel"
        @update:model-value="(val) => emit('update:selectedModel', val)"
      />
      <el-select
        :model-value="selectedKnowledgeBaseId"
        placeholder="选择知识库"
        class="knowledge-selector"
        clearable
        @update:model-value="(val) => selectedKnowledgeBaseId = val"
      >
        <el-option
          v-for="kb in sessionStore.knowledgeBases"
          :key="kb.id"
          :label="kb.name"
          :value="kb.id"
        >
          <div class="knowledge-option">
            <el-icon class="knowledge-icon"><FolderOpened /></el-icon>
            <span>{{ kb.name }}</span>
          </div>
        </el-option>
      </el-select>
      <el-select
        :model-value="currentMode"
        placeholder="选择模式"
        class="mode-selector"
        popper-class="mode-selector-popper"
        @update:model-value="(val) => emit('update:currentMode', val)"
      >
        <el-option
          v-for="(desc, mode) in availableModes"
          :key="mode"
          :label="`${getModeConfig(mode).icon} ${getModeConfig(mode).label}`"
          :value="mode"
        >
          <div class="mode-option">
            <span class="mode-icon">{{ getModeConfig(mode).icon }}</span>
            <div class="mode-info">
              <span class="mode-name">{{ getModeConfig(mode).label }}</span>
              <span class="mode-desc">{{ getModeConfig(mode).desc || desc }}</span>
            </div>
          </div>
        </el-option>
      </el-select>
    </div>
  </div>
</template>

<style scoped>
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding: 10px 24px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.page-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.knowledge-selector {
  width: 160px;
}

.knowledge-option {
  display: flex;
  align-items: center;
  gap: 8px;
}

.knowledge-icon {
  color: var(--el-color-primary);
}

.mode-selector {
  width: 180px;
}

.mode-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
}

.mode-icon {
  font-size: 20px;
  flex-shrink: 0;
  width: 28px;
  text-align: center;
}

.mode-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mode-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  line-height: 1.3;
}

.mode-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.3;
}
</style>
