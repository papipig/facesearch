/* ── State ── */
let activeTab = 'single';
let singleFile = null;
let multipleFiles = [];

/* ── Tab switching ── */
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    activeTab = btn.dataset.tab;
    document.getElementById(`tab-${activeTab}`).classList.add('active');
  });
});

/* ── Single image: drag-and-drop + file picker ── */
const dropZone   = document.getElementById('drop-zone');
const singleInput = document.getElementById('single-input');

dropZone.addEventListener('click', () => singleInput.click());

dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  dropZone.classList.add('dragover');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) setSingleFile(file);
});
singleInput.addEventListener('change', () => {
  if (singleInput.files[0]) setSingleFile(singleInput.files[0]);
});

function setSingleFile(file) {
  singleFile = file;
  const preview = document.getElementById('single-preview');
  preview.innerHTML = '';
  preview.appendChild(makePreviewItem(file));
}

/* ── Multiple images ── */
document.getElementById('multiple-input').addEventListener('change', e => {
  multipleFiles = Array.from(e.target.files);
  const preview = document.getElementById('multiple-preview');
  preview.innerHTML = '';
  multipleFiles.forEach(f => preview.appendChild(makePreviewItem(f)));
});

/* ── Search ── */
document.getElementById('search-btn').addEventListener('click', runSearch);

async function runSearch() {
  document.getElementById('results-section').classList.add('hidden');

  if (activeTab === 'single') {
    if (!singleFile) { alert('Please select an image.'); return; }
    setLoading(true, 'Searching\u2026');
    try {
      const formData = new FormData();
      formData.append('images', singleFile);
      const resp = await fetch('/search', { method: 'POST', body: formData });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        alert(`Search failed: ${err.detail}`);
        return;
      }
      renderResults(await resp.json());
    } catch (e) {
      alert(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }

  } else if (activeTab === 'multiple') {
    if (!multipleFiles.length) { alert('Please select at least one image.'); return; }
    const total = multipleFiles.length;
    setLoading(true, 'Processing images\u2026');
    setProgress(0, total);
    const results = [];
    try {
      for (let i = 0; i < total; i++) {
        setLoadingText(`Processing image ${i + 1}\u202f/\u202f${total}\u2026`);
        setProgress(i, total);
        const fd = new FormData();
        fd.append('images', multipleFiles[i]);
        const resp = await fetch('/search', { method: 'POST', body: fd });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          alert(`Search failed on image ${i + 1}: ${err.detail}`);
          return;
        }
        results.push(...(await resp.json()));
        setProgress(i + 1, total);
      }
      renderResults(results);
    } catch (e) {
      alert(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }

  } else {
    const url = document.getElementById('url-input').value.trim();
    if (!url) { alert('Please enter a URL.'); return; }
    setLoading(true, 'Scraping page\u2026');
    const results = [];
    try {
      const formData = new FormData();
      formData.append('url', url);
      const resp = await fetch('/search/stream', { method: 'POST', body: formData });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        alert(`Search failed: ${err.detail}`);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === 'scraped') {
            setLoadingText(event.total ? 'Processing images\u2026' : 'No images found on page.');
            setProgress(0, event.total);
          } else if (event.type === 'progress') {
            setLoadingText(`Processing image ${event.current}\u202f/\u202f${event.total}\u2026`);
            setProgress(event.current, event.total);
          } else if (event.type === 'result') {
            results.push(event.data);
          } else if (event.type === 'error') {
            alert(`Search failed: ${event.detail}`);
            break outer;
          }
        }
      }
      renderResults(results);
    } catch (e) {
      alert(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }
}

/* ── Render results ── */
function renderResults(results) {
  const section   = document.getElementById('results-section');
  const container = document.getElementById('results-container');
  const heading   = document.getElementById('results-heading');
  container.innerHTML = '';
  section.classList.remove('hidden');

  if (!results.length) {
    container.innerHTML = '<p style="color:var(--text-muted)">No images found or processed.</p>';
    return;
  }

  const matchCount = results.filter(r => r.match).length;
  heading.textContent = `Results — ${matchCount} / ${results.length} image(s) matched`;

  if (activeTab === 'single' && results.length === 1) {
    renderSingleResult(results[0], container);
  } else {
    renderGallery(results, container);
  }
}

/* Single-image view */
function renderSingleResult(result, container) {
  const wrapper    = document.createElement('div');
  wrapper.className = 'single-result';

  const imgWrapper = document.createElement('div');
  imgWrapper.className = 'image-wrapper';

  const img    = document.createElement('img');
  const canvas = document.createElement('canvas');
  img.alt = result.filename;

  // Append to DOM first so layout is available when onload fires
  imgWrapper.appendChild(img);
  imgWrapper.appendChild(canvas);
  wrapper.appendChild(imgWrapper);
  container.appendChild(wrapper);

  img.onload = () => requestAnimationFrame(() =>
    drawBoundingBoxes(img, canvas, result.faces)
  );
  img.src = `/images/${result.sha256}`;
  if (img.complete) requestAnimationFrame(() => drawBoundingBoxes(img, canvas, result.faces));

  // Face list
  const faceList = buildFaceList(result.faces);
  wrapper.appendChild(faceList);
}

