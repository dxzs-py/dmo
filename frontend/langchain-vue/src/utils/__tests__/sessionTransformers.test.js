/**
 * sessionTransformers 单元测试（Task 7.4）
 *
 * 覆盖：深层嵌套转换、数组内对象转换、Date/RegExp/File/Blob/FormData 不被遍历、
 * null/undefined/原始值原样返回、type 字符串值不被转换（只转键名）、toSnakeCase 对称性。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
import {
  toCamelCase,
  toSnakeCase,
  convertSnakeToCamel,
} from '../sessionTransformers.js'

describe('sessionTransformers', () => {
  it('深层嵌套对象：键名递归转换、字符串值不被转换', () => {
    const input = {
      session_id: 's1',
      message_list: {
        total_count: 2,
        items: [{ message_id: 'm1', author_name: 'bob' }],
      },
      error_message: 'some_error_text',
    }
    const out = toCamelCase(input)
    expect(out).toStrictEqual({
      sessionId: 's1',
      messageList: {
        totalCount: 2,
        items: [{ messageId: 'm1', authorName: 'bob' }],
      },
      errorMessage: 'some_error_text',
    })
  })

  it('数组内对象：递归转换', () => {
    const out = toCamelCase([{ tool_call_id: 't1', nested: { a_b: 1 } }, { tool_call_id: 't2' }])
    expect(out).toStrictEqual([
      { toolCallId: 't1', nested: { aB: 1 } },
      { toolCallId: 't2' },
    ])
  })

  it('Date 不被遍历：保持 Date 实例不被转成空对象', () => {
    const d = new Date('2024-01-01T00:00:00Z')
    const out = toCamelCase({ created_at: d, nested: { updated_at: d } })
    expect(out.createdAt).toBeInstanceOf(Date)
    expect(out.createdAt.getTime()).toBe(d.getTime())
    expect(out.nested.updatedAt).toBeInstanceOf(Date)
    expect(out.nested.updatedAt.getTime()).toBe(d.getTime())
  })

  it('RegExp 不被遍历：保持 RegExp 实例', () => {
    const r = /ab+c/gi
    const out = toCamelCase({ pattern: r, nested: { pattern: r } })
    expect(out.pattern).toBeInstanceOf(RegExp)
    expect(out.pattern.source).toBe('ab+c')
    expect(out.pattern.flags).toBe('gi')
    expect(out.nested.pattern).toBeInstanceOf(RegExp)
    expect(out.nested.pattern.source).toBe('ab+c')
  })

  it('Blob/File/FormData 不被遍历', () => {
    const blob = new Blob(['hello'])
    const out = toCamelCase({ blob_data: blob, nested: { blob_data: blob } })
    expect(out.blobData).toBeInstanceOf(Blob)
    expect(out.blobData.size).toBe(5)
    expect(out.nested.blobData).toBeInstanceOf(Blob)

    if (typeof File === 'function' && typeof FormData === 'function') {
      const file = new File(['content'], 'a.txt')
      const fd = new FormData()
      fd.append('k', 'v')
      const out2 = toCamelCase({ file_data: file, form_data: fd })
      expect(out2.fileData).toBeInstanceOf(File)
      expect(out2.formData).toBeInstanceOf(FormData)
    }
  })

  it('null/undefined 原样返回', () => {
    expect(toCamelCase(null)).toBe(null)
    expect(toCamelCase(undefined)).toBe(undefined)
    expect(toSnakeCase(null)).toBe(null)
    expect(toSnakeCase(undefined)).toBe(undefined)
  })

  it('原始值原样返回（字符串值不做转换）', () => {
    expect(toCamelCase('hello_world')).toBe('hello_world')
    expect(toCamelCase(123)).toBe(123)
    expect(toCamelCase(true)).toBe(true)
    expect(toSnakeCase('helloWorld')).toBe('helloWorld')
    expect(toSnakeCase(42)).toBe(42)
  })

  it('type 字符串值不被转换（协议标识符保持 snake_case，仅键名转换）', () => {
    const out = toCamelCase({
      type: 'status_change',
      data: { step_name: 'waiting_for_answers' },
      task_id: 't1',
    })
    expect(out.type).toBe('status_change')
    expect(out.data.stepName).toBe('waiting_for_answers')
    expect(out.taskId).toBe('t1')
  })

  it('toSnakeCase：深层嵌套与数组对称转换', () => {
    const obj = {
      sessionId: 's1',
      messageList: { totalCount: 2, items: [{ toolCallId: 't1' }] },
    }
    expect(toSnakeCase(obj)).toStrictEqual({
      session_id: 's1',
      message_list: { total_count: 2, items: [{ tool_call_id: 't1' }] },
    })
  })

  it('toSnakeCase(toCamelCase(x)) 对称性（纯 snake_case 输入还原）', () => {
    const input = {
      session_id: 's1',
      deep: { tool_call_id: 't1', arr: [{ a_b: 1 }] },
      type: 'status_change',
    }
    expect(toSnakeCase(toCamelCase(input))).toStrictEqual(input)
  })

  it('convertSnakeToCamel：单字符串值转换（WorkflowView step 值场景）', () => {
    expect(convertSnakeToCamel('waiting_for_answers')).toBe('waitingForAnswers')
    expect(convertSnakeToCamel('workflow_step')).toBe('workflowStep')
    expect(convertSnakeToCamel('alreadyCamel')).toBe('alreadyCamel')
  })
})
