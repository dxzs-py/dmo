/**
 * @file 工具适配层
 *
 * 抽象自 ToolCallCard.vue 中针对工具的硬编码渲染逻辑。
 * 提供统一的工具显示配置、参数格式化、结果格式化能力，
 * 支持运行时注册自定义工具适配器。
 *
 * 设计原则：
 * - 纯函数模块，不依赖 Vue 响应式 API
 * - 只读工具清单与 types/index.js 的 READONLY_TOOL_NAMES 对齐
 * - 注册表优先，回退到只读默认配置，再回退到通用默认配置
 * - displayConfig 只控制样式，审批面板由 approvalData 驱动（关注点分离）
 *
 * 提取来源：components/chat/ToolCallCard.vue
 *   - isReadonlyTool 判定（原 isInternalTool）
 *   - 只读工具默认展开（L97）
 *   - 只读状态符号 ✓/✗/… （L100-107）
 *   - formatContent 通用序列化（L169-175）
 *   - 内部字段过滤 approved_by_middleware（L51-60）
 */

import { READONLY_TOOL_NAMES } from '@/types'

// ==================== 注册表与默认配置 ====================

/** 注册表：toolName → adapterConfig */
const adapterRegistry = new Map()

/**
 * 默认显示配置（非内置工具使用）
 */
const DEFAULT_DISPLAY_CONFIG = {
  icon: 'el-icon-cpu',
  collapsible: true,
  collapsedByDefault: false,
  statusSymbols: {
    pending: '⏳',
    running: '⚡',
    completed: '✓',
    failed: '✗',
    waiting: '...',
    timeout: '⏱',
  },
}

/**
 * 内置工具显示配置（提取自 ToolCallCard.vue）
 *
 * 与 DEFAULT_DISPLAY_CONFIG 的差异：
 * - statusSymbols.pending 为空字符串（组件 default 分支返回 ''）
 * - statusSymbols.running 使用 '…'（horizontal ellipsis），与组件 L104 一致
 */
const INTERNAL_DISPLAY_CONFIG = {
  icon: 'el-icon-cpu',
  collapsible: true,
  // 内置工具默认展开（ToolCallCard.vue L97）
  collapsedByDefault: false,
  statusSymbols: {
    pending: '',
    running: '…',
    completed: '✓',
    failed: '✗',
    waiting: '...',
    timeout: '⏱',
  },
}

// ==================== 内部辅助函数 ====================

/**
 * 规范化参数为对象形式
 * 处理 string（JSON）/Array/Object/null 等多种入参
 * @param {*} params - 原始参数
 * @returns {Object} 规范化后的对象
 */
function normalizeParams(params) {
  if (params == null) return {}
  if (typeof params === 'string') {
    const trimmed = params.trim()
    if (!trimmed) return {}
    try {
      const parsed = JSON.parse(trimmed)
      return typeof parsed === 'object' && parsed !== null
        ? (Array.isArray(parsed) ? { items: parsed } : parsed)
        : { value: parsed }
    } catch {
      return { value: params }
    }
  }
  if (Array.isArray(params)) return { items: params }
  if (typeof params === 'object') return params
  return { value: params }
}

/**
 * 通用内容序列化（提取自 ToolCallCard.vue formatContent L169-175）
 * @param {*} content - 原始内容
 * @returns {string}
 */
function formatContent(content) {
  if (content == null) return ''
  if (typeof content === 'object') {
    try {
      return JSON.stringify(content, null, 2)
    } catch {
      return String(content)
    }
  }
  return String(content)
}

/**
 * 从结果对象中提取文本内容
 * 兼容多种字段命名：content/text/output/result/data
 * @param {*} result - 原始结果
 * @returns {string}
 */
function extractText(result) {
  if (result == null) return ''
  if (typeof result === 'string') return result
  if (typeof result === 'object') {
    if (typeof result.content === 'string') return result.content
    if (typeof result.text === 'string') return result.text
    if (typeof result.output === 'string') return result.output
    if (typeof result.result === 'string') return result.result
    if (typeof result.data === 'string') return result.data
    return formatContent(result)
  }
  return String(result)
}

