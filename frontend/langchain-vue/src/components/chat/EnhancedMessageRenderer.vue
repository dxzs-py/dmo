<script setup>
import { computed } from 'vue';
import MarkdownRenderer from '../MarkdownRenderer.vue';
import ChainOfThought from '../ChainOfThought.vue';
import Plan from '../Plan.vue';
import AiReasoning from '../ai-elements/AiReasoning.vue';
import AiTool from '../ai-elements/AiTool.vue';
import Sources from '../Sources.vue';
import AiQueue from '../ai-elements/AiQueue.vue';
import AiTask from '../ai-elements/AiTask.vue';
import AiContext from '../ai-elements/AiContext.vue';
import AiCheckpoint from '../ai-elements/AiCheckpoint.vue';
import AiSuggestions from '../ai-elements/AiSuggestions.vue';

const props = defineProps({
  message: {
    type: Object,
    required: true
  },
  isStreaming: {
    type: Boolean,
    default: false
  }
});

const hasContent = computed(() => {
  return props.message.content?.trim() || 
    props.message.tools?.length || 
    props.message.chainOfThought || 
    props.message.plan || 
    props.message.reasoning || 
    props.message.sources?.length || 
    props.message.contextUsage;
});

const displayContent = computed(() => {
  const trimmed = props.message.content?.trim();
  if (trimmed) {
    return props.message.content;
  }

  const toolResult = props.message.tools?.find(tool => tool.result && tool.result.trim());
  if (toolResult?.result) {
    return toolResult.result;
  }

  return '';
});
</script>

<template>
  <div class="enhanced-message-renderer">
    <template v-if="message.role === 'user'">
      <div class="user-message">
        <div class="message-content">
          <MarkdownRenderer :content="message.content" />
        </div>
      </div>
    </template>

    <template v-else>
      <div class="assistant-message">
        <div v-if="message.chainOfThought" class="message-section">
          <ChainOfThought :steps="message.chainOfThought.steps" />
        </div>

        <div v-if="message.plan" class="message-section">
          <Plan 
            :title="message.plan.title"
            :description="message.plan.description"
            :steps="message.plan.steps"
            :is-streaming="isStreaming && message.plan.isStreaming"
          />
        </div>

        <div v-if="message.reasoning" class="message-section">
          <AiReasoning :content="message.reasoning.content" />
        </div>

        <div v-if="message.queue && message.queue.length > 0" class="message-section">
          <AiQueue :items="message.queue" />
        </div>

        <div v-if="message.tasks && message.tasks.length > 0" class="message-section">
          <div v-for="task in message.tasks" :key="task.id">
            <AiTask :task="task" />
          </div>
        </div>

        <div v-if="message.tools && message.tools.length > 0" class="message-section">
          <div v-for="tool in message.tools" :key="tool.id">
            <AiTool 
              :name="tool.name"
              :description="tool.description"
              :parameters="tool.parameters"
              :result="tool.result"
              :error="tool.error"
              :state="tool.state"
            />
          </div>
        </div>

        <div v-if="hasContent" class="message-section">
          <div class="message-content">
            <MarkdownRenderer :content="displayContent" />
          </div>
        </div>

        <div v-if="message.sources && message.sources.length > 0" class="message-section">
          <Sources :sources="message.sources" />
        </div>

        <div v-if="message.contextUsage" class="message-section">
          <AiContext :context-usage="message.contextUsage" />
        </div>

        <div v-if="message.checkpoints && message.checkpoints.length > 0" class="message-section">
          <div v-for="checkpoint in message.checkpoints" :key="checkpoint.id">
            <AiCheckpoint 
              :label="checkpoint.label"
              :tooltip="checkpoint.tooltip"
              :timestamp="checkpoint.timestamp"
            />
          </div>
        </div>

        <div v-if="message.metadata?.suggestions && message.metadata.suggestions.length > 0 && !isStreaming" class="message-section">
          <AiSuggestions :suggestions="message.metadata.suggestions" />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.enhanced-message-renderer {
  width: 100%;
}

.user-message .message-content {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  color: white;
  border-radius: 12px;
  padding: 12px 16px;
  max-width: 80%;
  margin-left: auto;
}

.assistant-message {
  width: 100%;
}

.message-section {
  margin-bottom: 16px;
}

.message-section:last-child {
  margin-bottom: 0;
}

.assistant-message .message-content {
  background: #f9fafb;
  border-radius: 12px;
  padding: 12px 16px;
  line-height: 1.8;
}
</style>
