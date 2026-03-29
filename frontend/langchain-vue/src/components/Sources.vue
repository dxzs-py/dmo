<script setup>
defineOptions({
  name: 'SourceList'
})

import { ref, computed } from 'vue'
import { ElTag, ElLink, ElCollapseTransition } from 'element-plus'
import { Document, Link, ArrowRight } from '@element-plus/icons-vue'

const props = defineProps({
  sources: {
    type: Array,
    default: () => []
  },
  isStreaming: {
    type: Boolean,
    default: false
  }
})

const isExpanded = ref(false)

const sourceCount = computed(() => props.sources.length)

const formatSource = (source, index) => {
  return {
    ...source,
    index: index + 1,
    displayTitle: source.title || `来源 ${index + 1}`,
    displayHref: source.href || source.url || '#'
  }
}

const toggleExpand = () => {
  isExpanded.value = !isExpanded.value
}
</script>

<template>
  <div class="sources" :class="{ 'is-streaming': isStreaming }">
    <div class="sources-header" @click="toggleExpand">
      <div class="sources-info">
        <el-icon><Document /></el-icon>
        <span class="sources-count">
          使用了 {{ sourceCount }} 个来源
        </span>
        <el-tag v-if="isStreaming" type="info" size="small" :loading="isStreaming">
          生成中
        </el-tag>
      </div>
      <el-icon class="expand-icon" :class="{ 'is-expanded': isExpanded }">
        <ArrowRight />
      </el-icon>
    </div>

    <el-collapse-transition>
      <div v-if="isExpanded && sources.length > 0" class="sources-content">
        <div
          v-for="source in sources.map(formatSource)"
          :key="source.index"
          class="source-item"
        >
          <div class="source-header">
            <span class="source-index">{{ source.index }}</span>
            <el-link
              :href="source.displayHref"
              target="_blank"
              class="source-title"
              :underline="false"
            >
              <el-icon><Link /></el-icon>
              {{ source.displayTitle }}
            </el-link>
            <el-tag v-if="source.similarity" type="warning" size="small">
              {{ (source.similarity * 100).toFixed(0) }}%
            </el-tag>
          </div>
          <div v-if="source.content" class="source-content">
            {{ source.content }}
          </div>
          <div v-if="source.snippet" class="source-snippet">
            {{ source.snippet }}
          </div>
        </div>
      </div>
    </el-collapse-transition>

    <div v-if="sources.length === 0 && !isStreaming" class="sources-empty">
      暂无来源信息
    </div>
  </div>
</template>

<style scoped>
.sources {
  background-color: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  margin: 12px 0;
  overflow: hidden;
  transition: all 0.3s ease;
}

.sources.is-streaming {
  border-color: var(--el-color-primary-light-5);
}

.sources-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.sources-header:hover {
  background-color: var(--el-fill-color);
}

.sources-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.sources-count {
  font-weight: 500;
}

.expand-icon {
  transition: transform 0.3s ease;
  color: var(--el-text-color-secondary);
}

.expand-icon.is-expanded {
  transform: rotate(90deg);
}

.sources-content {
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.source-item {
  background-color: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 12px;
  transition: box-shadow 0.2s;
}

.source-item:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.source-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.source-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background-color: var(--el-color-primary-light-8);
  color: var(--el-color-primary);
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
}

.source-title {
  flex: 1;
  font-size: 14px;
  color: var(--el-color-primary) !important;
}

.source-content {
  margin-top: 8px;
  padding: 8px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

.source-snippet {
  margin-top: 8px;
  padding: 8px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  border-left: 3px solid var(--el-color-primary);
}

.sources-empty {
  padding: 12px 16px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
