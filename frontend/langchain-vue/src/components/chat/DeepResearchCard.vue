<script setup>
/**
 * 深度研究卡片 + 继续研究按钮。
 *
 * 当 assistant 消息关联深度研究任务时展示卡片，点击跳转深度研究模块；
 * 流式输出中显示"研究进行中"，完成后显示"研究已完成"。
 * 非流式状态下额外显示"继续研究"按钮，可恢复对应研究任务。
 */
import { Search, ArrowRight, Loading } from '@element-plus/icons-vue'

defineProps({
  /** 是否展示深度研究卡片（由父组件依据 research_task_id 计算） */
  show: {
    type: Boolean,
    default: false,
  },
  /** 是否处于流式输出中（用于切换卡片文案） */
  isStreaming: {
    type: Boolean,
    default: false,
  },
  /** 是否展示"继续研究"按钮 */
  showContinueResearch: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits({
  navigate: () => true,
  'continue-research': () => true,
})
</script>

<template>
  <!-- 深度研究进度卡片 -->
  <div v-if="show" class="deep-research-card" @click.stop="emit('navigate')">
    <div class="deep-research-card-icon">
      <el-icon :size="20"><Search /></el-icon>
    </div>
    <div class="deep-research-card-content">
      <div class="deep-research-card-title">深度研究任务已创建</div>
      <div class="deep-research-card-desc">
        <template v-if="isStreaming">
          <el-icon class="is-loading" :size="12"><Loading /></el-icon>
          <span>研究进行中，点击查看实时进度</span>
        </template>
        <template v-else>
          <span>研究已完成，点击查看详细报告和文件</span>
        </template>
      </div>
    </div>
    <div class="deep-research-card-arrow">
      <el-icon :size="14"><ArrowRight /></el-icon>
    </div>
  </div>

  <!-- 继续研究按钮 -->
  <div v-if="showContinueResearch" class="continue-research-btn" @click.stop="emit('continue-research')">
    <el-icon :size="14"><Search /></el-icon>
    <span>继续研究</span>
  </div>
</template>

<style scoped>
.deep-research-card {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding: 12px 0;
  border-radius: 10px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--sidebar-primary) 8%, transparent), color-mix(in srgb, var(--sidebar-primary) 3%, transparent));
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 20%, transparent);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.deep-research-card:hover {
  background: linear-gradient(135deg, color-mix(in srgb, var(--sidebar-primary) 14%, transparent), color-mix(in srgb, var(--sidebar-primary) 6%, transparent));
  border-color: color-mix(in srgb, var(--sidebar-primary) 35%, transparent);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}

.deep-research-card-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--sidebar-primary) 15%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sidebar-primary);
  flex-shrink: 0;
}

.deep-research-card-content {
  flex: 1;
  min-width: 0;
}

.deep-research-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
}

.deep-research-card-desc {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted-foreground);
  margin-top: 2px;
}

.deep-research-card-desc .is-loading {
  color: var(--sidebar-primary);
}

.deep-research-card-arrow {
  color: var(--muted-foreground);
  flex-shrink: 0;
  transition: transform var(--transition-fast);
}

.deep-research-card:hover .deep-research-card-arrow {
  color: var(--sidebar-primary);
  transform: translateX(2px);
}

.continue-research-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 6px 14px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--sidebar-primary) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 20%, transparent);
  color: var(--sidebar-primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  user-select: none;
}

.continue-research-btn:hover {
  background: color-mix(in srgb, var(--sidebar-primary) 15%, transparent);
  border-color: color-mix(in srgb, var(--sidebar-primary) 35%, transparent);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}

.continue-research-btn:active {
  transform: translateY(0);
}
</style>
