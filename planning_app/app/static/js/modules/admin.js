/**
 * Factory Dashboards — Admin module
 *
 * Handles two admin pages — auto-detected by DOM element presence:
 *  - admin/schedules.html  (#toast-container[data-url-jobs])
 *  - admin/epicor_sync.html (#progress-panel[data-url-run-one])
 *
 * Uses window.fmtIso from app.js (already loaded on every page).
 * Uses window.planningFetch from api.js for all POST requests.
 */

'use strict';

// ── Schedules page ────────────────────────────────────────────────────────────

(function initSchedules() {
    const toastContainer = document.getElementById('toast-container');
    if (!toastContainer || !toastContainer.dataset.urlJobs) return;

    const BASE = toastContainer.dataset.urlJobs;

    function jobUrl(id, ...parts)    { return [BASE, id, ...parts].join('/'); }
    function itemUrl(jid, iid, ...p) { return [BASE, jid, 'items', iid, ...p].join('/'); }

    async function apiPost(url, payload) {
        const r = await planningFetch(url, { method: 'POST', body: JSON.stringify(payload || {}) });
        return r.json();
    }

    function showToast(msg, type) {
        const id  = 'toast-' + Date.now();
        const col = (type === 'danger') ? 'bg-danger' : 'bg-success';
        const ico = (type === 'danger') ? 'bi-x-circle-fill' : 'bi-check-circle-fill';
        toastContainer.insertAdjacentHTML('beforeend',
            '<div id="' + id + '" class="toast align-items-center text-white ' + col + ' border-0 show" role="alert">' +
              '<div class="d-flex">' +
                '<div class="toast-body"><i class="bi ' + ico + ' me-2"></i>' + msg + '</div>' +
                '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>' +
              '</div></div>');
        setTimeout(() => document.getElementById(id)?.remove(), 4000);
    }

    function fmtNow() {
        const d   = new Date();
        const pad = n => String(n).padStart(2, '0');
        const m   = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
        return pad(d.getDate()) + ' ' + m + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function highlightPreset(jobId, minutes) {
        document.querySelectorAll('.preset-btn[data-job="' + jobId + '"]').forEach(btn => {
            const match = parseInt(btn.dataset.minutes) === parseInt(minutes);
            btn.classList.toggle('btn-primary', match);
            btn.classList.toggle('btn-outline-secondary', !match);
        });
        const ci = document.querySelector('.custom-interval[data-job="' + jobId + '"]');
        if (ci) ci.value = minutes;
    }

    function buildItemParams(itemId, importerKey) {
        if (importerKey === 'production_output') {
            const mode = document.querySelector('input[name="prod_mode_item_' + itemId + '"]:checked')?.value;
            if (!mode || mode === 'auto') return null;
            if (mode === 'today') return { mode: 'today', DateFrom: '__today__', DateTo: '__today__' };
            if (mode === 'range') {
                const from = document.querySelector('.prod-from[data-item="' + itemId + '"]')?.value;
                const to   = document.querySelector('.prod-to[data-item="'   + itemId + '"]')?.value;
                if (from && to) return { mode: 'range', DateFrom: from, DateTo: to };
            }
        }
        if (importerKey === 'sales_closed') {
            const mode = document.querySelector('input[name="sc_mode_item_' + itemId + '"]:checked')?.value;
            if (!mode || mode === 'auto') return null;
            if (mode === 'range') {
                const from = document.querySelector('.sc-from[data-item="' + itemId + '"]')?.value;
                const to   = document.querySelector('.sc-to[data-item="'   + itemId + '"]')?.value;
                if (from && to) return { mode: 'range', OrderDateFrom: from, OrderDateTo: to };
            }
        }
        return null;
    }

    async function saveItemParams(itemId, jobId, importerKey) {
        const params = buildItemParams(itemId, importerKey);
        const data   = await apiPost(itemUrl(jobId, itemId), { action: 'save_params', schedule_params: params });
        if (data.status !== 'ok') { showToast(data.message || 'Save failed', 'danger'); return; }
        const badge = document.querySelector('.params-badge-' + itemId);
        if (badge) {
            badge.textContent = data.params_label;
            badge.className = 'badge ' + (params ? 'bg-info text-dark' : 'bg-light text-muted border') + ' small params-badge-' + itemId;
        }
        const toggleBtn = document.querySelector('.params-toggle-btn[data-item="' + itemId + '"]');
        if (toggleBtn) {
            toggleBtn.classList.toggle('btn-info', params !== null);
            toggleBtn.classList.toggle('btn-outline-secondary', params === null);
        }
        showToast('Params saved');
    }

    function startProgressBar(cell) {
        const wrap = document.createElement('div');
        wrap.className = 'progress mt-1';
        wrap.style.cssText = 'height:3px;border-radius:2px;';
        wrap.innerHTML = '<div class="progress-bar progress-bar-striped progress-bar-animated bg-primary" style="width:0%;transition:width .4s ease-out;"></div>';
        cell.appendChild(wrap);
        const bar = wrap.querySelector('.progress-bar');
        let pct = 0;
        const timer = setInterval(() => {
            const step = Math.max(0.3, (88 - pct) * 0.06);
            pct = Math.min(88, pct + step);
            bar.style.width = pct + '%';
        }, 350);
        return function stop(success) {
            clearInterval(timer);
            bar.style.transition = 'width .2s ease-in';
            bar.style.width = '100%';
            bar.classList.remove('progress-bar-animated', 'progress-bar-striped', 'bg-primary');
            bar.classList.add(success ? 'bg-success' : 'bg-danger');
            setTimeout(() => wrap.remove(), 600);
        };
    }

    async function runJobLive(jobId) {
        const collapseEl = document.getElementById('job-body-' + jobId);
        if (collapseEl) bootstrap.Collapse.getOrCreateInstance(collapseEl).show();

        const rows = [...document.querySelectorAll('#items-table-' + jobId + ' tbody tr[id^="item-row-"]')];
        if (!rows.length) { showToast('No importers in this job', 'danger'); return; }

        rows.forEach(row => {
            const id = row.dataset.item;
            const sc = document.querySelector('.item-status-' + id);
            if (sc) sc.innerHTML = '<span class="badge bg-secondary">Waiting</span>';
        });

        const jobStatusEl = document.getElementById('job-status-' + jobId);
        if (jobStatusEl) jobStatusEl.innerHTML = '<span class="badge bg-warning text-dark">Running</span>';

        const results = [];
        for (const row of rows) {
            const itemId = row.dataset.item;
            const sc = document.querySelector('.item-status-' + itemId);
            const lr = document.querySelector('.item-last-run-' + itemId);
            if (sc) sc.innerHTML = '<span class="spinner-border spinner-border-sm text-primary me-1"></span><span class="text-muted" style="font-size:.75rem">Running\u2026</span>';
            const stopBar = sc ? startProgressBar(sc) : null;

            const url = BASE + '/' + jobId + '/items/' + itemId + '/run-one';
            let ok = false;
            try {
                const data = await apiPost(url);
                ok = data.status === 'ok';
                await new Promise(r => setTimeout(r, 200));
                if (stopBar) stopBar(ok);
                await new Promise(r => setTimeout(r, 250));
                if (sc) {
                    sc.innerHTML = ok
                        ? '<span class="badge bg-success">OK \u00b7 ' + (data.row_count ?? 0) + '</span>'
                        : '<span class="badge bg-danger" title="' + (data.message || '').replace(/"/g, "'") + '" data-bs-toggle="tooltip">Failed</span>';
                    if (!ok) new bootstrap.Tooltip(sc.querySelector('[data-bs-toggle="tooltip"]'));
                }
                if (lr && ok) lr.textContent = fmtNow();
            } catch (_) {
                if (stopBar) stopBar(false);
                if (sc) sc.innerHTML = '<span class="badge bg-danger">Error</span>';
            }
            results.push(ok);
        }

        const allOk      = results.every(Boolean);
        const anyOk      = results.some(Boolean);
        const finalStatus = allOk ? 'success' : (anyOk ? 'partial' : 'failed');
        const badgeCls    = allOk ? 'bg-success' : (anyOk ? 'bg-warning text-dark' : 'bg-danger');
        const badgeLbl    = allOk ? 'OK' : (anyOk ? 'Partial' : 'Failed');
        if (jobStatusEl) jobStatusEl.innerHTML = '<span class="badge ' + badgeCls + '">' + badgeLbl + '</span>';

        const timingEl = document.getElementById('job-timing-' + jobId);
        if (timingEl) {
            const nextMatch = timingEl.textContent.match(/Next\s[\w\d: ]+/);
            timingEl.textContent = 'Last ' + fmtNow() + (nextMatch ? ' \u00b7 ' + nextMatch[0] : '');
        }

        apiPost(jobUrl(jobId), { last_status: finalStatus }).catch(() => {});
        showToast('Job complete \u2014 ' + results.filter(Boolean).length + '/' + results.length + ' importers OK');
    }

    function renumberItems(jobId) {
        let n = 1;
        document.querySelectorAll('#items-table-' + jobId + ' tbody tr[id^="item-row-"]').forEach(tr => {
            const cell = tr.querySelector('td:first-child');
            if (cell) cell.textContent = n++;
        });
    }

    // ── Job name rename ───────────────────────────────────────────────────
    document.querySelectorAll('.job-name-input').forEach(inp => {
        const save = async () => {
            const id  = inp.dataset.job;
            const val = inp.value.trim();
            if (!val) { inp.value = inp.defaultValue; return; }
            if (val === inp.defaultValue) return;
            const data = await apiPost(jobUrl(id), { name: val });
            if (data.status !== 'ok') { showToast(data.message || 'Rename failed', 'danger'); inp.value = inp.defaultValue; return; }
            inp.defaultValue = val;
            showToast('Job renamed');
        };
        inp.addEventListener('blur', save);
        inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } });
    });

    // ── Enable toggle ─────────────────────────────────────────────────────
    document.querySelectorAll('.job-toggle').forEach(chk => {
        chk.addEventListener('change', async function () {
            const id = this.dataset.job, enabled = this.checked;
            const data = await apiPost(jobUrl(id), { enabled });
            if (data.status !== 'ok') { this.checked = !enabled; showToast(data.message || 'Failed', 'danger'); return; }
            const timing = document.getElementById('job-timing-' + id);
            if (timing && data.next_run_at) timing.textContent = 'Next ' + window.fmtIso(data.next_run_at);
            showToast('Job ' + (enabled ? 'enabled' : 'disabled'));
        });
    });

    // ── Interval preset buttons ───────────────────────────────────────────
    document.querySelectorAll('.preset-btn').forEach(btn => {
        btn.addEventListener('click', async function () {
            const id = this.dataset.job, minutes = parseInt(this.dataset.minutes);
            const data = await apiPost(jobUrl(id), { interval_minutes: minutes });
            if (data.status !== 'ok') { showToast(data.message || 'Failed', 'danger'); return; }
            highlightPreset(id, minutes);
            showToast('Interval \u2192 ' + minutes + ' min');
        });
    });

    // ── Custom interval ───────────────────────────────────────────────────
    document.querySelectorAll('.custom-interval').forEach(inp => {
        let debounce;
        inp.addEventListener('input', function () {
            const id = this.dataset.job, minutes = parseInt(this.value);
            if (!minutes || minutes < 1) return;
            clearTimeout(debounce);
            debounce = setTimeout(async () => {
                const data = await apiPost(jobUrl(id), { interval_minutes: minutes });
                if (data.status !== 'ok') { showToast(data.message || 'Failed', 'danger'); return; }
                highlightPreset(id, minutes);
                showToast('Interval \u2192 ' + minutes + ' min');
            }, 800);
        });
    });

    // ── Run now button ────────────────────────────────────────────────────
    document.querySelectorAll('.run-job-btn').forEach(btn => {
        btn.addEventListener('click', async function () {
            const jobId = this.dataset.job;
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Running\u2026';
            try { await runJobLive(jobId); }
            finally { this.disabled = false; this.innerHTML = '<i class="bi bi-play-fill"></i> Run now'; }
        });
    });

    // ── Delete job ────────────────────────────────────────────────────────
    document.querySelectorAll('.delete-job-btn').forEach(btn => {
        btn.addEventListener('click', async function () {
            const id   = this.dataset.job;
            const card = document.getElementById('job-card-' + id);
            const name = card?.querySelector('.job-name-input')?.value || 'this job';
            if (!confirm('Delete "' + name + '"? This cannot be undone.')) return;
            const data = await apiPost(jobUrl(id, 'delete'));
            if (data.status !== 'ok') { showToast(data.message || 'Delete failed', 'danger'); return; }
            card?.remove();
            showToast('Job deleted');
        });
    });

    // ── Add item ──────────────────────────────────────────────────────────
    document.querySelectorAll('.add-item-btn').forEach(btn => {
        btn.addEventListener('click', async function () {
            const jobId = this.dataset.job;
            const sel   = document.querySelector('.add-item-select[data-job="' + jobId + '"]');
            const key   = sel?.value;
            if (!key) { showToast('Select an importer first', 'danger'); return; }
            const data = await apiPost(jobUrl(jobId, 'items'), { importer_key: key });
            if (data.status !== 'ok') { showToast(data.message || 'Add failed', 'danger'); return; }
            sel.value = '';
            sel.querySelector('option[value="' + key + '"]')?.remove();
            location.reload();
        });
    });

    // ── Remove item ───────────────────────────────────────────────────────
    document.querySelectorAll('.remove-item-btn').forEach(btn => {
        btn.addEventListener('click', async function () {
            const jobId  = this.dataset.job;
            const itemId = this.dataset.item;
            const data   = await apiPost(itemUrl(jobId, itemId, 'delete'));
            if (data.status !== 'ok') { showToast(data.message || 'Remove failed', 'danger'); return; }
            document.getElementById('item-row-' + itemId)?.remove();
            document.getElementById('params-subrow-' + itemId)?.remove();
            renumberItems(jobId);
            showToast('Importer removed');
            const sel = document.querySelector('.add-item-select[data-job="' + jobId + '"]');
            if (sel) {
                const opt = document.createElement('option');
                opt.value = data.importer_key || '';
                opt.textContent = (data.importer_key || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                sel.appendChild(opt);
            }
        });
    });

    // ── Move up / down ────────────────────────────────────────────────────
    document.querySelectorAll('.move-up-btn, .move-down-btn').forEach(btn => {
        btn.addEventListener('click', async function () {
            const jobId  = this.dataset.job;
            const itemId = this.dataset.item;
            const dir    = this.classList.contains('move-up-btn') ? 'move_up' : 'move_down';
            const data   = await apiPost(itemUrl(jobId, itemId), { action: dir });
            if (data.status !== 'ok') { showToast(data.message || 'Reorder failed', 'danger'); return; }
            location.reload();
        });
    });

    // ── Params — production_output ────────────────────────────────────────
    document.querySelectorAll('.prod-mode-radio').forEach(radio => {
        radio.addEventListener('change', function () {
            const itemId = this.dataset.item;
            const range  = document.getElementById('prod-range-' + itemId);
            if (this.value === 'range') range?.classList.remove('d-none');
            else range?.classList.add('d-none');
            saveItemParams(itemId, this.dataset.job, 'production_output');
        });
    });
    document.querySelectorAll('.prod-from, .prod-to').forEach(inp => {
        let deb;
        inp.addEventListener('change', function () {
            const id = this.dataset.item; clearTimeout(deb);
            deb = setTimeout(() => saveItemParams(id, this.dataset.job, 'production_output'), 600);
        });
    });

    // ── Params — sales_closed ─────────────────────────────────────────────
    document.querySelectorAll('.sc-mode-radio').forEach(radio => {
        radio.addEventListener('change', function () {
            const itemId = this.dataset.item;
            const range  = document.getElementById('sc-range-' + itemId);
            if (this.value === 'range') range?.classList.remove('d-none');
            else range?.classList.add('d-none');
            saveItemParams(itemId, this.dataset.job, 'sales_closed');
        });
    });
    document.querySelectorAll('.sc-from, .sc-to').forEach(inp => {
        let deb;
        inp.addEventListener('change', function () {
            const id = this.dataset.item; clearTimeout(deb);
            deb = setTimeout(() => saveItemParams(id, this.dataset.job, 'sales_closed'), 600);
        });
    });

    // ── Auto-refresh job status every 30 s ───────────────────────────────
    setInterval(() => {
        fetch(location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(r => r.text())
            .then(html => {
                const doc = new DOMParser().parseFromString(html, 'text/html');
                document.querySelectorAll('[id^="job-status-"]').forEach(el => {
                    const fresh = doc.getElementById(el.id);
                    if (fresh) el.innerHTML = fresh.innerHTML;
                });
                document.querySelectorAll('[id^="job-timing-"]').forEach(el => {
                    const fresh = doc.getElementById(el.id);
                    if (fresh) el.innerHTML = fresh.innerHTML;
                });
                document.querySelectorAll('time.fmt-utc[data-utc]').forEach(el => {
                    el.textContent = window.fmtIso(el.dataset.utc, el.dataset.fmt);
                });
            })
            .catch(() => {});
    }, 30000);

    // ── Tooltips + chevron rotation ───────────────────────────────────────
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
    document.querySelectorAll('[id^="job-body-"]').forEach(collapseEl => {
        const jobId   = collapseEl.id.replace('job-body-', '');
        const chevron = document.querySelector('#job-card-' + jobId + ' .job-chevron');
        collapseEl.addEventListener('show.bs.collapse', () => { if (chevron) chevron.style.transform = 'rotate(180deg)'; });
        collapseEl.addEventListener('hide.bs.collapse', () => { if (chevron) chevron.style.transform = 'rotate(0deg)'; });
    });
})();

// ── Epicor sync page ──────────────────────────────────────────────────────────

(function initEpicorSync() {
    const progressPanel = document.getElementById('progress-panel');
    if (!progressPanel || !progressPanel.dataset.urlRunOne) return;

    const RUN_ONE = progressPanel.dataset.urlRunOne;

    function getFormParams(form) {
        const p = {};
        new FormData(form).forEach((v, k) => { if (k !== 'csrf_token') p[k] = v; });
        return p;
    }

    function mkRow(key, state, detail) {
        const icons  = { wait: '<i class="bi bi-clock text-muted me-2"></i>', run: '<span class="spinner-border spinner-border-sm me-2 text-primary"></span>', ok: '<i class="bi bi-check-circle-fill text-success me-2"></i>', err: '<i class="bi bi-x-circle-fill text-danger me-2"></i>' };
        const badges = { wait: '<span class="badge bg-secondary">Waiting</span>', run: '<span class="badge bg-warning text-dark">Running</span>', ok: '<span class="badge bg-success">OK</span>', err: '<span class="badge bg-danger">Failed</span>' };
        return '<td class="fw-semibold small" style="width:140px">' + key + '</td>' +
               '<td class="small">' + (icons[state] || '') + detail + '</td>' +
               '<td class="text-center" style="width:80px">' + (badges[state] || '') + '</td>';
    }

    function startProgressBar(cell) {
        const wrap = document.createElement('div');
        wrap.className = 'progress mt-1';
        wrap.style.cssText = 'height:3px;border-radius:2px;';
        wrap.innerHTML = '<div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width:0%;transition:width 0.4s ease-out;"></div>';
        cell.appendChild(wrap);
        const inner = wrap.querySelector('.progress-bar');
        let pct = 0;
        const timer = setInterval(() => {
            const step = Math.max(0.2, (85 - pct) * 0.06);
            pct = Math.min(85, pct + step);
            inner.style.width = pct + '%';
        }, 400);
        return function stop(success) {
            clearInterval(timer);
            inner.style.transition = 'width 0.25s ease-in';
            inner.style.width = '100%';
            inner.classList.remove('progress-bar-animated', 'progress-bar-striped');
            inner.classList.add(success ? 'bg-success' : 'bg-danger');
        };
    }

    async function runOne(key, params, tr) {
        tr.innerHTML = mkRow(key, 'run', 'Syncing ' + key + '...');
        const stopProgress = startProgressBar(tr.querySelectorAll('td')[1]);
        try {
            const r = await planningFetch(RUN_ONE, { method: 'POST', body: JSON.stringify({ baq_key: key, params }) });
            let data;
            try { data = await r.json(); }
            catch (_) { stopProgress(false); await new Promise(r => setTimeout(r, 350)); tr.innerHTML = mkRow(key, 'err', 'Server timed out \u2014 try a smaller date range.'); return false; }
            const ok = data.status === 'ok' || data.status === 'success';
            await new Promise(r => setTimeout(r, 200));
            stopProgress(ok);
            await new Promise(r => setTimeout(r, 350));
            tr.innerHTML = ok ? mkRow(key, 'ok', '') : mkRow(key, 'err', data.message || data.error || data.status);
            return ok;
        } catch (e) {
            stopProgress(false);
            await new Promise(r => setTimeout(r, 350));
            tr.innerHTML = mkRow(key, 'err', 'Network error: ' + e.message);
            return false;
        }
    }

    const tbody = document.getElementById('progress-rows');

    document.querySelectorAll('.individual-sync').forEach(form => {
        form.addEventListener('submit', async e => {
            e.preventDefault();
            progressPanel.classList.remove('d-none');
            progressPanel.scrollIntoView({ behavior: 'smooth' });
            const tr = document.createElement('tr');
            tbody.appendChild(tr);
            const ok = await runOne(form.dataset.key, getFormParams(form), tr);
            if (ok) location.reload();
        });
    });

    const syncAllBtn = document.getElementById('sync-all-btn');
    if (syncAllBtn) {
        syncAllBtn.addEventListener('click', async () => {
            tbody.innerHTML = '';
            progressPanel.classList.remove('d-none');
            progressPanel.scrollIntoView({ behavior: 'smooth' });
            const forms = [...document.querySelectorAll('.individual-sync')];
            const rows  = forms.map(form => {
                const tr = document.createElement('tr');
                tr.innerHTML = mkRow(form.dataset.key, 'wait', 'Waiting...');
                tbody.appendChild(tr);
                return { form, tr };
            });
            syncAllBtn.disabled = true;
            document.querySelectorAll('.individual-sync button').forEach(b => b.disabled = true);
            for (const { form, tr } of rows) {
                await runOne(form.dataset.key, getFormParams(form), tr);
                await new Promise(r => setTimeout(r, 500));
            }
            location.reload();
        });
    }

    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
})();
