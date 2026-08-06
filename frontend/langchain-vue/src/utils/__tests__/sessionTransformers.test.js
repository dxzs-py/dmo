/**
 * sessionTransformers 单元测试（Task 7.4）
 *
 * 覆盖：深层嵌套转换、数组内对象转换、Date/RegExp/File/Blob/FormData 不被遍历、
 * null/undefined/原始值原样返回、type 字符串值不被转换（只转键名）、toSnakeCase 对称性。
 *
 * 运行方式（项目 package.json 为 "type": "module"，使用 Node 内置 assert，无需第三方框架）：
 *   node src/utils/__tests__/sessionTransformers.test.js
 */
import { strict as assert } from 'node:assert'
import {
  toCamelCase,
  toSnakeCase,
  convertSnakeToCamel,
} from '../sessionTransformers.js'

// 抑制 toCamelCase/toSnakeCase 顶层的转换 debug 日志，保持测试输出整洁
const originalDebug = console.debug
console.debug = () => {}

let passed = 0
let failed = 0

function test(name, fn) {
  try {
    fn()
    passed++
    console.log(`  ✓ ${name}`)
  } catch (err) {
    failed++
    console.error(`  ✗ ${name}`)
    console.error(`    ${err && err.message ? err.message : err}`)
  }
}

console.log('sessionTransformers 单元测试\n')

test('深层嵌套对象：键名递归转换、字符串值不被转换', () => {
  const input = {
    session_id: 's1',
    message_list: {
      total_count: 2,
      items: [{ message_id: 'm1', author_name: 'bob' }],
    },
    error_message: 'some_error_text',
  }
  const out = toCamelCase(input)
  assert.deepStrictEqual(out, {
    sessionId: 's1',
    messageList: {
      totalCount: 2,
      items: [{ messageId: 'm1', authorName: 'bob' }],
    },
    errorMessage: 'some_error_text',
  })
})

test('数组内对象：递归转换', () => {
  const out = toCamelCase([{ tool_call_id: 't1', nested: { a_b: 1 } }, { tool_call_id: 't2' }])
  assert.deepStrictEqual(out, [
    { toolCallId: 't1', nested: { aB: 1 } },
    { toolCallId: 't2' },
  ])
})

test('Date 不被遍历：保持 Date 实例不被转成空对象', () => {
  const d = new Date('2024-01-01T00:00:00Z')
  const out = toCamelCase({ created_at: d, nested: { updated_at: d } })
  assert.ok(out.createdAt instanceof Date)
  assert.strictEqual(out.createdAt.getTime(), d.getTime())
  assert.ok(out.nested.updatedAt instanceof Date)
  assert.strictEqual(out.nested.updatedAt.getTime(), d.getTime())
})

test('RegExp 不被遍历：保持 RegExp 实例', () => {
  const r = /ab+c/gi
  const out = toCamelCase({ pattern: r, nested: { pattern: r } })
  assert.ok(out.pattern instanceof RegExp)
  assert.strictEqual(out.pattern.source, 'ab+c')
  assert.strictEqual(out.pattern.flags, 'gi')
  assert.ok(out.nested.pattern instanceof RegExp)
  assert.strictEqual(out.nested.pattern.source, 'ab+c')
})

test('Blob/File/FormData 不被遍历', () => {
  const blob = new Blob(['hello'])
  const out = toCamelCase({ blob_data: blob, nested: { blob_data: blob } })
  assert.ok(out.blobData instanceof Blob)
  assert.strictEqual(out.blobData.size, 5)
  assert.ok(out.nested.blobData instanceof Blob)

  if (typeof File === 'function' && typeof FormData === 'function') {
    const file = new File(['content'], 'a.txt')
    const fd = new FormData()
    fd.append('k', 'v')
    const out2 = toCamelCase({ file_data: file, form_data: fd })
    assert.ok(out2.fileData instanceof File)
    assert.ok(out2.formData instanceof FormData)
  }
})

test('null/undefined 原样返回', () => {
  assert.strictEqual(toCamelCase(null), null)
  assert.strictEqual(toCamelCase(undefined), undefined)
  assert.strictEqual(toSnakeCase(null), null)
  assert.strictEqual(toSnakeCase(undefined), undefined)
})

test('原始值原样返回（字符串值不做转换）', () => {
  assert.strictEqual(toCamelCase('hello_world'), 'hello_world')
  assert.strictEqual(toCamelCase(123), 123)
  assert.strictEqual(toCamelCase(true), true)
  assert.strictEqual(toSnakeCase('helloWorld'), 'helloWorld')
  assert.strictEqual(toSnakeCase(42), 42)
})

test('type 字符串值不被转换（协议标识符保持 snake_case，仅键名转换）', () => {
  const out = toCamelCase({
    type: 'status_change',
    data: { step_name: 'waiting_for_answers' },
    task_id: 't1',
  })
  assert.strictEqual(out.type, 'status_change')
  assert.strictEqual(out.data.stepName, 'waiting_for_answers')
  assert.strictEqual(out.taskId, 't1')
})

test('toSnakeCase：深层嵌套与数组对称转换', () => {
  const obj = {
    sessionId: 's1',
    messageList: { totalCount: 2, items: [{ toolCallId: 't1' }] },
  }
  assert.deepStrictEqual(toSnakeCase(obj), {
    session_id: 's1',
    message_list: { total_count: 2, items: [{ tool_call_id: 't1' }] },
  })
})

test('toSnakeCase(toCamelCase(x)) 对称性（纯 snake_case 输入还原）', () => {
  const input = {
    session_id: 's1',
    deep: { tool_call_id: 't1', arr: [{ a_b: 1 }] },
    type: 'status_change',
  }
  assert.deepStrictEqual(toSnakeCase(toCamelCase(input)), input)
})

test('convertSnakeToCamel：单字符串值转换（WorkflowView step 值场景）', () => {
  assert.strictEqual(convertSnakeToCamel('waiting_for_answers'), 'waitingForAnswers')
  assert.strictEqual(convertSnakeToCamel('workflow_step'), 'workflowStep')
  assert.strictEqual(convertSnakeToCamel('alreadyCamel'), 'alreadyCamel')
})

console.debug = originalDebug

console.log('')
console.log(`通过 ${passed} 项，失败 ${failed} 项`)
if (failed > 0) {
  process.exitCode = 1
}
