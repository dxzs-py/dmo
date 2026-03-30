<script setup>
defineOptions({
  name: 'LearningPlan'
})

import { ref, computed } from 'vue'
import { ElCard, ElButton, ElTag, ElProgress, ElEmpty } from 'element-plus'
import { List, CircleCheck, Clock, Loading, Check } from '@element-plus/icons-vue'

const props = defineProps({
  title: {
    type: String,
    default: '学习计划'
  },
  description: {
    type: String,
    default: ''
  },
  steps: {
    type: Array,
    default: () => []
  },
  isStreaming: {
    type: Boolean,
    default: false
  },
  collapsible: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['step-click', 'toggle'])

const isExpanded = ref(true)

const completedSteps = computed(() => {
  return props.steps.filter(step => step.status === 'completed').length
})

const progress = computed(() => {
  if (props.steps.length === 0) return 0
  return Math.round((completedSteps.value / props.steps.length) * 100)
})

const getStepStatusType = (status) => {
  const typeMap = {
    pending: 'info',
    in_progress: 'warning',
    completed: 'success',
    failed: 'danger'
  }
  return typeMap[status] || 'info'
}

const getStepStatusText = (status) => {
  const textMap = {
    pending: '待开始',
    in_progress: '进行中',
    completed: '已完成',
    failed: '失败'
  }
  return textMap[status] || status
}

const getStepIcon = (status) => {
  if (status === 'completed') return CircleCheck
  if (status === 'in_progress') return Loading
  return Clock
}

const toggleExpand = () => {
  if (!props.collapsible) return
  isExpanded.value = !isExpanded.value
  emit('toggle', isExpanded.value)
}

const handleStepClick = (step, index) => {
  emit('step-click', step, index)
}
</script>

<template>
  <div class="plan" :class="{ 'is-streaming': isStreaming }">
    <el-card class="plan-card" shadow="never">
      <template #header>
        <div class="plan-header" @click="toggleExpand">
          <div class="plan-header-left">
            <el-icon class="plan-icon"><List /></el-icon>
            <div class="plan-title-section">
              <div class="plan-title">
                {{ title }}
                <el-tag
                  v-if="isStreaming"
                  type="warning"
                  size="small"
                  effect="plain"
                  class="streaming-tag"
                >
                  <el-icon class="is-loading"><Loading /></el-icon>
                  生成中
                </el-tag>
              </div>
              <div v-if="description" class="plan-description">
                {{ description }}
              </div>
            </div>
          </div>
          <div class="plan-header-right">
            <div class="plan-progress">
              <span class="progress-text">{{ completedSteps }}/{{ steps.length }}</span>
              <el-progress
                :percentage="progress"
                :stroke-width="6"
                :show-text="false"
                :color="progress === 100 ? '#67c23a' : '#409eff'"
              />
            </div>
            <el-button
              v-if="collapsible"
              :icon="List"
              text
              class="expand-btn"
              :class="{ 'is-expanded': isExpanded }"
            />
          </div>
        </div>
      </template>

      <el-collapse-transition>
        <div v-show="isExpanded" class="plan-content">
          <div v-if="steps.length === 0 && !isStreaming" class="plan-empty">
            <el-empty description="暂无计划步骤" :image-size="80" />
          </div>

          <div v-else class="plan-steps">
            <div
              v-for="(step, index) in steps"
              :key="step.id || index"
              class="plan-step"
              :class="[`step-${step.status}`, { clickable: step.status === 'pending' || step.status === 'in_progress' }]"
              @click="handleStepClick(step, index)"
            >
              <div class="step-indicator">
                <el-icon
                  class="step-icon"
                  :class="`status-${step.status}`"
                >
                  <component :is="getStepIcon(step.status)" />
                </el-icon>
                <div v-if="index < steps.length - 1" class="step-line" />
              </div>

              <div class="step-content">
                <div class="step-header">
                  <span class="step-order">{{ index + 1 }}</span>
                  <span class="step-title">{{ step.title }}</span>
                  <el-tag
                    :type="getStepStatusType(step.status)"
                    size="small"
                    effect="plain"
                  >
                    {{ getStepStatusText(step.status) }}
                  </el-tag>
                </div>

                <div v-if="step.description" class="step-description">
                  {{ step.description }}
                </div>

                <div v-if="step.progress !== undefined" class="step-progress">
                  <el-progress
                    :percentage="step.progress"
                    :stroke-width="4"
                    :show-text="false"
                    size="small"
                  />
                </div>

                <div v-if="step.subSteps && step.subSteps.length > 0" class="step-substeps">
                  <div
                    v-for="(subStep, subIndex) in step.subSteps"
                    :key="subIndex"
                    class="substep-item"
                  >
                    <el-icon v-if="subStep.completed" class="substep-check" color="#67c23a"><Check /></el-icon>
                    <span v-else class="substep-bullet">•</span>
                    <span class="substep-text">{{ subStep.text }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-collapse-transition>
    </el-card>
  </div>
</template>

<style scoped>
.plan {
  margin: 16px 0;
}

.plan.is-streaming {
  border: 1px solid var(--el-color-primary-light-5);
  border-radius: 8px;
}

.plan-card {
  --el-card-border-radius: 8px;
}

.plan-card :deep(.el-card__header) {
  padding: 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.plan-card :deep(.el-card__body) {
  padding: 0;
}

.plan-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  cursor: pointer;
}

.plan-header-left {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.plan-icon {
  font-size: 24px;
  color: var(--el-color-primary);
  margin-top: 2px;
}

.plan-title-section {
  flex: 1;
}

.plan-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.streaming-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.plan-description {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.plan-header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.plan-progress {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  min-width: 100px;
}

.progress-text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.expand-btn {
  transition: transform 0.3s ease;
}

.expand-btn.is-expanded {
  transform: rotate(180deg);
}

.plan-content {
  padding: 16px;
}

.plan-empty {
  padding: 24px;
  text-align: center;
}

.plan-steps {
  display: flex;
  flex-direction: column;
}

.plan-step {
  display: flex;
  gap: 16px;
  min-height: 60px;
}

.plan-step.clickable {
  cursor: pointer;
}

.plan-step.clickable:hover {
  background-color: var(--el-fill-color-light);
  border-radius: 8px;
}

.step-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 24px;
}

.step-icon {
  font-size: 18px;
  padding: 3px;
  border-radius: 50%;
  background-color: var(--el-fill-color-light);
}

.step-icon.status-pending {
  color: var(--el-text-color-secondary);
}

.step-icon.status-in_progress {
  color: var(--el-color-warning);
}

.step-icon.status-completed {
  color: var(--el-color-success);
  background-color: var(--el-color-success-light-9);
}

.step-icon.status-failed {
  color: var(--el-color-danger);
}

.step-line {
  flex: 1;
  width: 2px;
  background-color: var(--el-border-color-lighter);
  margin: 4px 0;
  min-height: 20px;
}

.step-content {
  flex: 1;
  padding-bottom: 16px;
}

.step-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.step-order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background-color: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
}

.step-title {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.step-description {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  margin-top: 4px;
  margin-left: 28px;
}

.step-progress {
  margin-top: 8px;
  margin-left: 28px;
}

.step-substeps {
  margin-top: 8px;
  margin-left: 28px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.substep-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.substep-check {
  font-size: 14px;
}

.substep-bullet {
  color: var(--el-text-color-secondary);
}
</style>
