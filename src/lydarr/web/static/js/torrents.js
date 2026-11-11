// Torrent browser: search Nyaa, see what each release is, and add one to the right folder.
//
// "Save to" names a watchlist entry. Whatever is added goes to that entry's own
// save path, however the search box is edited afterwards.

import { $, esc, api, post, toast, fmtAge } from './util.js';
import { state, on, emit, findEntry } from './state.js';

const UNITS = { b: 1, kib: 1024, mib: 1024 ** 2, gib: 1024 ** 3, tib: 1024 ** 4 };
const SORTERS = {
  seeders: (a, b) => b.row.seeders - a.row.seeders,
  newest: (a, b) => (b.row.published || 0) - (a.row.published || 0),
  size: (a, b) => sizeBytes(b.row.size) - sizeBytes(a.row.size),
  name: (a, b) => a.row.name.localeCompare(b.row.name),
};

let rows = [];
let judged = false;
let searched = false;
let lastSearch = null;
let ticket = 0;
const added = new Set();

function sizeBytes(text) {
  const match = String(text || '').match(/([\d.]+)\s*(B|KiB|MiB|GiB|TiB)/i);
  return match ? parseFloat(match[1]) * (UNITS[match[2].toLowerCase()] || 1) : 0;
}

function pad(number) {
  return String(number).padStart(2, '0');
}

function episodeLabel(row) {
  if (row.episode === null || row.episode === undefined) return '';
  if (row.season !== null && row.season !== undefined) return `S${pad(row.season)}E${pad(row.episode)}`;
  return `ep ${pad(row.episode)}`;
}

