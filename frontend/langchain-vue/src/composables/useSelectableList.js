import { toValue } from 'vue'

/**
 * 选择列表状态机（spec 组10：tool-selector 三列表统一）
 *
 * 收敛 SkillList / LangchainToolList / McpServerList 共同的
 * isSelected / toggle / selectAll / clear / 删除后反选 / diff 选中新项 逻辑。
 * 与父级的双向同步语义保持不变：所有变更统一经 `emit('update:selected', 新数组)` 上报，
 * 组件自身不持有选中副本，选中真源始终在父级（props.selected）。
 *
 * 键形态差异（name vs tool.name vs `skill_` 前缀包装）经 `getKey` 回调注入。
 * `items` 支持两种形态：
 * - 扁平数组（或返回数组的 ref/computed/getter）：单组，如 langchainTools / mcpServers；
 * - 数组的数组：多组（如 SkillList 的 [技能, 技能包]）。多组间独立求键、独立 diff，
 *   与旧实现逐集合 diff 语义严格一致（避免跨组键碰撞导致的语义漂移）；
 *   selectAll 时按组序展平（与旧实现的展开顺序一致）。
 */
export function useSelectableList({ items, getKey = (item) => item?.name, selected, emit }) {
  /** 读取父级当前选中数组（getter/ref/数组均可），非数组时安全回退为空数组 */
  const resolveSelected = () => {
    const raw = toValue(selected)
    return Array.isArray(raw) ? raw : []
  }

  /** 归一化条目来源：扁平数组 → 单组；数组的数组 → 多组；其余 → 无条目 */
  const normalizeGroups = (value) => {
    const raw = toValue(value)
    if (!Array.isArray(raw)) return []
    return raw.some(Array.isArray) ? raw : [raw]
  }

  /** 判断某键是否已选中 */
  const isSelected = (key) => resolveSelected().includes(key)

  /** 切换某键选中态：已选 → 移除，未选 → 追加（均 emit 新数组，不改动父级原数组） */
  const toggle = (key) => {
    const current = resolveSelected()
    if (current.includes(key)) {
      emit('update:selected', current.filter((k) => k !== key))
    } else {
      emit('update:selected', [...current, key])
    }
  }

  /** 全选：按组序展平全部条目键集合后整体 emit */
  const selectAll = () => {
    const keys = normalizeGroups(items).flatMap((group, groupIndex) =>
      group.map((item) => getKey(item, groupIndex)),
    )
    emit('update:selected', keys)
  }

  /** 清空选中 */
  const clear = () => {
    emit('update:selected', [])
  }

  /** 反选（删除条目后调用）：移除指定键并 emit 新数组（键不存在时仍 emit，与旧 filter 行为一致） */
  const deselect = (key) => {
    emit('update:selected', resolveSelected().filter((k) => k !== key))
  }

  /**
   * 自动选中新出现的条目（上传/创建成功后调用）：
   * 按组与旧列表快照 diff（键经 getKey 求取），新增键追加到当前选中尾部，
   * 存在新增时 emit 一次；无新增不 emit（与旧实现一致）。
   *
   * @param {Array|Array<Array>} newItems 最新条目列表（扁平或多组）
   * @param {Array|Array<Array>} oldItems 刷新前的条目列表快照（结构需与 newItems 同构）
   */
  const autoSelectNew = (newItems, oldItems) => {
    const newGroups = normalizeGroups(newItems)
    const oldGroups = normalizeGroups(oldItems)
    const addedKeys = []
    newGroups.forEach((group, groupIndex) => {
      const oldKeys = new Set((oldGroups[groupIndex] ?? []).map((item) => getKey(item, groupIndex)))
      for (const item of group) {
        const key = getKey(item, groupIndex)
        if (!oldKeys.has(key)) {
          addedKeys.push(key)
        }
      }
    })
    if (addedKeys.length > 0) {
      emit('update:selected', [...resolveSelected(), ...addedKeys])
    }
  }

  return { isSelected, toggle, selectAll, clear, deselect, autoSelectNew }
}