/**
 * 截断字符串到指定长度，超出追加省略号
 * @param {string} str
 * @param {number} maxLen
 * @returns {string}
 */
function truncate(str, maxLen) {
  if (!str) return ''
  const s = String(str)
  return s.length > maxLen ? s.slice(0, maxLen) + '…' : s
}

/** 文件扩展名 → 语法高亮 language 映射 */
const EXT_LANG_MAP = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript',
  jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  vue: 'vue',
  py: 'python',
  rb: 'ruby',
  go: 'go',
  rs: 'rust',
  java: 'java',
  kt: 'kotlin',
  php: 'php',
  c: 'c', h: 'c',
  cpp: 'cpp', cc: 'cpp', cxx: 'cpp', hpp: 'cpp',
  cs: 'csharp',
  swift: 'swift',
  html: 'html', htm: 'html',
  css: 'css', scss: 'scss', sass: 'sass', less: 'less',
  json: 'json',
  yaml: 'yaml', yml: 'yaml',
  toml: 'toml',
  xml: 'xml',
  md: 'markdown', markdown: 'markdown',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  bat: 'bat', cmd: 'bat',
  ps1: 'powershell',
  sql: 'sql',
  ini: 'ini', conf: 'ini',
  txt: 'text',
}

/**
 * 根据文件路径推断语法高亮 language
 * 支持无扩展名特殊文件（Dockerfile/Makefile）
 * @param {string} filePath - 文件路径
 * @returns {string} language 标识符
 */
export function inferLanguageFromPath(filePath) {
  if (!filePath || typeof filePath !== 'string') return 'text'
  const base = filePath.split(/[\\/]/).pop() || ''
  const lower = base.toLowerCase()
  if (lower === 'dockerfile') return 'dockerfile'
  if (lower === 'makefile' || lower === 'gnumakefile') return 'makefile'
  const dotIdx = lower.lastIndexOf('.')
  if (dotIdx < 0) return 'text'
  const ext = lower.slice(dotIdx + 1)
  return EXT_LANG_MAP[ext] || 'text'
}

/**
 * 根据内容启发式推断 language（result 不含路径时的回退策略）
 * @param {string} text - 内容文本
 * @returns {string}
 */
function inferLanguageFromContent(text) {
  if (!text) return 'text'
  const trimmed = text.trim()
  if (!trimmed) return 'text'
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    try {
      JSON.parse(trimmed)
      return 'json'
    } catch {
      // fallthrough
    }
  }
  if (trimmed.startsWith('<?xml')) return 'xml'
  if (trimmed.startsWith('<!DOCTYPE html') || trimmed.startsWith('<html')) return 'html'
  if (/^#{1,6}\s/.test(trimmed)) return 'markdown'
  return 'text'
}

// ==================== 内置工具参数格式化器 ====================

/**
 * 每个内置工具的参数格式化器
 * 返回 { label, formatted, displayMode } 结构
 *
 * displayMode 取值约定：
 * - 'inline'   单行展示（如 file_path）
 * - 'json'     JSON 序列化展示
 * - 'diff'     差异展示（如 edit_file）
 * - 'command'  命令行展示
 * - 'list'     列表展示
 */
