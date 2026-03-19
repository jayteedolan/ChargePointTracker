'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let watchActive = false;
let autoRefreshTimer = null;
const AUTO_REFRESH_MS = 60_000; // 60 seconds

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchStatus() {
  const res = await fetch('/api/status');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function postRefresh() {
  const res = await fetch('/api/refresh', { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function postWatch(enabled) {
  const res = await fetch('/api/watch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render(data) {
  renderPorts(data);
  renderWatch(data);
  renderErrors(data);
  document.getElementById('last-updated').textContent =
    'Updated ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function renderPorts(data) {
  const grid = document.getElementById('port-grid');
  const aggregateNote = document.getElementById('aggregate-note');
  const isAggregate = data.ports.some(p => p.status_source === 'aggregate');

  aggregateNote.hidden = !isAggregate;

  if (isAggregate) {
    renderAggregate(data, grid);
  } else {
    renderPerPort(data, grid);
  }
}

function renderPerPort(data, grid) {
  // Ensure we have exactly the right cards (add if missing, remove extras)
  const portNums = data.ports.map(p => p.port_number);

  // Remove any cards not in the new data
  grid.querySelectorAll('.port-card').forEach(card => {
    const num = parseInt(card.dataset.port, 10);
    if (!portNums.includes(num)) card.remove();
  });

  for (const port of data.ports) {
    let card = document.getElementById(`port-${port.port_number}-card`);
    if (!card) {
      card = document.createElement('div');
      card.className = 'port-card';
      card.id = `port-${port.port_number}-card`;
      card.dataset.port = port.port_number;
      card.innerHTML = `
        <div class="port-label">Port ${port.port_number}</div>
        <div class="status-badge"></div>
        <div class="time-info"></div>
      `;
      grid.appendChild(card);
    }

    const badge = card.querySelector('.status-badge');
    const timeEl = card.querySelector('.time-info');

    card.classList.remove('available', 'occupied', 'skeleton', 'aggregate-card');
    card.classList.add(port.is_available ? 'available' : 'occupied');

    badge.textContent = port.is_available ? 'AVAILABLE' : 'IN USE';
    timeEl.textContent = port.is_available
      ? `Free for ${formatDuration(port.duration_seconds)}`
      : `In use for ${formatDuration(port.duration_seconds)}`;
  }
}

function renderAggregate(data, grid) {
  // In aggregate mode, replace the grid with a single merged card
  const availableCount = data.ports.filter(p => p.is_available).length;
  const totalCount = data.ports.length;

  // Find or create the aggregate card
  let card = grid.querySelector('.aggregate-card');
  if (!card) {
    grid.innerHTML = ''; // clear individual port cards
    card = document.createElement('div');
    card.className = 'port-card aggregate-card';
    card.innerHTML = `
      <div class="port-label">Charger Hub</div>
      <div class="aggregate-count"></div>
      <div class="status-badge"></div>
      <div class="time-info"></div>
    `;
    grid.appendChild(card);
  }

  const countEl = card.querySelector('.aggregate-count');
  const badge = card.querySelector('.status-badge');
  const timeEl = card.querySelector('.time-info');

  countEl.textContent = `${availableCount} / ${totalCount}`;
  countEl.classList.toggle('none', availableCount === 0);

  card.classList.remove('available', 'occupied');
  card.classList.add(availableCount > 0 ? 'available' : 'occupied');

  if (availableCount === 0) {
    badge.textContent = 'ALL IN USE';
    // For aggregate, show the longest "in use" duration we have
    const maxDuration = Math.max(...data.ports.map(p => p.duration_seconds));
    timeEl.textContent = `In use for ${formatDuration(maxDuration)}`;
  } else if (availableCount === totalCount) {
    badge.textContent = 'ALL AVAILABLE';
    const maxDuration = Math.max(...data.ports.map(p => p.duration_seconds));
    timeEl.textContent = `Free for ${formatDuration(maxDuration)}`;
  } else {
    badge.textContent = 'PORTS AVAILABLE';
    timeEl.textContent = `${availableCount} of ${totalCount} ports free`;
  }
}

function renderWatch(data) {
  watchActive = data.watch_mode_active;
  const btn = document.getElementById('watch-btn');
  const iconEl = document.getElementById('watch-btn-icon');
  const labelEl = document.getElementById('watch-btn-label');
  const statusEl = document.getElementById('watch-status');
  const sinceEl = document.getElementById('watch-since');

  if (watchActive) {
    btn.classList.add('active');
    iconEl.textContent = '🔕';
    labelEl.textContent = 'Stop Watching';
    statusEl.hidden = false;
    sinceEl.textContent = data.watch_mode_since
      ? new Date(data.watch_mode_since).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : '—';
  } else {
    btn.classList.remove('active');
    iconEl.textContent = '🔔';
    labelEl.textContent = 'Notify Me When Available';
    statusEl.hidden = true;
  }
}

function renderErrors(data) {
  const errorBanner = document.getElementById('error-banner');
  const pollStatus = document.getElementById('poll-status');
  const pollMsg = document.getElementById('poll-error-msg');

  if (data.last_poll_error) {
    pollStatus.hidden = false;
    pollMsg.textContent = `⚠ Last poll failed: ${data.last_poll_error}`;
  } else {
    pollStatus.hidden = true;
  }

  // Clear any fetch-level error if we got a clean response
  errorBanner.hidden = true;
}

function showFetchError(msg) {
  const banner = document.getElementById('error-banner');
  banner.textContent = `Connection error: ${msg}`;
  banner.hidden = false;
}

// ---------------------------------------------------------------------------
// Duration formatting
// ---------------------------------------------------------------------------

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const h = Math.floor(m / 60);
  if (h > 0) {
    const rem = m % 60;
    return rem > 0 ? `${h}h ${rem}m` : `${h}h`;
  }
  return `${m}m`;
}

// ---------------------------------------------------------------------------
// Auto-refresh
// ---------------------------------------------------------------------------

function startAutoRefresh() {
  clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(async () => {
    try {
      const data = await fetchStatus();
      render(data);
    } catch (e) {
      showFetchError(e.message);
    }
  }, AUTO_REFRESH_MS);
}

function resetAutoRefresh() {
  startAutoRefresh(); // clearInterval + restart
}

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

document.getElementById('refresh-btn').addEventListener('click', async () => {
  const btn = document.getElementById('refresh-btn');
  btn.classList.add('spinning');
  btn.disabled = true;
  try {
    const data = await postRefresh();
    render(data);
    resetAutoRefresh();
  } catch (e) {
    showFetchError(e.message);
  } finally {
    btn.disabled = false;
    btn.classList.remove('spinning');
  }
});

document.getElementById('watch-btn').addEventListener('click', async () => {
  const btn = document.getElementById('watch-btn');
  btn.disabled = true;
  try {
    await postWatch(!watchActive);
    // Fetch full status so we get the updated watch_mode_since timestamp
    const data = await fetchStatus();
    render(data);
  } catch (e) {
    showFetchError(e.message);
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

(async function init() {
  try {
    const data = await fetchStatus();
    render(data);
  } catch (e) {
    showFetchError(e.message);
  }
  startAutoRefresh();
})();
