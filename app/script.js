/**
 * app/script.js
 * =============
 * DishUp Menu Intelligence — Frontend JavaScript.
 *
 * Architecture: flat module pattern using const objects.
 * No framework, no build step, no external JS dependencies.
 *
 * Modules:
 *   API      — fetch helpers
 *   Toast    — notification toasts
 *   Tab      — tab navigation
 *   Upload   — upload form + drag-and-drop
 *   Dashboard— stats, charts, product table
 *   Products — grid, filters, modal, delete, toggle
 *   Chat     — chatbot UI, intent suggestions
 *
 * All console usage is intentional (errors only via console.error).
 * There are no console.log calls in production code paths.
 */

'use strict';

/* ==========================================================================
   API MODULE — centralised fetch wrapper
   ========================================================================== */
const API = {
  /** Base URL — empty string means same-origin */
  BASE: '',

  /**
   * Perform a fetch request and parse the JSON response.
   * Throws an Error (with the server's error message) on non-2xx responses.
   *
   * @param {string} path   - URL path, e.g. "/api/products"
   * @param {object} opts   - fetch options (method, body, headers, …)
   * @returns {Promise<any>}
   */
  async fetch(path, opts = {}) {
    const res = await fetch(API.BASE + path, opts);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || 'HTTP ' + res.status);
    }
    return res.json();
  },

  get:    (path)         => API.fetch(path),
  delete: (path)         => API.fetch(path, { method: 'DELETE' }),
  patch:  (path)         => API.fetch(path, { method: 'PATCH' }),
  post:   (path, body)   => API.fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }),
  upload: (path, formData) => API.fetch(path, { method: 'POST', body: formData }),
};

/* ==========================================================================
   TOAST MODULE — transient notifications
   ========================================================================== */
const Toast = {
  /** @type {number|null} */
  _timer: null,

  /**
   * Show a toast message for 3.5 seconds.
   * @param {string} message
   * @param {'success'|'error'|'info'} type
   */
  show(message, type = 'success') {
    // Remove any existing toast first
    const existing = document.getElementById('toast');
    if (existing) existing.remove();

    const el = document.createElement('div');
    el.id = 'toast';
    el.className = 'toast toast--' + type;
    el.textContent = message;
    document.body.appendChild(el);

    clearTimeout(this._timer);
    this._timer = setTimeout(() => el.remove(), 3500);
  },
};

/* ==========================================================================
   HELPERS — pure utility functions
   ========================================================================== */

/** Escape HTML special characters to prevent XSS. */
function esc(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Format an integer IDR price as "Rp35.000". */
function formatPrice(price) {
  if (!price) return '<span style="color:var(--c-muted);font-size:.8em">Harga belum tersedia</span>';
  return 'Rp' + Number(price).toLocaleString('id-ID');
}

/** Format an ISO date string to local Indonesian short format. */
function formatDate(isoStr) {
  if (!isoStr) return '—';
  return new Date(isoStr).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'short' });
}

/** Return a category badge HTML string. */
function catBadge(cat) {
  const map = { Bento:'bento', Dessert:'dessert', Drink:'drink', Snack:'snack' };
  const cls = map[cat] || 'other';
  return `<span class="badge badge--${cls}">${esc(cat || '—')}</span>`;
}

/** Return a PO-day badge HTML string. */
function poBadge(day) {
  const map = { Jumat:'po-jumat', Sabtu:'po-sabtu', Minggu:'po-minggu' };
  const cls = map[day] || 'po';
  return `<span class="badge badge--${cls}">PO ${esc(day)}</span>`;
}

/** Return a review-status badge HTML string. */
function statusBadge(status) {
  return status === 'ok'
    ? '<span class="badge badge--ok">OK</span>'
    : '<span class="badge badge--review">Needs Review</span>';
}

/* ==========================================================================
   TAB MODULE — section navigation
   ========================================================================== */
