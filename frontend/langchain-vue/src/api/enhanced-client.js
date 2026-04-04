import settings from '../settings';
import { useUserStore } from '../stores/user';

export async function* chatStreamEnhanced(request) {
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
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    if (!response.body) {
      throw new Error('Response body is null');
    }
  } catch (error) {
    console.error('Failed to initiate chat stream:', error);
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
    body: JSON.stringify(request),
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
