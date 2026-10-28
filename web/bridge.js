/* Browser-only transport: no credential storage, cookies, redirects, or API caching. */
window.ArrowStoryWeb = {
  async request(url, method, token, payload, callback) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 90000);
    try {
      const response = await fetch(url, {
        method, headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
        body: method === 'GET' ? undefined : payload,
        mode: 'cors', credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal
      });
      const reader = response.body.getReader();
      const chunks = []; let size = 0;
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 64 * 1024 * 1024) { await reader.cancel(); throw new Error('response limit'); }
        chunks.push(value);
      }
      const bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      callback(response.status, new TextDecoder('utf-8', {fatal: true}).decode(bytes));
    } catch (_) {
      callback(0, '{"error":{"message":"Connection failed. Check the service URL, HTTPS and allowed browser origins."}}');
    } finally { clearTimeout(timeout); }
  }
};
