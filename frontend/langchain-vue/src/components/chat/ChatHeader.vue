<script setup>
import { InfoFilled, Tools } from '@element-plus/icons-vue'
import ModelSelector from '../ModelSelector.vue'

/**
 * ChatHeader组件 - 聊天页面头部
 * @component
 * @description 显示标题、模型选择器、模式切换、调试面板控制等
 */
defineProps({
  /**
   * 页面标题
   * @type {string}
   */
  title: {
    type: String,
    default: '智能聊天',
  },
  /**
   * 当前选中的模型（v-model）
   * @type {string}
   */
  selectedModel: {
    type: String,
    default: 'deepseek-chat',
  },
  /**
   * 当前聊天模式
   * @type {string}
   * @required
   */
  currentMode: {
    type: String,
    required: true,
  },
  /**
   * 可用模式配置对象
   * @type {Object.<string, string>}
   * @required
   * @example { 'basic-agent': '基础代理', 'rag': 'RAG 检索' }
   */
  availableModes: {
    type: Object,
    required: true,
    validator: (value) => {
      if (!value || typeof value !== 'object') return false
      return Object.values(value).every(v => typeof v === 'string')
    },
  },
  /**
   * 是否显示调试面板
   * @type {boolean}
   */
  showDebug: {
    type: Boolean,
    default: false,
  },
  /**
   * 是否显示右侧详情面板
   * @type {boolean}
   */
  showRightPanel: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits({
  /**
   * 更新选中模型事件（v-model）
   * @param {string} model - 模型名称
   */
  'update:selectedModel': (model) => typeof model === 'string',
  /**
   * 更新当前模式事件（v-model）
   * @param {string} mode - 模式标识符
   */
  'update:currentMode': (mode) => typeof mode === 'string',
  /**
   * 切换调试面板显示状态
   */
  toggleDebug: () => true,
  /**
   * 切换右侧面板显示状态
   */
  toggleRightPanel: () => true,
})
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
          @click="emit('toggleRightPanel')"
        >
          <el-icon><InfoFilled /></el-icon>
        </el-button>
      </el-tooltip>
      <el-tooltip :content="showDebug ? '关闭调试模式' : '开启调试模式'">
        <el-button
          :type="showDebug ? 'primary' : 'default'"
          size="small"
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
        :model-value="currentMode" 
        placeholder="选择模式"
        style="width: 180px;" 
        @update:model-value="(val) => emit('update:currentMode', val)"
      >
        <el-option
          v-for="(desc, mode) in availableModes"
          :key="mode"
          :label="desc"
          :value="mode"
        />
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
  border-bottom: 1px solid var(--el-border-color);
  padding: 12px 24px;
}

.header-left {
  display: flex;
  align-items: center;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
</style>
