// Client for the GulfEye API. The token lives in sessionStorage (cleared when the tab closes) and is sent as a
// bearer header; the WebSocket gets it as a query parameter because browsers cannot set headers on it.
const KEY = 'gulfeye-token';

let token = null;
try { token = sessionStorage.getItem(KEY); } catch { /* storage blocked: sign in each load */ }

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export const api = {
  get token() { return token; },
  setToken(t) {
    token = t;
    try { if (t) sessionStorage.setItem(KEY, t); else sessionStorage.removeItem(KEY); } catch { /* ignore */ }
  },
  headers(extra = {}) { return token ? { Authorization: `Bearer ${token}`, ...extra } : extra; },

  async request(method, path, body, { raw = false } = {}) {
    const opts = { method, headers: this.headers(), cache: 'no-store' };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = `${res.status}`;
      try {
        const j = await res.json();
        msg = typeof j.detail === 'string' ? j.detail
          : Array.isArray(j.detail) ? j.detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
            : j.error || msg;
      } catch { /* not JSON */ }
      if (res.status === 401 && path !== '/api/auth/login') window.dispatchEvent(new CustomEvent('gulfeye:signed-out'));
      throw new ApiError(res.status, msg);
    }
    return raw ? res : res.json();
  },
  get(path) { return this.request('GET', path); },
  post(path, body = {}) { return this.request('POST', path, body); },
  patch(path, body = {}) { return this.request('PATCH', path, body); },

  async download(path, fallbackName) {
    const res = await this.request('GET', path, undefined, { raw: true });
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const name = (cd.match(/filename="([^"]+)"/) || [])[1] || fallbackName;
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },

  connect(onEvent) {
    if (!token) return null;
    let ws;
    let stopped = false;
    let retry = 1000;
    const open = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
      ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch { /* ignore malformed */ } };
      ws.onopen = () => { retry = 1000; onEvent({ type: 'connected' }); };
      ws.onclose = (e) => {
        onEvent({ type: 'disconnected' });
        if (!stopped && e.code !== 4401) setTimeout(open, retry = Math.min(retry * 2, 30000));
      };
    };
    open();
    const ping = setInterval(() => { if (ws?.readyState === 1) ws.send('ping'); }, 25000);
    return { close() { stopped = true; clearInterval(ping); ws?.close(); } };
  },
};

// Is the GulfEye backend serving this page? (False when served by the old static scripts/serve.py.)
export async function backendAvailable() {
  try {
    const res = await fetch('/api/health', { cache: 'no-store' });
    if (!res.ok) return false;
    return Boolean((await res.json()).backend);
  } catch { return false; }
}
