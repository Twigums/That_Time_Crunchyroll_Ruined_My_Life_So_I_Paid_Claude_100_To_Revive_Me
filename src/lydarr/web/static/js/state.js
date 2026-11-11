// Shared page state and a tiny event bus, so the panels never import each other.
//
// Events:
//   entries        the watchlist was (re)loaded
//   status         tracker / Transmission status was polled
//   view           the visible section changed (detail: view name)
//   goto           ask main.js to show a section (detail: view name)
//   reload         ask main.js to reload the watchlist
//   open-torrents  ask the torrent browser to search (detail: { entry, episode })

export const state = {
  entries: [],
  anilist: {},
  defaultDir: '',
  status: null,
  view: 'watchlist',
};

const handlers = new Map();

export function on(name, handler) {
  if (!handlers.has(name)) handlers.set(name, []);
  handlers.get(name).push(handler);
}

export function emit(name, detail) {
  for (const handler of handlers.get(name) || []) handler(detail);
}

export function findEntry(title) {
  return state.entries.find(entry => entry.title === title) || null;
}

export function activeAnime() {
  return state.entries.filter(entry => !entry.deprecated && entry.type !== 'manga');
}
