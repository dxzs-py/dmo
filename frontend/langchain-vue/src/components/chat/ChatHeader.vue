<script setup>
import { InfoFilled, Tools } from '@element-plus/icons-vue'
import ModelSelector from '../ModelSelector.vue'

defineProps({
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

const emit = defineEmits(['update:selectedModel', 'update:currentMode', 'toggleDebug', 'toggleRightPanel'])
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
        @update:model-value="(val) => emit('update:currentMode', val)"
        placeholder="选择模式" 
        style="width: 180px;"
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
