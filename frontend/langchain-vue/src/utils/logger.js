const isDev = import.meta.env.DEV

export const logger = {
  log: (...args) => {
    if (isDev) {
      console.log(...args)
    }
  },
  warn: (...args) => {
    if (isDev) {
      console.warn(...args)
    }
  },
  error: (...args) => {
    console.error(...args)
  },
  info: (...args) => {
    if (isDev) {
      console.info(...args)
    }
  },
  debug: (...args) => {
    if (isDev) {
      console.debug(...args)
    }
  },
  group: (...args) => {
    if (isDev) {
      console.group(...args)
    }
  },
  groupEnd: () => {
    if (isDev) {
      console.groupEnd()
    }
  },
}

export default logger
