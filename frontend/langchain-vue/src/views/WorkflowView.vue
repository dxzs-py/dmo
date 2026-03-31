<template>
  <div class="workflow-view">
    <div class="view-content">
      <el-card v-if="!execution || execution.current_step === 'start'" class="start-card">
        <template #header>
          <div class="card-header">
            <span class="page-title">学习工作流</span>
          </div>
        </template>
        <el-form :model="workflowForm" label-width="100px">
          <el-form-item label="学习主题">
            <el-input
              v-model="workflowForm.query"
              type="textarea"
              :rows="3"
              placeholder="请输入您想学习的主题..."
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="startWorkflow" :loading="isLoading">
              启动工作流
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-card v-if="execution && execution.learning_plan" class="plan-card">
        <template #header>
          <div class="card-header">
            <span>📚 学习计划</span>
            <el-tag type="info">{{ execution.learning_plan.difficulty }}</el-tag>
          </div>
        </template>
        
        <h4>{{ execution.learning_plan.topic }}</h4>
        
        <div class="plan-section">
          <h5>🎯 学习目标</h5>
          <ul>
            <li v-for="(obj, idx) in execution.learning_plan.objectives" :key="idx">{{ obj }}</li>
          </ul>
        </div>
        
        <div class="plan-section">
          <h5>💡 关键知识点</h5>
          <ul>
            <li v-for="(point, idx) in execution.learning_plan.key_points" :key="idx">{{ point }}</li>
          </ul>
        </div>
        
        <div class="plan-info">
          <el-tag>预计时间: {{ execution.learning_plan.estimated_time }} 分钟</el-tag>
        </div>
      </el-card>
      
      <el-card v-if="execution && execution.quiz" class="quiz-card">
        <template #header>
          <div class="card-header">
            <span>📝 练习题</span>
            <el-tag type="warning">等待答题</el-tag>
          </div>
        </template>
        
        <el-form :model="answersForm" label-width="0">
          <div v-for="question in execution.quiz.questions" :key="question.id" class="question-item">
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
          
          <el-button type="primary" @click="submitAnswers" :loading="isSubmitting" size="large">
            提交答案
          </el-button>
        </el-form>
      </el-card>
      
      <el-card v-if="execution && execution.score !== null" class="result-card">
        <template #header>
          <div class="card-header">
            <span>🎓 测验结果</span>
            <el-tag :type="execution.score >= 60 ? 'success' : 'danger'">
              {{ execution.score }} 分
            </el-tag>
          </div>
        </template>
        
        <div v-if="execution.feedback" class="feedback">
          <h5>📋 反馈</h5>
          <p>{{ execution.feedback }}</p>
        </div>
        
        <div v-if="execution.should_retry" class="retry-notice">
          <el-alert type="warning" title="未通过测验，将重新生成练习题..." show-icon />
        </div>
        
        <el-button v-if="!execution.should_retry" type="primary" @click="resetWorkflow">
          重新开始
        </el-button>
      </el-card>
      
      <el-card v-if="execution" class="status-card">
        <template #header>
          <div class="card-header">
            <span>状态信息</span>
            <el-tag :type="getStepType(execution.current_step)">
              {{ getStepText(execution.current_step) }}
            </el-tag>
          </div>
        </template>
        
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="线程ID">{{ execution.thread_id }}</el-descriptions-item>
          <el-descriptions-item label="查询">{{ execution.user_question }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ execution.created_at }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ execution.updated_at }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onUnmounted } from 'vue'
import { workflowAPI } from '../api/client'
import { ElMessage } from 'element-plus'

const isLoading = ref(false)
const isSubmitting = ref(false)
const execution = ref(null)
const answersForm = reactive({})
let pollingInterval = null

const workflowForm = reactive({
  query: '',
})

const getStepType = (step) => {
  const typeMap = {
    start: 'info',
    planner: 'primary',
    retrieval: 'primary',
    quiz_generator: 'warning',
    waiting_for_answers: 'warning',
    grading: 'primary',
    feedback: 'success',
    end: 'success',
    completed: 'success',
  }
  return typeMap[step] || 'info'
}

