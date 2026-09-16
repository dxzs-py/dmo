/**
 * buildResearchStartPayload 单元测试（Spec: 沙箱执行加固 / 任务级开关 3.4）
 *
 * 覆盖：沙箱开关默认值（false）、开关开启时 enableSandbox 透传、
 * 既有字段保持、经 toSnakeCase 后网络契约键 enable_sandbox 正确（camelCase → snake_case）。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
import { buildResearchStartPayload } from '../researchPayload.js'
import { toSnakeCase } from '../sessionTransformers.js'

const baseForm = () => ({
  query: '测试主题',
  enableWebSearch: true,
  enableSandbox: false,
  knowledgeBaseIds: ['kb-1'],
  providerId: 'openai',
  modelName: 'gpt-4o',
  useMcp: false,
  selectedMcpServers: [],
  selectedTools: ['web_search'],
})

const baseModelConfig = () => ({
  providerId: 'openai',
  modelName: 'gpt-4o',
  temperature: 0.7,
  maxTokens: 10240,
  specialParams: { thinking: { type: 'enabled' } },
})

describe('buildResearchStartPayload', () => {
  it('沙箱开关默认值：enableSandbox 缺省时负载中为 false', () => {
    const form = baseForm()
    delete form.enableSandbox
    const payload = buildResearchStartPayload({ form, modelConfig: baseModelConfig(), enableDeepThinking: true })
    expect(payload.enableSandbox).toBe(false)
  })

  it('沙箱开关关闭（false）时负载 enableSandbox 为 false', () => {
    const payload = buildResearchStartPayload({ form: baseForm(), modelConfig: baseModelConfig(), enableDeepThinking: true })
    expect(payload.enableSandbox).toBe(false)
  })

  it('沙箱开关开启（true）时负载 enableSandbox 为 true（参数透传）', () => {
    const form = baseForm()
    form.enableSandbox = true
    const payload = buildResearchStartPayload({ form, modelConfig: baseModelConfig(), enableDeepThinking: true })
    expect(payload.enableSandbox).toBe(true)
  })

  it('既有字段保持：query/enableWebSearch/enableDocAnalysis 等原样透传', () => {
    const form = baseForm()
    const payload = buildResearchStartPayload({ form, modelConfig: baseModelConfig(), enableDeepThinking: true })
    expect(payload.query).toBe('测试主题')
    expect(payload.enableWebSearch).toBe(true)
    expect(payload.enableDocAnalysis).toBe(true)
    expect(payload.knowledgeBaseIds).toEqual(['kb-1'])
    expect(payload.providerId).toBe('openai')
    expect(payload.modelName).toBe('gpt-4o')
    expect(payload.temperature).toBe(0.7)
    expect(payload.maxTokens).toBe(10240)
  })

  it('网络边界：enableSandbox 经 toSnakeCase 转为 enable_sandbox', () => {
    const form = baseForm()
    form.enableSandbox = true
    const payload = buildResearchStartPayload({ form, modelConfig: baseModelConfig(), enableDeepThinking: true })
    const network = toSnakeCase(payload)
    expect(network.enable_sandbox).toBe(true)
    expect(network.enable_sandbox).toBe(payload.enableSandbox)
  })
})
