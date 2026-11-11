// Page shell: section switching, header controls, watchlist loading, panel resizing.

import { $, $$, api, post, toast, load, save, fmtRelative } from './util.js';
import { state, on, emit } from './state.js';
import { initWatchlist } from './watchlist.js';
import { initSearch } from './search.js';
import { initCalendar } from './calendar.js';
import { initTorrents } from './torrents.js';
import { initActivity } from './activity.js';

const MIDDLE_VIEWS = ['watchlist', 'calendar', 'activity'];
const LIST_REFRESH = 60000;
const LIST_REFRESH_BUSY = 3000;
const LIST_RETRY = 15000;
const STATUS_REFRESH = 15000;

let listTimer = null;

// ── sections ────────────────────────────────────────────────────────────────

function setView(view) {
  state.view = view;
  document.body.dataset.view = view;
  if (MIDDLE_VIEWS.includes(view)) {
    for (const name of MIDDLE_VIEWS) $(`#pane-${name}`).hidden = name !== view;
    for (const button of $$('.tabs button')) button.classList.toggle('active', button.dataset.view === view);
    save('pane', view);
  }
  for (const button of $$('.bottom-nav button')) button.classList.toggle('active', button.dataset.view === view);
  emit('view', view);
}

// ── watchlist data ──────────────────────────────────────────────────────────

function scheduleList(delay) {
  clearTimeout(listTimer);
  listTimer = setTimeout(loadEntries, delay);
}

function applyList(data) {
  state.entries = data.entries || [];
  state.anilist = data.anilist || {};
  state.defaultDir = data.default_dir || '';
  renderFreshness();
  emit('entries');
}

async function loadEntries() {
  if (document.hidden) {
    scheduleList(LIST_REFRESH);
    return;
  }
  try {
    const data = await api('/api/anime/list');
    applyList(data);
    save('list', data);
    scheduleList(state.anilist.refreshing ? LIST_REFRESH_BUSY : LIST_REFRESH);
  } catch (error) {
    renderFreshness(error.message);
    scheduleList(LIST_RETRY);
  }
}

function renderFreshness(problem) {
  const node = $('#anilist-state');
  const info = state.anilist || {};
  let text = '';
  let warn = false;
  if (problem) {
    text = problem;
    warn = true;
  } else if (info.error) {
    text = info.as_of ? `AniList unavailable · showing data from ${fmtRelative(info.as_of)}` : 'AniList unavailable';
    warn = true;
  } else if (info.refreshing) {
    text = 'Updating from AniList…';
  } else if (info.as_of) {
    text = `AniList data from ${fmtRelative(info.as_of)}`;
  }
  node.textContent = text;
  node.title = info.error || '';
  node.classList.toggle('warn', warn);
  $('#refresh').classList.toggle('spinning', Boolean(info.refreshing));
}

// ── tracker and Transmission ────────────────────────────────────────────────

function setPill(pill, isOn) {
  pill.classList.toggle('on', isOn === true);
  pill.classList.toggle('off', isOn === false);
  $('b', pill).textContent = isOn === null ? '…' : (isOn ? 'On' : 'Off');
}

function bothRunning() {
  const s = state.status;
  return Boolean(s && s.tracker.running && s.transmission.online);
}

function renderStatus() {
  const s = state.status;
  setPill($('#pill-tracker'), s ? s.tracker.running : null);
  setPill($('#pill-transmission'), s ? s.transmission.online : null);
  const power = $('#power');
  if (!power.dataset.busy) power.textContent = bothRunning() ? 'Stop' : 'Start';
  const waiting = s ? s.review : 0;
  for (const badge of $$('[data-review-count]')) {
    badge.hidden = !waiting;
    badge.textContent = waiting;
  }
}

async function pollStatus() {
  if (document.hidden) return;
  try {
    state.status = await api('/api/status');
  } catch {
    state.status = null;
  }
  renderStatus();
  emit('status');
}

async function runBusy(button, label, work) {
  button.disabled = true;
  button.dataset.busy = '1';
  if (label) button.textContent = label;
  try {
    await work();
  } catch (error) {
    toast(error.message, 'error');
  }
  button.disabled = false;
  delete button.dataset.busy;
  await pollStatus();
}

