// logger 测试桩：node 环境无 import.meta.env（vite 专属），单元测试重定向使用
export const logger = {
  debug: () => {},
  info: () => {},
  warn: (...args) => console.warn('[test-logger]', ...args),
  error: (...args) => console.error('[test-logger]', ...args),
}
