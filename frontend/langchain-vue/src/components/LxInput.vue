<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  modelValue: {
    type: [String, Number],
    default: ''
  },
  type: {
    type: String,
    default: 'text'
  },
  placeholder: {
    type: String,
    default: ''
  },
  disabled: {
    type: Boolean,
    default: false
  },
  readonly: {
    type: Boolean,
    default: false
  },
  clearable: {
    type: Boolean,
    default: false
  },
  showPassword: {
    type: Boolean,
    default: false
  },
  size: {
    type: String,
    default: 'medium',
    validator: (v) => ['large', 'medium', 'small', 'mini'].includes(v)
  },
  prefixIcon: {
    type: [String, Object],
    default: null
  },
  suffixIcon: {
    type: [String, Object],
    default: null
  }
})

const emit = defineEmits(['update:modelValue', 'input', 'change', 'focus', 'blur', 'clear'])

const focused = ref(false)
const showPwdVisible = ref(false)

const inputValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const handleInput = (event) => {
  emit('input', event.target.value)
}

const handleChange = (event) => {
  emit('change', event.target.value)
}

const handleFocus = (event) => {
  focused.value = true
  emit('focus', event)
}

const handleBlur = (event) => {
  focused.value = false
  emit('blur', event)
}

const handleClear = () => {
  emit('update:modelValue', '')
  emit('clear')
}

const togglePasswordVisible = () => {
  showPwdVisible.value = !showPwdVisible.value
}

const classes = computed(() => [
  'lx-input',
  `lx-input--${props.size}`,
  {
    'is-disabled': props.disabled,
    'is-readonly': props.readonly,
    'is-focused': focused.value,
    'has-prefix': props.prefixIcon,
    'has-suffix': props.suffixIcon || props.clearable || props.showPassword,
  }
])
</script>

<template>
  <div :class="classes">
    <div v-if="prefixIcon" class="lx-input__prefix">
      <slot name="prefix">
        <el-icon class="lx-input__icon">
          <component :is="prefixIcon" />
        </el-icon>
      </slot>
    </div>
    <input
      v-model="inputValue"
      :type="showPassword ? (showPwdVisible ? 'text' : 'password') : type"
      :class="['lx-input__inner']"
      :placeholder="placeholder"
      :disabled="disabled"
      :readonly="readonly"
      @input="handleInput"
      @change="handleChange"
      @focus="handleFocus"
      @blur="handleBlur"
    />
    <div v-if="suffixIcon || clearable || showPassword" class="lx-input__suffix">
      <el-icon
        v-if="clearable && inputValue"
        class="lx-input__icon lx-input__clear"
        @click="handleClear"
      >
        <Close />
      </el-icon>
      <el-icon
        v-if="showPassword"
        class="lx-input__icon lx-input__password"
        @click="togglePasswordVisible"
      >
        <View v-if="!showPwdVisible" />
        <Hide v-else />
      </el-icon>
      <slot v-else name="suffix" />
    </div>
  </div>
</template>

<style scoped>
.lx-input {
  position: relative;
  display: inline-flex;
  align-items: center;
  width: 100%;
  font-size: 14px;
}

.lx-input__prefix,
.lx-input__suffix {
  position: absolute;
  display: flex;
  align-items: center;
  color: var(--el-text-color-placeholder);
  pointer-events: none;
}

.lx-input__prefix {
  left: 8px;
}

.lx-input__suffix {
  right: 8px;
}

.lx-input__inner {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  font-size: inherit;
  color: var(--el-text-color-regular);
  background-color: var(--el-bg-color);
  outline: none;
  transition: border-color 0.2s;
}

.lx-input__inner:focus {
  border-color: var(--el-color-primary);
}

.lx-input.has-prefix .lx-input__inner {
  padding-left: 32px;
}

.lx-input.has-suffix .lx-input__inner {
  padding-right: 32px;
}

.lx-input.is-disabled .lx-input__inner {
  background-color: var(--el-fill-color-light);
  color: var(--el-text-color-placeholder);
  cursor: not-allowed;
}

.lx-input__icon {
  cursor: pointer;
  pointer-events: auto;
}

.lx-input__clear:hover,
.lx-input__password:hover {
  color: var(--el-color-primary);
}

.lx-input--large .lx-input__inner {
  padding: 10px 14px;
}

.lx-input--small .lx-input__inner {
  padding: 6px 10px;
}

.lx-input--mini .lx-input__inner {
  padding: 4px 8px;
}
</style>
