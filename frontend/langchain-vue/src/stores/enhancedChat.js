import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { nanoid } from 'nanoid';

export const useEnhancedChatStore = defineStore('enhancedChat', () => {
  const messages = ref(new Map());
  const isStreaming = ref(false);
  const error = ref(null);
  const currentStreamingMessageId = ref(null);
  const abortController = ref(null);
  const modelSuggestions = ref([]);

  const allMessages = computed(() => {
    return Array.from(messages.value.values()).sort(
      (a, b) => a.timestamp.getTime() - b.timestamp.getTime()
    );
  });

  const lastAssistantMessage = computed(() => {
    const msgs = allMessages.value;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') {
        return msgs[i];
      }
    }
    return undefined;
  });

  function subscribe(callback) {
  }

  function notify() {
  }

  function getAllMessages() {
    return allMessages.value;
  }

  function getMessage(id) {
    return messages.value.get(id);
  }

  function addMessage(message) {
    messages.value.set(message.id, message);
  }

  function addMessages(msgs) {
    msgs.forEach(msg => messages.value.set(msg.id, msg));
  }

  function updateMessage(id, updates) {
    const message = messages.value.get(id);
    if (message) {
      Object.assign(message, updates);
    }
  }

  function appendContent(id, content) {
    const message = messages.value.get(id);
    if (message) {
      message.content += content;
    }
  }

  function setContent(id, content) {
    const message = messages.value.get(id);
    if (message) {
      message.content = content;
    }
  }

  function addToolCall(id, tool) {
    const message = messages.value.get(id);
    if (message) {
      if (!message.tools) {
        message.tools = [];
      }
      message.tools.push(tool);
    }
  }

  function updateToolResult(messageId, toolId, updates) {
    const message = messages.value.get(messageId);
    if (message?.tools) {
      const tool = message.tools.find(t => t.id === toolId);
      if (tool) {
        Object.assign(tool, updates);
      }
    }
  }

  function addSource(id, source) {
    const message = messages.value.get(id);
    if (message) {
      if (!message.sources) {
        message.sources = [];
      }
      message.sources.push(source);
    }
  }

  function addSources(id, sources) {
    const message = messages.value.get(id);
    if (message) {
      if (!message.sources) {
        message.sources = [];
      }
      message.sources.push(...sources);
    }
  }

  function setReasoning(id, reasoning) {
    const message = messages.value.get(id);
    if (message) {
      message.reasoning = reasoning;
    }
  }

  function setPlan(id, plan) {
    const message = messages.value.get(id);
    if (message) {
      message.plan = plan;
    }
  }

  function addTask(id, task) {
    const message = messages.value.get(id);
    if (message) {
      if (!message.tasks) {
        message.tasks = [];
      }
      message.tasks.push(task);
    }
  }

  function setQueue(id, queue) {
    const message = messages.value.get(id);
    if (message) {
      message.queue = queue;
    }
  }

  function setContextUsage(id, contextUsage) {
    const message = messages.value.get(id);
    if (message) {
      message.contextUsage = contextUsage;
    }
  }

  function addCitation(id, citation) {
    const message = messages.value.get(id);
    if (message) {
      if (!message.citations) {
        message.citations = [];
      }
      message.citations.push(citation);
    }
  }

  function setMetadata(id, updates) {
    const message = messages.value.get(id);
    if (message) {
      if (!message.metadata) {
        message.metadata = {};
      }
      Object.entries(updates).forEach(([key, value]) => {
        if (value === undefined) {
          delete message.metadata[key];
        } else {
          message.metadata[key] = value;
        }
      });
    }
  }

  function setChainOfThought(id, chainOfThought) {
    const message = messages.value.get(id);
    if (message) {
      message.chainOfThought = chainOfThought;
    }
  }

  function deleteMessage(id) {
    messages.value.delete(id);
  }

  function clear() {
    messages.value.clear();
  }

  function handleStreamChunk(messageId, chunk) {
    switch (chunk.type) {
      case 'start':
        break;
      case 'chunk':
        {
          const existingMessage = getMessage(messageId);
          const pendingToolResult = existingMessage?.metadata?.lastToolResult;
          if (pendingToolResult && pendingToolResult === chunk.content) {
            setContent(messageId, chunk.content);
            setMetadata(messageId, { lastToolResult: undefined });
          } else {
            appendContent(messageId, chunk.content);
          }
        }
        break;
      case 'tool':
        addToolCall(messageId, chunk.data);
        break;
      case 'tool_result':
        updateToolResult(messageId, chunk.data.id, chunk.data);
        if (chunk.data.result) {
          const targetMessage = getMessage(messageId);
          if (targetMessage) {
            if (!targetMessage.content?.trim()) {
              setContent(messageId, chunk.data.result);
            } else {
              appendContent(messageId, chunk.data.result);
            }
            setMetadata(messageId, { lastToolResult: chunk.data.result });
          }
        }
        break;
      case 'reasoning':
        setReasoning(messageId, chunk.data);
        break;
      case 'source':
        addSource(messageId, chunk.data);
        break;
      case 'sources':
        addSources(messageId, chunk.data);
        break;
      case 'plan':
        setPlan(messageId, chunk.data);
        break;
      case 'task':
        addTask(messageId, chunk.data);
        break;
      case 'queue':
        setQueue(messageId, chunk.data);
        break;
      case 'context':
        setContextUsage(messageId, chunk.data);
        break;
      case 'citation':
        addCitation(messageId, chunk.data);
        break;
      case 'chainOfThought':
        setChainOfThought(messageId, chunk.data);
        break;
      case 'suggestions':
        modelSuggestions.value = Array.isArray(chunk.data) ? chunk.data : [];
        setMetadata(messageId, { suggestions: modelSuggestions.value });
        break;
      case 'end':
        break;
      case 'error':
        error.value = new Error(chunk.message);
        break;
    }
  }

  function createUserMessage(text) {
    return {
      id: nanoid(),
      role: 'user',
      content: text.trim(),
      timestamp: new Date(),
    };
  }

  function createAssistantMessage() {
    return {
      id: nanoid(),
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    };
  }

  function setStreaming(value) {
    isStreaming.value = value;
  }

  function setCurrentStreamingMessageId(id) {
    currentStreamingMessageId.value = id;
  }

  function setAbortController(controller) {
    abortController.value = controller;
  }

  function stopStreaming() {
    if (abortController.value) {
      abortController.value.abort();
      abortController.value = null;
    }
    isStreaming.value = false;
    currentStreamingMessageId.value = null;
  }

  function setError(err) {
    error.value = err;
  }

  function getChatHistory() {
    return allMessages.value.map(msg => ({
      role: msg.role,
      content: msg.content,
    }));
  }

  return {
    messages,
    isStreaming,
    error,
    currentStreamingMessageId,
    abortController,
    modelSuggestions,
    allMessages,
    lastAssistantMessage,
    getAllMessages,
    getMessage,
    addMessage,
    addMessages,
    updateMessage,
    appendContent,
    setContent,
    addToolCall,
    updateToolResult,
    addSource,
    addSources,
    setReasoning,
    setPlan,
    addTask,
    setQueue,
    setContextUsage,
    addCitation,
    setMetadata,
    setChainOfThought,
    deleteMessage,
    clear,
    handleStreamChunk,
    createUserMessage,
    createAssistantMessage,
    setStreaming,
    setCurrentStreamingMessageId,
    setAbortController,
    stopStreaming,
    setError,
    getChatHistory,
  };
});
