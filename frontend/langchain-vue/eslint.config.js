import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import pluginOxlint from 'eslint-plugin-oxlint'
import skipFormatting from 'eslint-config-prettier/flat'

export default defineConfig([
  {
    name: 'app/files-to-lint',
    files: ['**/*.{vue,js,mjs,jsx}'],
  },

  globalIgnores(['**/dist/**', '**/dist-ssr/**', '**/coverage/**', 'src/auto-imports.d.ts', 'src/components.d.ts']),

  {
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },

  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],

  ...pluginOxlint.buildFromOxlintConfigFile('.oxlintrc.json'),

  {
    rules: {
      'no-empty': ['error', { allowEmptyCatch: false }],
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      // 前端标识符统一 camelCase（工作区命名规范）；object properties 不检查——
      // 网络边界（sessionTransformers 出入站转换、协议 snake_case 键）豁免
      'camelcase': ['error', { properties: 'never', ignoreDestructuring: true }],
      'vue/multi-word-component-names': 'off',
      'vue/no-v-html': 'warn'
    },
  },

  skipFormatting,
])