function initHeader() {
  $('#power').addEventListener('click', () => {
    const stopping = bothRunning();
    runBusy($('#power'), stopping ? 'Stopping…' : 'Starting…', async () => {
      const result = await post(stopping ? '/api/power/stop' : '/api/power/start');
      if (!result.ok) toast(`Transmission: ${result.reason || 'no response'}`, 'error');
    });
  });

  $('#pill-tracker').addEventListener('click', () => {
    const running = Boolean(state.status && state.status.tracker.running);
    runBusy($('#pill-tracker'), null, async () => {
      await post(running ? '/api/daemon/stop' : '/api/daemon/start');
    });
  });

  $('#pill-transmission').addEventListener('click', () => {
    const online = Boolean(state.status && state.status.transmission.online);
    if (online && !confirm('Stop Transmission? Downloads in progress will pause.')) return;
    runBusy($('#pill-transmission'), null, async () => {
      const result = await post(online ? '/api/transmission/stop' : '/api/transmission/start');
      if (!result.ok) toast(`Transmission: ${result.reason || 'no response'}`, 'error');
    });
  });

  $('#refresh').addEventListener('click', async () => {
    try {
      const result = await post('/api/anime/refresh');
      state.anilist = result.anilist || state.anilist;
      renderFreshness();
      if (!result.started && !state.anilist.refreshing) toast('AniList data was refreshed a moment ago');
    } catch (error) {
      toast(error.message, 'error');
    }
    scheduleList(1500);
  });
}

const clockFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });

function tickClock() {
  $('#clock').textContent = clockFormat.format(new Date());
}

// ── resizable side panels ───────────────────────────────────────────────────

function initSplitters() {
  const root = document.documentElement;
  const widths = load('widths', {});
  for (const side of ['search', 'torrents']) {
    if (widths[side]) root.style.setProperty(`--w-${side}`, `${widths[side]}px`);
  }
  for (const splitter of $$('.splitter')) {
    splitter.addEventListener('pointerdown', event => {
      const side = splitter.dataset.side;
      const startX = event.clientX;
      const startWidth = $(`#panel-${side}`).getBoundingClientRect().width;
      const move = moveEvent => {
        const delta = moveEvent.clientX - startX;
        const wanted = side === 'search' ? startWidth + delta : startWidth - delta;
        const width = Math.round(Math.max(220, Math.min(window.innerWidth - 560, wanted)));
        root.style.setProperty(`--w-${side}`, `${width}px`);
        widths[side] = width;
      };
      const stop = () => {
        splitter.classList.remove('dragging');
        splitter.removeEventListener('pointermove', move);
        splitter.removeEventListener('pointerup', stop);
        splitter.removeEventListener('pointercancel', stop);
        save('widths', widths);
      };
      splitter.setPointerCapture(event.pointerId);
      splitter.classList.add('dragging');
      splitter.addEventListener('pointermove', move);
      splitter.addEventListener('pointerup', stop);
      splitter.addEventListener('pointercancel', stop);
      event.preventDefault();
    });
  }
}

// ── start ───────────────────────────────────────────────────────────────────

function init() {
  initSplitters();
  initHeader();
  initWatchlist();
  initSearch();
  initCalendar();
  initTorrents();
  initActivity();

  for (const button of $$('.tabs button, .bottom-nav button')) {
    button.addEventListener('click', () => setView(button.dataset.view));
  }
  on('goto', setView);
  on('reload', () => {
    loadEntries();
    pollStatus();
  });

  document.addEventListener('click', event => {
    for (const menu of $$('details.menu[open]')) {
      if (!menu.contains(event.target)) menu.open = false;
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      loadEntries();
      pollStatus();
    }
  });

  // Paint the last known watchlist immediately; fresh data replaces it a moment later.
  const remembered = load('list', null);
  if (remembered) applyList(remembered);

  const pane = load('pane', 'watchlist');
  setView(MIDDLE_VIEWS.includes(pane) ? pane : 'watchlist');

  tickClock();
  setInterval(tickClock, 20000);
  loadEntries();
  pollStatus();
  setInterval(pollStatus, STATUS_REFRESH);
}

init();
