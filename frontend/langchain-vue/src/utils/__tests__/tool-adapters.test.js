import { describe, it, expect, beforeEach } from 'vitest'
import {
  formatToolParameters,
  formatToolResult,
  getToolDisplayConfig,
  isReadonlyTool,
  inferLanguageFromPath,
  registerToolAdapter,
} from '../toolAdapters'
import { ToolCallStatus } from '@/types'

// ==================== SubTask 9.8: formatToolParameters ====================

describe('SubTask 9.8 formatToolParameters', () => {
  it('read_file → 显示 file_path（inline 模式）', () => {
    const result = formatToolParameters('read_file', { file_path: '/test.js' })
    expect(result.label).toBe('文件路径')
    expect(result.formatted).toBe('/test.js')
    expect(result.displayMode).toBe('inline')
  })

  it('read_file → 含 offset/limit 时拼接显示', () => {
    const result = formatToolParameters('read_file', {
      file_path: '/test.js',
      offset: 10,
      limit: 50,
    })
    expect(result.formatted).toBe('/test.js · offset=10 · limit=50')
  })

  it('read_file → path 字段作为 file_path 的回退', () => {
    const result = formatToolParameters('read_file', { path: '/fallback.js' })
    expect(result.formatted).toBe('/fallback.js')
  })

  it('write_file → 显示 file_path + content 截断预览（json 模式）', () => {
    const result = formatToolParameters('write_file', {
      file_path: '/out.txt',
      content: 'hello world',
    })
    expect(result.label).toBe('写入文件')
    expect(result.displayMode).toBe('json')
    const parsed = JSON.parse(result.formatted)
    expect(parsed.file_path).toBe('/out.txt')
    expect(parsed.content).toBe('hello world')
  })

  it('write_file → content 超过 200 字符时截断', () => {
    const longContent = 'x'.repeat(300)
    const result = formatToolParameters('write_file', {
      file_path: '/out.txt',
      content: longContent,
    })
    const parsed = JSON.parse(result.formatted)
    // 截断后长度 = 200 + 省略号字符
    expect(parsed.content.length).toBeLessThanOrEqual(201)
    expect(parsed.content.endsWith('…')).toBe(true)
  })

  it('edit_file → 显示 file_path + old_string + new_string（diff 模式）', () => {
    const result = formatToolParameters('edit_file', {
      file_path: '/edit.js',
      old_string: 'foo',
      new_string: 'bar',
    })
    expect(result.label).toBe('编辑文件')
    expect(result.displayMode).toBe('diff')
    const parsed = JSON.parse(result.formatted)
    expect(parsed.file_path).toBe('/edit.js')
    expect(parsed.old_string).toBe('foo')
    expect(parsed.new_string).toBe('bar')
  })

  it('execute → 显示 command（command 模式）', () => {
    const result = formatToolParameters('execute', { command: 'ls -la' })
    expect(result.label).toBe('命令')
    expect(result.formatted).toBe('ls -la')
    expect(result.displayMode).toBe('command')
  })

  it('execute → shell_command 字段作为 command 的回退', () => {
    const result = formatToolParameters('execute', { shell_command: 'pwd' })
    expect(result.formatted).toBe('pwd')
  })

  it('glob → 显示 pattern + path（inline 模式）', () => {
    const result = formatToolParameters('glob', { pattern: '*.js', path: '/src' })
    expect(result.label).toBe('匹配模式')
    expect(result.displayMode).toBe('inline')
    expect(result.formatted).toBe('pattern=*.js · path=/src')
  })

  it('grep → 显示 pattern + path（inline 模式）', () => {
    const result = formatToolParameters('grep', { pattern: 'TODO', path: '/src' })
    expect(result.label).toBe('搜索模式')
    expect(result.formatted).toBe('pattern=TODO · path=/src')
  })

  it('ls → 显示 path（inline 模式）', () => {
    const result = formatToolParameters('ls', { path: '/home' })
    expect(result.label).toBe('目录')
    expect(result.formatted).toBe('/home')
    expect(result.displayMode).toBe('inline')
  })

  it('write_todos → 显示 todos 列表（list 模式）', () => {
    const result = formatToolParameters('write_todos', {
      todos: ['task1', 'task2'],
    })
    expect(result.label).toBe('待办列表')
    expect(result.displayMode).toBe('list')
    const parsed = JSON.parse(result.formatted)
    expect(parsed).toEqual(['task1', 'task2'])
  })

  it('task → 显示任务描述（inline 模式）', () => {
    const result = formatToolParameters('task', { description: '执行测试' })
    expect(result.label).toBe('任务描述')
    expect(result.formatted).toBe('执行测试')
    expect(result.displayMode).toBe('inline')
  })

  it('shell_exec → 与 execute 共享格式化逻辑', () => {
    const result = formatToolParameters('shell_exec', { command: 'whoami' })
    expect(result.label).toBe('命令')
    expect(result.formatted).toBe('whoami')
    expect(result.displayMode).toBe('command')
  })

  it('非内置工具 → 通用 formatContent 序列化（json 模式）', () => {
    const result = formatToolParameters('custom_tool', { key: 'value' })
    expect(result.label).toBe('输入')
    expect(result.displayMode).toBe('json')
    expect(result.formatted).toBe(JSON.stringify({ key: 'value' }, null, 2))
  })

  it('null 参数 → 返回空内容', () => {
    const result = formatToolParameters('execute', null)
    expect(result.formatted).toBe('')
  })

  it('字符串参数（JSON 格式）→ normalizeParams 解析', () => {
    const result = formatToolParameters('execute', '{"command":"ls"}')
    expect(result.formatted).toBe('ls')
  })

  it('字符串参数（非 JSON）→ value 字段包装', () => {
    const result = formatToolParameters('custom_tool', 'plain text')
    // 非内置工具走通用 formatContent，字符串直接 String()
    expect(result.formatted).toBe('plain text')
  })
})

