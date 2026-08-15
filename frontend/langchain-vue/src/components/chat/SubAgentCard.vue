<script setup>
/**
 * SubAgentCard —— 子代理摘要卡片
 *
 * 主会话流只渲染此摘要卡片：名称 + 中文状态 + 结果预览（完成时）。
 * 点击卡片原地展开 SubAgentDetailPanel；本卡片不渲染内部思考/工具/审批控件。
 */
import { computed } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import {
  getSubagentStatusTagType,
  getSubagentStatusText,
  isPendingUserInput,
} from '../../utils/subagentStatus'

const props = defineProps({
  /** 子代理实例数据（SubAgentInstance 前端 camelCase 视图） */
  subagent: {
    type: Object,
    required: true,
  },
  /** 是否已展开（由父组件控制） */
  expanded: {
    type: Boolean,
    default: false,
  },
})

defineOptions({ name: 'SubAgentCard' })

const emit = defineEmits(['toggle'])

const name = computed(() => props.subagent.agentName || props.subagent.name || '子代理')
const statusText = computed(() => getSubagentStatusText(props.subagent.status))
const statusTagType = computed(() => getSubagentStatusTagType(props.subagent.status))
const resultPreview = computed(() => props.subagent.resultPreview || '')
const needsConfirm = computed(() => isPendingUserInput(props.subagent.status))

/** 待审批项数量（pending_interrupt_info.requests 批量结构，spec D12） */
const pendingCount = computed(() => {
  const info = props.subagent.pendingInterruptInfo
  const requests = info && typeof info === 'object' ? info.requests : null
  return Array.isArray(requests) ? requests.length : 0
})

// spec D8.4：等待你的确认时摘要卡片提示「等待你的确认，共 N 项待审批」
const hint = computed(() => {
  if (needsConfirm.value) {
    return pendingCount.value > 0 ? `共 ${pendingCount.value} 项待审批` : '点击卡片进入处理确认操作'
  }
  if (props.expanded) return '点击收起'
  return '点击展开详情'
})

const handleClick = () => emit('toggle')
</script>

<template>
  <div
    class="subagent-card"
    :class="{ 'subagent-card--confirm': needsConfirm, 'subagent-card--expanded': expanded }"
    @click="handleClick"
  >
    <div class="subagent-card__header">
      <el-icon class="subagent-card__arrow" :class="{ 'is-expanded': expanded }">
        <ArrowRight />
      </el-icon>
      <span class="subagent-card__name">{{ name }}</span>
      <el-tag size="small" :type="statusTagType" effect="plain">{{ statusText }}</el-tag>
      <span class="subagent-card__hint">{{ hint }}</span>
    </div>

    <div v-if="resultPreview" class="subagent-card__preview">{{ resultPreview }}</div>
  </div>
</template>

<style scoped>
.subagent-card {
  width: 100%;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background-color: var(--el-bg-color-page);
  margin: 8px 0;
  cursor: pointer;
  overflow: hidden;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.subagent-card:hover {
  border-color: var(--el-color-primary-light-5);
}

.subagent-card--confirm {
  border-color: var(--el-color-warning-light-5);
}

.subagent-card--expanded {
  box-shadow: var(--el-box-shadow-light);
}

.subagent-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}

.subagent-card__arrow {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  transition: transform 0.2s;
}

.subagent-card__arrow.is-expanded {
  transform: rotate(90deg);
}

.subagent-card__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.subagent-card__hint {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.subagent-card__preview {
  padding: 0 12px 8px 28px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
