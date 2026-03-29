<script setup>
import { computed } from 'vue'
import { Check, Clock } from '@element-plus/icons-vue'

const props = defineProps({
  task: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['click'])

const isCompleted = computed(() => props.task.completed)

const statusIcon = computed(() => {
  if (isCompleted.value) return Check
  return Clock
})

const handleClick = () => {
  emit('click', props.task)
}
</script>

<template>
  <div :class="['ai-task', { 'is-completed': isCompleted }]" @click="handleClick">
    <div class="ai-task__checkbox">
      <el-icon :size="16" :class="['status-icon', { 'is-completed': isCompleted }]">
        <component :is="statusIcon" />
      </el-icon>
    </div>
    <div class="ai-task__content">
      <div class="ai-task__title">{{ task.title }}</div>
      <div v-if="task.description" class="ai-task__description">
        {{ task.description }}
      </div>
      <div v-if="task.files && task.files.length > 0" class="ai-task__files">
        <span v-for="(file, index) in task.files" :key="index" class="file-tag">
          {{ file }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-task {
  display: flex;
  gap: 12px;
  padding: 12px;
  background-color: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  margin: 8px 0;
  cursor: pointer;
  transition: all 0.2s;
}

.ai-task:hover {
  background-color: var(--el-fill-color);
}

.ai-task.is-completed {
  opacity: 0.7;
}

.ai-task__checkbox {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-icon {
  color: var(--el-color-info);
}

.status-icon.is-completed {
  color: var(--el-color-success);
}

.ai-task__content {
  flex: 1;
  min-width: 0;
}

.ai-task__title {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.ai-task.is-completed .ai-task__title {
  text-decoration: line-through;
  color: var(--el-text-color-secondary);
}

.ai-task__description {
  margin-top: 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.ai-task__files {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.file-tag {
  font-size: 11px;
  padding: 2px 8px;
  background-color: var(--el-fill-color);
  border-radius: 4px;
  color: var(--el-text-color-secondary);
}
</style>
