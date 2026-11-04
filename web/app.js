let movies = [];
// Read-only introspection hook for the browser QA harness (no mutation path).
Object.defineProperty(window, '__cqMovies', { get: () => movies });
let filter = 'unwatched';
let selectedGenres = new Set();

const BASE_PATH = (() => {
  const scriptUrl = document.currentScript?.src;
  if (scriptUrl) {
    try {
      return new URL(scriptUrl).pathname.replace(/\/[^/]*$/, '').replace(/\/+$/, '');
    } catch (error) { /* fall through */ }
  }
  let path = window.location.pathname;
  const movieMarker = path.indexOf('/movie/');
  if (movieMarker > -1) path = path.slice(0, movieMarker + 1);
  const dir = path.endsWith('/') ? path : path.replace(/[^/]*$/, '/');
  return dir.replace(/\/+$/, '');
})();
const base = (path) => `${BASE_PATH}${path}`;
// Install-time capabilities from /api/config (optional agent requests, where-to-watch region).
let appConfig = { agent: { ready: false, name: 'your Wizard' }, watch: { enabled: false, region: '', region_name: '' } };
const list = document.querySelector('#list');
const detail = document.querySelector('#detail');
const template = document.querySelector('#rowTemplate');
const tileTemplate = document.querySelector('#tileTemplate');
const searchEl = document.querySelector('#search');
const sortEl = document.querySelector('#sort');
let queueSort = 'queue-order';
const genreChips = document.querySelector('#genreChips');
const addOpen = document.querySelector('#addOpen');
const addRequestPanel = document.querySelector('#addRequestPanel');
const addRequestBackdrop = document.querySelector('#addRequestBackdrop');
const addRequestClose = document.querySelector('#addRequestClose');
const addRequestForm = document.querySelector('#addRequestForm');
const addRequestInput = document.querySelector('#addRequestInput');
const addRequestButton = document.querySelector('#addRequestButton');
const addRequestStatus = document.querySelector('#addRequestStatus');
const srStatus = document.querySelector('#srStatus');
const undoBar = document.querySelector('#undoBar');
const undoText = document.querySelector('#undoText');
const undoButton = document.querySelector('#undoButton');
const brandMeta = document.querySelector('#brandMeta');
const listChrome = ['.toolbar', '.genre-chips', '.filterbar', '#list'].map(sel => document.querySelector(sel));
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

async function api(path, options = {}) {
  const hold = options.method && options.method !== 'GET' ? window.WizardApp?.update.hold('Saving Movies changes') : null;
  try {
    const res = await fetch(base(path), { headers: { 'Content-Type': 'application/json' }, ...options });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const error = new Error(data.error || 'Request failed'); error.data = data; throw error; }
    return data;
  } finally { hold?.release(); }
}
let draftHold = null;
function syncDraftHold() {
  if (addRequestInput.value.trim()) draftHold ||= window.WizardApp?.update.hold('Unsent movie request');
  else { draftHold?.release(); draftHold = null; }
}
addRequestInput.addEventListener('input', syncDraftHold);
document.querySelector('#discardDraft').addEventListener('click', () => {
  addRequestInput.value = ''; syncDraftHold(); setAddRequestStatus('Draft discarded.');
});
let requestTimer = 0, lastRequests = '';
async function refreshRequests() {
  clearTimeout(requestTimer);
  try {
    const { requests = [] } = await api('/api/add-requests');
    const recent = requests.slice(0, 20);
    const progress = document.querySelector('#requestProgress');
    // Only requests that need the owner, or are visibly on their way, earn a card. Finished ones show up as the movie itself.
    const visible = recent.map(requestCardModel).filter(Boolean).slice(0, 5);
    progress.innerHTML = visible.map(renderRequestCard).join('');
    progress.querySelectorAll('[data-request-action]').forEach(b => b.addEventListener('click', async () => {
      const { requestId, requestAction, answer } = b.dataset;
      b.closest('.request-card')?.querySelectorAll('button').forEach(x => { x.disabled = true; });
      const body = requestAction === 'answer' ? { status:'answer', answer } : { status:requestAction };
      try {
        await api('/api/add-requests/' + encodeURIComponent(requestId), { method:'POST', body:JSON.stringify(body) });
        if (requestAction === 'answer') announce(`Got it: ${answer}. Adding it now.`);
      } catch (_) { announce('Could not send that right now. Your request is saved.'); }
      finally { await refreshRequests(); }
    }));
    const signature = JSON.stringify(recent.map(r => [r.id,r.status,r.updated_at]));
    if (lastRequests && signature !== lastRequests && recent.some(r => r.status === 'completed')) await loadMovies();
    lastRequests = signature;
  } catch (_) { /* durable status remains visible through a transient outage */ }
  requestTimer = setTimeout(refreshRequests, document.hidden ? 30000 : 5000);
}

