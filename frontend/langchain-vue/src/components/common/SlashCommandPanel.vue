<template>
  <div class="slash-command-panel">
    <div v-if="loading" class="command-loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>加载命令...</span>
    </div>
    <div v-else-if="commands.length > 0" class="command-list">
      <div
        v-for="(cmd, idx) in filteredCommands"
        :key="cmd.name"
        class="command-item"
        :class="{ active: idx === selectedIndex }"
        v-memo="[cmd.name, cmd.description, cmd.category, idx === selectedIndex]"
        @click="selectCommand(cmd)"
      >
        <div class="command-name">
          <span class="command-slash">/</span>{{ cmd.name }}
        </div>
        <div class="command-desc">{{ cmd.description }}</div>
        <el-tag size="small" type="info" class="command-category">
          {{ categoryLabels[cmd.category] || cmd.category }}
        </el-tag>
      </div>
    </div>
    <div v-else class="command-empty">
      没有匹配的命令
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { chatAPI } from '@/api'
import { Loading } from '@element-plus/icons-vue'
import { logger } from '../../utils/logger'

const props = defineProps({
  filter: {
    type: String,
    default: '',
  },
  selectedIndex: {
    type: Number,
    default: 0,
  },
})

const emit = defineEmits(['select', 'commands-loaded'])

const commands = ref([])
const loading = ref(false)

const categoryLabels = {
  session: '会话',
  tools: '工具',
  info: '信息',
  settings: '设置',
  ai: 'AI',
}

const filteredCommands = computed(() => {
  if (!props.filter) return commands.value
  const filter = props.filter.toLowerCase()
  return commands.value.filter(
    (cmd) =>
      cmd.name.toLowerCase().includes(filter) ||
      cmd.description.toLowerCase().includes(filter)
  )
})

function selectCommand(cmd) {
  emit('select', cmd)
}

async function loadCommands() {
  loading.value = true
  try {
    const res = await chatAPI.getCommands()
    if (res.data?.code === 200) {
      commands.value = res.data.data.commands || []
      emit('commands-loaded', commands.value)
    }
  } catch (e) {
    logger.error('加载命令失败:', e)
    commands.value = getDefaultCommands()
    emit('commands-loaded', commands.value)
  } finally {
    loading.value = false
  }
}

function getDefaultCommands() {
  return [
    { name: 'help', description: '显示可用命令列表', category: 'info', usage: '/help' },
    { name: 'status', description: '查看当前会话状态', category: 'session', usage: '/status' },
    { name: 'compact', description: '压缩当前会话历史', category: 'session', usage: '/compact' },
    { name: 'model', description: '查看或切换AI模型', category: 'ai', usage: '/model [模型名]' },
    { name: 'cost', description: '查看Token使用量和成本', category: 'info', usage: '/cost' },
    { name: 'permissions', description: '查看或设置工具权限', category: 'settings', usage: '/permissions [模式]' },
    { name: 'clear', description: '清除当前会话', category: 'session', usage: '/clear' },
    { name: 'export', description: '导出当前对话', category: 'session', usage: '/export' },
    { name: 'version', description: '查看系统版本信息', category: 'info', usage: '/version' },
  ]
}

watch(
  () => filteredCommands.value,
  () => {
    emit('commands-loaded', filteredCommands.value)
  }
)

onMounted(() => {
  loadCommands()
})
</script>

<style scoped>
.slash-command-panel {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  max-height: 300px;
  overflow-y: auto;
}

.command-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: var(--el-text-color-secondary);
}

.command-list {
  padding: 4px;
}

.command-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.15s;
}

.command-item:hover,
.command-item.active {
  background: var(--el-fill-color-light);
}

.command-name {
  font-weight: 600;
  font-size: 13px;
  min-width: 120px;
}

.command-slash {
  color: var(--el-color-primary);
  font-weight: 700;
}

.command-desc {
  flex: 1;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.command-category {
  font-size: 11px;
}

.command-empty {
  padding: 16px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
