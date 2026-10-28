/* Browser-only transport: no credential storage, cross-site cookies, followed redirects, or API caching. */
window.ArrowStoryWeb = {
  async request(url, method, token, payload, callback) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 90000);
    // The PWA's own origin may sit behind Cloudflare Access; its sign-in cookie goes only there.
    const sameOrigin = new URL(url, location.href).origin === location.origin;
    const headers = {'Content-Type': 'application/json'};
    if (token) headers.Authorization = `Bearer ${token}`;
    try {
      const response = await fetch(url, {
        method, headers,
        body: method === 'GET' ? undefined : payload,
        mode: 'cors', credentials: sameOrigin ? 'same-origin' : 'omit', redirect: 'manual', cache: 'no-store', signal: controller.signal
      });
      if (response.type === 'opaqueredirect') {
        // Never follow: a redirect could carry the bearer token elsewhere. Behind Access it means sign-in expired.
        callback(0, '{"error":{"code":"signin_required","message":"The service redirected this request. If Arrow is behind Cloudflare Access, choose Sign in… to renew your sign-in; otherwise check the service URL."}}');
        return;
      }
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
