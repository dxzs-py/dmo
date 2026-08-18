<template>
  <div>
    <!-- 未评分时展示答题表单；评分后答题入口收敛到「练习历史」的修改答案 -->
    <el-card v-if="quiz && score === null" class="quiz-card">
      <template #header>
        <div class="card-header">
          <span>📝 练习题</span>
          <el-tag type="warning">等待答题</el-tag>
        </div>
      </template>

      <el-form :model="localAnswers" label-width="0">
        <div v-for="question in quiz.questions" :key="question.id" class="question-item">
          <div class="question-header">
            <span class="question-title">第 {{ question.id.replace('q', '') }} 题 ({{ question.points }} 分)</span>
            <el-tag size="small">{{ getQuestionTypeText(question.type) }}</el-tag>
          </div>
          <p class="question-text">{{ question.question }}</p>

          <div v-if="question.type === LearningQuestionType.MULTIPLE_CHOICE" class="options">
            <el-radio-group v-model="localAnswers[question.id]">
              <el-radio v-for="(opt, idx) in displayOptions(question.options)" :key="idx" :value="opt.value">
                {{ String.fromCharCode(65 + idx) }}. {{ opt.text }}
              </el-radio>
            </el-radio-group>
          </div>

          <el-input v-else-if="question.type === LearningQuestionType.FILL_BLANK" v-model="localAnswers[question.id]" placeholder="请填入答案" />

          <el-input v-else v-model="localAnswers[question.id]" type="textarea" :rows="3" placeholder="请输入答案" />
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

      <!-- 逐题对比 -->
      <div v-if="scoreDetails?.questionScores?.length" class="score-details">
        <h5>📊 答题详情</h5>
        <div v-for="item in scoreDetails.questionScores" :key="item.questionId" class="score-item">
          <div class="score-item-header">
            <span class="score-item-title">第 {{ item.questionId.replace('q', '') }} 题</span>
            <el-tag size="small" :type="item.isCorrect ? 'success' : 'danger'">
              {{ item.isCorrect ? '正确' : '错误' }} · {{ item.pointsEarned }}/{{ item.pointsPossible }}
            </el-tag>
          </div>
          <div class="score-item-row">
            <span class="label">你的答案：</span>{{ item.userAnswer || '未作答' }}
          </div>
          <div class="score-item-row">
            <span class="label">标准答案：</span>{{ item.correctAnswer }}
          </div>
          <div v-if="item.feedback" class="score-item-row">
            <span class="label">评语：</span>{{ item.feedback }}
          </div>
        </div>
      </div>

      <div v-if="shouldRetry" class="retry-notice">
        <el-alert type="warning" title="未通过测验，将重新生成练习题..." show-icon />
      </div>

      <div class="result-actions">
        <el-button v-if="canContinue" type="primary" :loading="continuing" @click="emit('continue')">
          继续练习
        </el-button>
        <el-button v-if="!shouldRetry && !canContinue" type="primary" @click="emit('reset')">
          重新开始
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
/**
 * 学习工作流 - 测验表单 + 结果
 * 包含两部分（均可独立显示）：
 *   - 练习题卡片（v-if="quiz"）：选择题 / 填空题 / 简答题，绑定 answersForm
 *   - 测验结果卡片（v-if="score !== null"）：分数 / 反馈 / 逐题对比 / 继续练习 / 重新开始
 *
 * answersForm 为 reactive 对象（父组件传入引用），子组件直接 v-model 绑定其属性。
 */
import { computed } from 'vue'
import { LearningQuestionType } from '@/types'

const props = defineProps({
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
  /** 逐题评分详情（score_details.question_scores，经 toCamelCase 转换后为 questionScores） */
  scoreDetails: {
    type: Object,
    default: null,
  },
  /** 是否显示"继续练习"按钮（工作流已完成且未达重试上限时） */
  canContinue: {
    type: Boolean,
    default: false,
  },
  /** 继续练习请求 loading 状态（防止重复点击） */
  continuing: {
    type: Boolean,
    default: false,
  },
  /** 题目类型 → 中文文案映射函数（来自 useWorkflowSteps） */
  getQuestionTypeText: {
    type: Function,
    required: true,
  },
})

const emit = defineEmits(['submit', 'reset', 'continue'])

/**
 * answersForm 为父组件传入的 reactive 对象引用（共享同一实例），
 * 通过 computed 别名引用，避免直接对 prop 赋值触发 vue/no-mutating-props。
 */
const localAnswers = computed(() => props.answersForm)

/** 剥离选项文本自带的 "A. " 前缀（LLM 出题格式不稳定），返回 {text, value} 列表 */
const displayOptions = (options) => {
  if (!Array.isArray(options)) return []
  return options.map((opt) => {
    const text = String(opt).replace(/^[A-Ha-h]\.\s*/, '')
    return { text, value: String(opt) }
  })
}
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

.feedback h5,
.score-details h5 {
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

.score-details {
  margin-bottom: 16px;
}

.score-item {
  padding: 10px;
  margin-bottom: 8px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  font-size: 13px;
}

.score-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.score-item-title {
  font-weight: 600;
}

.score-item-row {
  line-height: 1.6;
}

.score-item-row .label {
  color: var(--el-text-color-secondary);
}

.retry-notice {
  margin-bottom: 16px;
}

.result-actions {
  display: flex;
  gap: 12px;
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