const internalParameterFormatters = {
  /** read_file: 显示 file_path（含可选 offset/limit） */
  read_file: (params) => {
    const p = normalizeParams(params)
    const filePath = p.file_path || p.path || ''
    const parts = []
    if (filePath) parts.push(filePath)
    if (p.offset != null) parts.push(`offset=${p.offset}`)
    if (p.limit != null) parts.push(`limit=${p.limit}`)
    return {
      label: '文件路径',
      formatted: parts.join(' · '),
      displayMode: 'inline',
    }
  },

  /** write_file: 显示 file_path + content（content 截断预览） */
  write_file: (params) => {
    const p = normalizeParams(params)
    const filePath = p.file_path || p.path || ''
    const content = p.content != null ? p.content : ''
    const contentStr = typeof content === 'string' ? content : formatContent(content)
    return {
      label: '写入文件',
      formatted: JSON.stringify({
        file_path: filePath,
        content: truncate(contentStr, 200),
      }, null, 2),
      displayMode: 'json',
    }
  },

  /** edit_file: 显示 file_path + old_string + new_string（差异形式） */
  edit_file: (params) => {
    const p = normalizeParams(params)
    const filePath = p.file_path || p.path || ''
    const oldStr = p.old_string != null ? p.old_string : (p.old_str != null ? p.old_str : '')
    const newStr = p.new_string != null ? p.new_string : (p.new_str != null ? p.new_str : '')
    return {
      label: '编辑文件',
      formatted: JSON.stringify({
        file_path: filePath,
        old_string: truncate(String(oldStr), 200),
        new_string: truncate(String(newStr), 200),
      }, null, 2),
      displayMode: 'diff',
    }
  },

  /** execute: 显示 command（shell 命令） */
  execute: (params) => {
    const p = normalizeParams(params)
    const cmd = p.command || p.cmd || p.shell_command || ''
    return {
      label: '命令',
      formatted: String(cmd),
      displayMode: 'command',
    }
  },

  /** glob: 显示 pattern + path */
  glob: (params) => {
    const p = normalizeParams(params)
    const pattern = p.pattern || p.glob || ''
    const path = p.path || p.cwd || ''
    const parts = []
    if (pattern) parts.push(`pattern=${pattern}`)
    if (path) parts.push(`path=${path}`)
    return {
      label: '匹配模式',
      formatted: parts.join(' · '),
      displayMode: 'inline',
    }
  },

  /** grep: 显示 pattern + path */
  grep: (params) => {
    const p = normalizeParams(params)
    const pattern = p.pattern || p.regex || p.query || ''
    const path = p.path || p.cwd || ''
    const parts = []
    if (pattern) parts.push(`pattern=${pattern}`)
    if (path) parts.push(`path=${path}`)
    return {
      label: '搜索模式',
      formatted: parts.join(' · '),
      displayMode: 'inline',
    }
  },

  /** ls: 显示 path（目录） */
  ls: (params) => {
    const p = normalizeParams(params)
    const path = p.path || p.dir || p.directory || ''
    return {
      label: '目录',
      formatted: String(path),
      displayMode: 'inline',
    }
  },

  /** write_todos: 显示 todos 列表 */
  write_todos: (params) => {
    const p = normalizeParams(params)
    const todos = p.todos || p.items || []
    const list = Array.isArray(todos) ? todos : [todos]
    return {
      label: '待办列表',
      formatted: JSON.stringify(list, null, 2),
      displayMode: 'list',
    }
  },

  /** task: 显示 task 描述 */
  task: (params) => {
    const p = normalizeParams(params)
    const desc = p.description || p.task || p.prompt || ''
    return {
      label: '任务描述',
      formatted: String(desc),
      displayMode: 'inline',
    }
  },
}

// ==================== 内置工具结果格式化器 ====================

/**
 * 每个内置工具的结果格式化器
 * 返回 { formatted, displayMode, language } 结构
 *
 * displayMode 取值约定：
 * - 'text'       普通文本
 * - 'code'       代码（需配合 language 进行语法高亮）
 * - 'monospace'  等宽字体展示（如命令输出）
 * - 'list'       列表展示（如文件列表）
 */