const Tab = {
  /** @type {string} */
  current: '',

  /**
   * Activate a named tab section.
   * @param {string} name — 'dashboard' | 'upload' | 'products' | 'chat'
   */
  set(name) {
    // Hide all sections
    document.querySelectorAll('.tab-section').forEach(s => s.classList.add('hidden'));

    // Deactivate all nav buttons — reset SVG stroke to inactive color
    document.querySelectorAll('.nav-tab').forEach(b => {
      b.classList.remove('is-active');
      const svg = b.querySelector('svg');
      if (svg) svg.setAttribute('stroke', '#9CA3AF');
    });

    // Show target section
    const section = document.getElementById('section-' + name);
    if (section) {
      section.classList.remove('hidden');
      section.classList.add('fade-up');
      section.addEventListener('animationend', () => section.classList.remove('fade-up'), { once: true });
    }

    // Activate tab button — set SVG stroke to navy
    const btn = document.getElementById('tab-' + name);
    if (btn) {
      btn.classList.add('is-active');
      const svg = btn.querySelector('svg');
      if (svg) svg.setAttribute('stroke', '#1B2CC1');
    }

    this.current = name;

    // Trigger lazy data loads
    if (name === 'dashboard') Dashboard.load();
    if (name === 'products')  { Products.loadSellers(); Products.load(); }
  },
};

/* ==========================================================================
   UPLOAD MODULE — file upload form + ETL trigger
   ========================================================================== */
const Upload = {
  /** @type {File|null} */
  file: null,

  init() {
    document.getElementById('upload-form').addEventListener('submit', e => {
      e.preventDefault();
      this.submit();
    });
  },

  /** Called when a file is selected via the input or drag-drop. */
  setFile(file) {
    this.file = file;
    const lbl = document.getElementById('file-label');
    lbl.textContent = file.name + '  (' + (file.size / 1024).toFixed(1) + ' KB)';
    lbl.classList.remove('hidden');
  },

  async submit() {
    const sellerName = document.getElementById('seller-name').value.trim();
    if (!sellerName) { Toast.show('Masukkan nama seller terlebih dahulu', 'error'); return; }
    if (!this.file)  { Toast.show('Pilih file dokumen terlebih dahulu', 'error');   return; }

    // UI: loading state
    const btn     = document.getElementById('upload-btn');
    const btnText = document.getElementById('upload-btn-text');
    const spinner = document.getElementById('upload-spinner');
    btn.disabled = true;
    btnText.textContent = 'Memproses...';
    spinner.classList.remove('hidden');

    try {
      const fd = new FormData();
      fd.append('seller_name', sellerName);
      fd.append('file', this.file);

      const result = await API.upload('/api/upload', fd);
      this._showResult(result);
      Toast.show('Berhasil menyimpan ' + result.rows_inserted + ' produk baru');

      // Refresh JSON preview from DB
      this._loadJsonPreview(result.rows_inserted);
    } catch (err) {
      Toast.show('Upload gagal: ' + err.message, 'error');
    } finally {
      btn.disabled = false;
      btnText.textContent = '+ Upload New Product';
      spinner.classList.add('hidden');
    }
  },

  _showResult(r) {
    const wrap = document.getElementById('upload-result');
    wrap.classList.remove('hidden');
    document.getElementById('upload-result-body').innerHTML = `
      <div class="result-grid">
        <div class="result-box result-box--blue">
          <div class="result-box__value">${r.rows_parsed}</div>
          <div class="result-box__label">Diparsing</div>
        </div>
        <div class="result-box result-box--green">
          <div class="result-box__value">${r.rows_inserted}</div>
          <div class="result-box__label">Tersimpan</div>
        </div>
        <div class="result-box result-box--yellow">
          <div class="result-box__value">${r.rows_duplicate}</div>
          <div class="result-box__label">Duplikat</div>
        </div>
        <div class="result-box result-box--amber">
          <div class="result-box__value">${r.rows_needs_review || 0}</div>
          <div class="result-box__label">Needs Review</div>
        </div>
        <div class="result-box result-box--red">
          <div class="result-box__value">${r.rows_error}</div>
          <div class="result-box__label">Error</div>
        </div>
      </div>
      <p style="font-size:.75rem;color:var(--c-muted);margin-top:8px">
        Log #${r.log_id}
      </p>
    `;
  },

  async _loadJsonPreview(count) {
    if (count === 0) return;
    try {
      const products = await API.get('/api/products');
      const preview = products.slice(0, count).map(p => ({
        name:          p.name,
        price:         p.price,
        category:      p.category,
        ingredients:   p.ingredients,
        allergens:     p.allergens,
        po_days:       p.po_days,
        review_status: p.review_status,
      }));
      const pre = document.getElementById('extract-json');
      if (pre) {
        pre.textContent = JSON.stringify(preview, null, 2);
        document.getElementById('extract-preview').classList.remove('hidden');
      }
    } catch (_) { /* preview is optional */ }
  },
};

