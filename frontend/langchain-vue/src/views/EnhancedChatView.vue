<script setup>
import { ref, onMounted } from 'vue';
import { useChatStore } from '../stores/chat';
import { useSessionStore } from '../stores/session';
import { ElMessage } from 'element-plus';
import ChatHeader from '../components/chat/ChatHeader.vue';
import ChatEnhanced from '../components/chat/ChatEnhanced.vue';

const chatStore = useChatStore();
const sessionStore = useSessionStore();
const selectedModel = ref('deepseek-chat');
const useWebSearch = ref(false);
const isScrolled = ref(false);

const handleScrollChange = (scrolled) => {
  isScrolled.value = scrolled;
};

const handleRegenerate = async (index) => {
  ElMessage.success('正在重新生成回复...');
};

const handleSuggestionClick = async (suggestion) => {
  ElMessage.info(`点击建议: ${suggestion}`);
};

onMounted(() => {
  chatStore.fetchModes();
});
</script>

<template>
  <div class="enhanced-chat-view">
    <ChatHeader
      title="增强版智能聊天"
      v-model:selected-model="selectedModel"
      :current-mode="chatStore.currentMode"
      :available-modes="chatStore.availableModes"
      @update:current-mode="(val) => chatStore.currentMode = val"
    />

    <ChatEnhanced
      :mode="chatStore.currentMode"
      :use-tools="true"
      @scroll-change="handleScrollChange"
    />
  </div>
</template>

<style scoped>
.enhanced-chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color-page);
}
</style>
