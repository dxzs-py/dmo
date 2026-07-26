import { config } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// 全局注册 Pinia 测试实例
setActivePinia(createPinia())

// 全局 stub Element Plus 组件/方法，避免在单元测试中渲染真实 UI
config.global.stubs = {
  'el-message': true,
  'el-message-box': true,
}
