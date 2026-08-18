<template>
  <el-card v-if="attempts.length > 0" class="history-card">
    <template #header>
      <div class="card-header">
        <span>📚 练习历史</span>
        <el-button size="small" text type="primary" :loading="loading" @click="loadAll">
          刷新
        </el-button>
      </div>
    </template>

    <div v-if="loading" class="history-loading">加载中...</div>

    <template v-else>
      <div v-for="attempt in attempts" :key="attempt.attemptIndex" class="attempt-block">
        <div class="attempt-header">
          <span class="attempt-title">第 {{ attempt.attemptIndex + 1 }} 轮练习</span>
          <el-tag v-if="attempt.totalScore !== null" :type="attempt.totalScore >= 60 ? 'success' : 'danger'">
            {{ attempt.totalScore }} 分
          </el-tag>
        </div>

        <div v-for="question in groupedQuestions(attempt.attemptIndex)" :key="question.questionId" class="history-question">
          <div class="question-header">
            <span class="question-title">第 {{ question.questionIndex }} 题 ({{ question.points }} 分)</span>
            <el-tag size="small">{{ getQuestionTypeText(question.type) }}</el-tag>
          </div>
          <p class="question-text">{{ question.question }}</p>

          <div v-if="question.type === 'multiple_choice'" class="options">
            <div
              v-for="(opt, idx) in displayOptions(question.options)"
              :key="idx"
              class="option-line"
            >
              <span
                :class="['option-mark', {
                  'is-correct': opt.value === question.correctAnswer,
                  'is-wrong': question.userAnswer && opt.value === question.userAnswer && opt.value !== question.correctAnswer,
                }]"
              >
                {{ String.fromCharCode(65 + idx) }}. {{ opt.text }}
              </span>
            </div>
          </div>

          <div class="answer-row">
            <span>你的答案：</span>
            <span v-if="!isEditing(question)">{{ question.userAnswer || '未作答' }}</span>
            <span v-else class="edit-inline">
              <el-radio-group v-if="question.type === 'multiple_choice'" v-model="editForm.answer">
                <el-radio v-for="(opt, idx) in displayOptions(question.options)" :key="idx" :value="opt.value">
                  {{ String.fromCharCode(65 + idx) }}. {{ opt.text }}
                </el-radio>
              </el-radio-group>
              <el-input
                v-else-if="question.type === 'fill_blank'"
                v-model="editForm.answer"
                placeholder="填入新答案"
                size="small"
              />
              <el-input
                v-else
                v-model="editForm.answer"
                type="textarea"
                :rows="2"
                placeholder="输入新答案"
                size="small"
              />
            </span>
            <span class="correct-answer">标准答案：{{ question.correctAnswer }}</span>
          </div>

          <div class="question-meta">
            <el-tag v-if="question.status === 'locked'" size="small" type="info">已锁定</el-tag>
            <el-tag v-else-if="question.isCorrect !== null" size="small" :type="question.isCorrect ? 'success' : 'danger'">
              {{ question.isCorrect ? '正确' : '错误' }}
            </el-tag>
            <el-tag v-if="question.pointsEarned !== null" size="small">
              得分 {{ question.pointsEarned }}/{{ question.points }}
            </el-tag>
          </div>

          <div v-if="question.explanation" class="explanation">
            <span class="explanation-label">解析：</span>{{ question.explanation }}
          </div>

          <div class="edit-actions">
            <template v-if="!isEditing(question)">
              <el-button size="small" link type="primary" @click="startEdit(question)">
                修改
              </el-button>
            </template>
            <template v-else>
              <el-button size="small" type="primary" :loading="saving" @click="saveEdit(question)">
                保存
              </el-button>
              <el-button size="small" @click="cancelEdit">
                取消
              </el-button>
            </template>
          </div>
        </div>
      </div>
    </template>
  </el-card>
</template>

<script setup>
/**
 * 学习工作流 - 练习历史（按轮次分组）+ 单题修改/重新评分
 *
 * 数据来源：
 *   - attempts：GET /learning/{thread_id}/attempts/（轮次历史）
 *   - questions：GET /learning/{thread_id}/questions/（跨轮次题目，含 attempt_index）
 * 修改流程：行内编辑 → PUT /learning/{thread_id}/questions/{question_id}/ → 更新本地分数
 */
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { workflowAPI } from '@/api/workflow'
import { logger } from '@/utils/logger'
import { LearningQuestionType } from '@/types'

const props = defineProps({
  /** 工作流线程 ID */
  threadId: {
    type: String,
    required: true,
  },
})

const emit = defineEmits(['score-updated'])

const loading = ref(false)
const saving = ref(false)
const attempts = ref([])
const questions = ref([])
const editingQuestionId = ref(null)
const editForm = ref({ answer: '' })