const STALE_REQUEST_MS = 6 * 3600 * 1000;
function requestCardModel(r) {
  const age = Date.now() - Date.parse(r.updated_at || r.created_at || 0);
  const stalled = ['queued','dispatched','processing'].includes(r.status) && age > STALE_REQUEST_MS;
  if (stalled) return { r, tone:'warn', line:'Didn’t finish', actions:[['retry','Try again'],['dismiss','Dismiss']] };
  if (r.status === 'blocked') {
    const choices = Array.isArray(r.choices) ? r.choices.filter(Boolean) : [];
    return { r, tone:'ask', line: r.status_detail || 'Which one did you mean?', choices, actions:[['dismiss','Skip']] };
  }
  if (r.status === 'failed') return { r, tone:'warn', line: r.status_detail || 'Couldn’t add this.', actions:[['retry','Try again'],['dismiss','Dismiss']] };
  if (['not_sent','dispatch_failed'].includes(r.status)) return { r, tone:'warn', line:'Saved, not sent yet', actions:[['retry','Send'],['dismiss','Dismiss']] };
  if (['queued','dispatched','processing'].includes(r.status)) return { r, tone:'busy', line:'Finding it…', actions:[] };
  return null; // completed, dismissed, unknown: nothing to act on
}
function renderRequestCard({ r, tone, line, choices = [], actions }) {
  const id = escapeHtml(r.id);
  const choiceButtons = choices.map(c => `<button type="button" class="request-choice" data-request-id="${id}" data-request-action="answer" data-answer="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('');
  const actionButtons = actions.map(([action, label]) => `<button type="button" class="request-action${action === 'dismiss' ? ' quiet' : ''}" data-request-id="${id}" data-request-action="${action}">${escapeHtml(label)}</button>`).join('');
  return `<div class="request-card ${tone}"><div class="request-head"><b>${escapeHtml(r.text)}</b>${tone === 'busy' ? '<span class="request-spinner" aria-hidden="true"></span>' : ''}</div><p>${escapeHtml(line)}</p>${choiceButtons ? `<div class="request-choices">${choiceButtons}</div>` : ''}${actionButtons ? `<div class="request-actions">${actionButtons}</div>` : ''}</div>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
function announce(message) {
  if (srStatus) srStatus.textContent = message;
}
function setAddRequestStatus(message, tone = '') {
  if (!addRequestStatus) return;
  addRequestStatus.textContent = message;
  addRequestStatus.className = `add-request-status ${tone}`.trim();
}
function agentReady() { return Boolean(appConfig.agent?.ready); }
function agentName() { return appConfig.agent?.name || 'your Wizard'; }
function syncAddMode() {
  const kicker = addRequestPanel?.querySelector('.add-request-kicker');
  const title = document.querySelector('#addRequestTitle');
  const copy = addRequestPanel?.querySelector('.add-request-copy');
  if (agentReady()) {
    if (kicker) kicker.textContent = 'new Movies request';
    if (title) title.textContent = `Ask ${agentName()}`;
    if (copy) copy.textContent = 'Request a proper enriched card. I’ll resolve the exact title before adding it.';
    if (addRequestButton) addRequestButton.textContent = 'Request add';
  } else {
    if (kicker) kicker.textContent = 'add to Movies';
    if (title) title.textContent = 'Add a title';
    if (copy) copy.textContent = 'Type one or more titles, like “Stalker 1979, Heat”. They go to the top of your queue as simple cards.';
    if (addRequestButton) addRequestButton.textContent = 'Add to queue';
  }
}
function openAddRequestPanel() {
  if (!addRequestPanel || !addRequestBackdrop) return;
  dismissUndo();
  syncAddMode();
  addRequestBackdrop.hidden = false;
  addRequestPanel.hidden = false;
  setAddRequestStatus('');
  // The sheet is a history entry so Android/browser Back closes it before anything else.
  if (history.state?.cq !== 'sheet') history.pushState({ cq: 'sheet' }, '', location.href);
  requestAnimationFrame(() => addRequestInput?.focus());
}
function hideAddRequestPanel() {
  if (!addRequestPanel || !addRequestBackdrop) return;
  addRequestPanel.hidden = true;
  addRequestBackdrop.hidden = true;
}
function closeAddRequestPanel() {
  hideAddRequestPanel();
  if (history.state?.cq === 'sheet') quietBack();
}
async function submitAddRequest(event) {
  event.preventDefault();
  const text = addRequestInput.value.trim();
  if (!text) {
    setAddRequestStatus('Type what to add first.', 'error');
    addRequestInput.focus();
    return;
  }
  addRequestButton.disabled = true;
  if (!agentReady()) {
    // No agent integration on this install: add simple cards directly (honest, no silent request).
    addRequestButton.textContent = 'Adding…';
    try {
      const data = await api('/api/add', { method: 'POST', body: JSON.stringify({ text }) });
      if (data.count) {
        const label = data.count === 1 ? (data.added?.[0]?.title || 'Title') : `${data.count} titles`;
        setAddRequestStatus(`${label} added to the top of your queue.`, 'ok');
        announce(`${label} added`);
        addRequestInput.value = '';
        syncDraftHold();
        await loadMovies();
        setTimeout(closeAddRequestPanel, 600);
      } else {
        setAddRequestStatus('Already in your queue, or nothing recognisable to add.', 'error');
      }
    } catch (error) {
      console.error(error);
      setAddRequestStatus('Could not add right now. Try again shortly.', 'error');
    } finally {
      addRequestButton.disabled = false;
      syncAddMode();
    }
    return;
  }
  addRequestButton.textContent = 'Requesting…';
  setAddRequestStatus(`Queuing with ${agentName()}…`);
  try {
    await api('/api/request-add', { method: 'POST', body: JSON.stringify({ text }) });
    setAddRequestStatus('Sent to your Wizard. Progress appears above the queue.', 'ok');
    addRequestInput.value = '';
    syncDraftHold();
    await refreshRequests();
    setTimeout(closeAddRequestPanel, 350);
  } catch (error) {
    console.error(error);
    setAddRequestStatus(error.data?.request ? 'Request saved but not sent. Use Retry saved request above the queue.' : 'Could not confirm your request. Your draft is still here; check progress before retrying.', 'error');
    if (error.data?.request) { addRequestInput.value = ''; syncDraftHold(); }
    await refreshRequests();
  } finally {
    addRequestButton.disabled = false;
    addRequestButton.textContent = 'Request add';
  }
}
function hue(id) { return [...String(id)].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 360; }
function initials(title) { return String(title || '?').replace(/^(the|a|an)\s+/i, '').slice(0, 2).toUpperCase(); }
function ratingValue(movie) {
  if (movie.rating_source !== 'imdb') return null;
  const value = Number(movie.rating);
  return Number.isFinite(value) && value > 0 && value <= 10 ? value : null;
}
function ratingClass(value) {
  if (value >= 7.5) return 'high';
  if (value >= 6.0) return 'medium';
  return 'low';
}
function ratingLabel(value) {
  return value.toFixed(1);
}
function ratingVoteCount(movie) {
  const value = Number(movie.rating_count);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : null;
}
function compactVoteCount(movie) {
  const count = ratingVoteCount(movie);
  return count === null ? '' : new Intl.NumberFormat('en', {
    notation: 'compact', maximumFractionDigits: 1,
  }).format(count);
}
function imdbRatingAriaLabel(movie, value) {
  const count = ratingVoteCount(movie);
  return `IMDb user rating ${ratingLabel(value)} out of 10${count === null ? '' : ` from ${count.toLocaleString()} votes`}`;
}
function runtimeMinutes(movie) {
  const value = Number(movie.runtime_minutes ?? movie.length_minutes ?? movie.duration_minutes ?? movie.runtime);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : null;
}
function runtimeLabel(movie) {
  const minutes = runtimeMinutes(movie);
  if (!minutes) return '';
  return `${minutes} min`;
}
function localToday() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}
function watchHistory(movie) {
  return Array.isArray(movie.watch_history) ? movie.watch_history : [];
}
function hasWatchHistory(movie) {
  return watchHistory(movie).length > 0 || Boolean(movie.watched);
}
function isQueued(movie) {
  return movie.queued !== false;
}
function latestWatchedOn(movie) {
  return movie.latest_watched_on || watchHistory(movie)[0]?.watched_on || '';
}
function formatWatchDate(value) {
  if (!value) return 'date unknown';
  const date = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(date.getTime())) return 'date unknown';
  return new Intl.DateTimeFormat('en', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  }).format(date);
}
function watchSummary(movie) {
  const count = Number(movie.watch_count || watchHistory(movie).length || 0);
  const countLabel = count > 1 ? `${count} watches` : '1 watch';
  return `Watched ${formatWatchDate(latestWatchedOn(movie))}${count > 1 ? ` · ${countLabel}` : ''}`;
}
function movieSearchText(movie) {
  return [movie.title, movie.year, ...(movie.genres || []), ...(movie.creators || []), ...(movie.directors || []), ...(movie.cast || [])].join(' ').toLowerCase();
}

function updateBrandMeta() {
  if (!brandMeta) return;
  const unwatched = movies.filter(isQueued).length;
  brandMeta.textContent = `${movies.length} films · ${unwatched} in queue`;
}

async function loadMovies() {
  const data = await api('/api/movies');
  movies = data.movies || [];
  updateBrandMeta();
  populateGenres();
  render();
  route();
}

function allGenres() {
  return [...new Set(movies.flatMap(m => m.genres || []))].sort();
}

function populateGenres() {
  const genres = allGenres();
  selectedGenres = new Set([...selectedGenres].filter(g => genres.includes(g)));
  genreChips.innerHTML = '';
  for (const genre of genres) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'genre-chip';
    button.dataset.genre = genre;
    button.classList.toggle('active', selectedGenres.has(genre));
    button.textContent = selectedGenres.has(genre) ? `✓ ${genre}` : genre;
    button.addEventListener('click', () => toggleGenre(genre));
    genreChips.append(button);
  }
}

function toggleGenre(genre) {
  if (selectedGenres.has(genre)) selectedGenres.delete(genre);
  else selectedGenres.add(genre);
  populateGenres();
  render();
}

function compareDateAddedNewestFirst(movieA, movieB) {
  const aTime = Date.parse(movieA.added_at || '');
  const bTime = Date.parse(movieB.added_at || '');
  const aHasDate = Number.isFinite(aTime);
  const bHasDate = Number.isFinite(bTime);
  if (aHasDate && bHasDate && aTime !== bTime) return bTime - aTime;
  if (aHasDate && !bHasDate) return -1;
  if (!aHasDate && bHasDate) return 1;
  return movieA._index - movieB._index;
}

function compareLatestWatchedNewestFirst(movieA, movieB) {
  const aTime = Date.parse(latestWatchedOn(movieA) || '');
  const bTime = Date.parse(latestWatchedOn(movieB) || '');
  const aHasDate = Number.isFinite(aTime);
  const bHasDate = Number.isFinite(bTime);
  if (aHasDate && bHasDate && aTime !== bTime) return bTime - aTime;
  if (aHasDate && !bHasDate) return -1;
  if (!aHasDate && bHasDate) return 1;
  return movieA._index - movieB._index;
}

function syncSortForFilter() {
  if (filter === 'watched') {
    if (sortEl.value !== 'watched-date') queueSort = sortEl.value;
    sortEl.value = 'watched-date';
    sortEl.disabled = true;
    return;
  }
  sortEl.disabled = false;
  if (sortEl.value === 'watched-date') sortEl.value = queueSort;
}