const internalResultFormatters = {
  /** read_file: 文件内容，language 根据扩展名推断 */
  read_file: (result) => {
    const text = extractText(result)
    let language = 'text'
    // 优先：result 对象本身携带 file_path，按扩展名推断
    if (result && typeof result === 'object' && (result.file_path || result.path)) {
      language = inferLanguageFromPath(result.file_path || result.path)
    } else {
      // 回退：根据内容启发式推断
      language = inferLanguageFromContent(text)
    }
    return {
      formatted: text,
      displayMode: 'code',
      language,
    }
  },

  /** write_file: 写入状态 */
  write_file: (result) => {
    const text = extractText(result)
    return {
      formatted: text || '已写入',
      displayMode: 'text',
      language: 'text',
    }
  },

  /** edit_file: 编辑状态 */
  edit_file: (result) => {
    const text = extractText(result)
    return {
      formatted: text || '已编辑',
      displayMode: 'text',
      language: 'text',
    }
  },

  /** execute: 命令输出（monospace） */
  execute: (result) => {
    const text = extractText(result)
    return {
      formatted: text,
      displayMode: 'monospace',
      language: 'bash',
    }
  },

  /** glob: 匹配文件列表 */
  glob: (result) => {
    const text = extractText(result)
    return {
      formatted: text,
      displayMode: 'list',
      language: 'text',
    }
  },

  /** grep: 匹配行（monospace） */
  grep: (result) => {
    const text = extractText(result)
    return {
      formatted: text,
      displayMode: 'monospace',
      language: 'text',
    }
  },

  /** ls: 目录列表 */
  ls: (result) => {
    const text = extractText(result)
    return {
      formatted: text,
      displayMode: 'list',
      language: 'text',
    }
  },

  /** write_todos: 写入状态 */
  write_todos: (result) => {
    const text = extractText(result)
    return {
      formatted: text || '已写入',
      displayMode: 'text',
      language: 'text',
    }
  },

  /** task: 任务状态/输出 */
  task: (result) => {
    const text = extractText(result)
    return {
      formatted: text,
      displayMode: 'text',
      language: 'text',
    }
  },
}

// ==================== 工具适配器注册 ====================

/**
 * 注册所有工具适配器
 *
 * 遍历 internalParameterFormatters 的所有 key，为每个工具挂载 displayConfig
 * 以及对应的 parameterFormatter / resultFormatter。
 *
 * 显示配置选择策略：
 * - 只读工具（ls/glob/grep）：使用 INTERNAL_DISPLAY_CONFIG（简化折叠样式）
 * - 有副作用工具（write_file/edit_file/execute/read_file/write_todos/task）：
 *   使用 DEFAULT_DISPLAY_CONFIG（完整展开样式，含审批面板）
 *
 * 注意：displayConfig 只控制样式，审批面板的显示由 ToolCallCard 中的
 * approvalData 驱动，与 displayConfig 无关。
 */
function registerInternalAdapters() {
  // 遍历所有有格式化器的工具（不限于只读工具）
  const allToolNames = new Set([
    ...Object.keys(internalParameterFormatters),
    ...Object.keys(internalResultFormatters),
  ])

  for (const toolName of allToolNames) {
    const parameterFormatter = internalParameterFormatters[toolName]
    const resultFormatter = internalResultFormatters[toolName]
    const isReadonly = READONLY_TOOL_NAMES.has(toolName)
    adapterRegistry.set(toolName, {
      displayConfig: isReadonly ? INTERNAL_DISPLAY_CONFIG : DEFAULT_DISPLAY_CONFIG,
      parameterFormatter,
      resultFormatter,
    })
  }

  // shell_exec 不在格式化器列表中（它是后端审批工具名），
  // 但与 execute 共享相同的参数/结果格式化逻辑，因此注册为同名适配器。
  adapterRegistry.set('shell_exec', {
    displayConfig: DEFAULT_DISPLAY_CONFIG,
    parameterFormatter: internalParameterFormatters.execute,
    resultFormatter: internalResultFormatters.execute,
  })
}

// 模块加载时自动注册内置工具适配器
registerInternalAdapters()

// ==================== 公共 API ====================

/**
 * 获取工具显示配置
 *
 * 优先级：注册表 adapter.displayConfig → INTERNAL_DISPLAY_CONFIG（内置工具）→ DEFAULT_DISPLAY_CONFIG
 * statusSymbols 进行深合并（adapter 的覆盖默认的）
 *
 * @param {string} toolName - 工具名称
 * @returns {Object} 显示配置（含 icon/collapsible/collapsedByDefault/statusSymbols）
 */
