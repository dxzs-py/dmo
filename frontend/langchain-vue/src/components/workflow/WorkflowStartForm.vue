<template>
  <el-card class="start-card">
    <template #header>
      <div class="card-header">
        <span class="page-title">学习工作流</span>
      </div>
    </template>
    <el-form :model="form" label-width="100px">
      <el-form-item label="学习主题">
        <el-input
          v-model="form.query"
          type="textarea"
          :rows="3"
          placeholder="请输入您想学习的主题..."
        />
      </el-form-item>
      <el-form-item label="知识库">
        <KnowledgeBaseSelector v-model="form.knowledgeBaseIds" />
        <div class="kb-tip">不选择知识库时将使用 AI 内置知识生成学习内容</div>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="isLoading" @click="emit('start')">
          启动工作流
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import KnowledgeBaseSelector from '@/components/common/KnowledgeBaseSelector.vue'

/**
 * 学习工作流 - 开始表单
 * 包含：学习主题输入 / 知识库选择 / 启动按钮
 *
 * form 为 reactive 对象（父组件传入引用），子组件直接 v-model 绑定其属性，
 * 与 ResearchStartCard 的 researchForm 处理方式一致。
 */
defineProps({
  /** 启动表单 reactive 对象（query / knowledge_base_ids） */
  form: {
    type: Object,
    required: true,
  },
  /** 启动按钮 loading 状态 */
  isLoading: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['start'])
</script>

<style scoped>
.start-card {
  margin-bottom: 20px;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.kb-tip {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 1024px) {
  .page-title {
    font-size: 16px;
  }
}

@media (max-width: 768px) {
  .page-title {
    font-size: 15px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}

@media (max-width: 480px) {
  .page-title {
    font-size: 14px;
  }
}
</style>
