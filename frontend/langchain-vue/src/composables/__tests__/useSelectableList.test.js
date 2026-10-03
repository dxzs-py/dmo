/**
 * useSelectableList 单元测试（spec 组10：tool-selector 选择列表统一）
 *
 * 被测：src/composables/useSelectableList.js
 * 锁定迁移前后行为等价：toggle/selectAll/clear/deselect/autoSelectNew 的
 * emit 载荷与旧内联实现逐一对应；键形态差异（name vs `skill_` 前缀包装）经 getKey 注入。
 *
 * 运行方式（frontend/langchain-vue 目录）：
 *   npx vitest run src/composables/__tests__/useSelectableList.test.js
 */
import { describe, it, expect, vi } from 'vitest'
import { useSelectableList } from '../useSelectableList.js'

/** 模拟父级 selected（真源在父级，emit 后由测试手动回写以模拟 props 回流） */
function setup({ items, getKey, selected = [] }) {
  const emit = vi.fn()
  let current = selected
  const api = useSelectableList({
    items,
    getKey,
    selected: () => current,
    emit,
  })
  const flush = () => {
    // 与父级 @update:selected="selectedSkills = $event" 的回流语义一致
    const last = emit.mock.calls[emit.mock.calls.length - 1]
    if (last) current = last[1]
  }
  return { emit, ...api, flush, getSelected: () => current }
}

describe('useSelectableList - isSelected / toggle', () => {
  it('isSelected 判定当前选中键', () => {
    const { isSelected } = setup({ items: [], selected: ['a', 'skill_b'] })
    expect(isSelected('a')).toBe(true)
    expect(isSelected('skill_b')).toBe(true)
    expect(isSelected('c')).toBe(false)
  })

  it('toggle 未选键 → 追加并 emit 新数组，不改动父级原数组', () => {
    const source = ['a']
    const { emit, toggle } = setup({ items: [], selected: source })
    toggle('b')
    expect(emit).toHaveBeenCalledTimes(1)
    expect(emit).toHaveBeenCalledWith('update:selected', ['a', 'b'])
    expect(source).toEqual(['a'])
  })

  it('toggle 已选键 → 移除并 emit 新数组', () => {
    const { emit, toggle } = setup({ items: [], selected: ['a', 'b'] })
    toggle('a')
    expect(emit).toHaveBeenCalledWith('update:selected', ['b'])
  })

  it('emit 回流后再次 toggle 基于最新父级选中', () => {
    const { emit, toggle, flush, getSelected } = setup({ items: [], selected: ['a'] })
    toggle('b')
    flush()
    toggle('b')
    flush()
    expect(getSelected()).toEqual(['a'])
    expect(emit).toHaveBeenCalledTimes(2)
  })
})

describe('useSelectableList - selectAll / clear / deselect', () => {
  it('扁平条目（Langchain/MCP 形态）：键 = name', () => {
    const items = [{ name: 't1' }, { name: 't2' }]
    const { emit, selectAll } = setup({ items, getKey: (t) => t.name })
    selectAll()
    expect(emit).toHaveBeenCalledWith('update:selected', ['t1', 't2'])
  })

  it('多组条目（SkillList 形态）：技能键原值 + 技能包 skill_ 前缀包装，按组序展平', () => {
    const skills = [{ name: 'skill_web' }, { name: 'skill_deep_research' }]
    const packages = [{ name: 'code_review' }, { name: 'data_analysis' }]
    const items = () => [skills, packages]
    const getKey = (item, groupIndex) => (groupIndex === 1 ? `skill_${item.name}` : item.name)
    const { emit, selectAll } = setup({ items, getKey })
    selectAll()
    expect(emit).toHaveBeenCalledWith('update:selected', [
      'skill_web',
      'skill_deep_research',
      'skill_code_review',
      'skill_data_analysis',
    ])
  })

  it('items 支持直接传数组（单组）', () => {
    const { emit, selectAll } = setup({ items: [{ name: 's1' }], getKey: (s) => s.name })
    selectAll()
    expect(emit).toHaveBeenCalledWith('update:selected', ['s1'])
  })

  it('clear → emit 空数组', () => {
    const { emit, clear } = setup({ items: [], selected: ['a'] })
    clear()
    expect(emit).toHaveBeenCalledWith('update:selected', [])
  })

  it('deselect 移除指定键（删除条目后的反选）', () => {
    const { emit, deselect } = setup({ items: [], selected: ['a', 'skill_b'] })
    deselect('skill_b')
    expect(emit).toHaveBeenCalledWith('update:selected', ['a'])
  })

  it('deselect 键不存在时仍 emit（与旧 filter 行为一致，保持新数组引用）', () => {
    const { emit, deselect } = setup({ items: [], selected: ['a'] })
    deselect('ghost')
    expect(emit).toHaveBeenCalledTimes(1)
    expect(emit).toHaveBeenCalledWith('update:selected', ['a'])
    expect(emit.mock.calls[0][1]).not.toBe(['a'])
  })
})

