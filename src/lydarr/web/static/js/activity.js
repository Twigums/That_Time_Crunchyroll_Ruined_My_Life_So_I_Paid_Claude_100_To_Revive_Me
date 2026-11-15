// Activity: every torrent that was added and when, whether Jellyfin then listed
// it, plus releases the tracker held back because their numbering was unexpected.
// A held release is added with one click, which also teaches the tracker that numbering.

import { $, esc, api, post, toast, fmtWhen, fmtAge, shortPath } from './util.js';
import { on, emit } from './state.js';

const KIND = {
  grabbed: 'Added',
  approved: 'Added after your check',
  manual: 'Added by hand',
  review: 'Held for your check',
  missed: 'Gave up',
  skipped: 'Skipped',
  completed: 'Completed',
  in_jellyfin: 'In Jellyfin',
  not_in_jellyfin: 'Not in Jellyfin',
};
const REFRESH = 30000;
const fullDate = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

let data = { review: [], events: [] };

function pad(number) {
  return String(number).padStart(2, '0');
}

function candidateTags(candidate) {
  const tags = [];
  if (candidate.group) tags.push(candidate.group);
  if (candidate.episode !== null && candidate.episode !== undefined) {
    tags.push(candidate.season ? `S${pad(candidate.season)}E${pad(candidate.episode)}` : `ep ${pad(candidate.episode)}`);
  }
  if (candidate.resolution) tags.push(`${candidate.resolution}p`);
  if (candidate.hevc) tags.push('HEVC');
  if (candidate.seeders !== null && candidate.seeders !== undefined) tags.push(`${candidate.seeders} seeds`);
  if (candidate.published) tags.push(`${fmtAge(candidate.published)} old`);
  return tags.map(text => `<span class="tag">${esc(text)}</span>`).join('');
}

function reviewCard(item, index) {
  const candidates = item.candidates.map((candidate, at) => `
    <div class="cand">
      <div class="cand-main">
        <div class="t-name">${esc(candidate.name)}</div>
        <div class="t-tags">${candidateTags(candidate)}</div>
      </div>
      <button type="button" class="btn small primary" data-approve="${index}:${at}">Add this</button>
    </div>`).join('');
  return `
    <div class="review">
      <div class="review-head">
        <strong>${esc(item.title)}</strong> · ep ${item.episode}
        <div class="item-meta">${esc(item.note || 'Unexpected episode number')}</div>
        <div class="item-meta" title="${esc(item.path)}">Saves to ${esc(shortPath(item.path))}</div>
      </div>
      ${candidates}
      <div class="review-foot">
        <button type="button" class="btn small" data-browse="${index}">Search by hand</button>
        <button type="button" class="btn small" data-skip="${index}">Skip this episode</button>
      </div>
    </div>`;
}

function eventRow(event, index) {
  const label = KIND[event.kind] || event.kind;
  const episode = event.episode !== null && event.episode !== undefined ? ` · ep ${event.episode}` : '';
  const lines = [];
  if (event.name) lines.push(esc(event.name));
  if (event.dir) lines.push(`→ ${esc(event.dir)}`);
  if (!event.name && event.detail) lines.push(esc(event.detail));
  const canRetry = (event.kind === 'missed' || event.kind === 'skipped') && episode;
  return `
    <div class="event">
      <div class="event-time" title="${esc(fullDate.format(new Date(event.ts * 1000)))}">${esc(fmtWhen(event.ts))}</div>
      <div><span class="event-what">${esc(label)}</span> ${esc(event.title)}${esc(episode)}</div>
      <div>${canRetry ? `<button type="button" class="btn small" data-retry="${index}">Try again</button>` : ''}</div>
      <div class="event-detail">${lines.join('<br>')}</div>
    </div>`;
}

function render() {
  const parts = [];
  if (data.review.length) {
    parts.push('<div class="section-label">Needs your check</div>');
    parts.push(data.review.map(reviewCard).join(''));
  }
  parts.push('<div class="section-label">History</div>');
  parts.push(data.events.length
    ? data.events.map(eventRow).join('')
    : '<p class="empty">Nothing has been added yet. Torrents the tracker or you add will be listed here.</p>');
  $('#pane-activity').innerHTML = parts.join('');
}

async function refresh() {
  try {
    const fresh = await api('/api/activity');
    data = { review: fresh.review || [], events: fresh.events || [] };
    render();
  } catch (error) {
    $('#pane-activity').innerHTML = `<div class="notice bad">${esc(error.message)}</div>`;
  }
}

async function act(path, body, done) {
  try {
    const result = await post(path, body);
    if (result.ok) toast(done, 'ok');
    else toast(result.error || 'That did not work', 'error');
  } catch (error) {
    toast(error.message, 'error');
  }
  await refresh();
  emit('reload');
}

function onClick(event) {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.approve !== undefined) {
    const [index, at] = button.dataset.approve.split(':').map(Number);
    const item = data.review[index];
    const candidate = item && item.candidates[at];
    if (!candidate) return;
    button.disabled = true;
    act('/api/activity/approve', { title: item.title, episode: item.episode, hash: candidate.hash },
      `Added ep ${item.episode} of ${item.title}`);
  } else if (button.dataset.skip !== undefined) {
    const item = data.review[Number(button.dataset.skip)];
    if (item && confirm(`Skip ep ${item.episode} of “${item.title}”?`)) {
      act('/api/activity/skip', { title: item.title, episode: item.episode }, 'Skipped');
    }
  } else if (button.dataset.browse !== undefined) {
    const item = data.review[Number(button.dataset.browse)];
    if (item) emit('open-torrents', { entry: item.title, episode: item.episode });
  } else if (button.dataset.retry !== undefined) {
    const item = data.events[Number(button.dataset.retry)];
    if (item) act('/api/activity/retry', { title: item.title, episode: item.episode }, 'Queued again');
  }
}

export function initActivity() {
  const pane = $('#pane-activity');
  pane.addEventListener('click', onClick);
  on('view', view => {
    if (view === 'activity') refresh();
  });
  setInterval(() => {
    if (!document.hidden && !pane.hidden) refresh();
  }, REFRESH);
}