// ==================== SubTask 9.9: formatToolResult ====================

describe('SubTask 9.9 formatToolResult', () => {
  it('read_file → 文件内容 + language 根据扩展名推断', () => {
    const result = formatToolResult('read_file', {
      file_path: '/test.js',
      content: 'const x = 1',
    })
    expect(result.displayMode).toBe('code')
    expect(result.language).toBe('javascript')
    expect(result.formatted).toBe('const x = 1')
  })

  it('read_file → 无 file_path 时根据内容启发式推断 language（JSON）', () => {
    const result = formatToolResult('read_file', '{"key":"value"}')
    expect(result.displayMode).toBe('code')
    expect(result.language).toBe('json')
  })

  it('read_file → 无 file_path 时根据内容启发式推断 language（XML）', () => {
    const result = formatToolResult('read_file', '<?xml version="1.0"?><root/>')
    expect(result.language).toBe('xml')
  })

  it('read_file → 无 file_path 时根据内容启发式推断 language（HTML）', () => {
    const result = formatToolResult('read_file', '<!DOCTYPE html><html></html>')
    expect(result.language).toBe('html')
  })

  it('read_file → 无 file_path 时根据内容启发式推断 language（Markdown）', () => {
    const result = formatToolResult('read_file', '# Title\n\ncontent')
    expect(result.language).toBe('markdown')
  })

  it('write_file → 写入状态（text 模式）', () => {
    const result = formatToolResult('write_file', { content: 'success' })
    expect(result.displayMode).toBe('text')
    expect(result.formatted).toBe('success')
  })

  it('write_file → 空结果时显示"已写入"', () => {
    const result = formatToolResult('write_file', null)
    expect(result.formatted).toBe('已写入')
  })

  it('edit_file → 编辑状态（text 模式）', () => {
    const result = formatToolResult('edit_file', { content: 'done' })
    expect(result.displayMode).toBe('text')
    expect(result.formatted).toBe('done')
  })

  it('edit_file → 空结果时显示"已编辑"', () => {
    const result = formatToolResult('edit_file', null)
    expect(result.formatted).toBe('已编辑')
  })

  it('execute → 命令输出（monospace 模式）', () => {
    const result = formatToolResult('execute', { output: 'total 0' })
    expect(result.displayMode).toBe('monospace')
    expect(result.language).toBe('bash')
    expect(result.formatted).toBe('total 0')
  })

  it('glob → 匹配文件列表（list 模式）', () => {
    const result = formatToolResult('glob', { content: 'file1\nfile2' })
    expect(result.displayMode).toBe('list')
    expect(result.formatted).toBe('file1\nfile2')
  })

  it('grep → 匹配行（monospace 模式）', () => {
    const result = formatToolResult('grep', { text: 'match line' })
    expect(result.displayMode).toBe('monospace')
    expect(result.formatted).toBe('match line')
  })

  it('ls → 目录列表（list 模式）', () => {
    const result = formatToolResult('ls', { data: 'dir1\ndir2' })
    expect(result.displayMode).toBe('list')
    expect(result.formatted).toBe('dir1\ndir2')
  })

  it('write_todos → 写入状态（text 模式）', () => {
    const result = formatToolResult('write_todos', { content: 'ok' })
    expect(result.displayMode).toBe('text')
    expect(result.formatted).toBe('ok')
  })

  it('write_todos → 空结果时显示"已写入"', () => {
    const result = formatToolResult('write_todos', null)
    expect(result.formatted).toBe('已写入')
  })

  it('task → 任务状态（text 模式）', () => {
    const result = formatToolResult('task', { result: 'completed' })
    expect(result.displayMode).toBe('text')
    expect(result.formatted).toBe('completed')
  })

  it('shell_exec → 与 execute 共享结果格式化', () => {
    const result = formatToolResult('shell_exec', { output: 'root' })
    expect(result.displayMode).toBe('monospace')
    expect(result.language).toBe('bash')
    expect(result.formatted).toBe('root')
  })

  it('非内置工具 → 通用 formatContent 序列化（text 模式）', () => {
    const result = formatToolResult('custom_tool', { key: 'value' })
    expect(result.displayMode).toBe('text')
    expect(result.language).toBe('text')
    expect(result.formatted).toBe(JSON.stringify({ key: 'value' }, null, 2))
  })

  it('字符串结果 → 直接返回字符串', () => {
    const result = formatToolResult('execute', 'plain string output')
    expect(result.formatted).toBe('plain string output')
  })

  it('null 结果 → 返回空字符串', () => {
    const result = formatToolResult('execute', null)
    expect(result.formatted).toBe('')
  })
})

