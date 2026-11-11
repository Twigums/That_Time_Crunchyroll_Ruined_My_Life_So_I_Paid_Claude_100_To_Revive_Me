// "Add a show": search AniList and start tracking a result.

import { $, $$, esc, api, post, toast, fmtWhen } from './util.js';
import { emit } from './state.js';

const STATUS = {
  RELEASING: 'airing',
  FINISHED: 'finished',
  NOT_YET_RELEASED: 'not yet aired',
  HIATUS: 'on hiatus',
  CANCELLED: 'cancelled',
};
const MANGA_NOTE = '<div class="notice">Manga tracking is experimental. AniList only lists a chapter count once a '
  + 'series has ended, so ongoing manga are not picked up automatically.</div>';

let type = 'anime';
let ticket = 0;
const results = { anime: null, manga: null };
const queries = { anime: '', manga: '' };

function hasAiredEpisodes(item) {
  if (item.type !== 'anime') return false;
  return item.status === 'FINISHED' || (item.status === 'RELEASING' && (item.next_airing_episode || 0) > 1);
}

function row(item, index) {
  const cover = item.cover_image
    ? `<img class="cover" src="${esc(item.cover_image)}" alt="" loading="lazy">`
    : '<div class="cover"></div>';
  const bits = [];
  if (item.format) bits.push(item.format.replace(/_/g, ' '));
  bits.push(STATUS[item.status] || String(item.status || '').toLowerCase());
  if (item.type === 'manga' && item.chapters) bits.push(`${item.chapters} ch`);
  if (item.type !== 'manga' && item.episodes) bits.push(`${item.episodes} eps`);
  if (item.next_airing_episode && item.next_airing_at) {
    bits.push(`ep ${item.next_airing_episode} ${fmtWhen(item.next_airing_at)}`);
  }
  let actions = '<span class="tag ok">Tracked</span>';
  if (!item.tracked) {
    actions = `<button type="button" class="btn small" data-track="${index}">Track</button>`;
    if (hasAiredEpisodes(item)) {
      actions += `<button type="button" class="btn small" data-track="${index}" data-backfill="1" `
        + 'title="Track it and also download the episodes that already aired">+ aired eps</button>';
    }
  }
  return `
    <div class="item">
      ${cover}
      <div class="item-main">
        <span class="item-title">${esc(item.title)}</span>
        <div class="item-meta">${esc(bits.join(' · '))}</div>
      </div>
      <div class="item-actions">${actions}</div>
    </div>`;
}

function render() {
  const box = $('#search-results');
  const note = type === 'manga' ? MANGA_NOTE : '';
  const items = results[type];
  if (items === null) {
    box.innerHTML = note;
  } else if (!items.length) {
    box.innerHTML = `${note}<p class="empty">No results.</p>`;
  } else {
    box.innerHTML = note + items.map(row).join('');
  }
}

async function search() {
  const query = $('#search-q').value.trim();
  if (!query) return;
  const wanted = type;
  const mine = ++ticket;
  queries[wanted] = query;
  $('#search-results').innerHTML = '<p class="empty">Searching AniList…</p>';
  try {
    const data = await api(`/api/anime/search?q=${encodeURIComponent(query)}&type=${wanted}`);
    if (mine !== ticket) return;
    results[wanted] = data;
    render();
  } catch (error) {
    if (mine !== ticket) return;
    results[wanted] = null;
    $('#search-results').innerHTML = `<div class="notice bad">${esc(error.message)}</div>`;
  }
}

async function track(button) {
  const items = results[type] || [];
  const item = items[Number(button.dataset.track)];
  if (!item) return;
  const backfill = button.dataset.backfill === '1';
  for (const other of $$(`[data-track="${button.dataset.track}"]`)) other.disabled = true;
  try {
    const result = await post('/api/anime/add', {
      title: item.title,
      type: item.type === 'manga' ? 'manga' : 'anime',
      anilist_id: item.id,
      backfill,
    });
    if (result.added) {
      item.tracked = true;
      if (result.warning) toast(`Added, but ${result.warning}`, 'error');
      else toast(backfill ? `Tracking ${item.title}, including aired episodes` : `Tracking ${item.title}`, 'ok');
      emit('reload');
    } else {
      toast(result.reason || 'Not added', 'error');
    }
  } catch (error) {
    toast(error.message, 'error');
  }
  render();
}

export function initSearch() {
  $('#search-form').addEventListener('submit', event => {
    event.preventDefault();
    search();
  });
  $('#search-type').addEventListener('click', event => {
    const button = event.target.closest('button[data-type]');
    if (!button || button.dataset.type === type) return;
    queries[type] = $('#search-q').value;
    type = button.dataset.type;
    for (const other of $$('#search-type button')) other.classList.toggle('active', other === button);
    $('#search-q').value = queries[type];
    ticket += 1;
    render();
  });
  $('#search-results').addEventListener('click', event => {
    const button = event.target.closest('button[data-track]');
    if (button) track(button);
  });
  render();
}