function tag(text, kind = '', title = '') {
  return `<span class="tag ${kind}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
}

function verdictTag(row) {
  if (!row.verdict) return '';
  if (row.verdict === 'match') return tag('Match', 'ok', row.reason);
  if (row.verdict === 'waiting') return tag(row.reason || 'waiting', 'warn');
  if (row.verdict === 'near') return tag(row.reason || 'check the number', 'warn');
  return tag(row.reason || 'other', '');
}

function rowHtml({ row, index }) {
  const tags = [verdictTag(row)];
  if (row.group) tags.push(tag(row.group));
  const number = episodeLabel(row);
  if (number) tags.push(tag(number));
  if (row.resolution) tags.push(tag(`${row.resolution}p`));
  if (row.hevc) tags.push(tag('HEVC'));
  if (row.batch) tags.push(tag('batch'));
  if (row.type === 'trusted') tags.push(tag('trusted'));
  if (row.type === 'remake') tags.push(tag('remake'));
  const magnet = row.magnet ? `<a href="${esc(row.magnet)}">magnet</a>` : '';
  const done = added.has(row.magnet);
  return `
    <div class="t-row${row.verdict === 'rejected' ? ' dim' : ''}">
      <div class="t-main">
        <div class="t-name">${esc(row.name)}</div>
        <div class="t-tags">${tags.join('')}${magnet}</div>
      </div>
      <div class="t-stats">
        <span class="t-num">${esc(row.size)}</span>
        <span class="t-num seeds" title="Seeders">${Number(row.seeders) || 0}</span>
        <span class="t-num" title="${esc(row.date)}">${esc(fmtAge(row.published))}</span>
      </div>
      <div class="t-add">
        <button type="button" class="btn small" data-add="${index}"${row.magnet && !done ? '' : ' disabled'}>${done ? 'Added' : 'Add'}</button>
      </div>
    </div>`;
}

function render() {
  const box = $('#torrent-results');
  if (!searched) {
    box.innerHTML = '<p class="empty">Pick a show from the watchlist or calendar, or search for a title.</p>';
    return;
  }
  const sort = $('#torrent-sort').value;
  const hideOthers = judged && $('#filter-fit').checked;
  let list = rows.map((row, index) => ({ row, index }));
  if ($('#filter-hevc').checked) list = list.filter(item => item.row.hevc);
  const beforeFit = list.length;
  if (hideOthers) list = list.filter(item => item.row.verdict !== 'rejected');
  const hidden = beforeFit - list.length;
  if (SORTERS[sort]) list.sort(SORTERS[sort]);

  const active = name => (sort === name ? ' active' : '');
  let html = `
    <div class="t-row t-head">
      <button type="button" class="${active('name').trim()}" data-sort="name">Name</button>
      <button type="button" class="t-num${active('size')}" data-sort="size">Size</button>
      <button type="button" class="t-num${active('seeders')}" data-sort="seeders">Seeds</button>
      <button type="button" class="t-num${active('newest')}" data-sort="newest">Age</button>
      <span></span>
    </div>`;
  html += list.map(rowHtml).join('');
  if (!list.length) {
    html += `<p class="empty">${rows.length ? 'Nothing fits the filters.' : 'No results.'}</p>`;
  }
  if (hidden) {
    html += `<p class="t-more muted">${hidden} other result${hidden === 1 ? '' : 's'} hidden `
      + '(earlier episodes, batches, other titles). '
      + '<button type="button" class="link" data-show-all="1">Show them</button></p>';
  }
  box.innerHTML = html;
}

function showDestination() {
  const entry = findEntry($('#torrent-dest').value);
  const path = entry ? entry.path : `${state.defaultDir || '…'}/<title of the release>`;
  const node = $('#torrent-dest-path');
  node.textContent = path;
  node.title = path;
}

function fillDestinations() {
  const select = $('#torrent-dest');
  const current = select.value;
  const options = ['<option value="">A new folder named after the release</option>'];
  for (const entry of state.entries) {
    if (!entry.deprecated) options.push(`<option value="${esc(entry.title)}">${esc(entry.title)}</option>`);
  }
  select.innerHTML = options.join('');
  select.value = [...select.options].some(option => option.value === current) ? current : '';
  showDestination();
}

async function search() {
  const query = $('#torrent-q').value.trim();
  if (!query) return;
  const episodeText = $('#torrent-ep').value.trim();
  const entry = $('#torrent-dest').value;
  const params = new URLSearchParams({ q: query, site: $('#torrent-site').value });
  if (episodeText) params.set('ep', episodeText);
  if (entry) params.set('entry', entry);

  const mine = ++ticket;
  searched = true;
  $('#torrent-results').innerHTML = '<p class="empty">Searching…</p>';
  try {
    const data = await api(`/api/torrents/search?${params}`);
    if (mine !== ticket) return;
    rows = data.results || [];
    judged = Boolean(data.judged);
    lastSearch = { entry, episode: judged ? data.episode : null };
    $('#filter-fit-wrap').hidden = !judged;
    const sort = $('#torrent-sort');
    // "Best match" is the tracker's own ranking; without it, most-seeded first is the useful default.
    if (judged) sort.value = 'best';
    else if (sort.value === 'best') sort.value = 'seeders';
    render();
  } catch (error) {
    if (mine !== ticket) return;
    rows = [];
    $('#torrent-results').innerHTML = `<div class="notice bad">${esc(error.message)}</div>`;
  }
}

async function add(button) {
  const row = rows[Number(button.dataset.add)];
  if (!row || !row.magnet) return;
  const entry = $('#torrent-dest').value || null;
  const body = { magnet: row.magnet, name: row.name, entry, published: row.published };
  // Only vouch for an episode number when these results were fetched for exactly that episode.
  if (entry && lastSearch && lastSearch.entry === entry && lastSearch.episode) body.episode = lastSearch.episode;

  button.disabled = true;
  button.textContent = 'Adding…';
  try {
    const result = await post('/api/torrents/add', body);
    if (result.ok) {
      added.add(row.magnet);
      button.textContent = 'Added';
      toast(`Saving to ${result.dir}`, 'ok');
      emit('reload');
      return;
    }
    toast(result.error || 'Could not add the torrent', 'error');
  } catch (error) {
    toast(error.message, 'error');
  }
  button.disabled = false;
  button.textContent = 'Add';
}

function open({ entry, episode }) {
  const found = findEntry(entry);
  $('#torrent-dest').value = found ? found.title : '';
  showDestination();
  $('#torrent-q').value = found ? (found.search_name || found.title) : (entry || '');
  $('#torrent-ep').value = episode || '';
  $('#torrent-site').value = 'nyaa_si';
  emit('goto', 'torrents');
  search();
}

export function initTorrents() {
  $('#torrent-form').addEventListener('submit', event => {
    event.preventDefault();
    search();
  });
  $('#torrent-dest').addEventListener('change', showDestination);
  $('#filter-hevc').addEventListener('change', render);
  $('#filter-fit').addEventListener('change', render);
  $('#torrent-sort').addEventListener('change', render);
  $('#torrent-results').addEventListener('click', event => {
    const addButton = event.target.closest('button[data-add]');
    if (addButton) {
      add(addButton);
      return;
    }
    const sortButton = event.target.closest('button[data-sort]');
    if (sortButton) {
      $('#torrent-sort').value = sortButton.dataset.sort;
      render();
      return;
    }
    if (event.target.closest('button[data-show-all]')) {
      $('#filter-fit').checked = false;
      render();
    }
  });
  on('entries', fillDestinations);
  on('open-torrents', open);
  fillDestinations();
  render();
}
