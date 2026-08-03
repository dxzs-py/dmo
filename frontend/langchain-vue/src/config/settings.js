const DEFAULT_API_BASE_URL = '/api/v1'

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
  host: '',
  get apiBaseUrl() {
    return getApiBaseUrl()
  },
  set apiBaseUrl(value) {
    if (typeof window !== 'undefined') {
      localStorage.setItem('lc-studylab-api-url', value)
    }
  },

  homePage: {
    redirectToChat: false,
    enableQuickStart: true
  },

  apiValidation: {
    messageMaxLength: 10000,
    chatHistoryMaxItems: 50,
    sessionIdPattern: /^[a-f0-9-]{36}$/,
    allowedModes: ['agent', 'deep-research'],
    batchCreateMaxItems: 50,
  }
}

export default settings