/* ==========================================================================
   DASHBOARD MODULE — stats, charts, table
   ========================================================================== */
const Dashboard = {
  async load() {
    await Promise.all([this._loadStats(), this._loadTable()]);
  },

  async _loadStats() {
    try {
      const s = await API.get('/api/stats');

      // Update stat cards
      document.getElementById('stat-total').textContent   = s.total_products;
      document.getElementById('stat-review').textContent  = s.needs_review;
      document.getElementById('stat-sellers').textContent = s.total_sellers;
      document.getElementById('stat-uploads').textContent = s.total_uploads;

      // Review alert banner
      const alert = document.getElementById('review-alert');
      if (s.needs_review > 0) {
        alert.classList.remove('hidden');
        document.getElementById('review-count').textContent = s.needs_review;
      } else {
        alert.classList.add('hidden');
      }

      // Render bar charts (pure CSS, no canvas needed)
      this._renderBarChart('chart-cat',      s.by_category, 'category', '#1B2CC1');
      this._renderBarChart('chart-allergen', s.by_allergen, 'allergen', '#3B82F6');
    } catch (err) {
      console.error('Dashboard stats error:', err);
    }
  },

  /**
   * Render a horizontal bar chart using only divs and CSS.
   * @param {string} containerId
   * @param {Array<{[labelKey]: string, cnt: number}>} data
   * @param {string} labelKey   — property name for the label
   * @param {string} fillColor  — CSS color string
   */
  _renderBarChart(containerId, data, labelKey, fillColor) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (!data || !data.length) {
      el.innerHTML = '<p style="font-size:.8rem;color:var(--c-muted)">Tidak ada data</p>';
      return;
    }
    const max = Math.max(...data.map(d => d.cnt));
    el.innerHTML = data.map(d => {
      const pct = max ? Math.round((d.cnt / max) * 100) : 0;
      return `
        <div class="bar-row">
          <span class="bar-row__label" title="${esc(d[labelKey])}">${esc(d[labelKey])}</span>
          <div class="bar-row__track">
            <div class="bar-row__fill" style="width:${pct}%;background:${fillColor}"></div>
          </div>
          <span class="bar-row__count">${d.cnt}</span>
        </div>`;
    }).join('');
  },

  async _loadTable() {
    const wrap = document.getElementById('dash-product-table');
    if (!wrap) return;
    try {
      const products = await API.get('/api/products');
      if (!products.length) {
        wrap.innerHTML = '<p style="font-size:.85rem;color:var(--c-muted)">Belum ada produk.</p>';
        return;
      }
      wrap.innerHTML = `
        <div class="data-table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>Nama</th><th>Harga</th><th>Kategori</th>
                <th>Alergen</th><th>PO</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              ${products.slice(0, 10).map(p => `
                <tr>
                  <td data-label="Nama"><strong>${esc(p.name)}</strong></td>
                  <td data-label="Harga" style="color:var(--c-navy);font-weight:700">${formatPrice(p.price)}</td>
                  <td data-label="Kategori">${catBadge(p.category)}</td>
                  <td data-label="Alergen" style="font-size:.75rem;color:var(--c-error)">
                    ${(p.allergens || []).join(', ') || '—'}
                  </td>
                  <td data-label="PO" style="font-size:.75rem">
                    ${(p.po_days || []).join(', ') || '—'}
                  </td>
                  <td data-label="Status">${statusBadge(p.review_status)}</td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    } catch (err) {
      console.error('Dashboard table error:', err);
    }
  },
};

