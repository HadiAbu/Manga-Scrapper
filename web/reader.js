// ── Reader state ──
const READER = {
  malId: null,
  chapters: [],
  chapterIdx: 0,
  pages: [],
  pageIdx: 0,
};

// ── DOM helpers ──
function rEl(id) { return document.getElementById(id); }

function readerShowLoading(text = 'Loading…') {
  rEl('reader-loading').classList.remove('hidden');
  rEl('reader-loading-text').textContent = text;
  rEl('reader-error').classList.add('hidden');
  rEl('reader-page-img').classList.add('hidden');
}

function readerShowError(msg) {
  rEl('reader-loading').classList.add('hidden');
  const err = rEl('reader-error');
  err.textContent = msg;
  err.classList.remove('hidden');
  rEl('reader-page-img').classList.add('hidden');
}

function readerShowPage(url, current, total) {
  rEl('reader-loading').classList.add('hidden');
  rEl('reader-error').classList.add('hidden');
  const img = rEl('reader-page-img');
  img.classList.remove('hidden');

  // Show a brief loading state while the image fetches
  img.style.opacity = '0.4';
  img.onload = () => { img.style.opacity = '1'; };
  img.onerror = () => { img.style.opacity = '1'; };
  img.src = url;

  rEl('reader-page-info').textContent = `${current} / ${total}`;
  updateNavButtons();
}

function updateNavButtons() {
  rEl('reader-prev').disabled = READER.pageIdx === 0 && READER.chapterIdx === 0;
  rEl('reader-next').disabled =
    READER.pageIdx === READER.pages.length - 1 &&
    READER.chapterIdx === READER.chapters.length - 1;
}

// ── Core reader logic ──
async function openReader(manga) {
  closeModal();

  READER.malId = manga.mal_id;
  READER.chapters = [];
  READER.pages = [];
  READER.chapterIdx = 0;
  READER.pageIdx = 0;

  rEl('reader-manga-title').textContent = manga.title;
  rEl('reader-chapter-select').innerHTML = '';
  rEl('reader-page-info').textContent = '';

  rEl('reader').classList.remove('hidden');
  document.body.style.overflow = 'hidden';

  readerShowLoading('Finding manga on MangaDex…');

  try {
    const res = await fetch(`/api/manga/${manga.mal_id}/chapters`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      readerShowError(body.detail || 'This manga is not available for reading.');
      return;
    }
    const data = await res.json();
    READER.chapters = data.chapters;

    const select = rEl('reader-chapter-select');
    data.chapters.forEach((ch, i) => {
      const opt = document.createElement('option');
      opt.value = i;
      const num = ch.chapter ? `Ch. ${ch.chapter}` : `#${i + 1}`;
      opt.textContent = ch.title ? `${num} — ${ch.title}` : num;
      select.appendChild(opt);
    });

    await loadChapter(0);
  } catch {
    readerShowError('Failed to load chapters. Please check your connection.');
  }
}

async function loadChapter(idx, startAtEnd = false) {
  if (!READER.chapters.length) return;
  READER.chapterIdx = idx;
  READER.pageIdx = 0;

  rEl('reader-chapter-select').value = idx;
  readerShowLoading('Loading pages…');

  try {
    const ch = READER.chapters[idx];
    const res = await fetch(`/api/chapters/${ch.id}/pages`);

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const msg = body.detail || 'This chapter is unavailable.';

      // If there are more chapters, offer to skip rather than dead-ending
      const hasNext = idx < READER.chapters.length - 1;
      const hasPrev = idx > 0;
      const hint = hasNext ? ' Try the next chapter.' : hasPrev ? ' Try the previous chapter.' : '';
      readerShowError(msg + hint);
      return;
    }

    const data = await res.json();
    READER.pages = data.pages;

    if (!READER.pages.length) {
      readerShowError('No pages found for this chapter.');
      return;
    }

    const targetPage = startAtEnd ? READER.pages.length - 1 : 0;
    showPage(targetPage);
  } catch {
    readerShowError('Failed to load pages. Please try again.');
  }
}

function showPage(idx) {
  idx = Math.max(0, Math.min(idx, READER.pages.length - 1));
  READER.pageIdx = idx;
  readerShowPage(READER.pages[idx], idx + 1, READER.pages.length);

  // Scroll viewport to top when page changes
  rEl('reader-viewport').scrollTop = 0;
}

function readerNext() {
  if (READER.pageIdx < READER.pages.length - 1) {
    showPage(READER.pageIdx + 1);
  } else if (READER.chapterIdx < READER.chapters.length - 1) {
    loadChapter(READER.chapterIdx + 1);
  }
}

function readerPrev() {
  if (READER.pageIdx > 0) {
    showPage(READER.pageIdx - 1);
  } else if (READER.chapterIdx > 0) {
    loadChapter(READER.chapterIdx - 1, true);
  }
}

function closeReader() {
  rEl('reader').classList.add('hidden');
  document.body.style.overflow = '';
}

// ── Event listeners ──
rEl('reader-close').addEventListener('click', closeReader);
rEl('reader-next').addEventListener('click', readerNext);
rEl('reader-prev').addEventListener('click', readerPrev);

rEl('reader-chapter-select').addEventListener('change', e => {
  loadChapter(parseInt(e.target.value, 10));
});

// Tap left/right half of the page image to navigate
rEl('reader-viewport').addEventListener('click', e => {
  if (e.target !== rEl('reader-page-img')) return; // only on the image itself
  const rect = e.currentTarget.getBoundingClientRect();
  if (e.clientX - rect.left > rect.width / 2) readerNext();
  else readerPrev();
});

// Keyboard navigation (only when reader is open)
document.addEventListener('keydown', e => {
  if (rEl('reader').classList.contains('hidden')) return;
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); readerNext(); }
  if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   { e.preventDefault(); readerPrev(); }
  if (e.key === 'Escape') closeReader();
});
