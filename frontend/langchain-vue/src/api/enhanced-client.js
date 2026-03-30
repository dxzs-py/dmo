const API_BASE_URL = 'http://localhost:8000';

export async function* chatStreamEnhanced(request) {
  const url = `${API_BASE_URL}/api/chat/stream/`;
  
  let response;
  
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
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
  const url = `${API_BASE_URL}/api/chat/`;
  
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Unknown error' }));
    throw new Error(error.error || `HTTP ${response.status}: ${response.statusText}`);
  }

  return response.json();
}

export async function healthCheck() {
  const response = await fetch(`${API_BASE_URL}/api/health/`, {
    method: 'GET',
  });

  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`);
  }

  return response.json();
}

export async function getAvailableModes() {
  const response = await fetch(`${API_BASE_URL}/api/chat/modes/`, {
    method: 'GET',
  });

  if (!response.ok) {
    throw new Error(`Failed to get modes: ${response.status}`);
  }

  return response.json();
}
