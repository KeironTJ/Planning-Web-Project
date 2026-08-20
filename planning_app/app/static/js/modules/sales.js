/**
 * Factory Dashboards — Sales module
 *
 * Handles: sales/customer_report.html — charts + TomSelect customer picker
 * Reads:   window.CUSTOMER_REPORT_DATA (set by template when a customer is selected)
 */

'use strict';

document.addEventListener('DOMContentLoaded', function () {

    // ── TomSelect on the customer picker (always present) ─────────────────
    var sel = document.getElementById('customerSelect');
    if (sel && typeof TomSelect !== 'undefined') {
        new TomSelect(sel, {
            placeholder: 'Type to search customers\u2026',
            plugins: ['remove_button', 'clear_button'],
            selectOnTab: true,
            maxOptions: 300,
            closeAfterSelect: false,
        });
    }

    // ── Charts (only present when a customer is selected) ─────────────────
    if (!window.CUSTOMER_REPORT_DATA) return;
    var D = window.CUSTOMER_REPORT_DATA;

    function fmtGbp(v) {
        return v >= 1000000 ? '\u00a3' + (v / 1000000).toFixed(1) + 'm'
             : v >= 1000    ? '\u00a3' + (v / 1000).toFixed(0) + 'k'
             :                '\u00a3' + v.toFixed(0);
    }

    // ── 1. Forward demand bar + line ──────────────────────────────────────
    var demandCtx = document.getElementById('chartDemand');
    if (demandCtx) {
        new Chart(demandCtx, {
            type: 'bar',
            data: {
                labels: D.weeklySchedule.labels,
                datasets: [
                    {
                        label: 'Value (\u00a3)',
                        data: D.weeklySchedule.values,
                        backgroundColor: D.weeklySchedule.labels.map(function (l) {
                            return l === 'Overdue' ? 'rgba(220,53,69,0.75)' : 'rgba(13,110,253,0.6)';
                        }),
                        yAxisID: 'yValue',
                    },
                    {
                        label: 'Units',
                        data: D.weeklySchedule.units,
                        type: 'line',
                        borderColor: 'rgba(25,135,84,0.85)',
                        backgroundColor: 'rgba(25,135,84,0.1)',
                        borderWidth: 2, pointRadius: 3, fill: false, tension: 0.3,
                        yAxisID: 'yUnits',
                    },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: true,
                plugins: { legend: { display: true, position: 'top' } },
                scales: {
                    yValue: { type: 'linear', position: 'left',  title: { display: true, text: 'Value (\u00a3)' }, ticks: { callback: fmtGbp } },
                    yUnits: { type: 'linear', position: 'right', title: { display: true, text: 'Units' },          grid: { drawOnChartArea: false } },
                },
            },
        });
    }

    // ── 2. Open vs Closed doughnut ────────────────────────────────────────
    var ocCtx = document.getElementById('chartOpenClosed');
    if (ocCtx) {
        new Chart(ocCtx, {
            type: 'doughnut',
            data: {
                labels: ['Open (outstanding)', 'Shipped (' + D.closedMonths + 'm)'],
                datasets: [{ data: [D.openSummary.open_value, D.closedSummary.closed_value], backgroundColor: ['rgba(13,110,253,0.75)', 'rgba(25,135,84,0.65)'], borderWidth: 2 }],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } }, tooltip: { callbacks: { label: function (ctx) { return ' ' + fmtGbp(ctx.parsed); } } } },
            },
        });
    }

    // ── 3. Monthly intake bar + line ──────────────────────────────────────
    if (D.monthlyIntake) {
        var mCtx = document.getElementById('chartMonthly');
        if (mCtx) {
            new Chart(mCtx, {
                type: 'bar',
                data: {
                    labels: D.monthlyIntake.map(function (r) { return r.month; }),
                    datasets: [
                        { label: 'Value (\u00a3)', data: D.monthlyIntake.map(function (r) { return r.value; }), backgroundColor: 'rgba(25,135,84,0.65)', yAxisID: 'yVal' },
                        { label: 'Orders', data: D.monthlyIntake.map(function (r) { return r.orders; }), type: 'line', borderColor: 'rgba(13,110,253,0.8)', pointRadius: 3, fill: false, tension: 0.3, yAxisID: 'yOrd' },
                    ],
                },
                options: {
                    responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { display: true, position: 'top' } },
                    scales: {
                        yVal: { type: 'linear', position: 'left',  ticks: { callback: fmtGbp } },
                        yOrd: { type: 'linear', position: 'right', title: { display: true, text: 'Orders' }, grid: { drawOnChartArea: false } },
                    },
                },
            });
        }
    }

    // ── 4. Lead time distribution ─────────────────────────────────────────
    var ltCtx = document.getElementById('chartLeadTime');
    if (ltCtx) {
        new Chart(ltCtx, {
            type: 'bar',
            data: { labels: D.leadTimeDist.labels, datasets: [{ label: 'Orders', data: D.leadTimeDist.counts, backgroundColor: 'rgba(255,193,7,0.7)', borderColor: 'rgba(255,193,7,1)', borderWidth: 1 }] },
            options: {
                responsive: true, maintainAspectRatio: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
            },
        });
    }

    // ── 5. Overdue age breakdown ──────────────────────────────────────────
    if (D.overdueAgeChart) {
        var ageCtx = document.getElementById('chartOverdueAge');
        if (ageCtx) {
            new Chart(ageCtx, {
                type: 'bar',
                data: { labels: D.overdueAgeChart.labels, datasets: [{ label: 'Orders', data: D.overdueAgeChart.counts, backgroundColor: 'rgba(220,53,69,0.7)', borderColor: 'rgba(220,53,69,1)', borderWidth: 1 }] },
                options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
            });
        }
    }

    // ── 6. Product group horizontal bar ───────────────────────────────────
    if (D.byProductGroup) {
        var pgCtx = document.getElementById('chartProdGroup');
        if (pgCtx) {
            new Chart(pgCtx, {
                type: 'bar',
                data: {
                    labels: D.byProductGroup.map(function (r) { return r.group; }),
                    datasets: [{ label: 'Value (\u00a3)', data: D.byProductGroup.map(function (r) { return r.value; }), backgroundColor: 'rgba(25,135,84,0.65)' }],
                },
                options: { indexAxis: 'y', responsive: true, maintainAspectRatio: true, plugins: { legend: { display: false } }, scales: { x: { ticks: { callback: fmtGbp } } } },
            });
        }
    }

    // ── 7. Order type bar + line ──────────────────────────────────────────
    if (D.byOrderType) {
        var otCtx = document.getElementById('chartOrderType');
        if (otCtx) {
            new Chart(otCtx, {
                type: 'bar',
                data: {
                    labels: D.byOrderType.map(function (r) { return r.order_type; }),
                    datasets: [
                        { label: 'Value (\u00a3)', data: D.byOrderType.map(function (r) { return r.value; }), backgroundColor: 'rgba(255,193,7,0.7)', yAxisID: 'yVal' },
                        { label: 'Orders', data: D.byOrderType.map(function (r) { return r.orders; }), type: 'line', borderColor: 'rgba(13,110,253,0.8)', pointRadius: 4, fill: false, tension: 0.2, yAxisID: 'yOrd' },
                    ],
                },
                options: {
                    responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { display: true, position: 'top' } },
                    scales: {
                        yVal: { type: 'linear', position: 'left',  ticks: { callback: fmtGbp } },
                        yOrd: { type: 'linear', position: 'right', title: { display: true, text: 'Orders' }, grid: { drawOnChartArea: false }, ticks: { precision: 0 } },
                    },
                },
            });
        }
    }

    // ── 8. Top models Pareto ──────────────────────────────────────────────
    if (D.byModel) {
        var paretoCtx = document.getElementById('chartModelsPareto');
        if (paretoCtx) {
            var modelValues = D.byModel.map(function (r) { return r.value; });
            var modelTotal  = modelValues.reduce(function (a, b) { return a + b; }, 0);
            var running = 0;
            var cumPct = modelValues.map(function (v) {
                running += v;
                return modelTotal > 0 ? Math.round(running / modelTotal * 1000) / 10 : 0;
            });
            new Chart(paretoCtx, {
                type: 'bar',
                data: {
                    labels: D.byModel.map(function (r) { return r.model; }),
                    datasets: [
                        { label: 'Value (\u00a3)', data: modelValues, backgroundColor: 'rgba(13,202,240,0.65)', yAxisID: 'yVal', order: 2 },
                        { label: 'Cumulative %', data: cumPct, type: 'line', borderColor: 'rgba(220,53,69,0.9)', borderWidth: 2, pointRadius: 3, fill: false, tension: 0.3, yAxisID: 'yPct', order: 1 },
                    ],
                },
                options: {
                    responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { display: true, position: 'top' } },
                    scales: {
                        yVal: { type: 'linear', position: 'left',  ticks: { callback: fmtGbp } },
                        yPct: { type: 'linear', position: 'right', min: 0, max: 100, title: { display: true, text: 'Cumulative %' }, ticks: { callback: function (v) { return v + '%'; } }, grid: { drawOnChartArea: false } },
                    },
                },
            });
        }
    }

    // ── Pagination helper (open orders + closed orders tables) ────────────
    function paginateTable(tableId, navId, initialPageSize) {
        var table = document.getElementById(tableId);
        var nav   = document.getElementById(navId);
        if (!table || !nav) return;
        var rows     = Array.from(table.querySelectorAll('tbody tr'));
        var total    = rows.length;
        var pageSize = initialPageSize;
        var pages    = Math.ceil(total / pageSize);
        var cur      = 1;

        function show(p) {
            cur   = Math.max(1, Math.min(p, Math.ceil(total / pageSize)));
            pages = Math.ceil(total / pageSize);
            rows.forEach(function (r, i) {
                r.style.display = (i >= (cur - 1) * pageSize && i < cur * pageSize) ? '' : 'none';
            });
            render();
        }

        function render() {
            pages = Math.ceil(total / pageSize);
            nav.innerHTML = '';

            var perPageSel = document.createElement('select');
            perPageSel.className  = 'form-select form-select-sm me-3';
            perPageSel.style.cssText = 'width:auto;font-size:0.8rem;height:1.9rem;';
            [10, 25, 50, 100, 200].forEach(function (n) {
                var opt = document.createElement('option');
                opt.value = n; opt.textContent = n + ' rows';
                if (n === pageSize) opt.selected = true;
                perPageSel.appendChild(opt);
            });
            perPageSel.addEventListener('change', function () { pageSize = parseInt(this.value, 10); show(1); });
            nav.appendChild(perPageSel);

            if (pages <= 1 && total <= pageSize) {
                var info2 = document.createElement('small');
                info2.className = 'text-muted'; info2.textContent = total + ' rows';
                nav.appendChild(info2); return;
            }

            var ul = document.createElement('ul');
            ul.className = 'pagination pagination-sm mb-0';

            function mkBtn(label, page, disabled) {
                var item = document.createElement('li');
                item.className = 'page-item' + (disabled ? ' disabled' : '');
                var a = document.createElement('a');
                a.className = 'page-link'; a.href = '#'; a.innerHTML = label;
                if (!disabled) a.addEventListener('click', function (e) { e.preventDefault(); show(page); });
                item.appendChild(a); return item;
            }
            ul.appendChild(mkBtn('&laquo;', cur - 1, cur === 1));

            var liInp = document.createElement('li');
            liInp.className = 'page-item';
            var spanW = document.createElement('span');
            spanW.className = 'page-link d-flex align-items-center gap-1 py-0';
            spanW.style.cssText = 'background:transparent;cursor:default;';
            var inp = document.createElement('input');
            inp.type = 'number'; inp.min = 1; inp.max = pages; inp.value = cur;
            inp.className = 'form-control form-control-sm text-center';
            inp.style.cssText = 'width:3.4rem;height:1.75rem;font-size:0.8rem;padding:0.1rem 0.3rem;';
            inp.addEventListener('change', function () { var v = parseInt(this.value, 10); if (v >= 1 && v <= pages) show(v); else this.value = cur; });
            inp.addEventListener('click', function () { this.select(); });
            var ofSpan = document.createElement('span');
            ofSpan.className = 'text-muted small text-nowrap'; ofSpan.textContent = 'of ' + pages;
            spanW.appendChild(inp); spanW.appendChild(ofSpan); liInp.appendChild(spanW);
            ul.appendChild(liInp);
            ul.appendChild(mkBtn('&raquo;', cur + 1, cur === pages));
            nav.appendChild(ul);

            var info = document.createElement('small');
            info.className = 'text-muted ms-3';
            var s = (cur - 1) * pageSize + 1, e = Math.min(cur * pageSize, total);
            info.textContent = s + '\u2013' + e + ' of ' + total;
            nav.appendChild(info);
        }
        show(1);
    }

    paginateTable('openOrdersTable',   'openOrdersNav',   10);
    paginateTable('closedOrdersTable', 'closedOrdersNav', 10);
});
