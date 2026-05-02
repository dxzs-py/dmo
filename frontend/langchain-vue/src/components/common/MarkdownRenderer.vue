<script setup>
import { computed, ref, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import 'highlight.js/styles/github-dark.css'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('md', markdown)

const EXTRA_LANG_LOADERS = {
  sql: () => import('highlight.js/lib/languages/sql'),
  java: () => import('highlight.js/lib/languages/java'),
  typescript: () => import('highlight.js/lib/languages/typescript'),
  go: () => import('highlight.js/lib/languages/go'),
  rust: () => import('highlight.js/lib/languages/rust'),
  c: () => import('highlight.js/lib/languages/c'),
  cpp: () => import('highlight.js/lib/languages/cpp'),
}

const loadedExtraLangs = new Set()

async function loadExtraLanguage(lang) {
  if (loadedExtraLangs.has(lang) || hljs.getLanguage(lang)) return
  const loader = EXTRA_LANG_LOADERS[lang]
  if (!loader) return
  try {
    const mod = await loader()
    hljs.registerLanguage(lang, mod.default)
    loadedExtraLangs.add(lang)
  } catch {}
}

marked.use({
  renderer: {
    code({ text, lang }) {
      const language = lang && hljs.getLanguage(lang) ? lang : null
      if (lang && !language && EXTRA_LANG_LOADERS[lang]) {
        loadExtraLanguage(lang)
      }
      const highlighted = language
        ? hljs.highlight(text, { language }).value
        : text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      const langLabel = language ? `<span class="code-lang">${language}</span>` : ''
      const lines = text.split('\n')
      const lineNumbersHtml = lines.map((_, i) => `<span class="line-num${props.highlightLines.includes(i + 1) ? ' is-highlighted' : ''}" data-line="${i + 1}">${i + 1}</span>`).join('')
      const codeLinesHtml = highlighted.split('\n').map((line, i) => `<span class="code-line${props.highlightLines.includes(i + 1) ? ' is-highlighted' : ''}">${line}</span>`).join('\n')
      return `<div class="code-block-wrapper">${langLabel}<button class="code-copy-btn" data-code="${encodeURIComponent(text)}" title="复制代码"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button><div class="code-block-body"><div class="line-numbers">${lineNumbersHtml}</div><pre><code class="hljs ${language ? `language-${language}` : ''}">${codeLinesHtml}</code></pre></div></div>`
    },
    image({ href, title, text }) {
      const alt = text || ''
      const titleAttr = title ? ` title="${title}"` : ''
      return `<span class="md-image-wrapper"><img data-src="${href}" alt="${alt}"${titleAttr} class="md-lazy-image" /><span class="md-image-placeholder"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg></span></span>`
    }
  },
  breaks: true,
  gfm: true
})

const props = defineProps({
  content: {
    type: [String, Object],
    default: ''
  },
  highlightLines: {
    type: Array,
    default: () => []
  },
  citations: {
    type: Array,
    default: () => []
  }
})

const copiedId = ref(null)
const previewVisible = ref(false)
const previewUrl = ref('')
const rendererRef = ref(null)
let lazyObserver = null

const handleImageClick = (e) => {
  const img = e.target.closest('img')
  if (!img) return
  e.preventDefault()
  previewUrl.value = img.src
  previewVisible.value = true
}

const closePreview = () => {
  previewVisible.value = false
  previewUrl.value = ''
}

const setupLazyLoading = () => {
  if (!rendererRef.value || !('IntersectionObserver' in window)) return
  lazyObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        const img = entry.target
        const dataSrc = img.getAttribute('data-src')
        if (dataSrc) {
          img.src = dataSrc
          img.removeAttribute('data-src')
          img.classList.add('is-loaded')
        }
        lazyObserver.unobserve(img)
      }
    })
  }, { rootMargin: '200px' })
  rendererRef.value.querySelectorAll('img[data-src]').forEach((img) => {
    lazyObserver.observe(img)
  })
}