describe('useSelectableList - autoSelectNew', () => {
  it('扁平 diff（LangchainToolList 上传形态）：仅追加新增键，顺序在选中尾部', () => {
    const oldItems = [{ name: 't1' }, { name: 't2' }]
    const newItems = [{ name: 't2' }, { name: 't3' }, { name: 't1' }]
    const { emit, autoSelectNew } = setup({
      items: [],
      getKey: (t) => t.name,
      selected: ['t1'],
    })
    autoSelectNew(newItems, oldItems)
    expect(emit).toHaveBeenCalledTimes(1)
    expect(emit).toHaveBeenCalledWith('update:selected', ['t1', 't3'])
  })

  it('无新增 → 不 emit（与旧实现一致）', () => {
    const oldItems = [{ name: 't1' }]
    const { emit, autoSelectNew } = setup({ items: [], getKey: (t) => t.name, selected: ['t1'] })
    autoSelectNew([{ name: 't1' }], oldItems)
    expect(emit).not.toHaveBeenCalled()
  })

  it('多组 diff（SkillList 创建形态）：技能与技能包各自独立 diff，键序 技能在前', () => {
    const getKey = (item, groupIndex) => (groupIndex === 1 ? `skill_${item.name}` : item.name)
    const oldSkills = [{ name: 'skill_web' }]
    const oldPackages = [{ name: 'code_review' }]
    const newSkills = [{ name: 'skill_web' }, { name: 'skill_pipeline' }]
    const newPackages = [{ name: 'code_review' }, { name: 'data_analysis' }]
    const { emit, autoSelectNew } = setup({ items: [], getKey, selected: ['skill_web'] })
    autoSelectNew([newSkills, newPackages], [oldSkills, oldPackages])
    expect(emit).toHaveBeenCalledTimes(1)
    expect(emit).toHaveBeenCalledWith('update:selected', [
      'skill_web',
      'skill_pipeline',
      'skill_data_analysis',
    ])
  })

  it('跨组键碰撞语义锁定：旧技能包包装键已存在时，新增技能仍按组独立判定为新增', () => {
    // 旧技能包 foo 已存在（键 skill_foo）；本次新增同名技能 skill_foo —— 旧实现按集合独立 diff 会选中它
    const getKey = (item, groupIndex) => (groupIndex === 1 ? `skill_${item.name}` : item.name)
    const { emit, autoSelectNew } = setup({ items: [], getKey, selected: [] })
    autoSelectNew([[{ name: 'skill_foo' }]], [[{ name: 'foo' }]])
    expect(emit).toHaveBeenCalledWith('update:selected', ['skill_foo'])
  })

  it('多组无新增 → 不 emit', () => {
    const getKey = (item, groupIndex) => (groupIndex === 1 ? `skill_${item.name}` : item.name)
    const { emit, autoSelectNew } = setup({ items: [], getKey, selected: [] })
    autoSelectNew([[{ name: 'skill_web' }], [{ name: 'code_review' }]], [[{ name: 'skill_web' }], [{ name: 'code_review' }]])
    expect(emit).not.toHaveBeenCalled()
  })
})
