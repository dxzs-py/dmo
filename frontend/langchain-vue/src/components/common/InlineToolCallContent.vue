<script setup>
/**
 * 单图层正文内联工具内容（Agent 图层嵌套规范 Task 6.1）
 *
 * 将单图层正文按工具 position 切段，content 段渲染 MarkdownRenderer，
 * tool 段通过插槽（scope: { toolCall, approvalDisabled }）由调用方注入
 * 工具卡片。单图层内 position 切段，无图层概念。
 */
import { computed } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { splitContentByToolPositions } from '../../utils/inlineContent'

const props = defineProps({
  /** 该图层正文（主层 message.content / 子层 subagentContents[subagentThreadId].content） */
  content: {
    type: String,
    default: '',
  },
  /** 该图层工具（平铺，任意顺序，内部按 position/seq 排序切段） */
  toolCalls: {
    type: Array,
    default: () => [],
  },
  /** 正文引用来源（传给 MarkdownRenderer citations） */
  citations: {
    type: Array,
    default: () => [],
  },
  /** 审批控件是否置灰（活跃版本 + 未固化 + 末尾轮次之外置灰） */
  approvalDisabled: {
    type: Boolean,
    default: false,
  },
})

const segments = computed(() =>
  splitContentByToolPositions(props.content, props.toolCalls)
)
</script>

<template>
  <div class="inline-tool-call-content">
    <template v-for="(seg, idx) in segments" :key="idx">
      <MarkdownRenderer
        v-if="seg.type === 'content'"
        :content="seg.text"
        :citations="citations"
      />
      <div v-else class="inline-tool-segment">
        <!-- 插槽 props 为 JS 标识符（camelCase），不参与 DOM attribute 大小写规则 -->
        <!-- eslint-disable vue/attribute-hyphenation -->
        <slot
          name="tool"
          :toolCall="seg.toolCall"
          :approvalDisabled="approvalDisabled"
        />
        <!-- eslint-enable vue/attribute-hyphenation -->
      </div>
    </template>
  </div>
</template>

<style scoped>
.inline-tool-call-content {
  width: 100%;
}

.inline-tool-segment {
  width: 100%;
  margin: 8px 0;
}
</style>
