<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  content: {
    type: String,
    default: ''
  },
  placement: {
    type: String,
    default: 'top',
    validator: (v) => ['top', 'bottom', 'left', 'right', 'top-start', 'top-end', 'bottom-start', 'bottom-end', 'left-start', 'left-end', 'right-start', 'right-end'].includes(v)
  },
  trigger: {
    type: String,
    default: 'hover',
    validator: (v) => ['hover', 'click', 'focus', 'manual'].includes(v)
  },
  disabled: {
    type: Boolean,
    default: false
  },
  transition: {
    type: String,
    default: 'fade'
  },
  openDelay: {
    type: Number,
    default: 0
  },
  closeDelay: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['visible-change'])

const isVisible = ref(false)
const triggerRef = ref(null)
const popperRef = ref(null)
const popperStyle = ref({})

let openTimeout = null
let closeTimeout = null

const placementMap = {
  top: 'bottom-start',
  bottom: 'top-start',
  left: 'right-start',
  right: 'left-start',
  'top-start': 'bottom-start',
  'top-end': 'bottom-end',
  'bottom-start': 'top-start',
  'bottom-end': 'top-end',
  'left-start': 'right-start',
  'left-end': 'right-end',
  'right-start': 'left-start',
  'right-end': 'left-end'
}

const actualPlacement = computed(() => placementMap[props.placement] || props.placement)

const showPopper = () => {
  if (props.disabled) return

  clearTimeout(closeTimeout)
  openTimeout = setTimeout(() => {
    isVisible.value = true
    emit('visible-change', true)
    updatePopperPosition()
  }, props.openDelay)
}

const hidePopper = () => {
  clearTimeout(openTimeout)
  closeTimeout = setTimeout(() => {
    isVisible.value = false
    emit('visible-change', false)
  }, props.closeDelay)
}

const togglePopper = () => {
  if (isVisible.value) {
    hidePopper()
  } else {
    showPopper()
  }
}

const handleTriggerClick = () => {
  if (props.trigger === 'click') {
    togglePopper()
  }
}

const handleTriggerFocus = () => {
  if (props.trigger === 'focus') {
    showPopper()
  }
}

const handleTriggerBlur = () => {
  if (props.trigger === 'focus') {
    hidePopper()
  }
}

const updatePopperPosition = () => {
  if (!triggerRef.value) return

  const triggerRect = triggerRef.value.getBoundingClientRect()
  const popperWidth = 200
  const popperHeight = 100

  let top = 0
  let left = 0

  const placement = actualPlacement.value

  if (placement.startsWith('top')) {
    top = triggerRect.top - popperHeight - 8
    left = triggerRect.left
  } else if (placement.startsWith('bottom')) {
    top = triggerRect.bottom + 8
    left = triggerRect.left
  } else if (placement.startsWith('left')) {
    top = triggerRect.top
    left = triggerRect.left - popperWidth - 8
  } else if (placement.startsWith('right')) {
    top = triggerRect.top
    left = triggerRect.right + 8
  }

  if (placement.endsWith('start')) {
    left = triggerRect.left
  } else if (placement.endsWith('end')) {
    left = triggerRect.right - popperWidth
  }

  if (top < 0) top = 8
  if (left < 0) left = 8
  if (left + popperWidth > window.innerWidth) {
    left = window.innerWidth - popperWidth - 8
  }

  popperStyle.value = {
    top: `${top}px`,
    left: `${left}px`
  }
}

watch(() => props.disabled, (val) => {
  if (val) {
    isVisible.value = false
  }
})

onMounted(() => {
  window.addEventListener('resize', updatePopperPosition)
  window.addEventListener('scroll', updatePopperPosition, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updatePopperPosition)
  window.removeEventListener('scroll', updatePopperPosition, true)
  clearTimeout(openTimeout)
  clearTimeout(closeTimeout)
})
</script>

<template>
  <div ref="wrapperRef" class="lx-tooltip-wrapper">
    <div
      ref="triggerRef"
      class="lx-tooltip__trigger"
      @click="handleTriggerClick"
      @mouseenter="trigger === 'hover' && showPopper()"
      @mouseleave="trigger === 'hover' && hidePopper()"
      @focus="handleTriggerFocus"
      @blur="handleTriggerBlur"
    >
      <slot />
    </div>
    <Teleport to="body">
      <Transition :name="transition">
        <div
          v-if="isVisible && !disabled"
          ref="popperRef"
          class="lx-tooltip"
          :class="`lx-tooltip--${placement}`"
          :style="popperStyle"
          role="tooltip"
        >
          <div class="lx-tooltip__content">
            <slot name="content">
              {{ content }}
            </slot>
          </div>
          <div class="lx-tooltip__arrow" />
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.lx-tooltip-wrapper {
  display: inline-block;
}

.lx-tooltip {
  position: fixed;
  z-index: 2000;
  max-width: 200px;
}

.lx-tooltip__content {
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.5;
  color: #fff;
  background-color: rgba(0, 0, 0, 0.85);
  border-radius: 4px;
  word-break: break-word;
}

.lx-tooltip__arrow {
  position: absolute;
  width: 0;
  height: 0;
  border: 6px solid transparent;
}

.lx-tooltip--top .lx-tooltip__arrow {
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border-top-color: rgba(0, 0, 0, 0.85);
}

.lx-tooltip--bottom .lx-tooltip__arrow {
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  border-bottom-color: rgba(0, 0, 0, 0.85);
}

.lx-tooltip--left .lx-tooltip__arrow {
  left: 100%;
  top: 50%;
  transform: translateY(-50%);
  border-left-color: rgba(0, 0, 0, 0.85);
}

.lx-tooltip--right .lx-tooltip__arrow {
  right: 100%;
  top: 50%;
  transform: translateY(-50%);
  border-right-color: rgba(0, 0, 0, 0.85);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