// ==================== getToolDisplayConfig ====================

describe('getToolDisplayConfig', () => {
  it('只读工具 → 返回 INTERNAL_DISPLAY_CONFIG（pending 符号为空字符串）', () => {
    const config = getToolDisplayConfig('ls')
    expect(config.icon).toBe('el-icon-cpu')
    expect(config.collapsible).toBe(true)
    expect(config.collapsedByDefault).toBe(false)
    // 只读工具 pending 符号为空字符串（与 ToolCallCard.vue default 分支一致）
    expect(config.statusSymbols.pending).toBe('')
    // 只读工具 running 使用 '…'
    expect(config.statusSymbols.running).toBe('…')
    expect(config.statusSymbols.completed).toBe('✓')
    expect(config.statusSymbols.failed).toBe('✗')
  })

  it('有副作用工具 → 返回 DEFAULT_DISPLAY_CONFIG（pending 符号为 ⏳）', () => {
    const config = getToolDisplayConfig('write_file')
    expect(config.statusSymbols.pending).toBe('⏳')
    expect(config.statusSymbols.running).toBe('⚡')
    expect(config.statusSymbols.completed).toBe('✓')
    expect(config.statusSymbols.failed).toBe('✗')
  })

  it('未注册的自定义工具 → 返回 DEFAULT_DISPLAY_CONFIG', () => {
    const config = getToolDisplayConfig('custom_tool')
    expect(config.statusSymbols.pending).toBe('⏳')
    expect(config.statusSymbols.running).toBe('⚡')
    expect(config.statusSymbols.completed).toBe('✓')
    expect(config.statusSymbols.failed).toBe('✗')
  })

  it('所有状态符号齐全（pending/running/completed/failed/waiting/timeout）', () => {
    const config = getToolDisplayConfig('execute')
    expect(config.statusSymbols).toHaveProperty('pending')
    expect(config.statusSymbols).toHaveProperty('running')
    expect(config.statusSymbols).toHaveProperty('completed')
    expect(config.statusSymbols).toHaveProperty('failed')
    expect(config.statusSymbols).toHaveProperty('waiting')
    expect(config.statusSymbols).toHaveProperty('timeout')
  })
})

// ==================== isReadonlyTool ====================