function visibleMovies() {
  const query = searchEl.value.trim().toLowerCase();
  let result = movies.map((movie, index) => ({ ...movie, _index: index + 1 })).filter(movie => {
    if (filter === 'watched' && !hasWatchHistory(movie)) return false;
    if (filter === 'unwatched' && !isQueued(movie)) return false;
    if (selectedGenres.size) {
      const movieGenres = new Set(movie.genres || []);
      const hasSelectedGenre = [...selectedGenres].some(genre => movieGenres.has(genre));
      if (!hasSelectedGenre) return false;
    }
    if (!query) return true;
    return movieSearchText(movie).includes(query);
  });
  result = [...result].sort((a, b) => {
    if (filter === 'watched' || sortEl.value === 'watched-date') return compareLatestWatchedNewestFirst(a, b);
    if (sortEl.value === 'queue-order') return a._index - b._index;
    if (sortEl.value === 'title') return a.title.localeCompare(b.title);
    if (sortEl.value === 'year-desc') return (b.year || 0) - (a.year || 0);
    if (sortEl.value === 'year-asc') return (a.year || 9999) - (b.year || 9999);
    if (sortEl.value === 'date-added') return compareDateAddedNewestFirst(a, b);
    return a._index - b._index;
  });
  return result;
}

/* ── View toggle (presentation-only, persisted) ─────────────── */
let view = localStorage.getItem('cinequeue.view') === 'grid' ? 'grid' : 'list';
function applyView() {
  list.dataset.view = view;
  document.querySelectorAll('.viewtoggle [data-view]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.view === view));
  });
}
document.querySelectorAll('.viewtoggle [data-view]').forEach(button => {
  button.addEventListener('click', () => {
    view = button.dataset.view === 'grid' ? 'grid' : 'list';
    localStorage.setItem('cinequeue.view', view);
    applyView();
    render();
  });
});
applyView();

/* ── Undo snackbar (one batched timer) ──────────────────────── */
const UNDO_WINDOW_MS = 3000;
let undoBatch = []; // [{ kind: 'archive'|'watch', id, index, title, eventId? }]
let undoTimer = null;

function renderUndoText() {
  const watched = undoBatch.filter(item => item.kind === 'watch').length;
  const archived = undoBatch.length - watched;
  if (watched && !archived) undoText.textContent = watched > 1 ? `Marked ${watched} films watched` : 'Marked watched';
  else if (archived && !watched) undoText.textContent = archived > 1 ? `Archived ${archived} films` : 'Archived 1 film';
  else undoText.textContent = `${undoBatch.length} actions completed`;
}
function startUndoTimer() {
  clearTimeout(undoTimer);
  undoTimer = setTimeout(() => {
    undoBatch = [];
    undoBar.hidden = true;
    undoBar.classList.remove('visible');
  }, UNDO_WINDOW_MS);
}
function showUndoBar() {
  undoBar.classList.remove('error');
  undoButton.textContent = 'Undo';
  renderUndoText();
  undoBar.hidden = false;
  requestAnimationFrame(() => undoBar.classList.add('visible'));
  startUndoTimer();
}
function dismissUndo() {
  clearTimeout(undoTimer);
  undoBatch = [];
  undoBar.hidden = true;
  undoBar.classList.remove('visible');
}
async function runUndo() {
  const batch = undoBatch;
  dismissUndo();
  const restored = [];
  const failedItems = [];
  for (const item of [...batch].sort((a, b) => a.index - b.index)) {
    try {
      if (item.kind === 'watch') {
        await api(`/api/watch-delete/${encodeURIComponent(item.id)}/${encodeURIComponent(item.eventId)}`, {
          method: 'POST', body: JSON.stringify({ requeue: true }),
        });
      } else {
        await api(`/api/restore/${encodeURIComponent(item.id)}`, { method: 'POST', body: '{}' });
      }
      restored.push(item);
    } catch (error) {
      console.error(error);
      failedItems.push(item);
    }
  }
  if (restored.length) await loadMovies();
  if (failedItems.length) {
    undoBatch = failedItems;
    undoText.textContent = failedItems.length > 1 ? `Could not restore ${failedItems.length} actions.` : 'Could not restore action.';
    undoButton.textContent = 'Retry';
    undoBar.classList.add('error');
    undoBar.hidden = false;
    requestAnimationFrame(() => undoBar.classList.add('visible'));
    clearTimeout(undoTimer);
    undoTimer = setTimeout(() => {
      undoBatch = [];
      undoBar.hidden = true;
      undoBar.classList.remove('visible', 'error');
    }, UNDO_WINDOW_MS);
  }
}
if (undoButton) undoButton.addEventListener('click', runUndo);
if (undoBar) {
  undoBar.addEventListener('mouseenter', () => clearTimeout(undoTimer));
  undoBar.addEventListener('mouseleave', () => { if (!undoBar.hidden && undoBatch.length) startUndoTimer(); });
  undoBar.addEventListener('focusin', () => clearTimeout(undoTimer));
  undoBar.addEventListener('focusout', () => { if (!undoBar.hidden && undoBatch.length) startUndoTimer(); });
}

/* ── Queue membership + watch history ───────────────────────── */
function mergeMovie(updated) {
  const index = movies.findIndex(movie => movie.id === updated.id);
  if (index > -1) movies[index] = { ...movies[index], ...updated };
  updateBrandMeta();
  return updated;
}
async function markWatched(movie, watchedOn = localToday()) {
  const updated = await api(`/api/watch/${encodeURIComponent(movie.id)}`, {
    method: 'POST', body: JSON.stringify({ watched_on: watchedOn }),
  });
  return mergeMovie(updated.movie);
}
async function queueAgain(movie) {
  const updated = await api(`/api/watch-again/${encodeURIComponent(movie.id)}`, { method: 'POST', body: '{}' });
  const index = movies.findIndex(item => item.id === updated.movie.id);
  const merged = index > -1 ? { ...movies[index], ...updated.movie } : updated.movie;
  if (index > -1) movies.splice(index, 1);
  movies.unshift(merged);
  updateBrandMeta();
  return merged;
}
async function editWatchDate(movie, eventId, watchedOn) {
  const updated = await api(`/api/watch-date/${encodeURIComponent(movie.id)}/${encodeURIComponent(eventId)}`, {
    method: 'POST', body: JSON.stringify({ watched_on: watchedOn }),
  });
  return mergeMovie(updated.movie);
}
async function deleteWatchEvent(movie, eventId, { requeue = false } = {}) {
  const updated = await api(`/api/watch-delete/${encodeURIComponent(movie.id)}/${encodeURIComponent(eventId)}`, {
    method: 'POST', body: JSON.stringify({ requeue }),
  });
  return mergeMovie(updated.movie);
}
function watchActionMode(movie, context = filter) {
  if (context === 'watched') return isQueued(movie) ? 'record-rewatch' : 'rewatch';
  return 'unwatched';
}
async function markWatchedWithUndo(movie, watchedOn = localToday()) {
  const beforeIds = new Set(watchHistory(movie).map(event => event.id));
  const index = movies.findIndex(item => item.id === movie.id);
  const updated = await markWatched(movie, watchedOn);
  const added = watchHistory(updated).find(event => !beforeIds.has(event.id));
  if (added) {
    undoBatch.push({ kind: 'watch', id: movie.id, eventId: added.id, index, title: movie.title });
    showUndoBar();
  }
  return updated;
}
async function primaryWatchAction(movie, mode = watchActionMode(movie), { undoWatch = false } = {}) {
  if (mode === 'rewatch') return queueAgain(movie);
  return undoWatch ? markWatchedWithUndo(movie) : markWatched(movie);
}
/* ── Archive / restore ──────────────────────────────────────── */
async function archiveMovie(movie, { onBeforePost } = {}) {
  const index = movies.findIndex(m => m.id === movie.id);
  const record = movies[index];
  if (!record) return false;
  if (onBeforePost) onBeforePost();
  else {
    movies.splice(index, 1);
    updateBrandMeta();
    render();
  }
  try {
    await api(`/api/archive/${encodeURIComponent(movie.id)}`, { method: 'POST', body: '{}' });
    undoBatch.push({ kind: 'archive', id: movie.id, index, title: movie.title });
    showUndoBar();
    announce(`Archived ${movie.title}, undo available`);
    return true;
  } catch (error) {
    console.error(error);
    const current = movies.findIndex(m => m.id === movie.id);
    if (current === -1) movies.splice(Math.min(index, movies.length), 0, record);
    updateBrandMeta();
    render();
    undoText.textContent = 'Archive failed';
    undoBar.classList.add('error');
    undoBar.hidden = false;
    requestAnimationFrame(() => undoBar.classList.add('visible'));
    clearTimeout(undoTimer);
    undoTimer = setTimeout(() => {
      undoBar.hidden = true;
      undoBar.classList.remove('visible', 'error');
    }, UNDO_WINDOW_MS);
    return false;
  }
}

