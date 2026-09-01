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

    let jobRunning = false;

    /**
     * Spawn the job server-side (returns immediately), then poll /status
     * every 2 s so item badges update as each importer completes.
     * Works even if the user navigates away — the server keeps running.
     */
    async function runJobLive(jobId) {
        const collapseEl = document.getElementById('job-body-' + jobId);
        if (collapseEl) bootstrap.Collapse.getOrCreateInstance(collapseEl).show();

        const rows = [...document.querySelectorAll('#items-table-' + jobId + ' tbody tr[id^="item-row-"]')];
        if (!rows.length) { showToast('No importers in this job', 'danger'); return; }

        const jobStatusEl = document.getElementById('job-status-' + jobId);

        // POST to spawn the thread — returns immediately.
        let startData;
        try {
            startData = await apiPost(jobUrl(jobId, 'run-now'));
        } catch (_) {
            showToast('Failed to start job', 'danger');
            return;
        }

        if (startData.status === 'error') {
            showToast(startData.message || 'Failed to start job', 'danger');
            return;
        }

        if (startData.status === 'already_running') {
            showToast('Job is already running', 'danger');
            return;
        }

        // Mark all items as waiting — thread is spawned but DB not yet updated.
        rows.forEach(row => {
            const sc = document.querySelector('.item-status-' + row.dataset.item);
            if (sc) sc.innerHTML =
                '<span class="spinner-border spinner-border-sm text-secondary me-1" style="width:.6rem;height:.6rem"></span>' +
                '<span class="text-muted" style="font-size:.75rem">Waiting\u2026</span>';
        });
        if (jobStatusEl) jobStatusEl.innerHTML =
            '<span class="spinner-border spinner-border-sm text-warning me-1" style="width:.6rem;height:.6rem"></span>' +
            '<span class="badge bg-warning text-dark">Starting\u2026</span>';

        const runRequestedAt = new Date();
        jobRunning = true;

        const POLL_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

        // Poll until the thread claims is_running, then until it releases it.
        await new Promise(resolve => {
            const deadline   = Date.now() + POLL_TIMEOUT_MS;
            const pollTimer  = setInterval(async () => {
                // Abort if we've been polling longer than the timeout.
                if (Date.now() > deadline) {
                    clearInterval(pollTimer);
                    jobRunning = false;
                    if (jobStatusEl) jobStatusEl.innerHTML =
                        '<span class="badge bg-danger">Timed out</span>';
                    showToast('Job polling timed out — check server logs', 'danger');
                    resolve();
                    return;
                }

                let d;
                try {
                    const r = await fetch(jobUrl(jobId, 'status'), {
                        headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    });
                    d = await r.json();
                } catch (_) { return; }

                if (d.status !== 'ok') return;

                if (d.is_running && jobStatusEl) {
                    jobStatusEl.innerHTML = '<span class="badge bg-warning text-dark">Running</span>';
                }

                // Update item badges from DB state.
                let firstPendingFound = false;
                d.items.forEach(item => {
                    const sc = document.querySelector('.item-status-' + item.id);
                    const lr = document.querySelector('#item-last-run-' + item.id);
                    if (!sc) return;

                    const ranThisRun = item.last_run_at && new Date(item.last_run_at) > runRequestedAt;

                    if (ranThisRun) {
                        if (item.last_status === 'success') {
                            sc.innerHTML = '<span class="badge bg-success">OK' +
                                (item.last_row_count != null ? ' \u00b7 ' + item.last_row_count : '') + '</span>';
                        } else {
                            sc.innerHTML = '<span class="badge bg-danger" title="' +
                                (item.last_error || '').replace(/"/g, "'") +
                                '" data-bs-toggle="tooltip">Failed</span>';
                            new bootstrap.Tooltip(sc.querySelector('[data-bs-toggle="tooltip"]'));
                        }
                        if (lr && item.last_run_at) lr.textContent = window.fmtIso(item.last_run_at, 'datetime');
                    } else if (d.is_running && !firstPendingFound) {
                        firstPendingFound = true;
                        sc.innerHTML =
                            '<span class="spinner-border spinner-border-sm text-primary me-1"></span>' +
                            '<span class="text-muted" style="font-size:.75rem">Running\u2026</span>';
                    }
                    // Remaining items stay as Waiting until their turn.
                });

                // Done when thread has released is_running.
                if (!d.is_running) {
                    clearInterval(pollTimer);
                    jobRunning = false;

                    const finalCls = d.last_status === 'success' ? 'bg-success'
                        : d.last_status === 'partial'            ? 'bg-warning text-dark'
                        : 'bg-danger';
                    const finalLbl = d.last_status === 'success' ? 'OK'
                        : d.last_status === 'partial'            ? 'Partial'
                        : (d.last_status ? 'Failed' : '\u2014');
                    if (jobStatusEl) jobStatusEl.innerHTML =
                        '<span class="badge ' + finalCls + '">' + finalLbl + '</span>';

                    const okCount = d.items.filter(i => {
                        const ranThisRun = i.last_run_at && new Date(i.last_run_at) > runRequestedAt;
                        return ranThisRun && i.last_status === 'success';
                    }).length;
                    const ranCount = d.items.filter(i =>
                        i.last_run_at && new Date(i.last_run_at) > runRequestedAt
                    ).length;

                    if (ranCount > 0) {
                        showToast('Job complete \u2014 ' + okCount + '/' + ranCount + ' importers OK');
                    } else {
                        showToast('Job finished (no items ran — check that importers are configured)', 'danger');
                    }

                    const timingEl = document.getElementById('job-timing-' + jobId);
                    if (timingEl && d.last_run_at) {
                        const lastPart = window.fmtIso(d.last_run_at, 'datetime');
                        const nextPart = d.next_run_at
                            ? ' \u00b7 Next ' + window.fmtIso(d.next_run_at, 'datetime')
                            : '';
                        timingEl.textContent = 'Last ' + lastPart + nextPart;
                    }
                    resolve();
                }
            }, 2000);
        });
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
            const up     = this.classList.contains('move-up-btn');
            const tbody = document.querySelector('#items-table-' + jobId + ' tbody');
            if (!tbody) return;

            const groups = Array.from(tbody.querySelectorAll('tr[id^="item-row-"]')).map(tr => {
                const id = tr.dataset.item;
                const subrow = document.getElementById('params-subrow-' + id);
                return { id, els: [tr, subrow].filter(Boolean) };
            });
            const idx = groups.findIndex(group => group.id === itemId);
            const swapIdx = up ? idx - 1 : idx + 1;
            if (idx === -1 || swapIdx < 0 || swapIdx >= groups.length) return;

            [groups[idx], groups[swapIdx]] = [groups[swapIdx], groups[idx]];
            const data = await apiPost(itemUrl(jobId, itemId), {
                action: 'reorder',
                item_ids: groups.map(group => Number(group.id)),
            });
            if (data.status !== 'ok') { showToast(data.message || 'Reorder failed', 'danger'); return; }

            // Keep each importer and its optional parameters row together.
            groups.forEach(group => group.els.forEach(el => tbody.appendChild(el)));
            renumberItems(jobId);
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

    // ── Auto-refresh job and item status every 30 s ──────────────────────
    const STATUS_URL = toastContainer.dataset.urlStatus;
    setInterval(() => {
        if (jobRunning || !STATUS_URL) return;
        fetch(STATUS_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'ok') return;
                data.jobs.forEach(job => {
                    const statusEl = document.getElementById('job-status-' + job.id);
                    if (statusEl) {
                        const cls = job.last_status === 'success' ? 'bg-success'
                            : job.last_status === 'partial'       ? 'bg-warning text-dark'
                            : job.last_status === 'failed'        ? 'bg-danger'
                            : job.is_running                      ? 'bg-warning text-dark'
                            : 'bg-secondary';
                        const lbl = job.is_running    ? 'Running'
                            : job.last_status === 'success' ? 'OK'
                            : job.last_status === 'partial' ? 'Partial'
                            : job.last_status === 'failed'  ? 'Failed'
                            : '\u2014';
                        statusEl.innerHTML = '<span class="badge ' + cls + '">' + lbl + '</span>';
                    }
                    const timingEl = document.getElementById('job-timing-' + job.id);
                    if (timingEl && job.last_run_at) {
                        const nextPart = job.next_run_at
                            ? ' \u00b7 Next ' + window.fmtIso(job.next_run_at, 'datetime')
                            : '';
                        timingEl.textContent = 'Last ' + window.fmtIso(job.last_run_at, 'datetime') + nextPart;
                    }
                    job.items.forEach(item => {
                        const sc = document.getElementById('item-status-' + item.id);
                        if (sc) {
                            // Dispose any existing tooltip before replacing innerHTML.
                            const existing = sc.querySelector('[data-bs-toggle="tooltip"]');
                            if (existing) bootstrap.Tooltip.getInstance(existing)?.dispose();

                            if (item.last_status === 'success') {
                                sc.innerHTML = '<span class="badge bg-success">OK' +
                                    (item.last_row_count != null ? ' \u00b7 ' + item.last_row_count : '') + '</span>';
                            } else if (item.last_status === 'failed') {
                                sc.innerHTML = '<span class="badge bg-danger" title="' +
                                    (item.last_error || '').replace(/"/g, "'") +
                                    '" data-bs-toggle="tooltip">Failed</span>';
                                new bootstrap.Tooltip(sc.querySelector('[data-bs-toggle="tooltip"]'));
                            }
                        }
                        const lr = document.getElementById('item-last-run-' + item.id);
                        if (lr && item.last_run_at) {
                            lr.textContent = window.fmtIso(item.last_run_at, 'datetime');
                        }
                    });
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
            if (!ok) await new Promise(r => setTimeout(r, 1500));
            location.reload();
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
