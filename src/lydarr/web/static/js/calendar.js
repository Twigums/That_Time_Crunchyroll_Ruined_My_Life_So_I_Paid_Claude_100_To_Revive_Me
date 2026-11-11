// Calendar of upcoming and recent episodes, as a list, a week or a month.
// The server answers from its AniList cache, so paging around costs no AniList requests.

import { $, $$, esc, api, fmtTime, load, save } from './util.js';
import { on, emit, activeAnime } from './state.js';

const MODES = ['list', 'week', 'month'];
const LIST_DAYS = 14;
const MONTH_CHIPS = 3;
const weekdayShort = new Intl.DateTimeFormat(undefined, { weekday: 'short' });
const dayLong = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
const dayShort = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const monthLong = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' });

let mode = 'month';
let cursor = startOfDay(new Date());
let episodes = [];
let note = '';
let loading = false;
let expandedDay = null;
let ticket = 0;
let signature = '';
const cache = new Map();

function startOfDay(date) {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function addDays(date, count) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + count);
  return copy;
}

function startOfWeek(date) {
  const day = startOfDay(date);
  return addDays(day, -day.getDay());
}

function dayKey(date) {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

function isToday(date) {
  return dayKey(date) === dayKey(new Date());
}

function range() {
  if (mode === 'week') return { start: startOfWeek(cursor), days: 7 };
  if (mode === 'list') return { start: startOfDay(cursor), days: LIST_DAYS };
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const last = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0);
  const start = startOfWeek(first);
  const end = addDays(startOfWeek(last), 7);
  return { start, days: Math.round((end - start) / 86400000) };
}

function title() {
  if (mode === 'month') return monthLong.format(cursor);
  const { start, days } = range();
  return `${dayShort.format(start)} – ${dayShort.format(addDays(start, days - 1))}`;
}

// Every show gets its own hue; the golden angle keeps neighbours far apart for any number of shows.
function hue(showTitle) {
  const titles = activeAnime().map(entry => entry.title).sort();
  const index = Math.max(0, titles.indexOf(showTitle));
  return Math.round((index * 137.508) % 360);
}

function tint(showTitle) {
  const h = hue(showTitle);
  return `border-left-color:hsl(${h} 60% 52%);background:hsl(${h} 60% 52% / 0.14)`;
}

function byDay() {
  const days = new Map();
  for (const episode of episodes) {
    const key = dayKey(new Date(episode.airing_at * 1000));
    if (!days.has(key)) days.set(key, []);
    days.get(key).push(episode);
  }
  for (const list of days.values()) list.sort((a, b) => a.airing_at - b.airing_at);
  return days;
}

function chip(episode) {
  const time = fmtTime(episode.airing_at);
  return `<button type="button" class="cal-chip" style="${tint(episode.title)}" `
    + `data-title="${esc(episode.title)}" data-episode="${episode.episode}" `
    + `title="${esc(episode.title)} · ep ${episode.episode} · ${esc(time)}">`
    + `<span class="t">${esc(time)}</span><span class="n">${esc(episode.title)}</span>`
    + `<span class="t">E${episode.episode}</span></button>`;
}

function renderMonth() {
  const { start, days } = range();
  const grouped = byDay();
  const month = cursor.getMonth();
  const cells = [];
  for (let i = 0; i < 7; i++) cells.push(`<div class="cal-dow">${esc(weekdayShort.format(addDays(start, i)))}</div>`);
  for (let i = 0; i < days; i++) {
    const day = addDays(start, i);
    const key = dayKey(day);
    const list = grouped.get(key) || [];
    const open = expandedDay === key;
    const shown = open ? list : list.slice(0, MONTH_CHIPS);
    let more = '';
    if (list.length > shown.length) {
      more = `<button type="button" class="cal-more" data-day="${key}">+${list.length - shown.length} more</button>`;
    } else if (open && list.length > MONTH_CHIPS) {
      more = `<button type="button" class="cal-more" data-day="${key}">less</button>`;
    }
    const classes = `cal-day${day.getMonth() === month ? '' : ' other'}${isToday(day) ? ' today' : ''}`;
    cells.push(`<div class="${classes}"><div class="cal-daynum">${day.getDate()}</div>${shown.map(chip).join('')}${more}</div>`);
  }
  return `<div class="cal-month">${cells.join('')}</div>`;
}

function renderWeek() {
  const { start } = range();
  const grouped = byDay();
  const cells = [];
  for (let i = 0; i < 7; i++) {
    const day = addDays(start, i);
    const list = grouped.get(dayKey(day)) || [];
    cells.push(`<div class="cal-day${isToday(day) ? ' today' : ''}">`
      + `<div class="cal-daynum">${esc(weekdayShort.format(day))} ${day.getDate()}</div>${list.map(chip).join('')}</div>`);
  }
  return `<div class="cal-week">${cells.join('')}</div>`;
}