describe('isReadonlyTool', () => {
  it('只读工具返回 true（ls/glob/grep）', () => {
    expect(isReadonlyTool('ls')).toBe(true)
    expect(isReadonlyTool('glob')).toBe(true)
    expect(isReadonlyTool('grep')).toBe(true)
  })

  it('有副作用工具返回 false（write_file/execute/read_file 等）', () => {
    expect(isReadonlyTool('read_file')).toBe(false)
    expect(isReadonlyTool('write_file')).toBe(false)
    expect(isReadonlyTool('edit_file')).toBe(false)
    expect(isReadonlyTool('execute')).toBe(false)
    expect(isReadonlyTool('write_todos')).toBe(false)
    expect(isReadonlyTool('task')).toBe(false)
    expect(isReadonlyTool('shell_exec')).toBe(false)
  })

  it('自定义工具返回 false', () => {
    expect(isReadonlyTool('custom_tool')).toBe(false)
    expect(isReadonlyTool('')).toBe(false)
    expect(isReadonlyTool(null)).toBe(false)
  })
})

// ==================== inferLanguageFromPath ====================

describe('inferLanguageFromPath', () => {
  it('常见扩展名正确推断', () => {
    expect(inferLanguageFromPath('/test.js')).toBe('javascript')
    expect(inferLanguageFromPath('/test.ts')).toBe('typescript')
    expect(inferLanguageFromPath('/test.tsx')).toBe('typescript')
    expect(inferLanguageFromPath('/test.vue')).toBe('vue')
    expect(inferLanguageFromPath('/test.py')).toBe('python')
    expect(inferLanguageFromPath('/test.go')).toBe('go')
    expect(inferLanguageFromPath('/test.rs')).toBe('rust')
    expect(inferLanguageFromPath('/test.java')).toBe('java')
    expect(inferLanguageFromPath('/test.json')).toBe('json')
    expect(inferLanguageFromPath('/test.yaml')).toBe('yaml')
    expect(inferLanguageFromPath('/test.yml')).toBe('yaml')
    expect(inferLanguageFromPath('/test.md')).toBe('markdown')
    expect(inferLanguageFromPath('/test.html')).toBe('html')
    expect(inferLanguageFromPath('/test.css')).toBe('css')
    expect(inferLanguageFromPath('/test.sh')).toBe('bash')
    expect(inferLanguageFromPath('/test.sql')).toBe('sql')
  })

  it('特殊文件名推断（无扩展名）', () => {
    expect(inferLanguageFromPath('Dockerfile')).toBe('dockerfile')
    expect(inferLanguageFromPath('Makefile')).toBe('makefile')
    expect(inferLanguageFromPath('GNUMakefile')).toBe('makefile')
  })

  it('未知扩展名 → text', () => {
    expect(inferLanguageFromPath('/test.unknownext')).toBe('text')
  })

  it('无扩展名 → text', () => {
    expect(inferLanguageFromPath('/noextension')).toBe('text')
  })

  it('空路径 → text', () => {
    expect(inferLanguageFromPath('')).toBe('text')
    expect(inferLanguageFromPath(null)).toBe('text')
  })

  it('Windows 路径分隔符兼容', () => {
    expect(inferLanguageFromPath('C:\\Users\\test\\app.js')).toBe('javascript')
  })

  it('大小写不敏感', () => {
    expect(inferLanguageFromPath('/TEST.JS')).toBe('javascript')
    expect(inferLanguageFromPath('/DOCKERFILE')).toBe('dockerfile')
  })
})

// ==================== registerToolAdapter（自定义适配器注册） ====================

describe('registerToolAdapter', () => {
  it('注册自定义工具适配器 → formatToolParameters 使用自定义格式化器', () => {
    registerToolAdapter('my_custom_tool', {
      parameterFormatter: (params) => ({
        label: '自定义',
        formatted: `custom:${JSON.stringify(params)}`,
        displayMode: 'inline',
      }),
      resultFormatter: (result) => ({
        formatted: `result:${result}`,
        displayMode: 'text',
        language: 'text',
      }),
    })

    const paramResult = formatToolParameters('my_custom_tool', { key: 'val' })
    expect(paramResult.label).toBe('自定义')
    expect(paramResult.formatted).toBe('custom:{"key":"val"}')

    const resultResult = formatToolResult('my_custom_tool', 'output')
    expect(resultResult.formatted).toBe('result:output')
  })

  it('registerToolAdapter → 无效 toolName 抛错', () => {
    expect(() => registerToolAdapter('', {})).toThrow()
    expect(() => registerToolAdapter(null, {})).toThrow()
  })

  it('registerToolAdapter → 无效 adapterConfig 抛错', () => {
    expect(() => registerToolAdapter('test_tool', null)).toThrow()
    expect(() => registerToolAdapter('test_tool', 'not-object')).toThrow()
  })
})
