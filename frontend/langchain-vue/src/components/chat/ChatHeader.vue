<script setup>
import { computed } from 'vue'
import { FolderOpened, Hide, View, Cpu } from '@element-plus/icons-vue'
import ModelSelector from '../common/ModelSelector.vue'
import { useSessionStore } from '../../stores/session'
import { ElMessage } from 'element-plus'

const props = defineProps({
  title: {
    type: String,
    default: '智能聊天',
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
  connectionStatus: {
    type: String,
    default: 'disconnected',
    validator: (v) => ['connected', 'disconnected', 'reconnecting', 'connecting'].includes(v),
  },
})

const emit = defineEmits({
  'model-change': (data) => true,
  'update:currentMode': (mode) => typeof mode === 'string',
  toggleDebug: () => true,
  toggleRightPanel: () => true,
})

const sessionStore = useSessionStore()

const selectedKnowledgeBaseId = computed({
  get: () => sessionStore.selectedKnowledgeBase?.id || null,
  set: (val) => {
    sessionStore.setSelectedKnowledgeBase(val)
    if (val) {
      const kb = sessionStore.knowledgeBases.find(k => k.id === val)
      if (kb) ElMessage.success(`已加载知识库: ${kb.name}`)
    }
  }
})

const modeConfig = {
  'basic-agent': { icon: '🤖', label: '基础代理', desc: '日常问答与任务执行', color: '#3b82f6' },
  'deep-thinking': { icon: '🧠', label: '深度思考', desc: '深度分析与推理', color: '#8b5cf6' },
  'rag': { icon: '📚', label: 'RAG 检索', desc: '基于知识库的检索增强', color: '#10b981' },
  'workflow': { icon: '🔄', label: '学习工作流', desc: '计划-练习-反馈学习', color: '#f59e0b' },
  'deep-research': { icon: '🔬', label: '深度研究', desc: '多步骤深度研究分析', color: '#ec4899' },
  'guarded': { icon: '🛡️', label: '安全代理', desc: '带安全防护的对话', color: '#6366f1' },
}

const currentModeConfig = computed(() => getModeConfig(props.currentMode))

function getModeConfig(mode) {
  return modeConfig[mode] || { icon: '💬', label: mode, desc: '', color: '#6b7280' }
}

const connectionLabel = computed(() => {
  const map = { connected: '已连接', disconnected: '已断开', reconnecting: '重连中', connecting: '连接中' }
  return map[props.connectionStatus] || ''
})

function handleModeChange(mode) {
  emit('update:currentMode', mode)
  const cfg = getModeConfig(mode)
  ElMessage.success(`已切换到${cfg.label}模式`)
}
</script>

<template>
  <div class="chat-header">
    <div class="header-left">
      <span class="page-title">{{ title }}</span>
      <div
        v-if="currentModeConfig"
        class="mode-badge"
        :style="{ '--badge-color': currentModeConfig.color }"
      >
        <span class="mode-badge-icon">{{ currentModeConfig.icon }}</span>
        <span class="mode-badge-label">{{ currentModeConfig.label }}</span>
      </div>
      <div
        class="connection-indicator"
        :class="connectionStatus"
        :title="connectionLabel"
      >
        <span class="connection-dot"></span>
        <span class="connection-text">{{ connectionLabel }}</span>
      </div>
    </div>
    <div class="header-right">
      <div class="header-group model-group">
        <ModelSelector
          @change="(data) => emit('model-change', data)"
        />
      </div>

      <div class="header-divider"></div>

      <div class="header-group config-group">
        <el-select
          :model-value="selectedKnowledgeBaseId"
          placeholder="知识库"
          class="knowledge-selector"
          clearable
          size="default"
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
          placeholder="模式"
          class="mode-selector"
          popper-class="mode-selector-popper"
          size="default"
          @update:model-value="handleModeChange"
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

      <div class="header-divider"></div>

      <div class="header-group action-group">
        <el-tooltip :content="showRightPanel ? '隐藏详情面板 (Ctrl+B)' : '显示详情面板 (Ctrl+B)'">
          <button
            :class="['header-icon-btn', { active: showRightPanel }]"
            @click="emit('toggleRightPanel')"
          >
            <el-icon :size="15"><component :is="showRightPanel ? Hide : View" /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip :content="showDebug ? '关闭调试模式' : '开启调试模式'">
          <button
            :class="['header-icon-btn debug-btn', { active: showDebug }]"
            @click="emit('toggleDebug')"
          >
            <el-icon :size="15"><Cpu /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--card);
  border-bottom: 1px solid var(--border);
  padding: 8px 20px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 0;
}

