<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Edit, Delete, Refresh, View,
} from '@element-plus/icons-vue'
import { toolsAPI } from '@/api/tools'
import { useToolsStore } from '../../stores/tools'
import MarkdownRenderer from '../common/MarkdownRenderer.vue'

const props = defineProps({
  selected: { type: Array, required: true },
  withPopoverLock: { type: Function, required: true },
  previewVisible: { type: Boolean, default: false },
})

const emit = defineEmits({
  'update:selected': (val) => Array.isArray(val),
  'update:previewVisible': (val) => typeof val === 'boolean',
  edit: () => true,
})

const toolsStore = useToolsStore()

// ── 本地可写代理：将 props.selected 转为可写形式（v-model 透传） ──
const selectedLocal = computed({
  get: () => props.selected,
  set: (val) => emit('update:selected', val),
})

// ── previewVisible 双向代理（父组件 watch 此状态以锁定 popover） ──
const previewVisibleLocal = computed({
  get: () => props.previewVisible,
  set: (val) => emit('update:previewVisible', val),
})

// ── 数据（从 store 读取） ──
const skills = computed(() => toolsStore.skills)
const skillPackages = computed(() => toolsStore.skillPackages)
const skillLoading = computed(() => toolsStore.skillLoading)

// ── Computeds ──
const systemSkills = computed(() => toolsStore.systemSkills)
const userSkills = computed(() => toolsStore.userSkills)

// ── Skill 预览状态（Tab 内部维护标题与内容，可见性通过 v-model 同步给父组件） ──
const skillPreviewTitle = ref('')
const skillPreviewContent = ref('')

// ── 数据获取（委托给 store） ──
const fetchSkills = (force = false) => toolsStore.loadSkills(force)
const fetchSkillPackages = (force = false) => toolsStore.loadSkillPackages(force)

// ── 选择操作 ──
const isSkillSelected = (name) => selectedLocal.value.includes(name)

const toggleSkill = (name) => {
  const idx = selectedLocal.value.indexOf(name)
  if (idx >= 0) {
    selectedLocal.value = selectedLocal.value.filter(n => n !== name)
  } else {
    selectedLocal.value = [...selectedLocal.value, name]
  }
}

const selectAllSkills = () => {
  selectedLocal.value = [
    ...skills.value.map(s => s.name),
    ...skillPackages.value.map(p => `skill_${p.name}`),
  ]
}

const clearSkills = () => {
  selectedLocal.value = []
}