function renderList() {
  const { start, days } = range();
  const grouped = byDay();
  const now = Date.now() / 1000;
  const parts = [];
  for (let i = 0; i < days; i++) {
    const day = addDays(start, i);
    const list = grouped.get(dayKey(day)) || [];
    if (!list.length) continue;
    parts.push(`<div class="cal-list-day${isToday(day) ? ' today' : ''}">${esc(dayLong.format(day))}${isToday(day) ? ' · today' : ''}</div>`);
    for (const episode of list) {
      parts.push(`<button type="button" class="cal-list-row${episode.airing_at < now ? ' past' : ''}" `
        + `style="border-left-color:hsl(${hue(episode.title)} 60% 52%)" `
        + `data-title="${esc(episode.title)}" data-episode="${episode.episode}">`
        + `<span class="t">${esc(fmtTime(episode.airing_at))}</span><span class="n">${esc(episode.title)}</span>`
        + `<span class="e">ep ${episode.episode}</span></button>`);
    }
  }
  return parts.join('');
}

function render() {
  $('#cal-title').textContent = title() + (loading ? ' · loading…' : '');
  $('#cal-note').innerHTML = note ? `<div class="notice">${esc(note)}</div>` : '';
  for (const button of $$('#cal-mode button')) button.classList.toggle('active', button.dataset.mode === mode);
  let html = '';
  if (mode === 'month') html = renderMonth();
  else if (mode === 'week') html = renderWeek();
  else html = renderList();
  if (!episodes.length && !loading) html += '<p class="empty">No episodes scheduled in this range.</p>';
  $('#cal-body').innerHTML = html;
}

async function loadRange(force = false) {
  const { start, days } = range();
  const end = addDays(start, days);
  const key = `${start.getTime()}-${days}`;
  if (force) cache.delete(key);
  const mine = ++ticket;

  if (cache.has(key)) {
    ({ episodes, note } = cache.get(key));
    loading = false;
    render();
    return;
  }
  episodes = [];
  note = '';
  loading = true;
  render();

  let result = { episodes: [], note: '' };
  try {
    const from = Math.floor(start.getTime() / 1000);
    const to = Math.floor(end.getTime() / 1000);
    const data = await api(`/api/anime/calendar?start=${from}&end=${to}`);
    const notes = [];
    if (data.error) notes.push(`AniList: ${data.error}. Showing what is cached.`);
    if ((data.unresolved || []).length) notes.push(`Not found on AniList: ${data.unresolved.join(', ')}`);
    result = { episodes: data.episodes || [], note: notes.join(' ') };
    if (!data.error) cache.set(key, result);
  } catch (error) {
    result = { episodes: [], note: error.message };
  }
  if (mine !== ticket) return;
  ({ episodes, note } = result);
  loading = false;
  render();
}

function visible() {
  return !$('#pane-calendar').hidden;
}

function shift(direction) {
  if (mode === 'week') cursor = addDays(cursor, 7 * direction);
  else if (mode === 'list') cursor = addDays(cursor, LIST_DAYS * direction);
  else cursor = new Date(cursor.getFullYear(), cursor.getMonth() + direction, 1);
  expandedDay = null;
  loadRange();
}

export function initCalendar() {
  const narrow = window.matchMedia('(max-width: 820px)').matches;
  const remembered = load('cal-mode', narrow ? 'list' : 'month');
  mode = MODES.includes(remembered) ? remembered : 'month';

  $('#cal-prev').addEventListener('click', () => shift(-1));
  $('#cal-next').addEventListener('click', () => shift(1));
  $('#cal-today').addEventListener('click', () => {
    cursor = startOfDay(new Date());
    expandedDay = null;
    loadRange();
  });
  $('#cal-mode').addEventListener('click', event => {
    const button = event.target.closest('button[data-mode]');
    if (!button || button.dataset.mode === mode) return;
    mode = button.dataset.mode;
    save('cal-mode', mode);
    expandedDay = null;
    loadRange();
  });
  $('#cal-body').addEventListener('click', event => {
    const more = event.target.closest('button[data-day]');
    if (more) {
      expandedDay = expandedDay === more.dataset.day ? null : more.dataset.day;
      render();
      return;
    }
    const episode = event.target.closest('button[data-episode]');
    if (episode) {
      emit('open-torrents', { entry: episode.dataset.title, episode: Number(episode.dataset.episode) });
    }
  });

  on('view', view => {
    if (view === 'calendar') loadRange();
  });
  on('entries', () => {
    // The set of tracked shows drives the calendar; reload only when that set changes.
    const next = activeAnime().map(entry => `${entry.title}#${entry.anilist_id || 0}`).sort().join('|');
    if (next === signature) return;
    signature = next;
    cache.clear();
    if (visible()) loadRange();
  });
}
