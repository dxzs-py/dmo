<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const props = defineProps({
  content: {
    type: String,
    required: true,
  },
})

marked.setOptions({
  highlight: function(code, lang) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext'
    return hljs.highlight(code, { language }).value
  },
  breaks: true,
  gfm: true,
})

const renderedContent = computed(() => {
  return marked(props.content)
})
</script>

<template>
  <div class="markdown-renderer" v-html="renderedContent"></div>
</template>

<style scoped>
.markdown-renderer {
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
}

.markdown-renderer :deep(h1),
.markdown-renderer :deep(h2),
.markdown-renderer :deep(h3),
.markdown-renderer :deep(h4),
.markdown-renderer :deep(h5),
.markdown-renderer :deep(h6) {
  margin-top: 20px;
  margin-bottom: 10px;
  font-weight: 600;
  line-height: 1.4;
  color: #303133;
}

.markdown-renderer :deep(h1) {
  font-size: 24px;
  border-bottom: 1px solid #e4e7ed;
  padding-bottom: 10px;
}

.markdown-renderer :deep(h2) {
  font-size: 20px;
}

.markdown-renderer :deep(h3) {
  font-size: 18px;
}

.markdown-renderer :deep(p) {
  margin: 10px 0;
}

.markdown-renderer :deep(ul),
.markdown-renderer :deep(ol) {
  margin: 10px 0;
  padding-left: 24px;
}

.markdown-renderer :deep(li) {
  margin: 4px 0;
}

.markdown-renderer :deep(code) {
  background-color: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  color: #c7254e;
}

.markdown-renderer :deep(pre) {
  background-color: #f5f7fa;
  border-radius: 8px;
  padding: 16px;
  margin: 14px 0;
  overflow-x: auto;
}

.markdown-renderer :deep(pre code) {
  background-color: transparent;
  padding: 0;
  color: inherit;
}

.markdown-renderer :deep(blockquote) {
  border-left: 4px solid #409eff;
  padding-left: 16px;
  margin: 14px 0;
  color: #606266;
  background-color: #ecf5ff;
  padding: 12px 16px;
  border-radius: 0 8px 8px 0;
}

.markdown-renderer :deep(a) {
  color: #409eff;
  text-decoration: none;
}

.markdown-renderer :deep(a:hover) {
  text-decoration: underline;
}

.markdown-renderer :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 14px 0;
}

.markdown-renderer :deep(th),
.markdown-renderer :deep(td) {
  border: 1px solid #e4e7ed;
  padding: 8px 12px;
  text-align: left;
}

.markdown-renderer :deep(th) {
  background-color: #f5f7fa;
  font-weight: 600;
}

.markdown-renderer :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
}

.markdown-renderer :deep(hr) {
  border: none;
  border-top: 1px solid #e4e7ed;
  margin: 20px 0;
}
</style>