const getQuestionTypeText = (type) => {
  const map = {
    [LearningQuestionType.MULTIPLE_CHOICE]: '选择题',
    [LearningQuestionType.FILL_BLANK]: '填空题',
    [LearningQuestionType.SHORT_ANSWER]: '简答题',
  }
  return map[type] || type
}

/** 剥离选项文本自带的 "A. " 前缀（LLM 出题格式不稳定），并返回 {text, value} 列表 */
const displayOptions = (options) => {
  if (!Array.isArray(options)) return []
  return options.map((opt) => {
    const text = String(opt).replace(/^[A-Ha-h]\.\s*/, '')
    return { text, value: String(opt) }
  })
}

/** 按轮次过滤题目 */
const groupedQuestions = (attemptIndex) => {
  return questions.value
    .filter(q => q.attemptIndex === attemptIndex)
    .sort((a, b) => a.questionIndex - b.questionIndex)
}

const isEditing = (question) => editingQuestionId.value === question.questionId

const loadAll = async () => {
  if (!props.threadId) return
  loading.value = true
  try {
    const [attemptRes, questionRes] = await Promise.all([
      workflowAPI.getAttempts(props.threadId),
      workflowAPI.getQuestions(props.threadId),
    ])
    attempts.value = (attemptRes.data?.data || attemptRes.data || []).map(attempt => ({
      attemptIndex: attempt.attemptIndex ?? 0,
      totalScore: attempt.totalScore,
      feedback: attempt.feedback,
    }))
    questions.value = (questionRes.data?.data || questionRes.data || []).map(q => ({
      questionId: q.questionId,
      attemptIndex: q.attemptIndex ?? 0,
      questionIndex: q.questionIndex ?? 0,
      type: q.type,
      question: q.question,
      options: q.options || [],
      correctAnswer: q.correctAnswer,
      explanation: q.explanation || '',
      points: q.points ?? 0,
      userAnswer: q.userAnswer || '',
      isCorrect: q.isCorrect,
      pointsEarned: q.pointsEarned,
      status: q.status,
    }))
  } catch (error) {
    logger.error('加载练习历史失败:', error)
    ElMessage.error('加载练习历史失败')
  } finally {
    loading.value = false
  }
}

const startEdit = (question) => {
  editingQuestionId.value = question.questionId
  editForm.value.answer = question.userAnswer || ''
}

const cancelEdit = () => {
  editingQuestionId.value = null
  editForm.value.answer = ''
}

const saveEdit = async (question) => {
  if (!editForm.value.answer) {
    ElMessage.warning('答案不能为空')
    return
  }
  saving.value = true
  try {
    const response = await workflowAPI.updateQuestion(props.threadId, question.questionId, editForm.value.answer)
    const data = response.data?.data || response.data
    ElMessage.success('已重新评分')

    // 更新本地题目
    const updated = data.question || {}
    const target = questions.value.find(q => q.questionId === question.questionId)
    if (target) {
      target.userAnswer = updated.userAnswer ?? editForm.value.answer
      target.isCorrect = updated.isCorrect
      target.pointsEarned = updated.pointsEarned
      target.status = updated.status || 'scored'
    }

    // 更新轮次总分（attempt_total_score）
    if (data.attemptTotalScore !== undefined && data.attemptTotalScore !== null) {
      const attempt = attempts.value.find(a => a.attemptIndex === question.attemptIndex)
      if (attempt) attempt.totalScore = data.attemptTotalScore
    }

    cancelEdit()
    emit('score-updated', data.attemptTotalScore)
  } catch (error) {
    logger.error('重新评分失败:', error)
    ElMessage.error(error.response?.data?.message || error.message || '重新评分失败')
  } finally {
    saving.value = false
  }
}

watch(() => props.threadId, () => {
  if (props.threadId) loadAll()
})

onMounted(() => {
  if (props.threadId) loadAll()
})

defineExpose({ loadAll })
</script>

<style scoped>
.history-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.history-loading {
  padding: 16px;
  text-align: center;
  color: var(--el-text-color-secondary);
}

.attempt-block {
  margin-bottom: 24px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
}

.attempt-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.attempt-title {
  font-weight: 600;
}

.history-question {
  margin-bottom: 16px;
  padding: 12px;
  background: var(--el-bg-color);
  border-radius: 4px;
}

.question-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.question-title {
  font-weight: 600;
}

.question-text {
  margin: 0 0 8px 0;
}

.options {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.option-mark.is-correct {
  color: var(--el-color-success);
  font-weight: 600;
}

.option-mark.is-wrong {
  color: var(--el-color-danger);
}

.answer-row {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.correct-answer {
  color: var(--el-color-success);
}

.question-meta {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}

.explanation {
  margin-top: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.explanation-label {
  font-weight: 600;
}

.edit-inline {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 4px 0;
}

.edit-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
</style>
