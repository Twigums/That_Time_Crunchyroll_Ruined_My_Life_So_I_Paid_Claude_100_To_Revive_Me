// Watchlist: one row per tracked show with its status, settings form and actions.

import { $, esc, post, toast, fmtWhen, fmtRelative, shortPath } from './util.js';
import { state, on, emit, findEntry } from './state.js';

const ANIME_STATUS = {
  RELEASING: 'Airing',
  FINISHED: 'Finished airing',
  NOT_YET_RELEASED: 'Not yet aired',
  HIATUS: 'On hiatus',
  CANCELLED: 'Cancelled',
};
const MANGA_STATUS = {
  RELEASING: 'Publishing',
  FINISHED: 'Finished',
  NOT_YET_RELEASED: 'Not yet published',
  HIATUS: 'On hiatus',
  CANCELLED: 'Cancelled',
};
const DOTS = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="1.4" fill="currentColor"/>'
  + '<circle cx="8" cy="8" r="1.4" fill="currentColor"/><circle cx="13" cy="8" r="1.4" fill="currentColor"/></svg>';

let editing = null;
let archivedOpen = false;

function statusLine(entry) {
  const status = entry.status || {};
  const progress = entry.progress || {};
  if (!status.status) {
    if (status.unresolved) return '<span class="warn-text">Not found on AniList</span>';
    return `<span class="muted">${state.anilist.refreshing ? 'Loading from AniList…' : 'No AniList data yet'}</span>`;
  }
  if (entry.type === 'manga') {
    const label = MANGA_STATUS[status.status] || status.status;
    return esc(entry.last_chapter ? `${label} · up to ch. ${entry.last_chapter}` : label);
  }
  const review = progress.review || [];
  if (review.length) {
    return `<button type="button" class="link warn-text" data-act="review">Ep ${review[0]} needs your check</button>`;
  }
  const searching = progress.searching || [];
  if (searching.length) {
    const more = searching.length > 1 ? ` (+${searching.length - 1} more)` : '';
    return `Looking for ep ${searching[0].episode}${more} `
      + `<span class="muted">· since ${esc(fmtWhen(searching[0].since))}</span>`;
  }
  if (status.next_airing_episode && status.next_airing_at) {
    return `Ep ${status.next_airing_episode} · ${esc(fmtWhen(status.next_airing_at))} `
      + `<span class="muted">· ${esc(fmtRelative(status.next_airing_at))}</span>`;
  }
  if (progress.completed) return 'Completed';
  return esc(ANIME_STATUS[status.status] || status.status);
}

function metaLine(entry) {
  const progress = entry.progress || {};
  const bits = [entry.submitters.length ? entry.submitters.join(', ') : 'any uploader'];
  if (entry.type !== 'manga' && entry.check_delay) bits.push(`+${entry.check_delay} min`);
  if (entry.episode_offset) bits.push(`numbering +${entry.episode_offset}`);
  if (progress.last) bits.push(`last: ep ${progress.last.episode}, ${fmtRelative(progress.last.at)}`);
  if ((progress.missed || []).length) bits.push(`missed ep ${progress.missed.join(', ')}`);
  return `${esc(bits.join(' · '))}<br><span title="${esc(entry.path)}">${esc(shortPath(entry.path))}</span>`;
}

function editForm(entry) {
  const animeFields = entry.type === 'manga' ? '' : `
    <div class="pair">
      <label>Check delay (minutes after air)
        <input type="number" name="check_delay" min="0" step="1" inputmode="numeric"
          value="${entry.check_delay || ''}" placeholder="${entry.default_check_delay}">
      </label>
      <label>Episode number offset
        <input type="number" name="episode_offset" step="1" inputmode="numeric"
          value="${entry.episode_offset || ''}" placeholder="auto">
      </label>
    </div>`;
  return `
    <form class="edit">
      <label>Search name on Nyaa
        <input type="text" name="search_name" value="${esc(entry.search_name)}" placeholder="Blank: search by title">
      </label>
      <label>Preferred uploaders, in order
        <input type="text" name="submitters" value="${esc(entry.submitters.join(', '))}" placeholder="SubsPlease, Erai-raws (blank: anyone)">
      </label>
      <label>Save path
        <input type="text" name="download_dir" value="${esc(entry.download_dir)}" placeholder="${esc(entry.default_path)}">
      </label>${animeFields}
      <div class="actions">
        <button type="submit" class="btn primary small">Save</button>
        <button type="button" class="btn small" data-act="cancel">Cancel</button>
      </div>
    </form>`;
}

function menuItems(entry) {
  if (entry.deprecated) {
    return '<button type="button" data-act="restore">Restore</button>'
      + '<button type="button" class="danger" data-act="remove">Remove</button>';
  }
  const canBackfill = entry.type !== 'manga' && !(entry.progress && entry.progress.backfill);
  return '<button type="button" data-act="edit">Edit settings</button>'
    + (canBackfill ? '<button type="button" data-act="backfill">Download aired episodes</button>' : '')
    + '<button type="button" data-act="archive">Archive</button>'
    + '<button type="button" class="danger" data-act="remove">Remove</button>';
}

