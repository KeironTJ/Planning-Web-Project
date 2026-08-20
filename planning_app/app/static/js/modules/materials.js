/**
 * Factory Dashboards — Materials module
 *
 * Handles:
 *  - materials/shortage.html          — pagination, CSV export, 2 charts
 *  - materials/component_shortage.html — pagination, CSV export, 2 charts
 *  - materials/stock_list.html        — doughnut chart with click filter
 *  - materials/index.html             — SO breakdown stacked chart + table
 *
 * Reads:
 *  - window.SHORTAGE_DATA          (shortage + component_shortage pages)
 *  - window.STOCK_LIST_DATA        (stock_list page)
 *  - window.MATERIALS_INDEX_DATA   (index page)
 *
 * Requires: components/table.js (initPagination, exportTableToCsv)
 */

'use strict';

document.addEventListener('DOMContentLoaded', function () {

    // ── Shortage / component shortage page ────────────────────────────────
    var S = window.SHORTAGE_DATA;
    if (S) {
        window.initPagination('summaryTable', 20);
        window.initPagination('detailTable', 50);

        var tmCtx = document.getElementById('topMaterialsChart');
        if (tmCtx && S.topMaterials.length) {
            tmCtx.style.height = Math.max(120, S.topMaterials.length * 24) + 'px';
            new Chart(tmCtx, {
                type: 'bar',
                data: {
                    labels: S.topMaterials.map(function (m) { return m.code; }),
                    datasets: [{ label: 'Shortage Qty', data: S.topMaterials.map(function (m) { return parseFloat(m.shortage); }),
                        backgroundColor: S.barColor + '99', borderColor: S.barColor, borderWidth: 1, borderRadius: 3 }],
                },
                options: {
                    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { callbacks: {
                        title: function (i) { return S.topMaterials[i[0].dataIndex].description || i[0].label; },
                        label: function (i) { return ' Shortage: ' + i.raw.toLocaleString('en-GB', { maximumFractionDigits: 2 }); },
                        afterLabel: function (i) { var d = S.topMaterials[i.dataIndex].earliest_due; return d ? ' Earliest due: ' + d : ''; },
                    }}},
                    scales: {
                        x: { beginAtZero: true, title: { display: true, text: 'Shortage quantity' } },
                        y: { grid: { display: false }, ticks: { font: { size: 11 } } },
                    },
                },
            });
        }

        var bcCtx = document.getElementById('byClassChart');
        if (bcCtx && S.byClass.length) {
            var palette = ['#dc3545','#fd7e14','#ffc107','#198754','#0dcaf0','#0d6efd','#6f42c1','#d63384','#20c997','#6c757d'];
            new Chart(bcCtx, {
                type: 'doughnut',
                data: {
                    labels: S.byClass.map(function (c) { return c.class_id; }),
                    datasets: [{ data: S.byClass.map(function (c) { return parseFloat(c.shortage_qty); }),
                        backgroundColor: S.byClass.map(function (_, i) { return palette[i % palette.length]; }), borderWidth: 2 }],
                },
                options: {
                    responsive: true, cutout: '58%',
                    plugins: {
                        legend: { position: 'right', labels: { font: { size: 11 }, boxWidth: 12 } },
                        tooltip: { callbacks: { label: function (i) {
                            return ' ' + i.label + ': ' + i.raw.toLocaleString('en-GB', { maximumFractionDigits: 2 })
                                + ' (' + S.byClass[i.dataIndex].line_count + ' lines)';
                        }}},
                    },
                },
            });
        }
    }

    // ── Stock list page ───────────────────────────────────────────────────
    var SL = window.STOCK_LIST_DATA;
    if (SL) {
        var slPalette = ['#0d6efd','#198754','#dc3545','#ffc107','#0dcaf0','#6f42c1','#fd7e14','#d63384','#20c997','#6c757d'];
        var slCtx = document.getElementById('classDonut');
        if (slCtx && SL.classes.length) {
            new Chart(slCtx, {
                type: 'doughnut',
                data: {
                    labels: SL.classes.map(function (c) { return c.class_id; }),
                    datasets: [{ data: SL.classes.map(function (c) { return c.count; }),
                        backgroundColor: SL.classes.map(function (_, i) { return slPalette[i % slPalette.length]; }), borderWidth: 2 }],
                },
                options: {
                    responsive: true, cutout: '58%',
                    plugins: {
                        legend: { position: 'right', labels: { font: { size: 11 }, boxWidth: 12 } },
                        tooltip: { callbacks: { label: function (i) {
                            return ' ' + i.label + ': ' + i.raw + ' lines'
                                + (SL.classes[i.dataIndex].deficit_count ? ' (' + SL.classes[i.dataIndex].deficit_count + ' in deficit)' : '');
                        }}},
                    },
                    onClick: function (e, els) {
                        if (els.length) window.location = SL.urlStockList + '?cls=' + encodeURIComponent(SL.classes[els[0].index].class_id);
                    },
                },
            });
        }
    }

    // ── Materials index (SO breakdown) page ───────────────────────────────
    var MI = window.MATERIALS_INDEX_DATA;
    if (!MI || !MI.soBreakdown || !MI.soBreakdown.has_data) return;

    var SD      = MI.soBreakdown;
    var SHORTAGE_URL = MI.urlShortage;
    var STATUSES = ['ok', 'low_risk', 'med_risk', 'late_po', 'high_risk'];
    var LABELS   = { ok: 'Mat. OK', low_risk: 'Soft Risk', med_risk: 'PO Reliant', late_po: 'Late PO', high_risk: 'Shortage' };
    var COLORS   = { ok: '#198754', low_risk: '#0dcaf0', med_risk: '#ffc107', late_po: '#fd7e14', high_risk: '#dc3545' };

    var raw = SD.weeks;
    var overdueWeek = raw.filter(function (w) { return w.is_overdue; });
    var chartRaw    = overdueWeek.concat(raw.filter(function (w) { return !w.is_overdue; }));
    var weekLabels  = chartRaw.map(function (w) { return w.week_label; });

    function weekUrl(w) {
        if (w.is_overdue) return SHORTAGE_URL + '?due_before=' + w.due_before + '&shortages_only=0';
        return SHORTAGE_URL + '?due_from=' + w.due_from + '&due_before=' + w.due_before + '&shortages_only=0';
    }
    function fmt(v) { return '\u00a3' + parseFloat(v).toLocaleString('en-GB', { maximumFractionDigits: 0 }); }
    function bg(s, idx) { return (chartRaw[idx] && chartRaw[idx].is_overdue) ? COLORS[s] + 'bb' : COLORS[s]; }

    var valueDatasets = STATUSES.map(function (s) { return {
        label: LABELS[s], data: chartRaw.map(function (w) { return parseFloat(w[s].value); }),
        backgroundColor: chartRaw.map(function (_, i) { return bg(s, i); }), borderRadius: 2,
    }; });
    var countDatasets = STATUSES.map(function (s) { return {
        label: LABELS[s], data: chartRaw.map(function (w) { return w[s].count; }),
        backgroundColor: chartRaw.map(function (_, i) { return bg(s, i); }), borderRadius: 2,
    }; });
    var pctDatasets = STATUSES.map(function (s) { return {
        label: LABELS[s], data: chartRaw.map(function (w) { return w.total_count ? parseFloat((w[s].count / w.total_count * 100).toFixed(1)) : 0; }),
        backgroundColor: COLORS[s], borderRadius: 2,
    }; });

    var mode = 'value';

    function renderTable(m) {
        var isValue = m === 'value', isPct = m === 'pct';
        var colHeader = isValue ? 'Value' : isPct ? '% Share' : 'SOs';
        var th = '<th>Week</th><th class="text-end">' + colHeader + '</th>';
        STATUSES.forEach(function (s) { th += '<th class="text-end" style="color:' + COLORS[s] + ';">' + LABELS[s] + '</th>'; });
        th += '<th class="text-end">% OK</th><th style="min-width:130px;">Coverage</th>';

        var rows = '';
        chartRaw.forEach(function (w) {
            var rowCls    = w.high_risk.count > 0 ? 'table-danger' : (w.med_risk.count > 0 ? 'table-warning' : '');
            var borderCls = w.is_overdue ? 'border-top border-2' : '';
            var labelCls  = w.is_overdue ? 'text-secondary fst-italic' : '';
            var totalCell = isValue ? fmt(w.total_value || 0) : w.total_count;
            var cells = '';
            STATUSES.forEach(function (s) {
                var val = isValue ? (parseFloat(w[s].value) > 0 ? fmt(w[s].value) : '\u2014')
                        : isPct  ? (w.total_count ? (w[s].count / w.total_count * 100).toFixed(1) + '%' : '\u2014')
                        : (w[s].count || '\u2014');
                cells += '<td class="text-end fw-semibold" style="color:' + COLORS[s] + ';">' + val + '</td>';
            });
            var pctOk  = w.total_count ? Math.round(w.ok.count / w.total_count * 100) : null;
            var pctCol = pctOk >= 80 ? '#198754' : (pctOk >= 50 ? '#e6a800' : '#dc3545');
            var pctCell = pctOk != null ? '<span style="color:' + pctCol + ';">' + pctOk + '%</span>' : '\u2014';
            var barSegs = '';
            if (w.total_count) STATUSES.forEach(function (s) {
                var p = (w[s].count / w.total_count * 100).toFixed(1);
                if (p > 0) barSegs += '<div class="progress-bar" style="width:' + p + '%;background:' + COLORS[s] + ';"></div>';
            });
            var bar = barSegs ? '<div class="progress" style="height:8px;">' + barSegs + '</div>' : '';
            rows += '<tr onclick="window.location=\'' + weekUrl(w) + '\'" style="cursor:pointer;" class="' + rowCls + ' ' + borderCls + '">'
                  + '<td class="fw-semibold ' + labelCls + '">' + w.week_label + '</td>'
                  + '<td class="text-end">' + totalCell + '</td>' + cells
                  + '<td class="text-end fw-semibold">' + pctCell + '</td>'
                  + '<td>' + bar + '</td></tr>';
        });

        document.getElementById('soTableContainer').innerHTML =
            '<div class="card border-0 shadow-sm"><div class="card-header bg-transparent">'
            + '<span class="fw-semibold">Sales Orders by Week</span>'
            + '<span class="text-muted small ms-2">\u2014 one row per sales order, worst-case coverage across all its lines</span>'
            + '</div><div class="card-body p-0"><div class="table-responsive">'
            + '<table class="table table-sm table-hover mb-0 align-middle" style="font-size:.83rem;">'
            + '<thead class="thead-subtle"><tr>' + th + '</tr></thead><tbody>' + rows + '</tbody></table>'
            + '</div></div></div>';
    }

    var ctx = document.getElementById('soBreakdownChart');
    if (!ctx) return;

    var chart = new Chart(ctx, {
        type: 'bar',
        data: { labels: weekLabels, datasets: valueDatasets },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'top' },
                tooltip: {
                    mode: 'index', intersect: false,
                    callbacks: {
                        label: function (item) {
                            var w = chartRaw[item.dataIndex], s = STATUSES[item.datasetIndex];
                            var cnt = w[s].count, pct = w.total_count ? (cnt / w.total_count * 100).toFixed(1) : 0;
                            if (mode === 'value') return ' ' + item.dataset.label + ': \u00a3' + item.raw.toLocaleString('en-GB', { maximumFractionDigits: 0 }) + '  \u00b7  ' + cnt + ' SO' + (cnt !== 1 ? 's' : '') + ' (' + pct + '%)';
                            if (mode === 'pct')   return ' ' + item.dataset.label + ': ' + item.raw + '% (' + cnt + ' SO' + (cnt !== 1 ? 's' : '') + ')';
                            return ' ' + item.dataset.label + ': ' + cnt + ' SO' + (cnt !== 1 ? 's' : '') + ' (' + pct + '%)';
                        },
                        footer: function (items) {
                            var w = chartRaw[items[0] && items[0].dataIndex];
                            if (mode === 'value') { var total = items.reduce(function (s, i) { return s + i.raw; }, 0); return 'Total: \u00a3' + total.toLocaleString('en-GB', { maximumFractionDigits: 0 }) + '  \u00b7  ' + (w && w.total_count) + ' SOs'; }
                            if (mode === 'pct') return 'Total SOs: ' + (w && w.total_count);
                            return 'Total: ' + (w && w.total_count) + ' SOs';
                        },
                    },
                },
            },
            scales: {
                x: { stacked: true, grid: { display: false } },
                y: { stacked: true, beginAtZero: true, title: { display: true, text: 'Order value (\u00a3)' },
                    ticks: { callback: function (v) { return mode === 'value' ? '\u00a3' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v) : mode === 'pct' ? v + '%' : v; } } },
            },
        },
    });

    renderTable('value');

    window.setSoChartMode = function (newMode) {
        mode = newMode;
        var isValue = mode === 'value', isPct = mode === 'pct';
        chart.data.datasets = isValue ? valueDatasets : isPct ? pctDatasets : countDatasets;
        chart.options.scales.y.title.text = isValue ? 'Order value (\u00a3)' : isPct ? 'Share of SOs (%)' : 'Sales orders';
        chart.options.scales.y.max = isPct ? 100 : undefined;
        chart.options.scales.y.ticks.callback = function (v) { return isValue ? '\u00a3' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v) : isPct ? v + '%' : v; };
        chart.update();
        renderTable(mode);
        document.getElementById('soChartTitle').textContent = 'Open Orders by Material Status \u2014 ' + (isValue ? 'Value' : isPct ? '% Share' : 'Volume');
        ['btnValue','btnVolume','btnPct'].forEach(function (id) {
            var active = (id === 'btnValue' && isValue) || (id === 'btnVolume' && !isValue && !isPct) || (id === 'btnPct' && isPct);
            var btn = document.getElementById(id);
            if (!btn) return;
            btn.classList.toggle('active', active);
            btn.classList.toggle('btn-primary', active);
            btn.classList.toggle('btn-outline-primary', !active);
        });
    };
});
