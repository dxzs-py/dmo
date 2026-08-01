<template>
  <div>
    <el-card v-if="quiz" class="quiz-card">
      <template #header>
        <div class="card-header">
          <span>📝 练习题</span>
          <el-tag type="warning">等待答题</el-tag>
        </div>
      </template>

      <el-form :model="answersForm" label-width="0">
        <div v-for="question in quiz.questions" :key="question.id" class="question-item">
          <div class="question-header">
            <span class="question-title">第 {{ question.id.replace('q', '') }} 题 ({{ question.points }} 分)</span>
            <el-tag size="small">{{ getQuestionTypeText(question.type) }}</el-tag>
          </div>
          <p class="question-text">{{ question.question }}</p>

          <div v-if="question.type === 'multiple_choice'" class="options">
            <el-radio-group v-model="answersForm[question.id]">
              <el-radio v-for="(opt, idx) in question.options" :key="idx" :value="opt">
                {{ String.fromCharCode(65 + idx) }}. {{ opt }}
              </el-radio>
            </el-radio-group>
          </div>

          <el-input v-else-if="question.type === 'fill_blank'" v-model="answersForm[question.id]" placeholder="请填入答案" />

          <el-input v-else v-model="answersForm[question.id]" type="textarea" :rows="3" placeholder="请输入答案" />
        </div>

        <el-button type="primary" :loading="isSubmitting" size="large" @click="emit('submit')">
          提交答案
        </el-button>
      </el-form>
    </el-card>

    <el-card v-if="score !== null" class="result-card">
      <template #header>
        <div class="card-header">
          <span>🎓 测验结果</span>
          <el-tag :type="score >= 60 ? 'success' : 'danger'">
            {{ score }} 分
          </el-tag>
        </div>
      </template>

      <div v-if="feedback" class="feedback">
        <h5>📋 反馈</h5>
        <p>{{ feedback }}</p>
      </div>

      <div v-if="shouldRetry" class="retry-notice">
        <el-alert type="warning" title="未通过测验，将重新生成练习题..." show-icon />
      </div>

      <el-button v-if="!shouldRetry" type="primary" @click="emit('reset')">
        重新开始
      </el-button>
    </el-card>
  </div>
</template>

<script setup>
/**
 * 学习工作流 - 测验表单 + 结果
 * 包含两部分（均可独立显示）：
 *   - 练习题卡片（v-if="quiz"）：选择题 / 填空题 / 简答题，绑定 answersForm
 *   - 测验结果卡片（v-if="score !== null"）：分数 / 反馈 / 重试提示 / 重新开始按钮
 *
 * answersForm 为 reactive 对象（父组件传入引用），子组件直接 v-model 绑定其属性。
 */
defineProps({
  /** 测验对象（questions 数组），为空时不渲染练习题卡片 */
  quiz: {
    type: Object,
    default: null,
  },
  /** 答题表单 reactive 对象（key=question.id） */
  answersForm: {
    type: Object,
    required: true,
  },
  /** 提交按钮 loading 状态 */
  isSubmitting: {
    type: Boolean,
    default: false,
  },
  /** 测验分数（null 表示未评分，不渲染结果卡片） */
  score: {
    type: Number,
    default: null,
  },
  /** 测验反馈文案 */
  feedback: {
    type: String,
    default: null,
  },
  /** 是否需要重试（未通过测验，将重新生成练习题） */
  shouldRetry: {
    type: Boolean,
    default: false,
  },
  /** 题目类型 → 中文文案映射函数（来自 useWorkflowSteps） */
  getQuestionTypeText: {
    type: Function,
    required: true,
  },
})

const emit = defineEmits(['submit', 'reset'])
</script>

<style scoped>
.quiz-card,
.result-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.question-item {
  margin-bottom: 24px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.question-item:last-child {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.question-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.question-title {
  font-weight: 600;
  font-size: 15px;
}

.question-text {
  margin: 0 0 16px 0;
  font-size: 15px;
  line-height: 1.6;
}

.options {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.feedback {
  margin-bottom: 16px;
}

.feedback h5 {
  margin: 0 0 8px 0;
  font-size: 14px;
}

.feedback p {
  margin: 0;
  padding: 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  line-height: 1.6;
}

.retry-notice {
  margin-bottom: 16px;
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}

@media (max-width: 480px) {
  .question-title {
    font-size: 14px;
  }

  .question-text {
    font-size: 14px;
  }
}
</style>
