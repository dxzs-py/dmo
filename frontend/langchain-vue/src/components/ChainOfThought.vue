<script setup>
import { ref, computed } from 'vue'
import { ArrowDown, MagicStick } from '@element-plus/icons-vue'

const props = defineProps({
  steps: {
    type: Array,
    default: () => []
  }
})

const isExpanded = ref(true)

const formattedSteps = computed(() => {
  return props.steps.map((step, index) => ({
    ...step,
    index: index + 1
  }))
})
</script>

<template>
  <div class="chain-of-thought">
    <div class="cot-header" @click="isExpanded = !isExpanded">
      <div class="cot-title">
        <el-icon class="brain-icon"><MagicStick /></el-icon>
        <span>思考过程</span>
        <el-tag type="info" size="small">{{ formattedSteps.length }} 步</el-tag>
      </div>
      <el-icon class="expand-icon" :class="{ 'rotated': isExpanded }">
        <ArrowDown />
      </el-icon>
    </div>
    
    <div v-if="isExpanded && formattedSteps.length > 0" class="cot-content">
      <div class="steps-container">
        <div
          v-for="(step, index) in formattedSteps"
          :key="step.index"
          class="step-item"
        >
          <div class="step-marker">
            <div class="step-number">{{ step.index }}</div>
            <div v-if="index < formattedSteps.length - 1" class="step-line"></div>
          </div>
          <div class="step-content">
            <div v-if="step.title" class="step-title">{{ step.title }}</div>
            <div v-if="step.content" class="step-text">{{ step.content }}</div>
            <div v-if="step.toolCall" class="step-tool-call">
              <ToolCallCard
                :tool-name="step.toolCall.name"
                :input="step.toolCall.input"
                :output="step.toolCall.output"
                :status="step.toolCall.status || 'completed'"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <div v-if="isExpanded && formattedSteps.length === 0" class="cot-empty">
      <span>暂无思考过程</span>
    </div>
  </div>
</template>

<script>
import ToolCallCard from './ToolCallCard.vue'

export default {
  components: {
    ToolCallCard
  }
}
</script>

<style scoped>
.chain-of-thought {
  background-color: var(--el-bg-color-page);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  margin: 8px 0;
  overflow: hidden;
}

.cot-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.cot-header:hover {
  background-color: var(--el-fill-color-light);
}

.cot-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brain-icon {
  color: var(--el-color-primary);
  font-size: 18px;
}

.cot-title span {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.expand-icon {
  color: var(--el-text-color-secondary);
  transition: transform 0.2s;
}

.expand-icon.rotated {
  transform: rotate(180deg);
}

.cot-content {
  padding: 0 16px 16px;
}

.steps-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.step-item {
  display: flex;
  gap: 16px;
}

.step-marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
}

.step-number {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--el-color-primary) 0%, var(--el-color-primary-light-3) 100%);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}

.step-line {
  flex: 1;
  width: 2px;
  background: var(--el-border-color);
  margin: 4px 0;
}

.step-content {
  flex: 1;
  min-width: 0;
}

.step-title {
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 4px;
}

.step-text {
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.step-tool-call {
  margin-top: 8px;
}

.cot-empty {
  padding: 24px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
</style>
