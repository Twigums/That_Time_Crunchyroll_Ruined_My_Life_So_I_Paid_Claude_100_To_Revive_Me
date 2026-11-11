// Small shared helpers: DOM lookup, escaping, API calls, time formatting, toasts, local storage.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ESCAPES[c]);
}

export async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error('Cannot reach lydarr');
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data && (data.error || (typeof data.detail === 'string' ? data.detail : null));
    const error = new Error(detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
const weekdayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short' });
const dateFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });

export function fmtTime(seconds) {
  return timeFormat.format(new Date(seconds * 1000));
}

// "Fri 9:30 AM" within the coming or past week, "Nov 13 9:30 AM" beyond it.
export function fmtWhen(seconds) {
  const date = new Date(seconds * 1000);
  const near = Math.abs(date.getTime() - Date.now()) < 6 * 86400000;
  return `${near ? weekdayFormat.format(date) : dateFormat.format(date)} ${timeFormat.format(date)}`;
}

export function fmtRelative(seconds) {
  const delta = seconds - Date.now() / 1000;
  const size = Math.abs(delta);
  let text;
  if (size < 90) text = '1 min';
  else if (size < 3600) text = `${Math.round(size / 60)} min`;
  else if (size < 36 * 3600) text = `${Math.round(size / 3600)} h`;
  else text = `${Math.round(size / 86400)} days`;
  return delta >= 0 ? `in ${text}` : `${text} ago`;
}

// Compact age for table cells: 12m, 5h, 3d, 4mo, 2y.
export function fmtAge(seconds) {
  if (!seconds) return '';
  const age = Math.max(0, Date.now() / 1000 - seconds);
  if (age < 3600) return `${Math.max(1, Math.round(age / 60))}m`;
  if (age < 48 * 3600) return `${Math.round(age / 3600)}h`;
  if (age < 60 * 86400) return `${Math.round(age / 86400)}d`;
  if (age < 730 * 86400) return `${Math.round(age / (30 * 86400))}mo`;
  return `${Math.round(age / (365 * 86400))}y`;
}

export function shortPath(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  return parts.length <= 2 ? String(path || '') : `…/${parts.slice(-2).join('/')}`;
}

export function toast(message, kind = 'info') {
  const box = $('#toasts');
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  node.addEventListener('click', () => node.remove());
  box.appendChild(node);
  setTimeout(() => node.remove(), kind === 'error' ? 9000 : 4500);
}

// Browser storage can be unavailable (private windows, blocked site data); never let that break the page.
export function load(key, fallback) {
  try {
    const raw = localStorage.getItem(`lydarr.${key}`);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function save(key, value) {
  try {
    localStorage.setItem(`lydarr.${key}`, JSON.stringify(value));
  } catch {
    // Nothing to do: the setting just will not be remembered.
  }
}
