import settings from '../settings';
import { useUserStore } from '../stores/user';

function validateEnhancedChatRequest(data) {
  const errors = [];

  if (!data.message || typeof data.message !== 'string') {
    errors.push('消息内容不能为空');
  } else {
    const trimmedMessage = data.message.trim();
    if (trimmedMessage.length === 0) {
      errors.push('消息内容不能为空或仅包含空格');
    } else if (trimmedMessage.length > settings.API_VALIDATION.MESSAGE_MAX_LENGTH) {
      errors.push(`消息内容不能超过${settings.API_VALIDATION.MESSAGE_MAX_LENGTH}个字符`);
    }
  }

  if (data.mode && !settings.API_VALIDATION.ALLOWED_MODES.includes(data.mode)) {
    errors.push(`不支持的模式: ${data.mode}`);
  }

  if (data.session_id && !settings.API_VALIDATION.SESSION_ID_PATTERN.test(data.session_id)) {
    errors.push('会话ID格式无效');
  }

  if (data.chat_history && Array.isArray(data.chat_history)) {
    if (data.chat_history.length > settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS) {
      errors.push(`聊天历史记录不能超过${settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS}条`);
    }

    data.chat_history.forEach((msg, index) => {
      if (!msg.role || !['user', 'assistant', 'system'].includes(msg.role)) {
        errors.push(`聊天历史第${index + 1}条消息的角色无效`);
      }
      if (msg.content && typeof msg.content === 'string' && msg.content.length > 50000) {
        errors.push(`聊天历史第${index + 1}条消息内容过长`);
      }
    });
  }

  return {
    valid: errors.length === 0,
    errors,
    sanitizedData: {
      ...data,
      message: data.message ? data.message.trim() : data.message,
    }
  };
}

export async function* chatStreamEnhanced(request) {
  const validation = validateEnhancedChatRequest(request);
  if (!validation.valid) {
    throw new Error(validation.errors.join('; '));
  }

  const url = `${settings.API_BASE_URL}/chat/stream/`;

  let response;

  try {
    const userStore = useUserStore();
    const headers = {
      'Content-Type': 'application/json',
    };
    if (userStore.token) {
      headers['Authorization'] = `Bearer ${userStore.token}`;
    }

    response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(validation.sanitizedData),
    });

    if (!response.ok) {
      const errorText = await response.text();
      let errorMessage;
      try {
        const errorJson = JSON.parse(errorText);
        errorMessage = errorJson.message || errorText;
      } catch {
        errorMessage = errorText;
      }
      throw new Error(`HTTP ${response.status}: ${errorMessage}`);
    }

    if (!response.body) {
      throw new Error('Response body is null');
    }
  } catch (error) {
    console.error('Failed to initiate enhanced chat stream:', error);
    throw error;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6);

          if (dataStr.trim()) {
            try {
              const data = JSON.parse(dataStr);
              yield data;
            } catch (parseError) {
              console.error('Failed to parse SSE data:', dataStr, parseError);
            }
          }
        }
      }
    }

    if (buffer.trim()) {
      if (buffer.startsWith('data: ')) {
        const dataStr = buffer.slice(6);
        if (dataStr.trim()) {
          try {
            const data = JSON.parse(dataStr);
            yield data;
          } catch (parseError) {
            console.error('Failed to parse remaining buffer:', dataStr, parseError);
          }
        }
      }
    }
  } catch (error) {
    console.error('Error reading stream:', error);
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export async function chatNonStreaming(request) {
  const validation = validateEnhancedChatRequest(request);
  if (!validation.valid) {
    throw new Error(validation.errors.join('; '));
  }

  const url = `${settings.API_BASE_URL}/chat/`;

  const userStore = useUserStore();
  const headers = {
    'Content-Type': 'application/json',
  };
  if (userStore.token) {
    headers['Authorization'] = `Bearer ${userStore.token}`;
  }

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(validation.sanitizedData),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Unknown error' }));
    throw new Error(error.error || `HTTP ${response.status}: ${response.statusText}`);
  }

  return response.json();
}

export async function healthCheck() {
  const userStore = useUserStore();
  const headers = {
    'Content-Type': 'application/json',
  };
  if (userStore.token) {
    headers['Authorization'] = `Bearer ${userStore.token}`;
  }

  const response = await fetch(`${settings.API_BASE_URL}/health/`, {
    method: 'GET',
    headers,
  });

  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`);
  }

  return response.json();
}

export async function getAvailableModes() {
  const userStore = useUserStore();
  const headers = {
    'Content-Type': 'application/json',
  };
  if (userStore.token) {
    headers['Authorization'] = `Bearer ${userStore.token}`;
  }

  const response = await fetch(`${settings.API_BASE_URL}/chat/modes/`, {
    method: 'GET',
    headers,
  });

  if (!response.ok) {
    throw new Error(`Failed to get modes: ${response.status}`);
  }

  return response.json();
}