/* ── Row overflow menu (single popover in #menuLayer) ───────── */
let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  openMenu.button.setAttribute('aria-expanded', 'false');
  openMenu.pop.remove();
  openMenu = null;
}
function openRowMenu(row, movie) {
  const button = row.querySelector('.row-menu');
  if (openMenu && openMenu.button === button) { closeMenu(); return; }
  closeMenu();
  const pop = document.createElement('div');
  pop.className = 'row-menu-pop';
  pop.setAttribute('role', 'menu');
  const watchedContext = filter === 'watched';
  const actionMode = watchActionMode(movie, watchedContext ? 'watched' : 'unwatched');
  const watchItem = document.createElement('button');
  watchItem.type = 'button';
  watchItem.setAttribute('role', 'menuitem');
  watchItem.dataset.menu = 'watch';
  watchItem.textContent = actionMode === 'rewatch'
    ? 'Watch again'
    : (actionMode === 'record-rewatch' ? 'Mark watched again' : 'Mark watched');
  const editItem = document.createElement('button');
  editItem.type = 'button';
  editItem.setAttribute('role', 'menuitem');
  editItem.dataset.menu = 'edit-watch-date';
  editItem.textContent = 'Edit watch date';
  const archiveItem = document.createElement('button');
  archiveItem.type = 'button';
  archiveItem.setAttribute('role', 'menuitem');
  archiveItem.dataset.menu = 'archive';
  archiveItem.textContent = 'Archive';
  pop.append(watchItem);
  if (watchedContext && hasWatchHistory(movie)) pop.append(editItem);
  pop.append(archiveItem);
  watchItem.addEventListener('click', async (event) => {
    event.stopPropagation();
    watchItem.disabled = true;
    closeMenu();
    button.focus();
    try {
      await primaryWatchAction(movie, actionMode, { undoWatch: true });
      render();
      announce(actionMode === 'rewatch'
        ? `${movie.title} added to your watchlist again`
        : (actionMode === 'record-rewatch'
          ? `Recorded another watch for ${movie.title}, undo available`
          : `Marked ${movie.title} watched, undo available`));
    } catch (error) {
      console.error(error);
    }
  });
  editItem.addEventListener('click', (event) => {
    event.stopPropagation();
    closeMenu();
    openMovie(movie.id);
    requestAnimationFrame(() => detail.querySelector('.watch-event input')?.focus());
  });
  archiveItem.addEventListener('click', (event) => {
    event.stopPropagation();
    closeMenu();
    const domIndex = visibleRowIndex(row);
    archiveMovie(movie).then((ok) => { if (ok) focusRowAtIndex(domIndex); });
  });
  const rect = button.getBoundingClientRect();
  pop.style.top = `${Math.round(rect.bottom + 4)}px`;
  pop.style.right = `${Math.round(window.innerWidth - rect.right)}px`;
  document.querySelector('#menuLayer').append(pop);
  button.setAttribute('aria-expanded', 'true');
  openMenu = { pop, button, row };
}
document.addEventListener('click', (event) => {
  if (openMenu && !openMenu.pop.contains(event.target) && event.target !== openMenu.button) closeMenu();
});
document.addEventListener('focusin', (event) => {
  if (openMenu && !openMenu.pop.contains(event.target) && !openMenu.row.contains(event.target)) closeMenu();
});

/* ── Swipe engine (list view, touch/pen only) ───────────────── */
const LOCK_PX = 10;
const AXIS_RATIO = 1.5;
const EDGE_GUARD = 20;
const RUBBER = 0.25;
const FLICK = 0.55; // px/ms
const TAP_SLOP = 8;
let activeSwipe = null;

function swipeThreshold(row) {
  return Math.min(140, Math.max(72, row.offsetWidth * 0.33));
}
function maxDrag(row) {
  return row.offsetWidth * 0.55;
}
function dragOffset(row, dx) {
  const max = maxDrag(row);
  if (Math.abs(dx) <= max) return dx;
  return Math.sign(dx) * (max + (Math.abs(dx) - max) * RUBBER);
}
function paintSwipe(row, dx) {
  const threshold = swipeThreshold(row);
  const surface = row.querySelector('.movie-surface');
  const progress = Math.min(1, Math.abs(dx) / threshold);
  surface.style.transform = `translate3d(${dx}px,0,0)`;
  const watchBg = row.querySelector('.swipe-bg.watch');
  const archiveBg = row.querySelector('.swipe-bg.archive');
  const active = dx >= 0 ? watchBg : archiveBg;
  const inactive = dx >= 0 ? archiveBg : watchBg;
  active.style.opacity = String(0.35 + progress * 0.65);
  inactive.style.opacity = '0';
  active.querySelector('.swipe-word').style.opacity = progress >= 1 ? '1' : '0';
  const word = active.querySelector('.swipe-word');
  word.textContent = dx >= 0
    ? (row.dataset.primaryAction === 'rewatch'
      ? 'Watch again'
      : (row.dataset.primaryAction === 'record-rewatch' ? 'Watched again' : 'Watched'))
    : 'Archive';
}
function resetSwipePaint(row) {
  const surface = row.querySelector('.movie-surface');
  surface.style.transform = '';
  row.querySelectorAll('.swipe-bg').forEach(bg => {
    bg.style.opacity = '0';
    bg.querySelector('.swipe-word').style.opacity = '0';
  });
}
function setRowAnimating(row, animating) {
  row.classList.toggle('swipe-animating', animating);
  row.querySelector('.movie-surface').classList.toggle('swipe-animating', animating);
}
function collapseRow(row, done) {
  if (REDUCED_MOTION.matches) {
    row.style.opacity = '0';
    done();
    return;
  }
  row.classList.add('swipe-collapsing');
  row.style.height = `${row.offsetHeight}px`;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      row.classList.add('swipe-collapsed');
      setTimeout(done, 150);
    });
  });
}
function commitSwipe(row, movie, direction) {
  const width = row.offsetWidth;
  if (width < 10) return; // hidden/zero-size row: nothing to animate, leave state alone
  const primaryMode = row.dataset.primaryAction || 'unwatched';
  const finishWatchedRender = () => {
    updateBrandMeta();
    render();
    announce(primaryMode === 'rewatch'
      ? `${movie.title} added to your watchlist again`
      : (primaryMode === 'record-rewatch'
        ? `Recorded another watch for ${movie.title}, undo available`
        : `Marked ${movie.title} watched, undo available`));
  };
  setRowAnimating(row, true);
  if (REDUCED_MOTION.matches) {
    row.querySelector('.movie-surface').style.transform = '';
    row.style.opacity = '0';
    // Deferred row removal — commitSwipe relies on the row staying in the
    // DOM so the collapse animation can measure height before removing.
    if (direction > 0) {
      primaryWatchAction(movie, primaryMode, { undoWatch: true })
        .then(() => { row.remove(); finishWatchedRender(); })
        .catch(() => { row.style.opacity = ''; render(); });
    } else {
      archiveMovie(movie, {
        onBeforePost: () => {
          movies.splice(movies.findIndex(m => m.id === movie.id), 1);
          updateBrandMeta();
          row.remove();
          render();
        },
      });
    }
    return;
  }
  const surface = row.querySelector('.movie-surface');
  surface.style.transform = `translate3d(${direction * width}px,0,0)`;
  setTimeout(() => {
    collapseRow(row, () => {
      if (direction > 0) {
        primaryWatchAction(movie, primaryMode, { undoWatch: true })
          .then(finishWatchedRender)
          .catch(error => { console.error(error); render(); });
      } else {
        archiveMovie(movie, {
          onBeforePost: () => {
            movies.splice(movies.findIndex(m => m.id === movie.id), 1);
            updateBrandMeta();
            row.remove();
            render();
          },
        });
      }
    });
  }, 180);
}
function attachSwipe(row, movie) {
  let state = null;

  row.addEventListener('pointerdown', (event) => {
    if (view !== 'list') return;
    if (event.pointerType !== 'touch' && event.pointerType !== 'pen') return;
    // One row owns one pointer stream. A second finger must not replace the
    // active state and leave the first drag painted but unfinishable.
    if (state || activeSwipe) return;
    closeMenu();
    state = {
      phase: 'IDLE',
      pointerId: event.pointerId,
      captureTarget: null,
      startX: event.clientX,
      startY: event.clientY,
      dx: 0,
      dy: 0,
      lastT: performance.now(),
      lastX: event.clientX,
      velocity: 0,
      dragged: false,
    };
    // A previous cancelled swipe may still be inside its snap-back window.
    // New touch input must always start with a transition-free surface.
    setRowAnimating(row, false);
    row.classList.remove('swipe-dragging');
    activeSwipe = state;
  });

  row.addEventListener('pointermove', (event) => {
    if (!state || event.pointerId !== state.pointerId) return;
    if (state.phase === 'ABANDONED') return;
    state.dx = event.clientX - state.startX;
    state.dy = event.clientY - state.startY;
    const now = performance.now();
    const dt = now - state.lastT;
    if (dt > 0) {
      state.velocity = state.velocity * 0.7 + ((event.clientX - state.lastX) / dt) * 0.3;
      state.lastT = now;
      state.lastX = event.clientX;
    }
    if (state.phase === 'IDLE') {
      const absX = Math.abs(state.dx);
      const absY = Math.abs(state.dy);
      if (absY >= LOCK_PX && absY > absX * AXIS_RATIO) {
        state.phase = 'ABANDONED';
        activeSwipe = null;
        return;
      }
      if (absX >= LOCK_PX && absX > absY * AXIS_RATIO) {
        if (state.startX <= EDGE_GUARD) {
          state.phase = 'ABANDONED';
          activeSwipe = null;
          return;
        }
        state.phase = 'DRAG';
        state.dragged = true;
        row.classList.add('swipe-dragging');
        row.dataset.swiped = '1';
        // Touch pointers are implicitly captured by the original hit child.
        // Transferring that capture to `row` emits lostpointercapture from the
        // child; treating that transfer as cancellation caused real Android
        // swipes to snap back after their first move. Keep implicit capture
        // when present and only capture the row when the browser has not.
        const hit = event.target;
        try {
          if (hit && typeof hit.hasPointerCapture === 'function' && hit.hasPointerCapture(event.pointerId)) {
            state.captureTarget = hit;
          } else {
            row.setPointerCapture(event.pointerId);
            state.captureTarget = row;
          }
        } catch (error) { /* synthetic or already-released pointer */ }
      } else {
        return;
      }
    }
    if (state.phase === 'DRAG') {
      event.preventDefault();
      paintSwipe(row, dragOffset(row, state.dx));
    }
  });

  const finish = (event, cancelled) => {
    // Always release the global active swipe on a matching pointer end, even
    // if this row was re-rendered mid-gesture (its own state is gone).
    if (activeSwipe && activeSwipe.pointerId === event.pointerId && activeSwipe !== state) {
      activeSwipe = null;
    }
    if (!state || event.pointerId !== state.pointerId) return;
    const s = state;
    state = null;
    if (activeSwipe === s) activeSwipe = null;
    row.classList.remove('swipe-dragging');
    if (s.phase !== 'DRAG' || cancelled) {
      if (s.phase === 'DRAG') {
        setRowAnimating(row, true);
        resetSwipePaint(row);
        setTimeout(() => setRowAnimating(row, false), 170);
      }
      return;
    }
    const threshold = swipeThreshold(row);
    const commitRight = s.dx >= threshold || (s.velocity >= FLICK && s.dx >= 40);
    const commitLeft = -s.dx >= threshold || (-s.velocity >= FLICK && -s.dx >= 40);
    if (commitRight) {
      commitSwipe(row, movie, 1);
    } else if (commitLeft) {
      commitSwipe(row, movie, -1);
    } else {
      setRowAnimating(row, true);
      resetSwipePaint(row);
      setTimeout(() => setRowAnimating(row, false), 170);
    }
  };
  row.addEventListener('pointerup', (event) => finish(event, false));
  row.addEventListener('pointercancel', (event) => finish(event, true));
  row.addEventListener('lostpointercapture', (event) => {
    // Ignore capture changes from descendants. In particular, moving implicit
    // touch capture between a child and this row is not browser cancellation.
    if (state && state.pointerId === event.pointerId && state.phase === 'DRAG'
        && event.target === state.captureTarget) finish(event, true);
  });
}