const getStepText = (step) => {
  const textMap = {
    start: '准备中',
    planner: '生成学习计划',
    retrieval: '检索资料',
    quiz_generator: '生成练习题',
    waiting_for_answers: '等待答题',
    grading: '评分中',
    feedback: '生成反馈',
    end: '已结束',
    completed: '已完成',
  }
  return textMap[step] || step
}

const getQuestionTypeText = (type) => {
  const map = {
    multiple_choice: '选择题',
    fill_blank: '填空题',
    short_answer: '简答题',
  }
  return map[type] || type
}

const pollExecutionStatus = async () => {
  if (!execution.value) {
    if (pollingInterval) {
      clearInterval(pollingInterval)
      pollingInterval = null
    }
    return
  }
  
  const currentStep = execution.value.current_step
  
  if (currentStep === 'waiting_for_answers' || currentStep === 'end' || currentStep === 'completed') {
    if (pollingInterval) {
      clearInterval(pollingInterval)
      pollingInterval = null
    }
    return
  }
  
  try {
    const response = await workflowAPI.getState(execution.value.thread_id)
    execution.value = { ...execution.value, ...response.data }
    
    if (response.data.quiz && !Object.keys(answersForm).length) {
      response.data.quiz.questions.forEach(q => {
        answersForm[q.id] = ''
      })
    }
  } catch (error) {
    console.error('获取工作流状态失败:', error)
  }
}

const startWorkflow = async () => {
  if (!workflowForm.query.trim()) {
    ElMessage.warning('请输入学习主题')
    return
  }
  
  isLoading.value = true
  execution.value = null
  Object.keys(answersForm).forEach(key => delete answersForm[key])
  
  try {
    const response = await workflowAPI.start(workflowForm)
    execution.value = response.data
    ElMessage.success('工作流已启动')
    
    pollingInterval = setInterval(pollExecutionStatus, 3000)
  } catch (error) {
    console.error('启动工作流失败:', error)
    ElMessage.error('启动工作流失败，请稍后重试')
  } finally {
    isLoading.value = false
  }
}

const submitAnswers = async () => {
  const hasEmpty = execution.value.quiz.questions.some(q => !answersForm[q.id])
  if (hasEmpty) {
    ElMessage.warning('请完成所有题目')
    return
  }
  
  isSubmitting.value = true
  
  try {
    const response = await workflowAPI.submitAnswers(execution.value.thread_id, answersForm)
    execution.value = { ...execution.value, ...response.data }
    ElMessage.success('答案已提交')
    
    if (response.data.should_retry) {
      Object.keys(answersForm).forEach(key => delete answersForm[key])
      pollingInterval = setInterval(pollExecutionStatus, 3000)
    }
  } catch (error) {
    console.error('提交答案失败:', error)
    ElMessage.error('提交答案失败，请稍后重试')
  } finally {
    isSubmitting.value = false
  }
}

const resetWorkflow = () => {
  execution.value = null
  Object.keys(answersForm).forEach(key => delete answersForm[key])
  workflowForm.query = ''
  if (pollingInterval) {
    clearInterval(pollingInterval)
    pollingInterval = null
  }
}

onUnmounted(() => {
  if (pollingInterval) {
    clearInterval(pollingInterval)
  }
})
</script>

<style scoped>
.workflow-view {
  height: 100%;
  padding: 24px;
  overflow-y: auto;
}

.view-content {
  max-width: 900px;
  margin: 0 auto;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.start-card,
.plan-card,
.quiz-card,
.result-card,
.status-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.plan-card h4 {
  margin: 0 0 16px 0;
  font-size: 18px;
}

.plan-section {
  margin-bottom: 16px;
}

.plan-section h5 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.plan-section ul {
  margin: 0;
  padding-left: 20px;
}

.plan-section li {
  margin-bottom: 6px;
}

.plan-info {
  margin-top: 16px;
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
</style>
