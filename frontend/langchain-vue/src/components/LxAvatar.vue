<script setup>
import { computed } from 'vue'

const props = defineProps({
  src: {
    type: String,
    default: ''
  },
  size: {
    type: [Number, String],
    default: 'medium',
    validator: (v) => typeof v === 'number' || ['large', 'medium', 'small', 'mini'].includes(v)
  },
  shape: {
    type: String,
    default: 'circle',
    validator: (v) => ['circle', 'square'].includes(v)
  },
  icon: {
    type: [String, Object],
    default: null
  },
  alt: {
    type: String,
    default: 'avatar'
  }
})

const sizeMap = {
  large: 40,
  medium: 32,
  small: 24,
  mini: 16
}

const sizeValue = computed(() => {
  if (typeof props.size === 'number') {
    return props.size
  }
  return sizeMap[props.size] || 32
})

const classes = computed(() => [
  'lx-avatar',
  `lx-avatar--${props.shape}`,
  `lx-avatar--${typeof props.size === 'string' ? props.size : 'custom'}`
])

const style = computed(() => ({
  width: `${sizeValue.value}px`,
  height: `${sizeValue.value}px`,
  fontSize: `${sizeValue.value / 2}px`
}))
</script>

<template>
  <div :class="classes" :style="style">
    <img
      v-if="src"
      :src="src"
      :alt="alt"
      class="lx-avatar__image"
      @error="handleError"
    />
    <el-icon v-else-if="icon" class="lx-avatar__icon">
      <component :is="icon" />
    </el-icon>
    <slot v-else />
  </div>
</template>

<style scoped>
.lx-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background-color: var(--el-color-info-light-7);
  color: var(--el-color-info);
  font-weight: 500;
}

.lx-avatar--circle {
  border-radius: 50%;
}

.lx-avatar--square {
  border-radius: 4px;
}

.lx-avatar__image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.lx-avatar__icon {
  width: 50%;
  height: 50%;
}
</style>
