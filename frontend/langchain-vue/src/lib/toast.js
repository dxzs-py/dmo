import { createApp, reactive } from 'vue'

const toastState = reactive({
  toasts: [],
  globalId: 0
})

let toastApp = null
let toastComponent = null

export function initToast(app) {
  const ToastComponent = {
    name: 'GlobalToast',
    setup() {
      const iconMap = {
        success: 'Success',
        warning: 'Warning',
        error: 'Error',
        info: 'Info',
      }

      const typeClass = {
        success: 'toast-success',
        warning: 'toast-warning',
        error: 'toast-error',
        info: 'toast-info',
      }

      const removeToast = (id) => {
        const index = toastState.toasts.findIndex(t => t.id === id)
        if (index !== -1) {
          toastState.toasts.splice(index, 1)
        }
      }

      const addToast = (options) => {
        const id = ++toastState.globalId
        const toast = {
          id,
          type: options.type || 'info',
          title: options.title || '',
          message: options.message || '',
          duration: options.duration ?? 3000,
          icon: iconMap[options.type] || 'Info',
          class: typeClass[options.type] || 'toast-info',
          closable: options.closable ?? true,
        }

        toastState.toasts.push(toast)

        if (toast.duration > 0) {
          setTimeout(() => {
            removeToast(id)
          }, toast.duration)
        }

        return id
      }

      const success = (message, options = {}) => addToast({ ...options, type: 'success', message })
      const error = (message, options = {}) => addToast({ ...options, type: 'error', message })
      const warning = (message, options = {}) => addToast({ ...options, type: 'warning', message })
      const info = (message, options = {}) => addToast({ ...options, type: 'info', message })

      return {
        toasts: toastState.toasts,
        removeToast,
        success,
        error,
        warning,
        info,
      }
    },
    template: `
      <Teleport to="body">
        <div class="global-toast-container position-top-right">
          <TransitionGroup name="toast">
            <div
              v-for="toast in toasts"
              :key="toast.id"
              class="toast-item"
              :class="toast.class"
            >
              <div class="toast-icon">
                <el-icon :size="20">
                  <component :is="toast.icon" />
                </el-icon>
              </div>
              <div class="toast-content">
                <div v-if="toast.title" class="toast-title">{{ toast.title }}</div>
                <div class="toast-message">{{ toast.message }}</div>
              </div>
              <button
                v-if="toast.closable"
                class="toast-close"
                @click="removeToast(toast.id)"
              >
                <el-icon :size="16"><Close /></el-icon>
              </button>
            </div>
          </TransitionGroup>
        </div>
      </Teleport>
    `
  }

  toastApp = createApp(ToastComponent)
  toastComponent = toastApp.mount(document.createElement('div'))
  document.body.appendChild(toastComponent.$el)

  app.config.globalProperties.$toast = {
    success: toastComponent.success,
    error: toastComponent.error,
    warning: toastComponent.warning,
    info: toastComponent.info,
    remove: toastComponent.removeToast,
  }

  app.provide('toast', {
    success: toastComponent.success,
    error: toastComponent.error,
    warning: toastComponent.warning,
    info: toastComponent.info,
    remove: toastComponent.removeToast,
  })
}

export const toast = {
  success: (message, options) => toastComponent?.success(message, options),
  error: (message, options) => toastComponent?.error(message, options),
  warning: (message, options) => toastComponent?.warning(message, options),
  info: (message, options) => toastComponent?.info(message, options),
  remove: (id) => toastComponent?.removeToast(id),
}

export default toast