function row(entry) {
  const status = entry.status || {};
  const cover = status.cover_image
    ? `<img class="cover" src="${esc(status.cover_image)}" alt="" loading="lazy">`
    : '<div class="cover"></div>';
  const title = entry.deprecated
    ? `<span class="item-title">${esc(entry.title)}</span>`
    : `<button type="button" class="item-title" data-act="open" title="Find torrents for this show">${esc(entry.title)}</button>`;
  const manga = entry.type === 'manga'
    ? '<span class="tag warn" title="Chapter tracking only works once AniList lists a chapter count">manga · experimental</span>'
    : '';
  return `
    <div class="item${entry.deprecated ? ' archived-row' : ''}" data-title="${esc(entry.title)}">
      ${cover}
      <div class="item-main">
        ${title}
        ${manga}
        <div class="item-status">${statusLine(entry)}</div>
        <div class="item-meta">${metaLine(entry)}</div>
        ${editing === entry.title ? editForm(entry) : ''}
      </div>
      <div class="item-actions">
        <details class="menu">
          <summary class="icon-btn" aria-label="Actions for ${esc(entry.title)}">${DOTS}</summary>
          <div class="menu-list">${menuItems(entry)}</div>
        </details>
      </div>
    </div>`;
}

function render(force = false) {
  const pane = $('#pane-watchlist');
  // A background refresh must not wipe a settings form someone is typing in.
  if (!force && editing && pane.querySelector('form.edit')) return;

  const archivedBox = pane.querySelector('details.archived');
  if (archivedBox) archivedOpen = archivedBox.open;
  const scrollTop = pane.scrollTop;

  if (!state.entries.length) {
    pane.innerHTML = '<p class="empty">Nothing tracked yet. Search AniList under “Add a show” to start.</p>';
    return;
  }
  const active = state.entries.filter(entry => !entry.deprecated);
  const archived = state.entries.filter(entry => entry.deprecated);
  let html = active.map(row).join('');
  if (!active.length) html = '<p class="empty">Every show is archived.</p>';
  if (archived.length) {
    html += `<details class="archived"${archivedOpen ? ' open' : ''}>`
      + `<summary>Archived (${archived.length})</summary>${archived.map(row).join('')}</details>`;
  }
  pane.innerHTML = html;
  pane.scrollTop = scrollTop;
}

async function change(path, entry, done) {
  try {
    const result = await post(path, { title: entry.title });
    if (result.ok === false || result.removed === false) toast(result.reason || 'Nothing changed', 'error');
    else toast(`${done}: ${entry.title}`, 'ok');
  } catch (error) {
    toast(error.message, 'error');
  }
  emit('reload');
}

async function backfill(entry) {
  const question = `Also download the episodes of “${entry.title}” that have already aired?`;
  if (!confirm(question)) return;
  try {
    const result = await post('/api/anime/backfill', { title: entry.title });
    if (!result.ok) toast(result.error || 'Could not queue earlier episodes', 'error');
    else if (result.error) toast(`Queued, but ${result.error}`, 'error');
    else toast('Earlier episodes queued', 'ok');
  } catch (error) {
    toast(error.message, 'error');
  }
  emit('reload');
}

async function onClick(event) {
  const target = event.target.closest('[data-act]');
  if (!target) return;
  const item = target.closest('.item');
  const entry = item ? findEntry(item.dataset.title) : null;
  if (!entry) return;
  const menu = target.closest('details.menu');
  if (menu) menu.open = false;

  const act = target.dataset.act;
  if (act === 'open') {
    emit('open-torrents', { entry: entry.title, episode: null });
  } else if (act === 'review') {
    emit('goto', 'activity');
  } else if (act === 'edit') {
    editing = entry.title;
    render(true);
    const first = $('#pane-watchlist form.edit input');
    if (first) first.focus();
  } else if (act === 'cancel') {
    editing = null;
    render(true);
  } else if (act === 'archive') {
    await change('/api/anime/deprecate', entry, 'Archived');
  } else if (act === 'restore') {
    await change('/api/anime/reactivate', entry, 'Restored');
  } else if (act === 'remove') {
    if (confirm(`Remove “${entry.title}” and its download history?`)) {
      await change('/api/anime/remove', entry, 'Removed');
    }
  } else if (act === 'backfill') {
    await backfill(entry);
  }
}

async function onSubmit(event) {
  const form = event.target.closest('form.edit');
  if (!form) return;
  event.preventDefault();
  const title = form.closest('.item').dataset.title;
  const data = new FormData(form);
  const text = name => String(data.get(name) || '').trim();
  const whole = name => {
    const value = parseInt(text(name), 10);
    return Number.isFinite(value) ? value : 0;
  };
  const body = {
    title,
    submitters: text('submitters').split(',').map(part => part.trim()).filter(Boolean),
    search_name: text('search_name'),
    download_dir: text('download_dir'),
    check_delay: Math.max(0, whole('check_delay')),
    episode_offset: whole('episode_offset'),
  };
  try {
    const result = await post('/api/anime/update', body);
    if (!result.ok) {
      toast(result.reason || 'Could not save', 'error');
      return;
    }
    const index = state.entries.findIndex(entry => entry.title === title);
    if (index >= 0 && result.entry) state.entries[index] = result.entry;
    editing = null;
    toast('Saved', 'ok');
    emit('entries');
  } catch (error) {
    toast(error.message, 'error');
  }
}

export function initWatchlist() {
  const pane = $('#pane-watchlist');
  pane.addEventListener('click', onClick);
  pane.addEventListener('submit', onSubmit);
  on('entries', () => render());
}