onMounted(() => {
  nextTick(() => setupLazyLoading())
})

onUnmounted(() => {
  if (lazyObserver) {
    lazyObserver.disconnect()
    lazyObserver = null
  }
})

watch(() => props.content, () => {
  nextTick(() => setupLazyLoading())
})

const renderedContent = computed(() => {
  let text = props.content
  if (typeof text === 'object' && text !== null) {
    text = text.content ?? text.text ?? JSON.stringify(text)
  }
  if (!text || typeof text !== 'string') {
    return ''
  }
  let rawHtml = marked(text)
  if (props.citations.length > 0) {
    rawHtml = rawHtml.replace(/\[(\d+)\]/g, (match, num) => {
      const idx = parseInt(num) - 1
      const citation = props.citations[idx]
      if (!citation) return match
      const href = citation.href || citation.url || ''
      const title = citation.title || `来源 ${num}`
      return href
        ? `<sup class="inline-citation"><a href="${href}" target="_blank" title="${title}">[${num}]</a></sup>`
        : `<sup class="inline-citation" title="${title}">[${num}]</sup>`
    })
  }
  return DOMPurify.sanitize(rawHtml, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ['target', 'data-code', 'title', 'data-src']
  })
})

function handleClick(e) {
  const img = e.target.closest('img')
  if (img && img.src) {
    handleImageClick(e)
    return
  }
  const btn = e.target.closest('.code-copy-btn')
  if (!btn) return
  const code = decodeURIComponent(btn.getAttribute('data-code') || '')
  navigator.clipboard.writeText(code).then(() => {
    copiedId.value = btn.closest('.code-block-wrapper')?.querySelector('code')?.textContent
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>'
    setTimeout(() => {
      btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>'
      copiedId.value = null
    }, 2000)
  })
}
</script>

<template>
  <div
    ref="rendererRef"
    class="markdown-renderer"
    v-html="renderedContent"
    @click="handleClick"
  ></div>
  <el-image-viewer
    v-if="previewVisible"
    :url-list="[previewUrl]"
    @close="closePreview"
  />
</template>

