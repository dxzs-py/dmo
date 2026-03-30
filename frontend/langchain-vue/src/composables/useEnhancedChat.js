import { ref, computed } from 'vue';
import { useEnhancedChatStore } from '../stores/enhancedChat';
import { chatStreamEnhanced } from '../api/enhanced-client';
import { ElMessage } from 'element-plus';

export function useEnhancedChat(options = {}) {
  const { mode = 'default', useTools = true, onError, onStreamStart, onStreamEnd } = options;
  
  const chatStore = useEnhancedChatStore();
  const text = ref('');

  const messages = computed(() => chatStore.allMessages);
  const isStreaming = computed(() => chatStore.isStreaming);
  const error = computed(() => chatStore.error);
  const modelSuggestions = computed(() => chatStore.modelSuggestions);

  const sendMessage = async (messageText) => {
    if (!messageText.trim() || isStreaming.value) {
      return;
    }

    try {
      chatStore.setError(null);
      chatStore.setStreaming(true);

      const userMessage = chatStore.createUserMessage(messageText);
      chatStore.addMessage(userMessage);

      const assistantMessage = chatStore.createAssistantMessage();
      chatStore.addMessage(assistantMessage);
      chatStore.setCurrentStreamingMessageId(assistantMessage.id);

      if (onStreamStart) {
        onStreamStart();
      }

      const chatHistory = chatStore.getChatHistory().filter(
        msg => msg.role !== 'assistant' || msg.content !== ''
      );

      const request = {
        message: messageText.trim(),
        chat_history: chatHistory,
        mode,
        use_tools: useTools,
      };

      const abortController = new AbortController();
      chatStore.setAbortController(abortController);

      for await (const chunk of chatStreamEnhanced(request)) {
        if (abortController.signal.aborted) {
          break;
        }
        chatStore.handleStreamChunk(assistantMessage.id, chunk);
      }

      if (onStreamEnd) {
        onStreamEnd();
      }

    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));
      console.error('Failed to send message:', error);
      chatStore.setError(error);
      
      if (onError) {
        onError(error);
      }

      if (chatStore.currentStreamingMessageId) {
        chatStore.updateMessage(chatStore.currentStreamingMessageId, {
          content: '抱歉，处理您的请求时出现错误。',
          metadata: { error: error.message },
        });
      }

      ElMessage.error('发送消息失败，请稍后重试');
    } finally {
      chatStore.setStreaming(false);
      chatStore.setCurrentStreamingMessageId(null);
      chatStore.setAbortController(null);
    }
  };

  const stopStreaming = () => {
    chatStore.stopStreaming();
    ElMessage.info('已停止生成');
  };

  const clearMessages = () => {
    chatStore.clear();
    text.value = '';
  };

  const regenerateLastResponse = async () => {
    const lastAssistantMsg = chatStore.lastAssistantMessage;
    
    if (!lastAssistantMsg) {
      return;
    }

    const allMessages = chatStore.getAllMessages();
    const assistantIndex = allMessages.findIndex(m => m.id === lastAssistantMsg.id);
    
    if (assistantIndex <= 0) {
      return;
    }

    let userMessage;
    for (let i = assistantIndex - 1; i >= 0; i--) {
      if (allMessages[i].role === 'user') {
        userMessage = allMessages[i];
        break;
      }
    }

    if (!userMessage) {
      return;
    }

    chatStore.deleteMessage(lastAssistantMsg.id);
    await sendMessage(userMessage.content);
  };

  const regenerateMessage = async (index) => {
    const msgs = chatStore.getAllMessages();
    if (index < 0 || index >= msgs.length) {
      return;
    }

    const targetMessage = msgs[index];
    if (targetMessage.role !== 'assistant') {
      return;
    }

    let userMessage;
    for (let i = index - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') {
        userMessage = msgs[i];
        break;
      }
    }

    if (!userMessage) {
      return;
    }

    for (let i = msgs.length - 1; i >= index; i--) {
      chatStore.deleteMessage(msgs[i].id);
    }

    await sendMessage(userMessage.content);
  };

  return {
    text,
    messages,
    isStreaming,
    error,
    modelSuggestions,
    sendMessage,
    stopStreaming,
    clearMessages,
    regenerateLastResponse,
    regenerateMessage,
  };
}
