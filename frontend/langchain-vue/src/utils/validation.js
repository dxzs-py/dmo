import { z } from 'zod'
import { MessageRole } from '@/types'

export const ChatRequestSchema = z.object({
  message: z.string().min(1, '消息内容不能为空'),
  chatHistory: z.array(z.object({
    role: z.enum(['user', 'assistant', 'system']),
    content: z.string(),
  })).optional(),
  mode: z.enum(['agent', 'deep-research']).optional(),
  useTools: z.boolean().optional(),
  useWebSearch: z.boolean().optional(),
  useKnowledgeBase: z.boolean().optional(),
  useDeepThinking: z.boolean().optional(),
  useMcp: z.boolean().optional(),
  selectedMcpServers: z.array(z.string()).optional().nullable(),
  selectedTools: z.array(z.string()).optional().nullable(),
  selectedKnowledgeBase: z.string().optional().nullable(),
  selectedKnowledgeBases: z.array(z.string()).optional().nullable(),
  attachmentIds: z.array(z.number().int().positive()).optional().default([]),
})

export function validateSchema(schema, data) {
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

/**
 * 校验消息对象结构是否合法
 * @param {Object} msg - 消息对象
 * @returns {boolean}
 */
export function validateMessage(msg) {
  if (!msg || typeof msg !== 'object') return false
  if (!msg.id || typeof msg.id !== 'string') return false
  if (!Object.values(MessageRole).includes(msg.role)) return false
  if (msg.content === undefined || typeof msg.content !== 'string') return false
  return true
}
