const DEFAULT_API_BASE_URL = '/api/v1'

const API_VERSIONS = {
  v1: '/api/v1',
  current: '/api/v1'
}

const getApiBaseUrl = () => {
  if (typeof window !== 'undefined') {
    const saved = localStorage.getItem('lc-studylab-api-url')
    if (saved && !saved.startsWith('/api')) {
      return saved
    }
  }
  return DEFAULT_API_BASE_URL
}

const settings = {
  Host: '',
  get API_BASE_URL() {
    return getApiBaseUrl()
  },
  set API_BASE_URL(value) {
    if (typeof window !== 'undefined') {
      localStorage.setItem('lc-studylab-api-url', value)
    }
  },

  API_VERSIONS,

  HOME_PAGE: {
    REDIRECT_TO_CHAT: false,
    ENABLE_QUICK_START: true
  },

  API_VALIDATION: {
    MESSAGE_MAX_LENGTH: 10000,
    CHAT_HISTORY_MAX_ITEMS: 50,
    SESSION_ID_PATTERN: /^[a-f0-9-]{36}$/,
    ALLOWED_MODES: ['basic-agent', 'rag', 'workflow', 'deep-research', 'guarded'],
    BATCH_CREATE_MAX_ITEMS: 50,
  }
}

export default settings
