<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useEnhancedChatStore } from '../stores/enhancedChat';
import { useSessionStore } from '../stores/session';
import { useThemeStore } from '../stores/theme';
import { ElMessage, ElMessageBox, ElDrawer } from 'element-plus';
import {
  Delete,
  Download,
  Moon,
  Sunny,
  Setting,
} from '@element-plus/icons-vue';
import ChatHeader from '../components/chat/ChatHeader.vue';
import ChatEnhanced from '../components/chat/ChatEnhanced.vue';

const enhancedChatStore = useEnhancedChatStore();
const sessionStore = useSessionStore();
const themeStore = useThemeStore();
const selectedModel = ref('deepseek-chat');
const isScrolled = ref(false);
const showSettings = ref(false);

const messages = computed(() => enhancedChatStore.allMessages);
const currentSession = computed(() => sessionStore.currentSession);

const handleScrollChange = (scrolled) => {
  isScrolled.value = scrolled;
};

const chatEnhancedRef = ref(null);

const handleRegenerate = async (index) => {
  try {
    if (chatEnhancedRef.value?.regenerateMessage) {
      await chatEnhancedRef.value.regenerateMessage(index);
    }
    ElMessage.success('正在重新生成回复...');
  } catch (err) {
    console.error('重新生成失败:', err);
    ElMessage.error('重新生成失败，请稍后重试');
  }
};

const handleSuggestionClick = async () => {
};

const handleClearMessages = async () => {
  try {
    await ElMessageBox.confirm('确定要清空所有消息吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    });
    enhancedChatStore.clear();
    ElMessage.success('消息已清空');
  } catch {
    // User cancelled
  }
};

const handleExportChat = () => {
  const chatData = {
    title: currentSession.value?.title || '聊天记录',
    timestamp: new Date().toISOString(),
    messages: messages.value.map(msg => ({
      role: msg.role,
      content: msg.content,
      timestamp: msg.timestamp,
      tools: msg.tools,
      sources: msg.sources,
      plan: msg.plan,
      chainOfThought: msg.chainOfThought,
    })),
  };

  const blob = new Blob([JSON.stringify(chatData, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `chat-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);

  ElMessage.success('聊天记录已导出');
};

const handleImportChat = (event) => {
  const file = event.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const data = JSON.parse(e.target.result);
      if (Array.isArray(data.messages)) {
        data.messages.forEach(msg => {
          enhancedChatStore.addMessage({
            ...msg,
            timestamp: new Date(msg.timestamp),
          });
        });
        ElMessage.success('聊天记录已导入');
      } else {
        ElMessage.error('无效的聊天记录文件');
      }
    } catch {
      console.error('导入失败');
      ElMessage.error('导入失败：无效的JSON格式');
    }
  };
  reader.readAsText(file);
};

const handleKeyDown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
    event.preventDefault();
    handleClearMessages();
  }
};

const handleToggleTheme = () => {
  themeStore.toggleTheme();
};

onMounted(() => {
  window.addEventListener('keydown', handleKeyDown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown);
});
</script>

<template>
  <div class="enhanced-chat-view">
    <ChatHeader
      v-model:selected-model="selectedModel"
      title="增强版智能聊天"
      :current-mode="enhancedChatStore.currentMode || 'basic-agent'"
      :available-modes="enhancedChatStore.availableModes || {}"
      @update:current-mode="(val) => enhancedChatStore.currentMode = val"
    />

    <div class="chat-main">
      <ChatEnhanced
        ref="chatEnhancedRef"
        :mode="enhancedChatStore.currentMode || 'basic-agent'"
        :use-tools="true"
        @scroll-change="handleScrollChange"
        @regenerate="handleRegenerate"
        @suggestion-click="handleSuggestionClick"
      />
    </div>

    <div class="chat-actions">
      <el-tooltip content="清空消息 (Ctrl+K)" placement="left">
        <el-button
          circle
          type="info"
          size="small"
          :disabled="messages.length === 0"
          @click="handleClearMessages"
        >
          <el-icon><Delete /></el-icon>
        </el-button>
      </el-tooltip>
      
      <el-tooltip content="导出聊天记录" placement="left">
        <el-button
          circle
          type="info"
          size="small"
          @click="handleExportChat"
        >
          <el-icon><Download /></el-icon>
        </el-button>
      </el-tooltip>
      
      <input
        id="import-export"
        type="file"
        accept=".json"
        style="display: none"
        @change="handleImportChat"
      />
      
      <el-tooltip content="切换主题" placement="left">
        <el-button
          circle
          type="info"
          size="small"
          @click="handleToggleTheme"
        >
          <el-icon v-if="themeStore.isDark"><Sunny /></el-icon>
          <el-icon v-else><Moon /></el-icon>
        </el-button>
      </el-tooltip>
      
      <el-tooltip content="设置" placement="left">
        <el-button
          circle
          type="info"
          size="small"
          @click="showSettings = true"
        >
          <el-icon><Setting /></el-icon>
        </el-button>
      </el-tooltip>
    </div>

    <el-drawer
      v-model="showSettings"
      title="设置"
      direction="rtl"
      size="400"
    >
      <div class="settings-content">
        <el-form label-width="100px">
          <el-form-item label="当前模式">
            <el-tag>{{ enhancedChatStore.currentMode || 'basic-agent' }}</el-tag>
          </el-form-item>
          
          <el-form-item label="消息数量">
            <el-tag type="success">{{ messages.length }}</el-tag>
          </el-form-item>
          
          <el-form-item label="流式输出">
            <el-switch v-model="enhancedChatStore.isStreaming" disabled />
          </el-form-item>
          
          <el-divider />
          
          <el-form-item label="快捷键">
            <el-descriptions :column="1" size="small">
              <el-descriptions-item label="清空消息">Ctrl+K</el-descriptions-item>
            </el-descriptions>
          </el-form-item>
        </el-form>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.enhanced-chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color-page);
  position: relative;
}

.chat-main {
  flex: 1;
  overflow: hidden;
  position: relative;
}

.chat-actions {
  position: absolute;
  right: 24px;
  bottom: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  z-index: 10;
}

.chat-actions .el-button {
  background-color: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;
}

.chat-actions .el-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.chat-actions .el-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.settings-content {
  padding: 24px;
}
</style>
