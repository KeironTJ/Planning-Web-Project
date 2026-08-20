/**
 * Factory Dashboards — Operations module
 *
 * Handles:
 *  - operations/daily_output.html — output chart, sync button, trend toggle
 *  - operations/wip_overview.html — WIP chart, drag-scroll, job comment modal
 *
 * Reads:
 *  - window.DAILY_OUTPUT_DATA  (set by daily_output.html)
 *  - window.WIP_OVERVIEW_DATA  (set by wip_overview.html)
 */

'use strict';

// ── Daily output: functions called by inline onclick handlers ─────────────────

/** Toggle the trendline dataset visibility on the output chart. */
function toggleTrend() {
    var chart = window._outputChart;
    if (!chart) return;
    var idx = chart.data.datasets.findIndex(function (d) { return d.label && d.label.startsWith('Trend'); });
    if (idx < 0) return;
    chart.setDatasetVisibility(idx, !chart.isDatasetVisible(idx));
    chart.update();
    var btn = document.getElementById('btn-trend');
    if (btn) btn.classList.toggle('active', chart.isDatasetVisible(idx));
}

/** Navigate to the daily output page filtered by a specific department. */
function applyDeptFilter(dept) {
    var url = new URL(window.location.href);
    if (dept) url.searchParams.set('section', dept);
    else url.searchParams.delete('section');
    url.searchParams.delete('page');
    window.location.href = url.toString();
}

/** Trigger a live sync from Epicor and show progress on the page. */
async function syncOutput() {
    var btn      = document.getElementById('btn-sync');
    var spinner  = document.getElementById('sync-spinner');
    var icon     = document.getElementById('sync-icon');
    var result   = document.getElementById('sync-result');
    var pbWrap   = document.getElementById('sync-pb-wrap');
    var pb       = document.getElementById('sync-pb');
    var syncUrl  = btn && btn.dataset.urlSync;
    if (!syncUrl) return;

    btn.disabled = true;
    spinner.classList.remove('d-none');
    icon.classList.add('d-none');
    result.className = 'alert alert-secondary py-1 px-2 mb-0 small';
    result.textContent = 'Syncing from Epicor \u2014 this may take a moment\u2026';
    pbWrap.classList.remove('d-none');

    var pct = 0;
    var pbTimer = setInterval(function () {
        var step = Math.max(0.2, (85 - pct) * 0.06);
        pct = Math.min(85, pct + step);
        pb.style.width = pct + '%';
    }, 400);

    function stopProgress(success) {
        clearInterval(pbTimer);
        pb.style.transition = 'width 0.25s ease-in';
        pb.style.width = '100%';
        pb.classList.remove('progress-bar-animated', 'progress-bar-striped');
        pb.classList.add(success ? 'bg-success' : 'bg-danger');
    }

    try {
        var resp = await planningFetch(syncUrl, { method: 'POST', body: '{}' });
        var data;
        try { data = await resp.json(); }
        catch (_) {
            throw new Error('Server timed out \u2014 the sync is taking too long. Please try again.');
        }
        if (data.status === 'ok') {
            stopProgress(true);
            location.reload();
        } else {
            stopProgress(false);
            result.className = 'alert alert-danger py-1 px-2 mb-0 small';
            result.textContent = data.message || 'Sync failed \u2014 see page for details.';
            setTimeout(function () { location.reload(); }, 3000);
        }
    } catch (err) {
        stopProgress(false);
        result.className = 'alert alert-danger py-1 px-2 mb-0 small';
        result.textContent = 'Network error \u2014 ' + err.message;
        btn.disabled = false;
        spinner.classList.add('d-none');
        icon.classList.remove('d-none');
    }
}

window.toggleTrend     = toggleTrend;
window.applyDeptFilter = applyDeptFilter;
window.syncOutput      = syncOutput;

