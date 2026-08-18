// Node ESM loader：将前端 `@/` 路径别名映射到 src/（单元测试专用）
// 用法：测试文件顶部 register 本 loader，再动态 import 被测模块。
import { existsSync } from 'node:fs'
import { resolve as pathResolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const srcRoot = pathResolve(fileURLToPath(import.meta.url), '../../..')

export async function resolve(specifier, context, next) {
  // node 环境无 vite 的 import.meta.env：logger 重定向到测试桩
  if (specifier === '@/utils/logger') {
    return { url: pathToFileURL(pathResolve(srcRoot, 'utils/__tests__/logger.stub.mjs')).href, shortCircuit: true }
  }
  if (specifier.startsWith('@/')) {
    const p = pathResolve(srcRoot, specifier.slice(2))
    const candidate = existsSync(p) ? p : `${p}.js`
    return { url: pathToFileURL(candidate).href, shortCircuit: true }
  }
  return next(specifier, context)
}
