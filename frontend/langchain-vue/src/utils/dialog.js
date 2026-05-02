import { ElMessageBox } from 'element-plus'

export async function confirmDelete(message, options = {}) {
  return ElMessageBox.confirm(message, '', {
    confirmButtonText: options.confirmButtonText || '删除',
    cancelButtonText: '取消',
    type: 'warning',
    customClass: 'confirm-dialog-danger',
    showClose: true,
    closeOnClickModal: false,
    closeOnPressEscape: true,
    roundButton: false,
    ...options,
  })
}

export async function confirmLogout() {
  return ElMessageBox.confirm('确定要退出登录吗？', '', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'info',
    showClose: true,
    closeOnClickModal: false,
  })
}

export async function confirmAction(message, options = {}) {
  const isDanger = options.type === 'warning' || options.type === 'delete'
  
  return ElMessageBox.confirm(message, '', {
    confirmButtonText: options.confirmButtonText || (isDanger ? '确定' : '确定'),
    cancelButtonText: '取消',
    type: isDanger ? 'warning' : 'info',
    customClass: isDanger ? 'confirm-dialog-danger' : '',
    showClose: true,
    closeOnClickModal: false,
    ...options,
  })
}

export default {
  confirmDelete,
  confirmLogout,
  confirmAction,
}