<style scoped>
.markdown-renderer {
  font-size: 14px;
  line-height: 1.8;
  color: var(--el-text-color-primary, #303133);
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
  color: var(--el-text-color-primary, #303133);
}

.markdown-renderer :deep(h1) {
  font-size: 24px;
  border-bottom: 1px solid var(--el-border-color-lighter, #e4e7ed);
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

.markdown-renderer :deep(.code-block-wrapper) {
  position: relative;
  margin: 12px 0;
  border-radius: 6px;
  overflow: hidden;
}

.markdown-renderer :deep(.code-block-body) {
  display: flex;
  overflow-x: auto;
}

.markdown-renderer :deep(.line-numbers) {
  display: flex;
  flex-direction: column;
  padding: 16px 0;
  min-width: 40px;
  text-align: right;
  user-select: none;
  border-right: 1px solid var(--el-border-color-extra-light, #eaecef);
  flex-shrink: 0;
}

.markdown-renderer :deep(.line-num) {
  display: block;
  padding: 0 8px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-placeholder, #c0c4cc);
  font-family: 'Fira Code', 'Consolas', monospace;
}

.markdown-renderer :deep(.line-num.is-highlighted) {
  color: var(--el-color-warning);
  font-weight: 700;
}

.markdown-renderer :deep(.code-line.is-highlighted) {
  background-color: rgba(255, 255, 255, 0.08);
  display: block;
  border-left: 3px solid var(--el-color-warning);
  padding-left: 8px;
  margin-left: -11px;
}

.markdown-renderer :deep(.code-lang) {
  position: absolute;
  top: 4px;
  left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  z-index: 1;
  pointer-events: none;
}

.markdown-renderer :deep(.code-copy-btn) {
  position: absolute;
  top: 4px;
  right: 4px;
  background: var(--el-fill-color-light, #f5f7fa);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 4px;
  padding: 4px 6px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s;
  color: var(--el-text-color-regular, #606266);
  z-index: 1;
  display: flex;
  align-items: center;
}

.markdown-renderer :deep(.code-block-wrapper:hover .code-copy-btn) {
  opacity: 1;
}

.markdown-renderer :deep(.code-copy-btn:hover) {
  background: var(--el-fill-color, #e9e9eb);
}

.markdown-renderer :deep(pre) {
  background-color: var(--el-fill-color-lighter, #f6f8fa);
  border-radius: 6px;
  padding: 16px;
  padding-top: 28px;
  overflow-x: auto;
  margin: 0;
}

.markdown-renderer :deep(code) {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 13px;
}

.markdown-renderer :deep(p code),
.markdown-renderer :deep(li code) {
  background-color: var(--el-fill-color-lighter, #f6f8fa);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
}

.markdown-renderer :deep(a) {
  color: var(--el-color-primary);
  text-decoration: none;
}

.markdown-renderer :deep(a:hover) {
  text-decoration: underline;
}

.markdown-renderer :deep(blockquote) {
  border-left: 4px solid var(--el-border-color, #dfe2e5);
  padding-left: 16px;
  margin: 12px 0;
  color: var(--el-text-color-secondary, #6a737d);
}

.markdown-renderer :deep(ul),
.markdown-renderer :deep(ol) {
  padding-left: 24px;
  margin: 12px 0;
}

.markdown-renderer :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}

.markdown-renderer :deep(th),
.markdown-renderer :deep(td) {
  border: 1px solid var(--el-border-color-lighter, #dfe2e5);
  padding: 8px 12px;
  text-align: left;
}

.markdown-renderer :deep(th) {
  background-color: var(--el-fill-color-lighter, #f6f8fa);
  font-weight: 600;
}

.markdown-renderer :deep(img) {
  max-width: 100%;
  border-radius: 6px;
}

.markdown-renderer :deep(.inline-citation) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  margin: 0 2px;
  font-size: 11px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-radius: 4px;
  vertical-align: super;
  line-height: 1;
}

.markdown-renderer :deep(.inline-citation a) {
  color: inherit;
  text-decoration: none;
}

.markdown-renderer :deep(.inline-citation a:hover) {
  text-decoration: underline;
}

.markdown-renderer :deep(.md-image-wrapper) {
  position: relative;
  display: inline-block;
  max-width: 100%;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
}

.markdown-renderer :deep(.md-image-wrapper:hover) {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

.markdown-renderer :deep(.md-lazy-image) {
  max-width: 100%;
  border-radius: 8px;
  opacity: 0;
  transition: opacity 0.3s ease;
  display: block;
}

.markdown-renderer :deep(.md-lazy-image.is-loaded) {
  opacity: 1;
}

.markdown-renderer :deep(.md-lazy-image[src]:not([data-src])) {
  opacity: 1;
}

.markdown-renderer :deep(.md-image-placeholder) {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  min-width: 200px;
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-placeholder);
  border-radius: 8px;
}

.markdown-renderer :deep(.md-image-wrapper:has(.md-lazy-image.is-loaded) .md-image-placeholder),
.markdown-renderer :deep(.md-image-wrapper:has(.md-lazy-image[src]:not([data-src])) .md-image-placeholder) {
  display: none;
}

:root.dark .markdown-renderer {
  color: var(--el-text-color-primary, #e5eaf3);
}

:root.dark .markdown-renderer :deep(pre) {
  background-color: #1b1b1f;
}

:root.dark .markdown-renderer :deep(p code),
:root.dark .markdown-renderer :deep(li code) {
  background-color: #2c2c30;
}

:root.dark .markdown-renderer :deep(.code-copy-btn) {
  background: #2c2c30;
  border-color: #3c3c42;
  color: #c9cdd4;
}

:root.dark .markdown-renderer :deep(.code-copy-btn:hover) {
  background: #3c3c42;
}

:root.dark .markdown-renderer :deep(th) {
  background-color: #2c2c30;
}
</style>