// ── DOMContentLoaded ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {

    // ── Daily output chart ────────────────────────────────────────────────
    var D = window.DAILY_OUTPUT_DATA;
    if (D && D.hasDepartments) {
        var datasets = D.deptData.map(function (d) {
            return { label: d.label, data: d.data, backgroundColor: d.backgroundColor, borderRadius: 2, borderSkipped: false };
        });

        if (D.trendData && D.trendData.some(function (v) { return v !== null && v !== undefined; })) {
            datasets.push({
                label:                'Trend' + (D.trendDeptLabel ? ' (' + D.trendDeptLabel + ')' : ''),
                data:                 D.trendData,
                type:                 'line',
                borderColor:          'rgba(99,102,241,0.85)',
                borderWidth:          1.5,
                borderDash:           [],
                pointRadius:          D.view === 'weekly' ? 3 : 0,
                pointHoverRadius:     4,
                pointBackgroundColor: 'rgba(99,102,241,0.9)',
                fill:                 false,
                tension:              0.3,
                order:                -3,
                hidden:               true,
            });
        }

        if (D.targetQty > 0) {
            datasets.push({
                label:       D.view === 'weekly' ? 'Weekly Target' : 'Target (' + D.targetQty + ')',
                data:        D.targetData.map(function (v) { return v > 0 ? v : null; }),
                type:        'line',
                borderColor: 'rgba(220,53,69,0.75)',
                borderWidth: 2,
                borderDash:  [6, 4],
                pointRadius: 0,
                fill:        false,
                tension:     0,
                order:       -1,
            });
        }

        var ctx = document.getElementById('outputChart');
        if (ctx) {
            window._outputChart = new Chart(ctx, {
                type: 'bar',
                data: { labels: D.labels, datasets: datasets },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 14, font: { size: 11 } } },
                        tooltip: { callbacks: {
                            footer: function (items) {
                                var total = items.reduce(function (s, i) { return s + i.parsed.y; }, 0);
                                return total > 0 ? 'Total: ' + Math.round(total) : '';
                            },
                        }},
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { font: { size: 11 } } },
                        y: { beginAtZero: true, ticks: { font: { size: 11 } }, grid: { color: 'rgba(0,0,0,0.05)' } },
                    },
                },
            });
        }
    }

    // Auto-reload every 2 minutes
    if (window.DAILY_OUTPUT_DATA) {
        setTimeout(function () { location.reload(); }, 2 * 60 * 1000);
    }

    // ── WIP overview chart ────────────────────────────────────────────────
    var W = window.WIP_OVERVIEW_DATA;
    if (W && W.hasWipOps) {
        var wipCtx = document.getElementById('wipChart');
        if (wipCtx) {
            new Chart(wipCtx, {
                type: 'bar',
                data: {
                    labels: W.chartLabels,
                    datasets: W.chartDatasets.map(function (d) {
                        return { label: d.label, data: d.data, backgroundColor: d.backgroundColor, borderRadius: 2, borderSkipped: false };
                    }),
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 14, font: { size: 11 } } },
                        tooltip: { callbacks: {
                            footer: function (items) {
                                var total = items.reduce(function (s, i) { return s + i.parsed.y; }, 0);
                                return total > 0 ? 'Total: ' + Math.round(total) : '';
                            },
                        }},
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false }, ticks: { font: { size: 11 } } },
                        y: { stacked: true, beginAtZero: true, ticks: { font: { size: 11 } }, grid: { color: 'rgba(0,0,0,0.05)' } },
                    },
                },
            });
        }
    }

    // ── WIP overview: drag-scroll on job table ────────────────────────────
    var wrapper = document.getElementById('jobTableWrapper');
    if (wrapper) {
        var dragging = false, startX = 0, scrollStart = 0, moved = false;
        wrapper.addEventListener('mousedown', function (e) {
            if (e.target.closest('button,a,input,select,label')) return;
            dragging = true; moved = false;
            startX = e.pageX - wrapper.getBoundingClientRect().left;
            scrollStart = wrapper.scrollLeft;
            wrapper.style.cursor = 'grabbing';
            e.preventDefault();
        });
        document.addEventListener('mousemove', function (e) {
            if (!dragging) return;
            var x = e.pageX - wrapper.getBoundingClientRect().left;
            var walk = x - startX;
            if (Math.abs(walk) > 3) moved = true;
            wrapper.scrollLeft = scrollStart - walk;
        });
        document.addEventListener('mouseup', function () {
            if (dragging) { dragging = false; wrapper.style.cursor = 'grab'; }
        });
        wrapper.addEventListener('click', function (e) { if (moved) e.stopPropagation(); }, true);
    }

    // ── WIP overview: job comment modal (with edit/delete) ────────────────
    var modal  = document.getElementById('jobCommentModal');
    if (!modal) return;
    var thread = document.getElementById('jobCommentThread');
    var form   = document.getElementById('jobCommentForm');
    var body   = document.getElementById('jobCommentBody');
    var submit = document.getElementById('jobCommentSubmit');
    var label  = document.getElementById('jobCommentModalLabel');
    var count  = document.getElementById('jobCommentCharCount');
    var activeJob = null, activeJobBtn = null;

    function esc(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
    function updateBtn(btn, n) {
        btn.dataset.count = n;
        btn.innerHTML = n > 0
            ? '<i class="bi bi-chat-fill text-warning" style="font-size:.9rem;"></i><sup style="font-size:.65rem;">' + n + '</sup>'
            : '<i class="bi bi-chat text-muted" style="font-size:.9rem;"></i>';
    }
    function actionBtns() {
        return '<button class="btn btn-link btn-sm p-0 text-secondary edit-comment-btn" style="font-size:.72rem;"><i class="bi bi-pencil me-1"></i>Edit</button>'
             + '<button class="btn btn-link btn-sm p-0 text-danger delete-comment-btn" style="font-size:.72rem;"><i class="bi bi-trash me-1"></i>Delete</button>';
    }
    function renderComment(c) {
        var div = document.createElement('div');
        div.className = 'mb-3 job-comment-item';
        div.dataset.id = c.id; div.dataset.body = c.body;
        var edited = c.updated_at ? ' <span class="text-muted" style="font-size:.68rem;">(edited ' + esc(c.updated_at) + ')</span>' : '';
        div.innerHTML =
            '<div class="d-flex justify-content-between align-items-baseline mb-1">' +
                '<span class="fw-semibold small">' + esc(c.user) + '</span>' +
                '<span class="comment-ts text-muted" style="font-size:.72rem;">' + esc(c.created_at) + edited + '</span>' +
            '</div>' +
            '<div class="comment-body rounded p-2 small" style="background:#f0f4ff;white-space:pre-wrap;">' + esc(c.body) + '</div>' +
            (c.can_edit ? '<div class="comment-actions mt-1 d-flex gap-2">' + actionBtns() + '</div>' : '');
        return div;
    }
    function loadComments(jobNum) {
        thread.innerHTML = '<div class="text-center text-muted small py-3"><span class="spinner-border spinner-border-sm me-1"></span> Loading...</div>';
        fetch('/api/v1/operations/jobs/' + encodeURIComponent(jobNum) + '/comments', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                thread.innerHTML = '';
                if (!data.ok) { thread.innerHTML = '<p class="text-danger small p-3">Failed to load.</p>'; return; }
                if (!data.comments.length) {
                    thread.innerHTML = '<p class="text-muted small text-center py-3">No notes yet \u2014 be the first to add one.</p>';
                } else {
                    data.comments.forEach(function (c) { thread.appendChild(renderComment(c)); });
                    thread.scrollTop = thread.scrollHeight;
                }
            })
            .catch(function () { thread.innerHTML = '<p class="text-danger small p-3">Could not load.</p>'; });
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.job-comment-btn');
        if (!btn) return;
        activeJob = btn.dataset.job; activeJobBtn = btn;
        label.textContent = 'Notes \u2014 Job ' + activeJob;
        body.value = ''; count.textContent = '0 / 1000';
        bootstrap.Modal.getOrCreateInstance(modal).show();
        loadComments(activeJob);
    });

    body.addEventListener('input', function () { count.textContent = body.value.length + ' / 1000'; });

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (!activeJob || !body.value.trim()) return;
        submit.disabled = true;
        planningFetch('/api/v1/operations/jobs/' + encodeURIComponent(activeJob) + '/comments', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
            body: 'body=' + encodeURIComponent(body.value),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            submit.disabled = false;
            if (!data.ok) { alert(data.error || 'Failed to post.'); return; }
            if (thread.querySelector('.text-muted.small.text-center')) thread.innerHTML = '';
            thread.appendChild(renderComment(data.comment));
            thread.scrollTop = thread.scrollHeight;
            body.value = ''; count.textContent = '0 / 1000';
            if (activeJobBtn) updateBtn(activeJobBtn, parseInt(activeJobBtn.dataset.count || '0', 10) + 1);
        })
        .catch(function () { submit.disabled = false; alert('Network error.'); });
    });

    thread.addEventListener('click', function (e) {
        var item = e.target.closest('.job-comment-item');
        if (!item) return;
        var id = item.dataset.id;
        var actions = item.querySelector('.comment-actions');

        if (e.target.closest('.edit-comment-btn')) {
            var ta = document.createElement('textarea');
            ta.className = 'form-control form-control-sm mb-1'; ta.rows = 3; ta.maxLength = 1000; ta.value = item.dataset.body;
            item.querySelector('.comment-body').replaceWith(ta);
            actions.innerHTML = '<button class="btn btn-primary btn-sm py-0 save-edit-btn">Save</button> '
                              + '<button class="btn btn-outline-secondary btn-sm py-0 cancel-edit-btn">Cancel</button>';
            return;
        }
        if (e.target.closest('.cancel-edit-btn')) {
            var ta2 = item.querySelector('textarea');
            if (!ta2) return;
            var d = document.createElement('div');
            d.className = 'comment-body rounded p-2 small'; d.style.cssText = 'background:#f0f4ff;white-space:pre-wrap;';
            d.textContent = item.dataset.body; ta2.replaceWith(d);
            actions.innerHTML = actionBtns(); return;
        }
        if (e.target.closest('.save-edit-btn')) {
            var ta3 = item.querySelector('textarea');
            if (!ta3) return;
            var newBody = ta3.value.trim(); if (!newBody) return;
            var saveBtn = e.target.closest('.save-edit-btn'); saveBtn.disabled = true;
            planningFetch('/api/v1/operations/jobs/comments/' + id, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
                body: 'body=' + encodeURIComponent(newBody),
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                saveBtn.disabled = false;
                if (!data.ok) { alert(data.error || 'Failed to save.'); return; }
                item.dataset.body = data.comment.body;
                var d2 = document.createElement('div');
                d2.className = 'comment-body rounded p-2 small'; d2.style.cssText = 'background:#f0f4ff;white-space:pre-wrap;';
                d2.textContent = data.comment.body; ta3.replaceWith(d2);
                if (data.comment.updated_at) {
                    var ts = item.querySelector('.comment-ts');
                    if (ts) ts.innerHTML = ts.textContent.replace(/\(edited.*\)/, '') + ' <span class="text-muted" style="font-size:.68rem;">(edited ' + esc(data.comment.updated_at) + ')</span>';
                }
                actions.innerHTML = actionBtns();
            })
            .catch(function () { saveBtn.disabled = false; alert('Network error.'); });
            return;
        }
        if (e.target.closest('.delete-comment-btn')) {
            actions.innerHTML = '<span class="text-danger me-2" style="font-size:.72rem;">Delete this note?</span>'
                              + '<button class="btn btn-danger btn-sm py-0 me-1 confirm-delete-btn" style="font-size:.72rem;">Delete</button>'
                              + '<button class="btn btn-outline-secondary btn-sm py-0 cancel-delete-btn" style="font-size:.72rem;">Cancel</button>';
            return;
        }
        if (e.target.closest('.cancel-delete-btn')) { actions.innerHTML = actionBtns(); return; }
        if (e.target.closest('.confirm-delete-btn')) {
            var delBtn = e.target.closest('.confirm-delete-btn'); delBtn.disabled = true;
            planningFetch('/api/v1/operations/jobs/comments/' + id, {
                method: 'DELETE',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.ok) { delBtn.disabled = false; alert(data.error || 'Failed to delete.'); return; }
                item.remove();
                if (activeJobBtn) updateBtn(activeJobBtn, Math.max(0, parseInt(activeJobBtn.dataset.count || '0', 10) - 1));
                if (!thread.querySelector('.job-comment-item')) {
                    thread.innerHTML = '<p class="text-muted small text-center py-3">No notes yet \u2014 be the first to add one.</p>';
                }
            })
            .catch(function () { delBtn.disabled = false; alert('Network error.'); });
        }
    });
});
