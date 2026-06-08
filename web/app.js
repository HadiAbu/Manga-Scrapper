const API = '/api';

function proxyImg(url) {
  return url ? `${API}/image-proxy?url=${encodeURIComponent(url)}` : '';
}
let currentPage = 1;
let currentQuery = '';
let currentGenre = '';
let searchTimer = null;
let currentModalManga = null;

// ── Data loading ──
async function loadGenres() {
  try {
    const res = await fetch(`${API}/genres`);
    const genres = await res.json();
    const select = document.getElementById('genre-filter');
    genres.forEach(g => {
      const opt = document.createElement('option');
      opt.value = g;
      opt.textContent = g;
      select.appendChild(opt);
    });
  } catch (e) {
    console.warn('Could not load genres', e);
  }
}

async function search(resetPage = true) {
  if (resetPage) currentPage = 1;

  const params = new URLSearchParams({ page: currentPage, limit: 24 });
  if (currentQuery) params.set('q', currentQuery);
  if (currentGenre) params.set('genre', currentGenre);

  const grid = document.getElementById('manga-grid');
  grid.innerHTML = '<p class="no-results">Loading...</p>';

  try {
    const res = await fetch(`${API}/search?${params}`);
    const data = await res.json();
    renderResults(data);
  } catch (e) {
    grid.innerHTML = '<p class="no-results">Failed to load results. Is the API running?</p>';
  }
}

function renderResults(data) {
  const grid       = document.getElementById('manga-grid');
  const info       = document.getElementById('result-info');
  const pagination = document.getElementById('pagination');

  grid.innerHTML = '';
  pagination.innerHTML = '';

  if (!data.results || data.results.length === 0) {
    grid.innerHTML = '<p class="no-results">No manga found.</p>';
    info.textContent = '';
    return;
  }

  info.textContent = `${data.total.toLocaleString()} results`;

  data.results.forEach(manga => {
    const card = document.createElement('div');
    card.className = 'manga-card';
    card.onclick = () => openModal(manga);

    const scoreHtml = manga.score
      ? `<span class="score">&#9733; ${manga.score}</span>`
      : '';
    const genreHtml = (manga.genres || [])
      .slice(0, 3)
      .map(g => `<span class="genre-tag">${g}</span>`)
      .join('');

    card.innerHTML = `
      <img src="${proxyImg(manga.cover_url)}" alt="${escHtml(manga.title)}" loading="lazy"
           onerror="this.style.background='#22222e'">
      <div class="card-body">
        <h3>${escHtml(manga.title)}</h3>
        <div class="meta">
          ${scoreHtml}
          <span class="status">${escHtml(manga.status || '')}</span>
        </div>
        <div class="genres">${genreHtml}</div>
      </div>
    `;
    grid.appendChild(card);
  });

  const totalPages = Math.ceil(data.total / data.limit);
  if (totalPages > 1) {
    if (currentPage > 1) {
      const btn = document.createElement('button');
      btn.textContent = '← Prev';
      btn.onclick = () => { currentPage--; search(false); scrollTo(0, 0); };
      pagination.appendChild(btn);
    }

    const pageSpan = document.createElement('span');
    pageSpan.textContent = `Page ${currentPage} of ${totalPages}`;
    pagination.appendChild(pageSpan);

    if (currentPage < totalPages) {
      const btn = document.createElement('button');
      btn.textContent = 'Next →';
      btn.onclick = () => { currentPage++; search(false); scrollTo(0, 0); };
      pagination.appendChild(btn);
    }
  }
}

function openModal(manga) {
  currentModalManga = manga;
  document.getElementById('modal-title').textContent    = manga.title || '';
  document.getElementById('modal-img').src              = proxyImg(manga.large_cover_url || manga.cover_url);
  document.getElementById('modal-img').alt              = manga.title || '';
  document.getElementById('modal-score').textContent    = manga.score
    ? `★ ${manga.score}  (${(manga.scored_by || 0).toLocaleString()} votes)`
    : '';
  document.getElementById('modal-status').textContent  = manga.status || '';
  document.getElementById('modal-volumes').textContent = manga.volumes
    ? `${manga.volumes} vol · ${manga.chapters || '?'} ch`
    : '';
  document.getElementById('modal-synopsis').textContent = manga.synopsis || 'No synopsis available.';
  document.getElementById('modal-authors').textContent  = (manga.authors || []).join(', ');
  document.getElementById('modal-genres').innerHTML     = (manga.genres || [])
    .map(g => `<span class="genre-tag">${escHtml(g)}</span>`)
    .join('');

  document.getElementById('modal').classList.add('open');
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Event listeners ──
document.getElementById('search-input').addEventListener('input', e => {
  currentQuery = e.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => search(), 400);
});

document.getElementById('genre-filter').addEventListener('change', e => {
  currentGenre = e.target.value;
  search();
});

document.getElementById('modal-close').addEventListener('click', closeModal);

document.getElementById('modal').addEventListener('click', e => {
  if (e.target === document.getElementById('modal')) closeModal();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

document.getElementById('modal-read-btn').addEventListener('click', () => {
  if (currentModalManga) openReader(currentModalManga);
});

// ── Init ──
loadGenres();
search();
