<script setup>
import { ref, computed } from 'vue'
import { DocumentCopy, Check } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  code: {
    type: String,
    required: true
  },
  language: {
    type: String,
    default: 'javascript'
  },
  filename: {
    type: String,
    default: ''
  },
  showLineNumbers: {
    type: Boolean,
    default: true
  },
  highlightLines: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['copy'])

const isCopied = ref(false)

const codeLines = computed(() => {
  return props.code.split('\n')
})

const handleCopy = async () => {
  try {
    await navigator.clipboard.writeText(props.code)
    isCopied.value = true
    ElMessage.success('代码已复制')
    emit('copy', props.code)
    setTimeout(() => {
      isCopied.value = false
    }, 2000)
  } catch {
    ElMessage.error('复制失败')
  }
}

const isHighlighted = (lineIndex) => {
  return props.highlightLines.includes(lineIndex + 1)
}
</script>

<template>
  <div class="ai-code-block">
    <div class="ai-code-block__header">
      <div class="ai-code-block__info">
        <span v-if="filename" class="ai-code-block__filename">
          {{ filename }}
        </span>
        <span v-else class="ai-code-block__language">
          {{ language }}
        </span>
      </div>
      <button class="ai-code-block__copy" @click="handleCopy">
        <el-icon :size="16">
          <Check v-if="isCopied" />
          <DocumentCopy v-else />
        </el-icon>
        <span>{{ isCopied ? '已复制' : '复制' }}</span>
      </button>
    </div>

    <div class="ai-code-block__content">
      <pre class="ai-code-block__pre"><code :class="`language-${language}`"><span v-for="(line, index) in codeLines" :key="index" :class="['code-line', { 'is-highlighted': isHighlighted(index) }]"><span v-if="showLineNumbers" class="line-number">{{ index + 1 }}</span>{{ line }}</span></code></pre>
    </div>
  </div>
</template>

<style scoped>
.ai-code-block {
  background-color: #1e1e1e;
  border-radius: 8px;
  overflow: hidden;
  margin: 12px 0;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
}

.ai-code-block__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background-color: #2d2d2d;
  border-bottom: 1px solid #3d3d3d;
}

.ai-code-block__info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ai-code-block__filename,
.ai-code-block__language {
  font-size: 12px;
  color: #9cdcfe;
}

.ai-code-block__copy {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: transparent;
  border: 1px solid #4a4a4a;
  border-radius: 4px;
  color: #cccccc;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s;
}

.ai-code-block__copy:hover {
  background-color: #3d3d3d;
  border-color: #5a5a5a;
}

.ai-code-block__content {
  overflow-x: auto;
}

.ai-code-block__pre {
  margin: 0;
  padding: 16px;
}

.code-line {
  display: block;
  line-height: 1.5;
  font-size: 14px;
  color: #d4d4d4;
}

.code-line.is-highlighted {
  background-color: rgba(255, 255, 255, 0.1);
}

.line-number {
  display: inline-block;
  width: 32px;
  padding-right: 16px;
  color: #6e7681;
  text-align: right;
  user-select: none;
}

.language-javascript { color: #d4d4d4; }
.language-python { color: #d4d4d4; }
.language-typescript { color: #d4d4d4; }
.language-html { color: #d4d4d4; }
.language-css { color: #d4d4d4; }
</style>