/* ==========================================================================
   PRODUCTS MODULE — grid, filters, modal
   ========================================================================== */
const Products = {
  /** @type {number|null} */
  _debounce: null,

  async loadSellers() {
    try {
      const sellers = await API.get('/api/sellers');
      const sel = document.getElementById('filter-seller');
      if (!sel) return;
      const current = sel.value;
      sel.innerHTML =
        '<option value="">Semua Seller</option>' +
        sellers.map(s =>
          `<option value="${s.id}"${s.id == current ? ' selected' : ''}>${esc(s.name)}</option>`
        ).join('');
    } catch (_) { /* seller list is decorative — swallow error */ }
  },

  /** Programmatically set the status filter and reload. */
  filterByStatus(value) {
    const sel = document.getElementById('filter-status');
    if (sel) { sel.value = value; this.load(); }
  },

  async load() {
    const grid  = document.getElementById('products-grid');
    const empty = document.getElementById('products-empty');
    if (!grid) return;

    // Show loading spinner
    grid.innerHTML = '<div class="empty-state"><div class="spinner spinner--navy"></div></div>';
    empty.classList.add('hidden');

    // Build query string from filter controls
    const params = new URLSearchParams();
    const seller = (document.getElementById('filter-seller')?.value || '');
    const cat    = (document.getElementById('filter-cat')?.value    || '');
    const status = (document.getElementById('filter-status')?.value || '');
    const search = (document.getElementById('search-input')?.value  || '').trim();
    if (seller) params.set('seller_id', seller);
    if (cat)    params.set('category',  cat);
    if (status) params.set('status',    status);
    if (search) params.set('search',    search);

    try {
      const products = await API.get('/api/products?' + params);
      if (!products.length) {
        grid.innerHTML = '';
        empty.classList.remove('hidden');
        return;
      }
      grid.innerHTML = products.map(p => this._card(p)).join('');
    } catch (err) {
      grid.innerHTML = `<div class="empty-state">
        <div class="empty-state__icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
        </div>
        <div class="empty-state__title">Gagal memuat produk</div>
        <div class="empty-state__body">${esc(err.message)}</div>
      </div>`;
    }
  },

  debouncedLoad() {
    clearTimeout(this._debounce);
    this._debounce = setTimeout(() => this.load(), 280);
  },

  /** Build HTML for one product card. */
  _card(p) {
    const needsReview = p.review_status === 'needs_review';
    const pkgIcon = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1B2CC1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/>
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
      <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
      <line x1="12" y1="22.08" x2="12" y2="12"/>
    </svg>`;
    return `
      <article
        class="product-card fade-up${needsReview ? ' product-card--review' : ''}${!p.is_available ? ' product-card--inactive' : ''}"
        onclick="Products.openModal(${p.id})"
        role="button" tabindex="0"
        aria-label="Detail produk ${esc(p.name)}"
      >
        <div class="product-card__icon-row">
          <div class="icon-box" aria-hidden="true">${pkgIcon}</div>
          <div>
            <h3 class="product-card__name">${esc(p.name)}</h3>
            <div class="product-card__price">${formatPrice(p.price)}</div>
          </div>
        </div>
        ${needsReview ? `
          <div class="review-reason">
            Perlu verifikasi: ${esc((p.review_reasons || []).join(', '))}
          </div>` : ''}
        <div class="product-card__tags">
          ${catBadge(p.category)}
          ${(p.po_days || []).map(d => poBadge(d)).join(' ')}
          ${statusBadge(p.review_status)}
        </div>
        ${(p.allergens || []).length
          ? `<div class="product-card__allergens">Alergen: ${(p.allergens).join(', ')}</div>`
          : ''}
        <div class="product-card__footer">
          <span class="product-card__seller">${esc(p.seller_name)}</span>
          <div class="product-card__actions" onclick="event.stopPropagation()">
            <button
              class="btn btn--outline" style="height:30px;padding:0 10px;font-size:11px"
              onclick="Products.toggle(${p.id})"
              aria-label="${p.is_available ? 'Nonaktifkan' : 'Aktifkan'} produk"
            >${p.is_available ? 'Nonaktifkan' : 'Aktifkan'}</button>
            <button
              class="btn btn--outline-danger" style="height:30px;padding:0 10px;font-size:11px"
              onclick="Products.delete(${p.id})"
              aria-label="Hapus produk ${esc(p.name)}"
            >Hapus</button>
          </div>
        </div>
      </article>`;
  },

  /** Open the product detail modal. */
  async openModal(id) {
    try {
      // Fetch fresh data (avoids stale cache)
      const products = await API.get('/api/products');
      const p = products.find(x => x.id === id);
      if (!p) return;

      const needsReview = p.review_status === 'needs_review';
      document.getElementById('modal-content').innerHTML = `
        <div class="modal__label">
          ${catBadge(p.category)} ${statusBadge(p.review_status)}
        </div>
        <h2 class="modal__name">${esc(p.name)}</h2>
        <div class="modal__price">${formatPrice(p.price)}</div>
        ${needsReview ? `
          <div class="card card--review" style="margin-bottom:16px;font-size:.8rem;color:#92400e">
            <strong>Perlu Verifikasi:</strong> ${esc((p.review_reasons || []).join(', '))}
          </div>` : ''}
        <div class="modal__detail-row">
          <span class="modal__detail-key">Bahan</span>
          <span class="modal__detail-val">${esc((p.ingredients || []).join(', ')) || '—'}</span>
        </div>
        <div class="modal__detail-row">
          <span class="modal__detail-key">Alergen</span>
          <span class="modal__detail-val" style="${(p.allergens||[]).length ? 'color:var(--c-error);font-weight:600' : ''}">
            ${(p.allergens || []).length ? esc(p.allergens.join(', ')) : 'Tidak ada'}
          </span>
        </div>
        <div class="modal__detail-row">
          <span class="modal__detail-key">Hari PO</span>
          <span class="modal__detail-val">${(p.po_days || []).map(d => poBadge(d)).join(' ') || '—'}</span>
        </div>
        <div class="modal__detail-row">
          <span class="modal__detail-key">Seller</span>
          <span class="modal__detail-val">${esc(p.seller_name)}</span>
        </div>
        <div class="modal__detail-row">
          <span class="modal__detail-key">Status</span>
          <span class="modal__detail-val" style="${p.is_available ? 'color:var(--c-ok)' : 'color:var(--c-muted)'}">
            ${p.is_available ? 'Tersedia' : 'Nonaktif'}
          </span>
        </div>
        <div class="modal__detail-row">
          <span class="modal__detail-key">Dibuat</span>
          <span class="modal__detail-val">${formatDate(p.created_at)}</span>
        </div>
        <div class="modal__actions">
          <button class="btn btn--ghost" style="flex:1"
            onclick="Products.toggle(${p.id}); Modal.close()">
            ${p.is_available ? 'Nonaktifkan' : 'Aktifkan'}
          </button>
          <button class="btn btn--danger" style="flex:1"
            onclick="Products.delete(${p.id}); Modal.close()">
            Hapus
          </button>
        </div>`;

      Modal.open();
    } catch (err) {
      Toast.show('Gagal memuat detail: ' + err.message, 'error');
    }
  },

  async toggle(id) {
    try {
      await API.patch('/api/products/' + id + '/toggle');
      await this.load();
      Toast.show('Status produk diperbarui');
    } catch (err) {
      Toast.show('Gagal memperbarui status: ' + err.message, 'error');
    }
  },

  async delete(id) {
    if (!confirm('Hapus produk ini secara permanen?')) return;
    try {
      await API.delete('/api/products/' + id);
      await this.load();
      Toast.show('Produk dihapus');
    } catch (err) {
      Toast.show('Gagal menghapus: ' + err.message, 'error');
    }
  },
};

/* ==========================================================================
   MODAL MODULE — generic modal open/close
   ========================================================================== */
const Modal = {
  open() {
    document.getElementById('prod-modal').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  },
  close() {
    document.getElementById('prod-modal').classList.add('hidden');
    document.body.style.overflow = '';
  },
};

/* ==========================================================================
   CHAT MODULE — buyer chatbot UI
   ========================================================================== */
const Chat = {
  /** @type {number} Rolling counter for typing indicator IDs. */
  _typingCounter: 0,

  /** Send the text currently in the chat input. */
  async send() {
    const input = document.getElementById('chat-input');
    const question = input.value.trim();
    if (!question) return;
    input.value = '';

    // Append the user's message bubble
    this._appendBubble(question, 'user');

    // Loading state
    const sendBtn = document.getElementById('chat-send');
    const sendIcon = document.getElementById('chat-send-icon');
    const spinner  = document.getElementById('chat-spinner');
    sendBtn.disabled = true;
    if (sendIcon)  sendIcon.classList.add('hidden');
    spinner.classList.remove('hidden');

    const typingId = this._appendTyping();

    try {
      const res = await API.post('/api/chat', { question });
      this._removeTyping(typingId);
      this._appendBubble(res.answer, 'bot');
    } catch (err) {
      this._removeTyping(typingId);
      this._appendBubble('Maaf, terjadi kesalahan: ' + err.message, 'bot');
    } finally {
      sendBtn.disabled = false;
      if (sendIcon) sendIcon.classList.remove('hidden');
      spinner.classList.add('hidden');
    }
  },

  /** Send a predefined suggestion text. */
  sendSuggestion(text) {
    document.getElementById('chat-input').value = text;
    this.send();
  },

  /** Append a chat bubble to the window. */
  _appendBubble(text, role) {
    const win = document.getElementById('chat-window');
    const isBot = role === 'bot';

    // Convert **bold** markdown to <strong> tags; newlines to <br>
    const html = esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>');

    const wrap = document.createElement('div');
    wrap.className = 'chat-bubble-wrap chat-bubble-wrap--' + role;
    wrap.innerHTML = `
      <div class="chat-bubble chat-bubble--${role}">
        <div>${html}</div>
        ${isBot
          ? '<div class="ai-label">-this message is AI Generated</div>'
          : ''}
      </div>`;
    win.appendChild(wrap);
    win.scrollTop = win.scrollHeight;
  },

  /** Append an animated typing indicator; returns its DOM id. */
  _appendTyping() {
    const id  = 'typing-' + (++this._typingCounter);
    const win = document.getElementById('chat-window');
    const div = document.createElement('div');
    div.id = id;
    div.className = 'chat-bubble-wrap chat-bubble-wrap--bot';
    div.innerHTML = `
      <div class="chat-bubble chat-bubble--bot">
        <div class="typing-dots">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
      </div>`;
    win.appendChild(div);
    win.scrollTop = win.scrollHeight;
    return id;
  },

  /** Remove the typing indicator by its id. */
  _removeTyping(id) {
    document.getElementById(id)?.remove();
  },
};

/* ==========================================================================
   DRAG & DROP — upload drop zone
   ========================================================================== */
function initDropZone() {
  const zone = document.getElementById('drop-zone');
  if (!zone) return;

  zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('is-hover'); });
  zone.addEventListener('dragleave', ()  => zone.classList.remove('is-hover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('is-hover');
    const file = e.dataTransfer.files[0];
    if (file) Upload.setFile(file);
  });

  document.getElementById('file-input').addEventListener('change', function() {
    if (this.files[0]) Upload.setFile(this.files[0]);
  });
}

/* ==========================================================================
   MODAL CLOSE — click-outside and ESC key
   ========================================================================== */
function initModalClose() {
  document.getElementById('prod-modal').addEventListener('click', e => {
    if (e.target === e.currentTarget) Modal.close();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') Modal.close();
  });
}

/* ==========================================================================
   BOOTSTRAP — wire everything together on DOMContentLoaded
   ========================================================================== */
document.addEventListener('DOMContentLoaded', () => {
  Upload.init();
  initDropZone();
  initModalClose();

  // Show dashboard on first load
  Tab.set('dashboard');
});