export function getToolDisplayConfig(toolName) {
  const adapter = adapterRegistry.get(toolName)
  const displayConfig = adapter?.displayConfig

  if (displayConfig) {
    return {
      ...DEFAULT_DISPLAY_CONFIG,
      ...displayConfig,
      statusSymbols: {
        ...DEFAULT_DISPLAY_CONFIG.statusSymbols,
        ...(displayConfig.statusSymbols || {}),
      },
    }
  }

  if (READONLY_TOOL_NAMES.has(toolName)) {
    return {
      ...DEFAULT_DISPLAY_CONFIG,
      ...INTERNAL_DISPLAY_CONFIG,
      statusSymbols: {
        ...DEFAULT_DISPLAY_CONFIG.statusSymbols,
        ...INTERNAL_DISPLAY_CONFIG.statusSymbols,
      },
    }
  }

  return {
    ...DEFAULT_DISPLAY_CONFIG,
    statusSymbols: { ...DEFAULT_DISPLAY_CONFIG.statusSymbols },
  }
}

/**
 * 格式化工具参数显示
 *
 * - 内置工具或已注册 adapter 的工具：调用对应的 parameterFormatter
 * - 其他工具：使用通用 formatContent 序列化（保留原始 JSON 行为）
 *
 * @param {string} toolName - 工具名称
 * @param {Object|Array|string|null} params - 原始参数
 * @returns {{label: string, formatted: string, displayMode: string}} 格式化后的参数显示对象
 */
export function formatToolParameters(toolName, params) {
  const adapter = adapterRegistry.get(toolName)
  if (adapter?.parameterFormatter) {
    return adapter.parameterFormatter(params)
  }
  // 非内置工具：返回原始 params 的通用序列化（与 ToolCallCard.vue formatContent 行为一致）
  return {
    label: '输入',
    formatted: formatContent(params),
    displayMode: 'json',
  }
}

/**
 * 格式化工具结果显示
 *
 * - 内置工具或已注册 adapter 的工具：调用对应的 resultFormatter
 * - 其他工具：使用通用 formatContent 序列化
 *
 * @param {string} toolName - 工具名称
 * @param {string|Object|null} result - 原始结果
 * @returns {{formatted: string, displayMode: string, language: string}} 格式化后的结果对象
 */
export function formatToolResult(toolName, result) {
  const adapter = adapterRegistry.get(toolName)
  if (adapter?.resultFormatter) {
    return adapter.resultFormatter(result)
  }
  return {
    formatted: formatContent(result),
    displayMode: 'text',
    language: 'text',
  }
}

/**
 * 注册自定义工具适配器
 *
 * 用于运行时扩展新工具的渲染规则。若 toolName 已存在，将覆盖原适配器。
 *
 * @param {string} toolName - 工具名称
 * @param {Object} adapterConfig - 适配器配置
 * @param {Object} [adapterConfig.displayConfig] - 显示配置（icon/collapsible/collapsedByDefault/statusSymbols）
 * @param {Function} [adapterConfig.parameterFormatter] - 参数格式化器 (params) => { label, formatted, displayMode }
 * @param {Function} [adapterConfig.resultFormatter] - 结果格式化器 (result) => { formatted, displayMode, language }
 */
export function registerToolAdapter(toolName, adapterConfig) {
  if (!toolName || typeof toolName !== 'string') {
    throw new Error('registerToolAdapter: toolName 必须为非空字符串')
  }
  if (!adapterConfig || typeof adapterConfig !== 'object') {
    throw new Error('registerToolAdapter: adapterConfig 必须为对象')
  }
  adapterRegistry.set(toolName, adapterConfig)
}

/**
 * 判断是否为只读工具
 *
 * 只读工具（ls/glob/grep）无副作用，后端不发起审批，
 * 前端使用简化折叠样式渲染。
 *
 * 注意：有副作用工具（write_file/execute 等）不在此集合中，
 * 其审批面板的显示由 approvalData 驱动。
 *
 * @param {string} toolName - 工具名称
 * @returns {boolean}
 */
export function isReadonlyTool(toolName) {
  return READONLY_TOOL_NAMES.has(toolName)
}

/** @deprecated 使用 isReadonlyTool 代替 */
export function isInternalTool(toolName) {
  return isReadonlyTool(toolName)
}
