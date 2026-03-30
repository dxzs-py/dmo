import { z } from 'zod'

export const ChatMessageSchema = z.object({
  id: z.string().min(1),
  role: z.enum(['user', 'assistant', 'system']),
  content: z.string().min(1),
  timestamp: z.string().optional(),
  sources: z.array(z.any()).optional(),
  plan: z.any().optional(),
  chainOfThought: z.any().optional(),
  toolCalls: z.array(z.any()).optional(),
})

export const ChatSessionSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  mode: z.enum(['basic-agent', 'rag', 'workflow', 'deep-research', 'guarded']),
  messageCount: z.number().int().nonnegative(),
  messages: z.array(ChatMessageSchema),
  createdAt: z.number().int(),
  updatedAt: z.number().int(),
})

export const ChatRequestSchema = z.object({
  message: z.string().min(1, '消息内容不能为空'),
  chat_history: z.array(z.object({
    role: z.enum(['user', 'assistant', 'system']),
    content: z.string(),
  })).optional(),
  mode: z.enum(['basic-agent', 'rag', 'workflow', 'deep-research', 'guarded']).optional(),
  use_tools: z.boolean().optional(),
  use_advanced_tools: z.boolean().optional(),
})

export const SettingsSchema = z.object({
  apiUrl: z.string().url('请输入有效的 API 地址'),
  defaultMode: z.enum(['basic-agent', 'rag', 'workflow', 'deep-research', 'guarded']),
  theme: z.enum(['light', 'dark', 'system']),
})

export const RAGQuerySchema = z.object({
  indexName: z.string().min(1, '索引名称不能为空'),
  query: z.string().min(1, '查询内容不能为空'),
  topK: z.number().int().positive().optional(),
})

export const WorkflowStartSchema = z.object({
  topic: z.string().min(1, '主题不能为空'),
  threadId: z.string().optional(),
})

export const DeepResearchStartSchema = z.object({
  topic: z.string().min(1, '研究主题不能为空'),
  sessionId: z.string().optional(),
})

export const validateSchema = (schema, data) => {
  try {
    const result = schema.parse(data)
    return { success: true, data: result, errors: null }
  } catch (error) {
    if (error instanceof z.ZodError) {
      const errors = error.issues.map(issue => ({
        field: issue.path.join('.'),
        message: issue.message,
      }))
      return { success: false, data: null, errors }
    }
    return { success: false, data: null, errors: [{ field: 'general', message: '验证失败' }] }
  }
}

export default {
  ChatMessageSchema,
  ChatSessionSchema,
  ChatRequestSchema,
  SettingsSchema,
  RAGQuerySchema,
  WorkflowStartSchema,
  DeepResearchStartSchema,
  validateSchema,
}