/* ── Row / tile rendering ───────────────────────────────────── */
function renderRow(movie) {
  const node = template.content.cloneNode(true);
  const row = node.querySelector('.movie');
  const thumb = node.querySelector('.thumb');
  const img = node.querySelector('.thumb img');
  const fallback = node.querySelector('.thumb b');
  const title = node.querySelector('h2');
  const year = node.querySelector('.info p');
  const ratingBadge = node.querySelector('.rating-badge');
  const menuButton = node.querySelector('.row-menu');

  row.dataset.movieId = movie.id;
  row.dataset.primaryAction = watchActionMode(movie, filter);
  row.setAttribute('aria-label', `Open ${movie.title} details`);
  thumb.style.setProperty('--glow', `hsla(${hue(movie.id)},80%,58%,.35)`);
  fallback.textContent = initials(movie.title);
  title.textContent = movie.title;
  year.textContent = filter === 'watched'
    ? [watchSummary(movie), movie.year || 'Year unknown', (movie.genres || [])[0]].filter(Boolean).join(' · ')
    : [movie.year || 'Year unknown', (movie.genres || [])[0], runtimeLabel(movie)].filter(Boolean).join(' · ');
  const value = ratingValue(movie);
  if (value) {
    const ariaLabel = imdbRatingAriaLabel(movie, value);
    ratingBadge.textContent = `IMDb ${ratingLabel(value)}`;
    ratingBadge.classList.add(ratingClass(value));
    ratingBadge.title = ariaLabel;
    ratingBadge.setAttribute('aria-label', ariaLabel);
  } else {
    ratingBadge.textContent = '';
    ratingBadge.removeAttribute('title');
    ratingBadge.removeAttribute('aria-label');
  }
  img.src = movie.poster || '';
  img.alt = `Poster for ${movie.title}`;
  if (!movie.poster) img.classList.add('broken');
  img.addEventListener('error', () => { img.classList.add('broken'); thumb.classList.add('no-img'); });

  menuButton.setAttribute('aria-label', `Actions for ${movie.title}`);
  menuButton.addEventListener('click', (event) => {
    event.stopPropagation();
    openRowMenu(row, movie);
  });

  // Capture-phase click suppression after any DRAG or pointer travel
  // (text-selection drags on desktop still fire click at mouse-up).
  let downX = null;
  let downY = null;
  row.addEventListener('click', (event) => {
    if (row.dataset.swiped === '1') {
      event.stopPropagation();
      event.preventDefault();
      return;
    }
    if (event.target.closest('.row-menu')) return; // let the ⋯ button handle it
    if (downX !== null && Math.hypot(event.clientX - downX, event.clientY - downY) > TAP_SLOP) {
      event.stopPropagation();
      event.preventDefault();
      return;
    }
    openMovie(movie.id);
  }, true);
  row.addEventListener('pointerdown', (event) => {
    delete row.dataset.swiped;
    downX = event.clientX;
    downY = event.clientY;
  }, true);

  row.addEventListener('keydown', (event) => {
    if (event.target !== row) {
      if (event.key === 'Escape' && openMenu) {
        closeMenu();
        menuButton.focus();
        event.stopPropagation();
      }
      return;
    }
    if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
      event.preventDefault();
      openMovie(movie.id);
    } else if (event.key === 'w' || event.key === 'W') {
      event.preventDefault();
      if (event.repeat || row.dataset.actionPending === '1') return;
      row.dataset.actionPending = '1';
      const actionMode = watchActionMode(movie, filter);
      primaryWatchAction(movie, actionMode, { undoWatch: true })
        .then(() => {
          render();
          announce(actionMode === 'rewatch'
            ? `${movie.title} added to your watchlist again`
            : (actionMode === 'record-rewatch'
              ? `Recorded another watch for ${movie.title}, undo available`
              : `Marked ${movie.title} watched, undo available`));
        })
        .catch(console.error)
        .finally(() => { delete row.dataset.actionPending; });
    } else if (event.key === 'e' || event.key === 'E' || event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      const domIndex = visibleRowIndex(row);
      archiveMovie(movie).then((ok) => { if (ok) focusRowAtIndex(domIndex); });
    }
  });

  attachSwipe(row, movie);
  return node;
}

function visibleRowIndex(row) {
  return [...list.querySelectorAll('.movie')].indexOf(row);
}

function focusRowAtIndex(index) {
  const rows = [...list.querySelectorAll('.movie')];
  if (!rows.length || index < 0) return;
  rows[Math.min(index, rows.length - 1)]?.focus();
}