.header-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.header-divider {
  width: 1px;
  height: 20px;
  background: var(--border);
  margin: 0 10px;
}

.header-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: var(--muted-foreground);
  cursor: pointer;
  transition: all 0.2s;
}

.header-icon-btn:hover {
  background: var(--accent);
  color: var(--foreground);
  border-color: var(--sidebar-primary);
}

.header-icon-btn.active {
  background: color-mix(in srgb, var(--sidebar-primary) 10%, transparent);
  color: var(--sidebar-primary);
  border-color: var(--sidebar-primary);
}

.debug-btn.active {
  background: color-mix(in srgb, #f59e0b 10%, transparent);
  color: #f59e0b;
  border-color: #f59e0b;
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--foreground);
}

.mode-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 16px;
  background: color-mix(in srgb, var(--badge-color) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--badge-color) 20%, transparent);
  font-size: 12px;
  font-weight: 500;
  color: var(--badge-color);
  transition: all 0.2s;
}

.mode-badge:hover {
  background: color-mix(in srgb, var(--badge-color) 15%, transparent);
}

.mode-badge-icon {
  font-size: 13px;
  line-height: 1;
}

.mode-badge-label {
  line-height: 1;
}

.connection-indicator {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 12px;
  background: var(--el-fill-color-lighter);
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.connection-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: all 0.3s;
}

.connection-text {
  line-height: 1;
}

.connection-indicator.connected .connection-dot {
  background: var(--el-color-success);
  box-shadow: 0 0 4px rgba(16, 185, 129, 0.4);
}

.connection-indicator.disconnected .connection-dot {
  background: var(--el-color-danger);
  box-shadow: 0 0 4px rgba(239, 68, 68, 0.4);
}

.connection-indicator.reconnecting .connection-dot {
  background: var(--el-color-warning);
  animation: pulse-dot 1.5s ease-in-out infinite;
}

.connection-indicator.connecting .connection-dot {
  background: var(--el-color-primary);
  animation: pulse-dot 2s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.3); }
}

.knowledge-selector {
  width: 140px;
}

.knowledge-option {
  display: flex;
  align-items: center;
  gap: 6px;
}

.knowledge-icon {
  color: var(--sidebar-primary);
}

.mode-selector {
  width: 150px;
}

.mode-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.mode-icon {
  font-size: 18px;
  flex-shrink: 0;
  width: 24px;
  text-align: center;
}

.mode-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.mode-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--foreground);
  line-height: 1.3;
}

.mode-desc {
  font-size: 11px;
  color: var(--muted-foreground);
  line-height: 1.3;
}

@media (max-width: 1200px) {
  .knowledge-selector {
    width: 120px;
  }
  .mode-selector {
    width: 130px;
  }
}

@media (max-width: 768px) {
  .chat-header {
    padding: 6px 12px;
  }

  .page-title {
    font-size: 14px;
  }

  .header-left {
    gap: 6px;
  }

  .connection-text {
    display: none;
  }

  .mode-badge {
    display: none;
  }

  .header-divider {
    margin: 0 6px;
  }

  .knowledge-selector {
    width: 100px;
  }

  .mode-selector {
    width: 110px;
  }
}

@media (max-width: 480px) {
  .chat-header {
    padding: 6px 8px;
    flex-wrap: wrap;
    gap: 6px;
  }

  .page-title {
    font-size: 13px;
  }

  .header-right {
    flex-wrap: wrap;
    gap: 4px;
  }

  .header-divider {
    display: none;
  }

  .header-group {
    gap: 4px;
  }

  .knowledge-selector {
    width: 90px;
  }

  .mode-selector {
    max-width: 100px;
  }
}
</style>
