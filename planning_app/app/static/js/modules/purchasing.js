/**
 * Factory Dashboards — Purchasing module
 *
 * Handles:
 *  - purchasing/overview.html         — PO schedule bar chart
 *  - purchasing/supplier_delivery.html — supplier overdue stacked bar
 *
 * Reads:
 *  - window.PURCHASING_OVERVIEW_DATA
 *  - window.SUPPLIER_DELIVERY_DATA
 */

'use strict';

document.addEventListener('DOMContentLoaded', function () {

    // ── PO schedule chart (overview page) ─────────────────────────────────
    var PO = window.PURCHASING_OVERVIEW_DATA;
    if (PO && PO.hasPOData && PO.weeks) {
        var ctx = document.getElementById('poScheduleChart');
        if (ctx) {
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: PO.weeks.map(function (w) { return w.week_label; }),
                    datasets: [{
                        label: 'Outstanding Value (GBP)',
                        data:  PO.weeks.map(function (w) { return parseFloat(w.value); }),
                        backgroundColor: PO.weeks.map(function (w) { return w.is_overdue ? '#dc3545' : '#0d6efd'; }),
                        borderColor:     PO.weeks.map(function (w) { return w.is_overdue ? '#b02a37' : '#0a58ca'; }),
                        borderWidth: 1, borderRadius: 3,
                    }],
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: {
                            label:  function (item) { return ' \u00a3' + item.raw.toLocaleString('en-GB', { maximumFractionDigits: 0 }); },
                            footer: function (items) { var w = PO.weeks[items[0].dataIndex]; return w.count + ' line' + (w.count !== 1 ? 's' : ''); },
                        }},
                    },
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, ticks: { callback: function (v) { return '\u00a3' + (v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v); } }, title: { display: true, text: 'Outstanding value (\u00a3)' } },
                    },
                },
            });
        }
    }

    // ── Supplier overdue chart (supplier_delivery page) ───────────────────
    var SD = window.SUPPLIER_DELIVERY_DATA;
    if (SD && SD.suppliers) {
        var top = SD.suppliers
            .filter(function (s) { return s.overdue_value > 0; })
            .sort(function (a, b) { return b.overdue_value - a.overdue_value; })
            .slice(0, 15);

        var sdCtx = document.getElementById('overdueChart');
        if (sdCtx && top.length) {
            new Chart(sdCtx, {
                type: 'bar',
                data: {
                    labels: top.map(function (s) { return s.name.length > 25 ? s.name.slice(0, 23) + '\u2026' : s.name; }),
                    datasets: [
                        { label: 'Overdue',  data: top.map(function (s) { return parseFloat(s.overdue_value); }),   backgroundColor: '#dc3545bb', borderColor: '#dc3545', borderWidth: 1, borderRadius: 3 },
                        { label: 'On Time',  data: top.map(function (s) { return parseFloat(s.total_value) - parseFloat(s.overdue_value); }), backgroundColor: '#198754bb', borderColor: '#198754', borderWidth: 1, borderRadius: 3 },
                    ],
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { position: 'top' },
                        tooltip: { callbacks: {
                            label:  function (item) { return ' ' + item.dataset.label + ': \u00a3' + item.raw.toLocaleString('en-GB', { maximumFractionDigits: 0 }); },
                            footer: function (items) { var s = top[items[0].dataIndex]; return 'Overdue: ' + s.overdue_pct + '%  (' + s.overdue_lines + ' lines)'; },
                        }},
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false }, ticks: { font: { size: 11 } } },
                        y: { stacked: true, beginAtZero: true, title: { display: true, text: 'Outstanding value (\u00a3)' }, ticks: { callback: function (v) { return '\u00a3' + (v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v); } } },
                    },
                    onClick: function (e, els) {
                        if (els.length) window.location = SD.urlPoList + '?q=' + encodeURIComponent(top[els[0].index].name);
                    },
                },
            });
        }
    }
});