function renderTile(movie) {
  const node = tileTemplate.content.cloneNode(true);
  const tile = node.querySelector('.tile');
  const img = node.querySelector('img');
  const fallback = node.querySelector('b');
  tile.dataset.movieId = movie.id;
  tile.setAttribute('aria-label', `Open ${movie.title} details`);
  tile.style.setProperty('--glow', `hsla(${hue(movie.id)},80%,58%,.35)`);
  fallback.textContent = initials(movie.title);
  img.src = movie.poster || '';
  img.alt = `Poster for ${movie.title}`;
  if (!movie.poster) img.classList.add('broken');
  img.addEventListener('error', () => { img.classList.add('broken'); tile.classList.add('no-img'); });
  let tileSwiped = false;
  tile.addEventListener('click', (event) => {
    if (tileSwiped) {
      event.preventDefault();
      tileSwiped = false;
      return;
    }
    openMovie(movie.id);
  });
  tile.addEventListener('pointerdown', () => { tileSwiped = false; }, true);
  tile.addEventListener('pointermove', (event) => {
    if (event.pointerType === 'mouse') return;
    tileSwiped = true; // any touch/pen move is a scroll, not a tap
  }, true);
  tile.addEventListener('keydown', (event) => {
    if (event.target !== tile) return;
    if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
      event.preventDefault();
      openMovie(movie.id);
    }
  });
  return node;
}

function showList() {
  detail.classList.add('hidden');
  listChrome.forEach(el => el && el.classList.remove('hidden'));
}

function detailNotesMarkup(movie) {
  const notes = String(movie.notes || '').trim();
  const episodes = Array.isArray(movie.featured_episodes)
    ? movie.featured_episodes.filter(episode => String(episode?.title || '').trim())
    : [];
  if (!notes && !episodes.length) return '';

  const heading = episodes.length ? 'Highlighted episodes' : 'Notes';
  const episodeList = episodes.length ? `
    <ul class="detail-highlights">
      ${episodes.map(episode => {
        const meta = [
          episode.season && episode.episode ? `S${episode.season}E${episode.episode}` : '',
          episode.runtime_minutes ? `${episode.runtime_minutes} min` : '',
        ].filter(Boolean).join(' · ');
        return `<li class="detail-highlight"><b>${escapeHtml(episode.title)}</b>${meta ? `<span>${escapeHtml(meta)}</span>` : ''}</li>`;
      }).join('')}
    </ul>` : '';

  return `
    <section class="detail-notes" aria-labelledby="detailNotesTitle">
      <h2 id="detailNotesTitle">${heading}</h2>
      ${notes ? `<p>${escapeHtml(notes)}</p>` : ''}
      ${episodeList}
    </section>`;
}

function safeWatchUrl(value, fallback = '') {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:' ? url.href : fallback;
  } catch (_error) {
    return fallback;
  }
}

function watchRegion() { return appConfig.watch?.region || ''; }
function watchRegionName() { return appConfig.watch?.region_name || watchRegion(); }
function justWatchSearchUrl(title) {
  return `https://www.justwatch.com/${encodeURIComponent((watchRegion() || 'us').toLowerCase())}/search?q=${encodeURIComponent(title || '')}`;
}
function whereToWatchHead() {
  return `
    <div class="where-to-watch-head">
      <div>
        <h2>Where to watch</h2>
        <p>Availability in ${escapeHtml(watchRegionName())}</p>
      </div>
      <span class="region-badge" aria-label="${escapeHtml(watchRegionName())}">${escapeHtml(watchRegion())}</span>
    </div>`;
}

function renderWhereToWatch(section, movie, data) {
  if (!section || section.dataset.movieId !== movie.id) return;
  const fallbackUrl = justWatchSearchUrl(movie.title);
  const sourceUrl = safeWatchUrl(data?.source?.url, fallbackUrl);
  const groups = Array.isArray(data?.groups) ? data.groups : [];
  const offers = groups.map(group => {
    const services = (Array.isArray(group.services) ? group.services : []).map(service => {
      const url = safeWatchUrl(service.url, sourceUrl);
      return `<a class="watch-provider" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(service.name)}</a>`;
    }).join('');
    if (!services) return '';
    return `
      <div class="watch-provider-group">
        <b>${escapeHtml(group.label)}</b>
        <div class="watch-provider-links">${services}</div>
      </div>`;
  }).join('');
  const status = offers
    ? `<div class="watch-provider-groups">${offers}</div>`
    : `<p class="watch-provider-empty">No ${escapeHtml(watchRegionName())} streaming options are listed yet.</p>`;
  const stale = data?.stale ? ' · Cached result' : '';
  section.innerHTML = `
    ${whereToWatchHead()}
    ${status}
    <p class="watch-attribution">Streaming data from <a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">JustWatch</a>${stale}</p>`;
}

async function loadWhereToWatch(movie) {
  const section = detail.querySelector('.where-to-watch');
  if (!section || section.dataset.movieId !== movie.id) return;
  try {
    const data = await api(`/api/watch-providers/${encodeURIComponent(movie.id)}`);
    renderWhereToWatch(section, movie, data);
  } catch (error) {
    console.error(error);
    if (section.dataset.movieId !== movie.id) return;
    const sourceUrl = justWatchSearchUrl(movie.title);
    section.innerHTML = `
      ${whereToWatchHead()}
      <p class="watch-provider-empty">${escapeHtml(watchRegionName())} availability couldn’t be refreshed.</p>
      <p class="watch-attribution"><a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">Check JustWatch</a></p>`;
  }
}

// Reply: opens an UNSENT main Chats draft with this title attached (WizardApp.nav.openContextDraft).
// Every fact is already in the view, so the tap calls the shell synchronously (user activation). Older Wizard
// shells have no openContextDraft: Reply just is not shown. Movies never sends anything.
const canReply = () => typeof window.WizardApp?.nav?.openContextDraft === 'function';
function clip(text, limit) {
  text = String(text || '').trim();
  if (text.length <= limit) return text;
  return text.slice(0, limit).replace(/\s+\S*$/, '').replace(/[\s,.;:]+$/, '') + ' …';
}
function replyContext(movie) {
  const kind = movie.media_type === 'series' ? 'series' : 'film';
  const queue = movies.filter(m => isQueued(m) && !m.archived);
  const pos = queue.findIndex(m => m.id === movie.id) + 1;
  const watches = watchHistory(movie).map(e => e.watched_on).filter(Boolean).sort().reverse();
  const status = movie.archived ? 'archived'
    : pos ? `in the Unwatched queue, #${pos} of ${queue.length}`
    : (watches.length ? 'watched' : 'not in the queue');
  const makers = movie.media_type === 'series' ? (movie.creators || movie.directors || []) : (movie.directors || []);
  const value = ratingValue(movie);
  const imdbId = /^tt\d{7,10}$/.test(String(movie.imdb_id || '')) ? movie.imdb_id : '';
  const lines = [`Movies ${kind}: ${movie.title}${movie.year ? ` (${movie.year})` : ''} (card id ${movie.id})`];
  const meta = [(movie.genres || []).join(', '), runtimeLabel(movie)].filter(Boolean);
  if (meta.length) lines.push(meta.join(' · '));
  lines.push(`Status: ${status}`);
  lines.push(watches.length ? `Watched ${watches.length}×: ${watches.slice(0, 10).join(', ')}` : 'Watched: never');
  if (makers.length) lines.push(`${movie.media_type === 'series' ? 'Creator' : 'Director'}: ${makers.join(', ')}`);
  if ((movie.cast || []).length) lines.push(`Lead cast: ${movie.cast.slice(0, 6).join(', ')}`);
  if (value) lines.push(`IMDb users: ${ratingLabel(value)}/10${compactVoteCount(movie) ? ` (${compactVoteCount(movie)} votes)` : ''}`);
  if (imdbId) lines.push(`IMDb: https://www.imdb.com/title/${imdbId}/`);
  if (movie.letterboxd_url) lines.push(`Letterboxd: ${movie.letterboxd_url}`);
  if (movie.trailer_url) lines.push(`Trailer: ${movie.trailer_url}`);
  if (movie.description) lines.push('', 'Synopsis:', clip(movie.description, 1500));
  if (movie.notes) lines.push('', 'Notes:', clip(movie.notes, 1500));
  const eps = (movie.featured_episodes || []).slice(0, 8);
  if (eps.length) lines.push('', 'Highlighted episodes:', ...eps.map(e => `- ${e.title || 'Episode'}${e.season ? ` S${e.season}` : ''}${e.episode ? `E${e.episode}` : ''}`));
  lines.push('', `Movies tools: cinequeue_get with id "${movie.id}" reads the card; cinequeue_update, cinequeue_bump, cinequeue_mark_watched change it (only on the owner's word).`);
  const key = String(movie.id).replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 120) || 'title';
  return { id: 'title-' + key, title: `Movies: ${movie.title}${movie.year ? ` (${movie.year})` : ''}`.slice(0, 200), message: lines.join('\n').slice(0, 32000) };
}
function replyToMovie(movie, statusEl) {
  const say = (text, tone) => { announce(text); if (statusEl) { statusEl.textContent = text; statusEl.className = `detail-reply-status ${tone}`; statusEl.hidden = false; } };
  try {
    const nav = window.WizardApp?.nav;
    if (typeof nav?.openContextDraft !== 'function') throw new Error('Context draft unavailable');
    const receipt = nav.openContextDraft(replyContext(movie));
    if (receipt?.kind !== 'unsent-draft' || receipt.sent !== false) throw new Error('Could not confirm an unsent draft. Nothing was sent.');
    say('Draft opened in Chats with this title attached. Nothing sent.', '');
  } catch (error) {
    say(error.message === 'Context draft unavailable' ? 'Reload Wizard (refresh the whole page) to turn on Reply. Nothing sent.' : (error.message || 'Could not open Chats. Nothing sent.'), 'error');
  }
}

