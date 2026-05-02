export function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text)
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
  return Promise.resolve()
}

export function downloadFile(content, filename, mimeType = 'text/plain') {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export function sanitizeHtml(html) {
  const div = document.createElement('div')
  div.textContent = html
  return div.innerHTML
}

export function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }
  return text.replace(/[&<>"']/g, m => map[m])
}

export function isValidUrl(string) {
  try { new URL(string); return true } catch { return false }
}

export function extractUrls(text) {
  const urlRegex = /(https?:\/\/[^\s<]+)/g
  const urls = []
  let match
  while ((match = urlRegex.exec(text)) !== null) urls.push(match[1])
  return urls
}

export function parseMarkdownCodeBlocks(content) {
  const codeBlockRegex = /```(\w+)?\n([\s\S]*?)```/g
  const blocks = []
  let match
  while ((match = codeBlockRegex.exec(content)) !== null) {
    blocks.push({ language: match[1] || 'text', code: match[2], fullMatch: match[0] })
  }
  return blocks
}
