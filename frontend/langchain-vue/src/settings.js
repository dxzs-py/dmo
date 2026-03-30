const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000/api'

const getApiBaseUrl = () => {
  if (typeof window !== 'undefined') {
    const saved = localStorage.getItem('lc-studylab-api-url')
    if (saved) {
      return saved
    }
  }
  return DEFAULT_API_BASE_URL
}

const settings = {
  Host: 'http://127.0.0.1:8000',
  get API_BASE_URL() {
    return getApiBaseUrl()
  },
  set API_BASE_URL(value) {
    if (typeof window !== 'undefined') {
      localStorage.setItem('lc-studylab-api-url', value)
    }
  },
  
  HOME_PAGE: {
    REDIRECT_TO_CHAT: false,
    ENABLE_QUICK_START: true
  }
}

export default settings
