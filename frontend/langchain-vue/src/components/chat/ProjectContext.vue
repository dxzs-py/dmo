<template>
  <div class="project-context">
    <div v-if="loading" class="context-loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>检测项目上下文...</span>
    </div>
    <div v-else-if="context.projectName" class="context-info">
      <div class="context-header">
        <h4>📁 {{ context.projectName }}</h4>
        <el-tag v-if="context.hasGit" size="small" type="success">
          Git: {{ context.gitBranch }}
        </el-tag>
      </div>

      <div class="context-tags">
        <el-tag
          v-for="lang in context.languages"
          :key="lang"
          size="small"
          type="info"
          class="context-tag"
        >
          {{ lang }}
        </el-tag>
        <el-tag
          v-for="fw in context.frameworks"
          :key="fw"
          size="small"
          type="warning"
          class="context-tag"
        >
          {{ fw }}
        </el-tag>
      </div>

      <div v-if="context.keyFiles?.length" class="context-files">
        <span class="context-label">关键文件:</span>
        <span class="context-value">{{ context.keyFiles.join(', ') }}</span>
      </div>

      <div v-if="context.instructionContent" class="context-instructions">
        <el-collapse>
          <el-collapse-item title="项目指令">
            <pre class="instructions-content">{{ context.instructionContent }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>
    <div v-else class="context-empty">
      未检测到项目上下文
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { chatAPI } from '../../api/chat'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps({
  projectPath: {
    type: String,
    default: null,
  },
})

const context = ref({})
const loading = ref(false)

async function detectContext() {
  loading.value = true
  try {
    const res = await chatAPI.getProjectContext(props.projectPath)
    if (res.data?.code === 200) {
      context.value = res.data.data || {}
    }
  } catch (e) {
    console.error('检测项目上下文失败:', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  detectContext()
})
</script>

<style scoped>
.project-context {
  padding: 12px;
}

.context-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.context-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.context-header h4 {
  margin: 0;
  font-size: 14px;
}

.context-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}

.context-tag {
  font-size: 11px;
}

.context-files {
  font-size: 12px;
  margin-bottom: 8px;
}

.context-label {
  color: var(--el-text-color-secondary);
  margin-right: 4px;
}

.context-value {
  color: var(--el-text-color-primary);
}

.instructions-content {
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  margin: 0;
  padding: 8px;
  background: var(--el-bg-color-page);
  border-radius: 4px;
}

.context-empty {
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  padding: 16px;
}
</style>
