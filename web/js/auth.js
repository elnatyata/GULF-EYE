// Sign-in screen. Shown before anything else when the GulfEye backend is running.
import { api } from './api.js';
import { esc } from './util.js';

const ROLE_NOTE = {
  admin: 'Sees everything: users, audit log, all agencies',
  analyst: 'AI alerts, intelligence, merging, exports, API keys',
  operator: 'Alerts, incidents, field and patrol reports',
  viewer: 'Read-only view of shared items',
};

async function demoAccounts() {
  try { return await api.get('/api/auth/demo'); } catch { return { enabled: false, accounts: [] }; }
}

function finish(root, res, onSignedIn) {
  api.setToken(res.token);
  root.hidden = true;
  root.innerHTML = '';
  document.body.classList.remove('is-locked');
  onSignedIn(res.user);
}

export async function showLogin(onSignedIn, message = '') {
  const demo = await demoAccounts();
  const root = document.getElementById('login');
  root.hidden = false;
  document.body.classList.add('is-locked');
  root.innerHTML = `
  <div class="login-box" role="dialog" aria-modal="true" aria-labelledby="login-title">
    <div class="login-brand">
      <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="9" fill="#0B5C7A"/>
        <circle cx="16" cy="16" r="9" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="1.6"/>
        <circle cx="16" cy="16" r="4.8" fill="none" stroke="#fff" stroke-opacity=".8" stroke-width="1.6"/>
        <path d="M16 16 23 9" stroke="#7DD3FC" stroke-width="2" stroke-linecap="round"/><circle cx="21" cy="19.5" r="2.2" fill="#FF4D5A"/></svg>
      <div><h1 id="login-title">GulfEye</h1><p>Maritime intelligence for Nigerian waters and the Gulf of Guinea</p></div>
    </div>
    ${demo.enabled ? `<div class="login-demo">
      <div class="label">Demo access: one click, no password. Pick a role to see what that agency sees.</div>
      <div class="demo-grid">${demo.accounts.map((a) => `<button type="button" class="demo-btn${a.role === 'admin' ? ' is-main' : ''}" data-demo="${esc(a.email)}">
        <b>${esc(a.agency_name)}</b><span>${esc(a.role_label)} · ${esc(a.name)}</span><small>${esc(ROLE_NOTE[a.role] || '')}</small></button>`).join('')}</div>
    </div>
    <details class="login-pw"><summary>Sign in with email and password</summary>` : ''}
    <form class="login-form" novalidate>
      <label>Email<input name="email" type="email" autocomplete="username" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <p class="login-error" role="alert">${esc(message)}</p>
      <button class="btn primary" type="submit">Sign in</button>
    </form>
    ${demo.enabled ? '</details>' : ''}
    <p class="login-note">Authorised users only. Every sign-in and action is recorded in the audit log. All data in this demo is simulated.</p>
  </div>`;
  const form = root.querySelector('form');
  const err = root.querySelector('.login-error');
  if (!demo.enabled) form.email.focus();
  root.querySelectorAll('[data-demo]').forEach((b) => b.addEventListener('click', async () => {
    root.querySelectorAll('[data-demo]').forEach((x) => { x.disabled = true; });
    b.classList.add('is-busy');
    try {
      finish(root, await api.post('/api/auth/demo', { email: b.dataset.demo }), onSignedIn);
    } catch (e) {
      err.textContent = e.message;
      root.querySelector('.login-pw')?.setAttribute('open', '');
      root.querySelectorAll('[data-demo]').forEach((x) => { x.disabled = false; });
    }
  }));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    err.textContent = '';
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      finish(root, await api.post('/api/auth/login', { email: form.email.value.trim(), password: form.password.value }), onSignedIn);
    } catch (e) {
      err.textContent = e.message;
      btn.disabled = false;
      form.password.select();
    }
  });
}