function openMovie(movieId, push = true, { preserveScroll = false, focusEventId = '', focusSelector = '' } = {}) {
  const priorScrollY = window.scrollY;
  const movie = movies.find(m => m.id === movieId);
  if (!movie) return showList();
  dismissUndo();
  closeMenu();
  listChrome.forEach(el => el && el.classList.add('hidden'));
  detail.classList.remove('hidden');
  if (push) history.pushState({ cq: 'movie' }, '', base(`/movie/${encodeURIComponent(movie.id)}`));

  const genres = (movie.genres || []).map(g => `<button class="chip" data-genre="${escapeHtml(g)}">${escapeHtml(g)}</button>`).join('');
  const makers = movie.media_type === 'series' ? (movie.creators || movie.directors || []) : (movie.directors || []);
  const makerLabel = movie.media_type === 'series' ? 'Creator' : 'Director';
  const makerText = makers.join(', ') || 'Unknown';
  const cast = (movie.cast || []).join(', ') || 'Unknown';
  const runtime = runtimeLabel(movie);
  const detailMetaItems = [(movie.genres || []).slice(0, 2).join(' / '), movie.year, runtime || 'Length not listed'].filter(Boolean);
  const queuePosition = movies.findIndex(m => m.id === movie.id) + 1;
  const isAtTop = queuePosition === 1;
  const watchedContext = filter === 'watched' || (!isQueued(movie) && hasWatchHistory(movie));
  const detailActionMode = watchActionMode(movie, watchedContext ? 'watched' : 'unwatched');
  const value = ratingValue(movie);
  const votes = compactVoteCount(movie);
  const rating = value ? `
    <div class="rating-spotlight ${ratingClass(value)}" aria-label="${escapeHtml(imdbRatingAriaLabel(movie, value))}">
      <span>IMDb users</span>
      <strong>${ratingLabel(value)}</strong>
      <small>/ 10${votes ? ` · ${escapeHtml(votes)} votes` : ''}</small>
    </div>` : '';
  const trailer = movie.trailer_url ? `<a href="${escapeHtml(movie.trailer_url)}" target="_blank" rel="noreferrer">Watch trailer</a>` : '';
  const imdbId = /^tt\d{7,10}$/.test(String(movie.imdb_id || '')) ? movie.imdb_id : '';
  const imdbUrl = imdbId ? `https://www.imdb.com/title/${imdbId}/` : '';
  const imdb = imdbUrl ? `<a href="${escapeHtml(imdbUrl)}" target="_blank" rel="noreferrer">Open IMDb</a>` : '';
  const lb = movie.letterboxd_url ? `<a href="${escapeHtml(movie.letterboxd_url)}" target="_blank" rel="noreferrer">Open Letterboxd</a>` : '';
  const bump = !watchedContext && isQueued(movie)
    ? `<button class="detail-bump" type="button" ${isAtTop ? 'disabled' : ''}>${isAtTop ? 'Already at top' : 'Bump to top'}</button>`
    : '';
  const archiveButton = `<button class="detail-archive" type="button">Archive</button>`;
  const backdrop = movie.poster ? `<div class="detail-backdrop" aria-hidden="true"><img src="${escapeHtml(movie.poster)}" alt=""></div>` : '';
  const notesMarkup = detailNotesMarkup(movie);
  const historyEvents = [...watchHistory(movie)].sort((a, b) => {
    const dateOrder = String(b.watched_on || '').localeCompare(String(a.watched_on || ''));
    return dateOrder || String(b.recorded_at || '').localeCompare(String(a.recorded_at || ''));
  });
  const historyCount = historyEvents.length;
  const historyMarkup = historyCount ? `
    <section class="watch-history" aria-labelledby="watchHistoryTitle">
      <div class="watch-history-head">
        <div>
          <h2 id="watchHistoryTitle">Watch history</h2>
          <p>${historyCount} ${historyCount === 1 ? 'watch' : 'watches'} recorded · latest ${escapeHtml(formatWatchDate(latestWatchedOn(movie)))}</p>
        </div>
      </div>
      <div class="watch-events">
        ${historyEvents.map((event, index) => {
          const eventLabel = index === 0 ? 'Latest watch' : `Watch ${historyCount - index}`;
          return `
          <form class="watch-event" data-event-id="${escapeHtml(event.id)}">
            <label>
              <span>${eventLabel}</span>
              <input type="date" value="${escapeHtml(event.watched_on || '')}" required aria-label="${eventLabel} date">
            </label>
            <div class="watch-event-actions">
              <button class="watch-date-save" type="submit" aria-label="Save ${eventLabel.toLowerCase()} date">Save date</button>
              <button class="watch-date-delete" type="button" data-watch-delete aria-label="Remove ${eventLabel.toLowerCase()}">Remove</button>
            </div>
          </form>`;
        }).join('')}
      </div>
    </section>` : '';
  const watchActionTitle = detailActionMode === 'rewatch'
    ? 'Want to watch it again?'
    : (historyCount ? 'Finished another watch?' : 'Finished it?');
  const watchActionCopy = detailActionMode === 'rewatch'
    ? 'Your watch history stays intact when this returns to Unwatched.'
    : (historyCount ? 'This adds another date to its watch history.' : 'Choose the date you watched it.');
  const watchDateControl = detailActionMode === 'rewatch' ? '' : `
    <label class="watch-date-control">
      <span>Watch date</span>
      <input class="detail-new-watch-date" type="date" value="${localToday()}" required>
    </label>`;
  const watchActionLabel = detailActionMode === 'rewatch'
    ? 'Watch again'
    : (detailActionMode === 'record-rewatch' ? 'Mark watched again' : 'Mark watched');
  detail.innerHTML = `
    <button class="back" type="button">← Back to watchlist</button>
    <article class="detail-card">
      ${backdrop}
      <div class="detail-poster">${movie.poster ? `<img src="${escapeHtml(movie.poster)}" alt="Poster for ${escapeHtml(movie.title)}">` : ''}</div>
      <div class="detail-body">
        <div class="kicker">${escapeHtml(detailMetaItems.join(' · '))}</div>
        <h1>${escapeHtml(movie.title)}</h1>
        <div class="chips">${genres}</div>
        <p class="synopsis">${escapeHtml(movie.description || 'No synopsis available yet.')}</p>
        ${notesMarkup}
        ${rating}
        <div class="facts">
          <div class="fact"><b>${makerLabel}:</b> ${escapeHtml(makerText)}</div>
          <div class="fact"><b>Lead cast:</b> ${escapeHtml(cast)}</div>
        </div>
        ${appConfig.watch?.enabled ? `<section class="where-to-watch" data-movie-id="${escapeHtml(movie.id)}" aria-live="polite">
          ${whereToWatchHead()}
          <p class="watch-provider-empty watch-provider-loading">Checking services…</p>
          <p class="watch-attribution">Streaming data from JustWatch</p>
        </section>` : ''}
        <div class="detail-links">
          <button class="detail-share" type="button">Share movie</button>
          ${canReply() ? '<button class="detail-reply" type="button" title="Start a chat with your agent about this title">Reply</button>' : ''}
          ${trailer}
          ${imdb}
          ${lb}
          ${bump}
          ${archiveButton}
        </div>
        <p class="detail-reply-status" role="status" aria-live="polite" hidden></p>
        <section class="watch-section" aria-label="Watch status action">
          <div class="watch-action-copy">
            <b>${watchActionTitle}</b>
            <span>${watchActionCopy}</span>
          </div>
          ${watchDateControl}
          <button class="detail-watch" type="button">${watchActionLabel}</button>
        </section>
        ${historyMarkup}
      </div>
    </article>`;
  loadWhereToWatch(movie);
  detail.querySelector('.back').addEventListener('click', returnToList);
  detail.querySelector('.detail-share').addEventListener('click', async () => {
    const result = await window.WizardApp?.share.text({ title:movie.title, text:`${movie.title}${movie.year ? ' (' + movie.year + ')' : ''}`, url:movie.imdb_url || movie.letterboxd_url || location.href });
    announce(result === 'shared' ? 'Shared' : result === 'copied' ? 'Copied' : result === 'cancelled' ? 'Share cancelled' : 'Sharing is unavailable');
  });
  detail.querySelector('.detail-reply')?.addEventListener('click', () => replyToMovie(movie, detail.querySelector('.detail-reply-status')));
  detail.querySelector('.detail-watch').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = detailActionMode === 'rewatch' ? 'Adding…' : 'Saving…';
    try {
      if (detailActionMode === 'rewatch') {
        await queueAgain(movie);
        announce(`${movie.title} added to your watchlist again`);
      } else {
        const watchedOn = detail.querySelector('.detail-new-watch-date')?.value || localToday();
        await markWatched(movie, watchedOn);
        announce(`Recorded ${movie.title} watched on ${formatWatchDate(watchedOn)}`);
      }
      render();
      openMovie(movie.id, false, { preserveScroll: true, focusSelector: '.detail-watch' });
    } catch (error) {
      console.error(error);
      button.disabled = false;
      button.textContent = watchActionLabel;
    }
  });
  detail.querySelectorAll('.watch-event').forEach(form => form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = form.querySelector('input');
    const button = form.querySelector('.watch-date-save');
    if (!input.value) return input.focus();
    button.disabled = true;
    button.textContent = 'Saving…';
    try {
      const eventId = form.dataset.eventId;
      await editWatchDate(movie, eventId, input.value);
      announce(`Updated ${movie.title} watch date to ${formatWatchDate(input.value)}`);
      render();
      openMovie(movie.id, false, { preserveScroll: true, focusEventId: eventId, focusSelector: '.watch-date-save' });
    } catch (error) {
      console.error(error);
      button.disabled = false;
      button.textContent = 'Save date';
    }
  }));
  detail.querySelectorAll('[data-watch-delete]').forEach(button => button.addEventListener('click', async () => {
    const form = button.closest('.watch-event');
    const label = form.querySelector('label span')?.textContent || 'this watch';
    if (!window.confirm(`Remove ${label.toLowerCase()} from ${movie.title}'s history?`)) return;
    button.disabled = true;
    button.textContent = 'Removing…';
    try {
      const updated = await deleteWatchEvent(movie, form.dataset.eventId);
      announce(`Removed a watch from ${movie.title}`);
      render();
      if (!hasWatchHistory(updated)) {
        returnToList();
      } else {
        openMovie(movie.id, false, { preserveScroll: true, focusSelector: '.watch-event [data-watch-delete]' });
      }
    } catch (error) {
      console.error(error);
      button.disabled = false;
      button.textContent = 'Remove';
    }
  }));
  detail.querySelector('.detail-archive').addEventListener('click', async () => {
    const ok = await archiveMovie(movie);
    if (ok) returnToList();
  });
  detail.querySelector('.detail-bump')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Bumping…';
    sortEl.value = 'queue-order';
    await api(`/api/bump/${encodeURIComponent(movie.id)}`, { method: 'POST', body: '{}' });
    await loadMovies();
  });
  detail.querySelectorAll('[data-genre]').forEach(btn => btn.addEventListener('click', () => {
    selectedGenres = new Set([btn.dataset.genre]);
    returnToList();
    populateGenres();
    render();
  }));
  if (preserveScroll) {
    requestAnimationFrame(() => {
      let focusRoot = detail;
      if (focusEventId) {
        focusRoot = [...detail.querySelectorAll('.watch-event')]
          .find(form => form.dataset.eventId === focusEventId) || detail;
      }
      focusRoot.querySelector(focusSelector)?.focus({ preventScroll: true });
      window.scrollTo({ top: priorScrollY, behavior: 'auto' });
    });
  } else {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

function route() {
  const path = window.location.pathname;
  const clean = BASE_PATH && path.startsWith(BASE_PATH) ? path.slice(BASE_PATH.length) : path;
  const match = clean.match(/^\/movie\/([^/]+)/);
  if (match) return openMovie(decodeURIComponent(match[1]), false);
  showList();
}

function render() {
  const shown = visibleMovies();
  list.innerHTML = '';
  if (!shown.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = movies.length ? 'No matching films.' : 'No movies yet.';
    list.append(empty);
    return;
  }
  for (const movie of shown) {
    list.append(view === 'grid' ? renderTile(movie) : renderRow(movie));
  }
}

document.querySelectorAll('[data-filter]').forEach(button => {
  button.addEventListener('click', () => {
    filter = button.dataset.filter;
    syncSortForFilter();
    document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('active'));
    button.classList.add('active');
    render();
  });
});
searchEl.addEventListener('input', render);
sortEl.addEventListener('change', () => {
  if (filter === 'unwatched') queueSort = sortEl.value;
  render();
});
if (addOpen) addOpen.addEventListener('click', openAddRequestPanel);
if (addRequestClose) addRequestClose.addEventListener('click', closeAddRequestPanel);
if (addRequestBackdrop) addRequestBackdrop.addEventListener('click', closeAddRequestPanel);
// Drag the sheet's header down to close it (design rules v0: ×, Esc, outside tap, Back and swipe-down).
const addRequestHead = addRequestPanel?.querySelector('.add-request-head');
let addRequestDragY = null;
if (addRequestHead) {
  addRequestHead.addEventListener('touchstart', (event) => { addRequestDragY = event.touches[0].clientY; }, { passive: true });
  addRequestHead.addEventListener('touchend', (event) => {
    if (addRequestDragY != null && event.changedTouches[0].clientY - addRequestDragY > 60) closeAddRequestPanel();
    addRequestDragY = null;
  }, { passive: true });
  addRequestHead.addEventListener('touchcancel', () => { addRequestDragY = null; }, { passive: true });
}
if (addRequestForm) addRequestForm.addEventListener('submit', submitAddRequest);
if (addRequestInput) addRequestInput.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') submitAddRequest(event);
});
window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (addRequestPanel?.hidden === false) {
      closeAddRequestPanel();
      return;
    }
    if (!undoBar.hidden && undoBatch.length) {
      dismissUndo();
      return;
    }
    if (openMenu) closeMenu();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    if (!undoBar.hidden && undoBatch.length) {
      event.preventDefault();
      runUndo();
    }
  }
});
// Core owns platform/root Back. These are only Movies domain steps.
let suppressPopUntil = 0;
function quietBack() { suppressPopUntil = Date.now() + 600; history.back(); }
function returnToList() {
  if (history.state?.cq === 'movie') quietBack();
  else history.replaceState({ cq:'root' }, '', base('/'));
  showList();
}
window.addEventListener('popstate', () => {
  if (Date.now() < suppressPopUntil) { suppressPopUntil = 0; return; }
  if (!addRequestPanel.hidden) { hideAddRequestPanel(); return; }
  route();
});
window.WizardApp?.back.useHistory({ isRoot: () => addRequestPanel.hidden && detail.classList.contains('hidden') });
window.WizardApp?.back.register({
  canGoBack: () => Boolean(openMenu) || (!detail.classList.contains('hidden') && history.state?.cq !== 'movie'),
  back: () => { if (openMenu) closeMenu(); else returnToList(); return true; }
});
window.WizardApp?.ready({ name:'Movies', version:'0.3.2' }).then(syncDraftHold);
const brandLink = document.querySelector('.brand');
if (brandLink) brandLink.addEventListener('click', (event) => {
  event.preventDefault();
  returnToList();
});
async function loadConfig() {
  try {
    const data = await api('/api/config');
    appConfig = { ...appConfig, ...data };
  } catch (error) {
    console.warn('Movies config unavailable; agent requests and where-to-watch stay off', error?.name || 'Error');
  }
  syncAddMode();
}
loadConfig().then(async () => { await loadMovies(); await refreshRequests(); }).catch(error => {
  list.innerHTML = '<div class="empty">Could not load movies.</div>';
  console.error(error);
});
