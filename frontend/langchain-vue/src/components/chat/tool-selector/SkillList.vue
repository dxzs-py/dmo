<script setup>
import { ref, computed, watch, inject } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, View, Refresh } from '@element-plus/icons-vue'
import { toolsAPI } from '@/api/tools'
import { useToolsStore } from '@/stores/tools'
import SkillUploadDialog from '../SkillUploadDialog.vue'
import MarkdownRenderer from '../../common/MarkdownRenderer.vue'

const props = defineProps({
  selected: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits({
  'update:selected': (val) => Array.isArray(val),
  'dialog-visibility-change': (val) => typeof val === 'boolean',
})

const toolsStore = useToolsStore()
/** 父容器（popover 拥有者）注入的 popover 锁包裹函数：异步操作期间锁定 popover 不关闭 */
const withPopoverLock = inject('toolSelectorWithPopoverLock')

// ── 数据（从 store 读取） ──
const skills = computed(() => toolsStore.skills)
const skillPackages = computed(() => toolsStore.skillPackages)
const skillLoading = computed(() => toolsStore.skillLoading)
const systemSkills = computed(() => toolsStore.systemSkills)
const userSkills = computed(() => toolsStore.userSkills)

const fetchSkills = (force = false) => toolsStore.loadSkills(force)
const fetchSkillPackages = (force = false) => toolsStore.loadSkillPackages(force)

// ── 对话框 ──
const showSkillUploadDialog = ref(false)
const editingSkill = ref(null)

// ── Skill 预览 ──
const skillPreviewVisible = ref(false)
const skillPreviewTitle = ref('')
const skillPreviewContent = ref('')

// 上报对话框可见性（上传 + 预览），父容器据此锁定 popover
watch([showSkillUploadDialog, skillPreviewVisible], ([a, b]) => {
  emit('dialog-visibility-change', a || b)
})

/** 供父容器 header「上传/添加」按钮调用：以新增模式打开上传对话框 */
const openUploadDialog = () => {
  editingSkill.value = null
  showSkillUploadDialog.value = true
}
defineExpose({ openUploadDialog })

// ── 选择操作 ──
const isSkillSelected = (name) => props.selected.includes(name)

const toggleSkill = (name) => {
  const idx = props.selected.indexOf(name)
  if (idx >= 0) {
    emit('update:selected', props.selected.filter(n => n !== name))
  } else {
    emit('update:selected', [...props.selected, name])
  }
}

const selectAllSkills = () => {
  emit('update:selected', [
    ...skills.value.map(s => s.name),
    ...skillPackages.value.map(p => `skill_${p.name}`),
  ])
}

const clearSkills = () => {
  emit('update:selected', [])
}

const handleDeleteSkill = (skill) => withPopoverLock(async () => {
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
    emit('update:selected', props.selected.filter(n => n !== skill.name))
    toolsStore.invalidateSkills()
    fetchSkills()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

// ── Skill Packages 操作 ──
const isSkillPackageSelected = (name) => {
  return props.selected.includes(`skill_${name}`)
}

const toggleSkillPackage = (name) => {
  const toolName = `skill_${name}`
  const idx = props.selected.indexOf(toolName)
  if (idx >= 0) {
    emit('update:selected', props.selected.filter(n => n !== toolName))
  } else {
    emit('update:selected', [...props.selected, toolName])
  }
}

const deleteSkillPackage = (name) => withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(`确定删除技能包 "${name}"？`, '确认删除', { type: 'warning' })
    await toolsAPI.deleteSkillPackage({ name })
    ElMessage.success(`技能包 "${name}" 已删除`)
    const toolName = `skill_${name}`
    emit('update:selected', props.selected.filter(n => n !== toolName))
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
    skillPreviewVisible.value = true
  } catch (e) {
    ElMessage.error('获取详情失败: ' + (e.response?.data?.message || e.message))
  }
}

const handleEditSkill = (skill) => {
  editingSkill.value = { ...skill }
  showSkillUploadDialog.value = true
}

const handleSkillCreateSuccess = async () => {
  showSkillUploadDialog.value = false
  editingSkill.value = null
  const oldSkillNames = new Set(skills.value.map(s => s.name))
  const oldPkgNames = new Set(skillPackages.value.map(p => p.name))
  toolsStore.invalidateSkills()
  await fetchSkills(true)
  await fetchSkillPackages(true)
  const newSkillNames = skills.value.filter(s => !oldSkillNames.has(s.name)).map(s => s.name)
  const newPkgNames = skillPackages.value.filter(p => !oldPkgNames.has(p.name)).map(p => `skill_${p.name}`)
  const newSelected = [...newSkillNames, ...newPkgNames]
  if (newSelected.length > 0) {
    emit('update:selected', [...props.selected, ...newSelected])
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
            v-if="pkg.source !== 'system'"
            size="small"
            circle
            text
            type="danger"
            title="删除"
            @click.stop="deleteSkillPackage(pkg.name)"
          >
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

  <SkillUploadDialog
    v-model="showSkillUploadDialog"
    :editing-skill="editingSkill"
    @success="handleSkillCreateSuccess"
  />
  <el-dialog
    v-model="skillPreviewVisible"
    :title="skillPreviewTitle"
    width="640px"
    :close-on-click-modal="true"
    append-to-body
  >
    <MarkdownRenderer :content="skillPreviewContent" />
  </el-dialog>
</template>