/* Gallery view */
function renderGallery(results, container) {
  const gallery = document.createElement('div');
  gallery.className = 'gallery';

  results.forEach(result => {
    const item = document.createElement('div');
    item.className = `gallery-item${result.match ? ' is-match' : ''}`;

    const img = document.createElement('img');
    img.src     = `/images/${result.sha256}`;
    img.alt     = result.filename;
    img.loading = 'lazy';

    const info = document.createElement('div');
    info.className = 'item-info';

    const badge = document.createElement('span');
    badge.className = `badge ${result.match ? 'match' : 'no-match'}`;
    badge.textContent = result.match ? 'MATCH' : 'NO MATCH';

    const name = document.createElement('div');
    name.className   = 'item-filename';
    name.title       = result.filename;
    name.textContent = result.filename;

    info.appendChild(badge);
    info.appendChild(name);
    item.appendChild(img);
    item.appendChild(info);
    item.addEventListener('click', () => openDetail(result));
    gallery.appendChild(item);
  });

  container.appendChild(gallery);
}

/* ── Detail overlay ── */
function openDetail(result) {
  const overlay = document.getElementById('detail-overlay');
  const img     = document.getElementById('detail-img');
  const canvas  = document.getElementById('detail-canvas');
  const info    = document.getElementById('detail-info');

  img.alt = result.filename;
  info.innerHTML = '';
  info.appendChild(buildFaceList(result.faces));

  img.onload = () => requestAnimationFrame(() =>
    drawBoundingBoxes(img, canvas, result.faces)
  );
  img.src = `/images/${result.sha256}`;
  if (img.complete) requestAnimationFrame(() => drawBoundingBoxes(img, canvas, result.faces));

  overlay.classList.remove('hidden');
}

document.getElementById('close-detail').addEventListener('click', closeDetail);
document.getElementById('detail-overlay').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeDetail();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeDetail();
});
function closeDetail() {
  document.getElementById('detail-overlay').classList.add('hidden');
}

/* ── Face list builder ── */
function buildFaceList(faces) {
  const list = document.createElement('div');
  list.className = 'face-list';

  if (!faces.length) {
    const p = document.createElement('p');
    p.style.color = 'var(--text-muted)';
    p.textContent = 'No faces detected in this image.';
    list.appendChild(p);
    return list;
  }

  faces.forEach(f => {
    const item = document.createElement('div');
    item.className = 'face-item';

    const badge = document.createElement('span');
    badge.className   = `badge ${f.match ? 'match' : 'no-match'}`;
    badge.textContent = f.match ? 'MATCH' : 'NO MATCH';

    const label = document.createElement('span');
    label.textContent = `Face ${f.face_index + 1}`;

    const conf = document.createElement('span');
    conf.className   = 'confidence';
    conf.textContent = `${(f.confidence * 100).toFixed(1)}%`;

    item.appendChild(badge);
    item.appendChild(label);
    item.appendChild(conf);
    list.appendChild(item);
  });

  return list;
}

/* ── Canvas bounding boxes ── */
function drawBoundingBoxes(img, canvas, faces) {
  const dw = img.offsetWidth;
  const dh = img.offsetHeight;
  if (!dw || !dh) return;

  const sx = dw / img.naturalWidth;
  const sy = dh / img.naturalHeight;

  canvas.width  = dw;
  canvas.height = dh;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, dw, dh);

  faces.forEach(face => {
    const { x, y, w, h } = face.bounding_box;
    const color = face.match ? '#22c55e' : '#ef4444';

    const rx = x * sx, ry = y * sy, rw = w * sx, rh = h * sy;

    // Box
    ctx.strokeStyle = color;
    ctx.lineWidth   = 2;
    ctx.strokeRect(rx, ry, rw, rh);

    // Label background + text
    const label = `${face.match ? 'MATCH' : 'NO MATCH'}  ${(face.confidence * 100).toFixed(0)}%`;
    ctx.font = 'bold 11px system-ui, sans-serif';
    const tw = ctx.measureText(label).width;
    const lh = 18;
    const lx = rx;
    const ly = ry > lh ? ry - lh : ry + rh;

    ctx.fillStyle = color;
    ctx.fillRect(lx, ly, tw + 8, lh);
    ctx.fillStyle = '#000';
    ctx.fillText(label, lx + 4, ly + lh - 4);
  });
}

/* ── Utilities ── */
function setLoading(on, text) {
  document.getElementById('loading').classList.toggle('hidden', !on);
  document.getElementById('search-btn').disabled = on;
  document.getElementById('loading-text').textContent = on ? (text || 'Searching\u2026') : 'Searching\u2026';
  // Reset progress bar when hiding
  if (!on) setProgress(0, 0);
}

function setLoadingText(text) {
  document.getElementById('loading-text').textContent = text;
}

function setProgress(current, total) {
  const wrap  = document.getElementById('progress-bar-wrap');
  const fill  = document.getElementById('progress-fill');
  const label = document.getElementById('progress-label');
  if (!total) {
    wrap.classList.add('hidden');
    label.classList.add('hidden');
    fill.style.width = '0%';
    return;
  }
  wrap.classList.remove('hidden');
  label.classList.remove('hidden');
  const pct = Math.round((current / total) * 100);
  fill.style.width = pct + '%';
  label.textContent = `${current}\u202f/\u202f${total}`;
}

function makePreviewItem(file) {
  const el = document.createElement('span');
  el.className = 'preview-item';
  const name = document.createTextNode(file.name);
  const size = document.createElement('span');
  size.className = 'file-size';
  size.textContent = formatSize(file.size);
  el.appendChild(name);
  el.appendChild(document.createTextNode('\u00a0'));
  el.appendChild(size);
  return el;
}

function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatSize(bytes) {
  if (bytes < 1024)        return `${bytes} B`;
  if (bytes < 1_048_576)   return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}
