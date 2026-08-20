/**
 * Factory Dashboards — Transport module
 *
 * Handles:
 *  - transport/loading_bay.html — clickable rows, expand/collapse, 3 charts, show-more
 *  - transport/bay_state.html   — bay show-more
 *
 * Reads: window.LOADING_BAY_DATA (loading_bay page only)
 */

'use strict';

document.addEventListener('DOMContentLoaded', function () {

    // ── Bay show-more (both bay_state and loading_bay) ────────────────────
    document.querySelectorAll('.bay-show-more').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var bayIdx = btn.dataset.bay;
            var el = document.getElementById('bay-' + bayIdx);
            if (!el) return;
            el.querySelectorAll('.bay-extra-item').forEach(function (li) { li.style.display = ''; });
            var moreRow = document.getElementById('bay-more-' + bayIdx);
            if (moreRow) moreRow.style.display = 'none';
        });
    });

    var D = window.LOADING_BAY_DATA;
    if (!D) return;

    // ── Clickable order rows ──────────────────────────────────────────────
    document.querySelectorAll('.clickable-order-row').forEach(function (tr) {
        tr.addEventListener('click', function (e) {
            if (e.target.closest('.toggle-btn')) return;
            window.location.href = tr.dataset.href;
        });
    });

    // ── Expand / collapse order detail rows ───────────────────────────────
    document.querySelectorAll('.toggle-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var target   = btn.dataset.target;
            var rows     = document.querySelectorAll('tr[id="' + target + '"]');
            var icon     = btn.querySelector('i');
            var isHidden = rows.length > 0 && rows[0].style.display === 'none';
            rows.forEach(function (r) { r.style.display = isHidden ? '' : 'none'; });
            icon.classList.toggle('bi-chevron-right', !isHidden);
            icon.classList.toggle('bi-chevron-down',   isHidden);
        });
    });

    // ── Customer bar chart ────────────────────────────────────────────────
    var custCtx = document.getElementById('chartCustomer');
    if (custCtx && D.customerChart.length) {
        new Chart(custCtx, {
            type: 'bar',
            data: {
                labels: D.customerChart.map(function (c) { return c.name; }),
                datasets: [
                    { label: 'Ready to Ship',         data: D.customerChart.map(function (c) { return c.ready_value; }),        backgroundColor: '#198754', borderRadius: 0 },
                    { label: 'Partial WIP',            data: D.customerChart.map(function (c) { return c.partial_value; }),      backgroundColor: '#ffc107', borderRadius: 0 },
                    { label: 'Partial WIP \u2014 On Hold', data: D.customerChart.map(function (c) { return c.partial_hold_value; }), backgroundColor: '#fd7e14', borderRadius: 0 },
                    { label: 'Ready \u2014 On Hold',   data: D.customerChart.map(function (c) { return c.ready_hold_value; }),   backgroundColor: '#dc3545', borderRadius: 0 },
                ],
            },
            options: {
                indexAxis: 'y', responsive: true,
                plugins: {
                    legend: { position: 'bottom', labels: { font: { size: 11 }, boxWidth: 12 } },
                    tooltip: { callbacks: {
                        label: function (ctx) {
                            if (!ctx.raw) return null;
                            return ' ' + ctx.dataset.label + ': \u00a3' + ctx.raw.toLocaleString('en-GB', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
                        },
                    }},
                },
                scales: {
                    x: { stacked: true, ticks: { callback: function (v) { return v >= 1000000 ? '\u00a3' + (v / 1000000).toFixed(1) + 'm' : v >= 1000 ? '\u00a3' + (v / 1000).toFixed(0) + 'k' : '\u00a3' + v; } } },
                    y: { stacked: true, ticks: { font: { size: 11 } } },
                },
            },
        });
    }

    // ── Readiness doughnut with value/units toggle ────────────────────────
    var readinessChartData = {
        value: [D.summary.readyValue, D.summary.partialValue, D.summary.partialHoldValue, D.summary.readyHoldValue],
        units: [D.summary.readyUnits, D.summary.partialUnits, D.summary.partialHoldUnits, D.summary.readyHoldUnits],
    };
    var readinessChart = null;
    var rdCtx = document.getElementById('chartReadiness');
    if (rdCtx) {
        readinessChart = new Chart(rdCtx, {
            type: 'doughnut',
            data: {
                labels: ['Ready to Ship', 'Partial WIP', 'Partial WIP \u2014 On Hold', 'Ready \u2014 On Hold'],
                datasets: [{ data: readinessChartData.value, backgroundColor: ['#198754','#ffc107','#fd7e14','#dc3545'], borderWidth: 2 }],
            },
            options: {
                responsive: true, cutout: '60%',
                plugins: {
                    legend: { position: 'bottom', labels: { font: { size: 11 } } },
                    tooltip: { callbacks: { label: function (ctx) {
                        var currentMode = readinessChart && readinessChart._currentMode;
                        return ' ' + ctx.label + ': ' + (currentMode === 'units' ? ctx.raw + ' units' : '\u00a3' + ctx.raw.toLocaleString('en-GB', { minimumFractionDigits: 0, maximumFractionDigits: 0 }));
                    }}},
                },
            },
        });
        readinessChart._currentMode = 'value';

        var btnVal   = document.getElementById('readinessByValue');
        var btnUnits = document.getElementById('readinessByUnits');
        if (btnVal && btnUnits) {
            btnVal.addEventListener('click', function () {
                readinessChart.data.datasets[0].data = readinessChartData.value;
                readinessChart._currentMode = 'value'; readinessChart.update();
                btnVal.classList.replace('btn-outline-primary', 'btn-primary');
                btnUnits.classList.replace('btn-primary', 'btn-outline-primary');
            });
            btnUnits.addEventListener('click', function () {
                readinessChart.data.datasets[0].data = readinessChartData.units;
                readinessChart._currentMode = 'units'; readinessChart.update();
                btnUnits.classList.replace('btn-outline-primary', 'btn-primary');
                btnVal.classList.replace('btn-primary', 'btn-outline-primary');
            });
        }
    }

    // ── Weekly loading timeline chart ─────────────────────────────────────
    var wlCtx = document.getElementById('chartWeeklyLoading');
    if (wlCtx) {
        new Chart(wlCtx, {
            type: 'bar',
            data: {
                labels: D.weeklyLoading.labels,
                datasets: [
                    { label: 'Ready to Ship',         data: D.weeklyLoading.ready,        backgroundColor: '#198754', borderRadius: 2 },
                    { label: 'Partial WIP',            data: D.weeklyLoading.partial,      backgroundColor: '#ffc107', borderRadius: 0 },
                    { label: 'Partial WIP \u2014 On Hold', data: D.weeklyLoading.partial_hold, backgroundColor: '#fd7e14', borderRadius: 0 },
                    { label: 'Ready \u2014 On Hold',   data: D.weeklyLoading.ready_hold,   backgroundColor: '#dc3545', borderRadius: 0 },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { font: { size: 11 }, boxWidth: 12 } },
                    tooltip: { callbacks: {
                        label: function (ctx) {
                            if (!ctx.raw) return null;
                            return ' ' + ctx.dataset.label + ': \u00a3' + ctx.raw.toLocaleString('en-GB', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
                        },
                        footer: function (items) {
                            var total = items.reduce(function (s, i) { return s + (i.raw || 0); }, 0);
                            return total ? 'Total: \u00a3' + total.toLocaleString('en-GB', { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : '';
                        },
                    }},
                },
                scales: {
                    x: { stacked: true, ticks: { font: { size: 11 } } },
                    y: { stacked: true, ticks: { callback: function (v) { return v >= 1000000 ? '\u00a3' + (v / 1000000).toFixed(1) + 'm' : v >= 1000 ? '\u00a3' + (v / 1000).toFixed(0) + 'k' : '\u00a3' + v; } } },
                },
            },
        });
    }

    // ── Show-more helpers ─────────────────────────────────────────────────
    var custMoreBtn = document.getElementById('custShowMoreBtn');
    if (custMoreBtn) {
        custMoreBtn.addEventListener('click', function () {
            document.querySelectorAll('.cust-extra-row').forEach(function (tr) { tr.style.display = ''; });
            var row = document.getElementById('custShowMoreRow');
            if (row) row.style.display = 'none';
        });
    }
});
