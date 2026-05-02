/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          50: 'var(--el-color-primary-light-9, #ecf5ff)',
          100: 'var(--el-color-primary-light-8, #d9ecff)',
          200: 'var(--el-color-primary-light-7, #b3d8ff)',
          300: 'var(--el-color-primary-light-5, #79bbff)',
          400: 'var(--el-color-primary-light-3, #53a8ff)',
          500: 'var(--el-color-primary, #409eff)',
          600: 'var(--el-color-primary-dark-2, #337ecc)',
        },
      },
      fontSize: {
        'xs': '12px',
        'sm': '13px',
        'base': '14px',
        'lg': '16px',
        'xl': '18px',
      },
    },
  },
  plugins: [],
  corePlugins: {
    preflight: false,
  },
}
