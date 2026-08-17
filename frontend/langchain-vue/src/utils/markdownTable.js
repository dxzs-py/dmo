/**
 * GFM 表格规范化工具（Markdown 渲染前预处理）
 *
 * 背景：LLM 生成的 GFM 表格经常列数不一致（表头/分隔行/数据行列数不同）。
 * marked 严格 GFM 要求分隔行列数 = 表头列数，不匹配时整块降级为纯文本段落，
 * 导致主消息中的表格无法渲染。本工具在 marked 解析前将畸形表格规范化：
 * 各列统一到最大列数，分隔行缺列用 `---` 补齐（空单元格会被 marked 拒绝），
 * 表头/数据行缺列用空单元格补齐。列数已一致的合法表格原样输出。
 *
 * 边界：仅处理以 `|` 首尾包裹且紧邻分隔行（`:?-+:?`）的表格块；
 * 围栏代码块（``` / ~~~）内的 `|` 行原样透传，不参与规范化；
 * 无表格或已合法表格返回原文。
 *
 * @param {string} markdown - 原始 markdown 文本
 * @returns {string} 规范化后的 markdown 文本
 */
export function normalizeGfmTables(markdown) {
  if (typeof markdown !== 'string' || markdown.length === 0) return markdown
  const lines = markdown.split('\n')
  const result = []
  let inFence = false
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    // 围栏代码块状态跟踪：块内原样透传，不做表格识别
    if (/^\s*(```|~~~)/.test(line)) {
      result.push(line)
      inFence = !inFence
      i++
      continue
    }
    if (!inFence && isTableLine(line) && isDelimiterLine(lines[i + 1])) {
      const block = []
      let j = i
      while (j < lines.length && isTableLine(lines[j])) {
        block.push(lines[j])
        j++
      }
      result.push(normalizeTableBlock(block).join('\n'))
      i = j
      continue
    }
    result.push(line)
    i++
  }
  return result.join('\n')
}

/**
 * 判断是否为候选表格行（`|` 首尾包裹，长度大于 1）
 * @param {string|undefined} line
 * @returns {boolean}
 */
const isTableLine = (line) => {
  if (!line) return false
  const trimmed = line.trim()
  return trimmed.length > 1 && trimmed.startsWith('|') && trimmed.endsWith('|')
}

/**
 * 判断是否为 GFM 分隔行（所有单元格匹配 `:?-+:?`，即横线可带左右冒号）
 * @param {string|undefined} line
 * @returns {boolean}
 */
const isDelimiterLine = (line) => {
  if (!line) return false
  const trimmed = line.trim()
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|')) return false
  const cells = splitCells(trimmed)
  return cells.length > 0 && cells.every((cell) => /^:?-{1,}:?$/.test(cell))
}

/**
 * 将表格行拆分为单元格（处理 `\|` 转义，避免管道符破坏单元格）
 * @param {string} line
 * @returns {string[]}
 */
const splitCells = (line) => {
  const body = line.trim().slice(1, -1)
  const cells = []
  let current = ''
  for (let k = 0; k < body.length; k++) {
    const ch = body[k]
    if (ch === '\\' && body[k + 1] === '|') {
      current += '\\|'
      k++
    } else if (ch === '|') {
      cells.push(current.trim())
      current = ''
    } else {
      current += ch
    }
  }
  cells.push(current.trim())
  return cells
}

/**
 * 将单元格数组重新拼装为表格行
 * @param {string[]} cells
 * @returns {string}
 */
const joinCells = (cells) => `| ${cells.join(' | ')} |`

/**
 * 规范化单个表格块：统一最大列数，分隔行缺列以 `---` 补齐
 * @param {string[]} block - 表格块原始行（header + delimiter + data rows）
 * @returns {string[]} 规范化后的行
 */
const normalizeTableBlock = (block) => {
  const rows = block.map(splitCells)
  const headerCols = rows[0].length
  const maxCols = Math.max(...rows.map((row) => row.length))
  // 列数一致（含分隔行）时原样输出，避免对合法表格做无谓改写
  if (maxCols === headerCols && rows.every((row) => row.length === headerCols)) {
    return block
  }
  return rows.map((row, rowIndex) => {
    const padded = Array.from({ length: maxCols }, (_, col) => row[col] ?? '')
    // 分隔行（rowIndex === 1）缺列用 '---' 补齐（空单元格会被 marked 拒绝）
    if (rowIndex === 1) {
      for (let col = 0; col < maxCols; col++) {
        if (!/^:?-{1,}:?$/.test(padded[col])) padded[col] = '---'
      }
    }
    return joinCells(padded)
  })
}