// ── Skill 操作 ──
const handleDeleteSkill = (skill) => props.withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(
      `确定删除技能 "${skill.name}" 吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await toolsAPI.deleteSkill({ name: skill.name })
    ElMessage.success(`技能 "${skill.name}" 已删除`)
    selectedLocal.value = selectedLocal.value.filter(n => n !== skill.name)
    toolsStore.invalidateSkills()
    fetchSkills()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

// ── 编辑（委托给父组件打开对话框） ──
const handleEditSkill = (skill) => {
  emit('edit', skill)
}

// ── Skill Packages 操作 ──
const isSkillPackageSelected = (name) => {
  return selectedLocal.value.includes(`skill_${name}`)
}

const toggleSkillPackage = (name) => {
  const toolName = `skill_${name}`
  const idx = selectedLocal.value.indexOf(toolName)
  if (idx >= 0) {
    selectedLocal.value = selectedLocal.value.filter(n => n !== toolName)
  } else {
    selectedLocal.value = [...selectedLocal.value, toolName]
  }
}

const deleteSkillPackage = (name) => props.withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(`确定删除技能包 "${name}"？`, '确认删除', { type: 'warning' })
    await toolsAPI.deleteSkillPackage({ name })
    ElMessage.success(`技能包 "${name}" 已删除`)
    const toolName = `skill_${name}`
    selectedLocal.value = selectedLocal.value.filter(n => n !== toolName)
    fetchSkillPackages(true)
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
    }
  }
})

const viewSkillPackageDetail = async (name) => {
  try {
    const res = await toolsAPI.getSkillPackageDetail({ name })
    const instructions = res.data?.data?.instructions || '无指令内容'
    skillPreviewTitle.value = `技能包: ${name}`
    skillPreviewContent.value = instructions
    previewVisibleLocal.value = true
  } catch (e) {
    ElMessage.error('获取详情失败: ' + (e.response?.data?.message || e.message))
  }
}
</script>

<template>
  <div class="tab-batch-actions">
    <el-button size="small" link type="primary" @click="selectAllSkills">全选</el-button>
    <el-button size="small" link type="info" @click="clearSkills">清空</el-button>
  </div>

  <div v-if="skillLoading" class="tool-loading">
    <el-icon class="is-loading"><Refresh /></el-icon> 加载中...
  </div>
  <div v-else-if="skills.length === 0 && skillPackages.length === 0" class="tool-empty">暂无技能</div>
  <div v-else class="tool-list">
    <div v-if="skillPackages.length > 0" class="tool-group">
      <div class="tool-group-label">技能包</div>
      <div
        v-for="pkg in skillPackages"
        :key="pkg.name"
        :class="['tool-item', { selected: isSkillPackageSelected(pkg.name) }]"
      >
        <div class="tool-info">
          <span class="tool-name">
            {{ pkg.name }}
            <el-tag v-if="pkg.source === 'system'" size="small" type="info">系统</el-tag>
            <el-tag size="small" type="success" effect="plain">顾问</el-tag>
          </span>
          <span v-if="pkg.description" class="tool-desc">{{ pkg.description }}</span>
        </div>
        <div class="tool-actions">
          <el-button size="small" circle text title="查看详情" @click.stop="viewSkillPackageDetail(pkg.name)">
            <el-icon><View /></el-icon>
          </el-button>
          <el-button
v-if="pkg.source !== 'system'" size="small" circle text type="danger"
            title="删除" @click.stop="deleteSkillPackage(pkg.name)">
            <el-icon><Delete /></el-icon>
          </el-button>
          <el-checkbox :model-value="isSkillPackageSelected(pkg.name)" @change="toggleSkillPackage(pkg.name)" />
        </div>
      </div>
    </div>

    <div v-if="systemSkills.length > 0" class="tool-group">
      <div class="tool-group-label">系统预置</div>
      <div
        v-for="skill in systemSkills"
        :key="skill.name"
        :class="['tool-item', { selected: isSkillSelected(skill.name) }]"
      >
        <div class="tool-info">
          <span class="tool-name">
            {{ skill.name.replace(/^skill_/, '') }}
            <el-tag v-if="skill.mode" size="small" :type="skill.mode === 'pipeline' ? 'primary' : skill.mode === 'advisor' ? 'success' : 'warning'" effect="plain">
              {{ skill.mode === 'pipeline' ? '管线' : skill.mode === 'advisor' ? '顾问' : '混合' }}
            </el-tag>
          </span>
          <span v-if="skill.description" class="tool-desc">{{ skill.description }}</span>
        </div>
        <div class="tool-actions">
          <el-checkbox
            :model-value="isSkillSelected(skill.name)"
            @change="toggleSkill(skill.name)"
          />
        </div>
      </div>
    </div>

    <div v-if="userSkills.length > 0" class="tool-group">
      <div class="tool-group-label">自定义</div>
      <div
        v-for="skill in userSkills"
        :key="skill.name"
        :class="['tool-item', { selected: isSkillSelected(skill.name) }]"
      >
        <div class="tool-info">
          <span class="tool-name">
            {{ skill.name.replace(/^skill_/, '') }}
            <span class="tool-tag-custom">自定义</span>
            <el-tag v-if="skill.mode" size="small" :type="skill.mode === 'pipeline' ? 'primary' : skill.mode === 'advisor' ? 'success' : 'warning'" effect="plain">
              {{ skill.mode === 'pipeline' ? '管线' : skill.mode === 'advisor' ? '顾问' : '混合' }}
            </el-tag>
          </span>
          <span v-if="skill.description" class="tool-desc">{{ skill.description }}</span>
        </div>
        <div class="tool-actions">
          <el-button
            :icon="Edit"
            size="small"
            circle
            text
            type="primary"
            title="编辑"
            @click.stop="handleEditSkill(skill)"
          />
          <el-button
            :icon="Delete"
            size="small"
            circle
            text
            type="danger"
            title="删除"
            @click.stop="handleDeleteSkill(skill)"
          />
          <el-checkbox
            :model-value="isSkillSelected(skill.name)"
            @change="toggleSkill(skill.name)"
          />
        </div>
      </div>
    </div>
  </div>

  <el-dialog
    v-model="previewVisibleLocal"
    :title="skillPreviewTitle"
    width="640px"
    :close-on-click-modal="true"
    append-to-body
  >
    <MarkdownRenderer :content="skillPreviewContent" />
  </el-dialog>
</template>
