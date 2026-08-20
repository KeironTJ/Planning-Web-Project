/**
 * Factory Dashboards — Orders module
 *
 * Handles:
 *  1. Row expand/collapse (.ob-toggle-btn) — orders/order_book.html
 *  2. Orders dashboard charts             — orders/dashboard.html
 *  3. Overdue report charts               — orders/overdue_report.html
 *
 * Reads:
 *  - window.ORDERS_DASH_DATA    (dashboard page)
 *  - window.OVERDUE_REPORT_DATA (overdue report page)
 *
 * Requires: components/chart.js (wireValueUnitsToggle, CHART_PALETTE)
 */

'use strict';

document.addEventListener('DOMContentLoaded', function () {

    // ── 1. Row expand/collapse (order_book.html) ──────────────────────────
    document.querySelectorAll('.ob-toggle-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var target   = btn.dataset.target;
            var rows     = document.querySelectorAll('tr[id="' + target + '"]');
            var icon     = btn.querySelector('i');
            var isHidden = rows.length > 0 && rows[0].style.display === 'none';
            rows.forEach(function (r) { r.style.display = isHidden ? '' : 'none'; });
            icon.classList.toggle('bi-chevron-right', !isHidden);
            icon.classList.toggle('bi-chevron-down',   isHidden);
            btn.title = isHidden ? 'Collapse lines' : 'Expand lines';
        });
    });

    // ── 2. Orders dashboard charts ────────────────────────────────────────
    if (window.ORDERS_DASH_DATA) {
    const D      = window.ORDERS_DASH_DATA;
    const PALETTE = window.CHART_PALETTE;
    const toggle  = window.wireValueUnitsToggle;

    // ── Due by week bar ───────────────────────────────────────────────────
    const weeklyCanvas = document.getElementById('chartWeekly');
    if (weeklyCanvas) {
        const weekBg = D.dueByWeek.labels.map(
            l => l === 'Overdue' ? 'rgba(220,53,69,.7)' : 'rgba(13,110,253,.6)'
        );
        const weeklyChart = new Chart(weeklyCanvas, {
            type: 'bar',
            data: {
                labels:   D.dueByWeek.labels,
                datasets: [{ label: 'Value (\u00a3)', data: D.dueByWeek.amounts, backgroundColor: weekBg, borderRadius: 4 }],
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: ctx => '\u00a3' + ctx.parsed.y.toLocaleString() } },
                },
                scales: { y: { beginAtZero: true } },
            },
        });
        toggle(weeklyChart, 'btnWeeklyValue', 'btnWeeklyUnits', D.dueByWeek.amounts, D.dueByWeek.units, 'y');
    }

    // ── Order intake over time (multi-year + value/units/avg modes) ───────
    const intakeCanvas = document.getElementById('chartIntake');
    if (intakeCanvas) {
        let intakeYear = String(D.intakeOverTime.current_year);
        let intakeMode = 'value';

        function intakeData() {
            const yr = D.intakeOverTime.by_year[intakeYear] || { amounts: [], units: [], avg: [] };
            return intakeMode === 'value' ? yr.amounts : intakeMode === 'units' ? yr.units : yr.avg;
        }
        function intakeLabel() {
            return intakeMode === 'value' ? 'Order value (\u00a3)' : intakeMode === 'units' ? 'Units' : 'Avg \u00a3 / unit';
        }
        function intakeTip() {
            if (intakeMode === 'value')  return { callbacks: { label: ctx => '\u00a3' + (ctx.parsed.y || 0).toLocaleString() } };
            if (intakeMode === 'units')  return { callbacks: { label: ctx => (ctx.parsed.y || 0).toLocaleString() + ' units' } };
            return { callbacks: { label: ctx => '\u00a3' + (ctx.parsed.y || 0).toLocaleString() + ' / unit' } };
        }

        const intakeChart = new Chart(intakeCanvas, {
            type: 'bar',
            data: {
                labels:   D.intakeOverTime.month_labels,
                datasets: [{ label: intakeLabel(), data: intakeData(), backgroundColor: 'rgba(25,135,84,.65)', borderColor: 'rgba(25,135,84,.9)', borderRadius: 3, tension: 0.3, fill: false, pointRadius: 4 }],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false }, tooltip: intakeTip() },
                scales: { y: { beginAtZero: false } },
            },
        });

        function intakeRefresh() {
            const isLine = intakeMode === 'avg';
            intakeChart.config.type = isLine ? 'line' : 'bar';
            intakeChart.data.datasets[0].data  = intakeData();
            intakeChart.data.datasets[0].label = intakeLabel();
            intakeChart.options.scales.y.beginAtZero = !isLine;
            intakeChart.options.plugins.tooltip = intakeTip();
            intakeChart.update();
        }

        document.querySelectorAll('.btn-intake-year').forEach(btn => {
            btn.addEventListener('click', function () {
                intakeYear = this.dataset.year;
                document.querySelectorAll('.btn-intake-year').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                intakeRefresh();
            });
        });
        const intakeModes = { btnIntakeValue: 'value', btnIntakeUnits: 'units', btnIntakeAvg: 'avg' };
        Object.entries(intakeModes).forEach(([id, mode]) => {
            const btn = document.getElementById(id);
            if (!btn) return;
            btn.addEventListener('click', function () {
                intakeMode = mode;
                Object.keys(intakeModes).forEach(oid => {
                    const ob = document.getElementById(oid);
                    if (ob) ob.classList.toggle('active', oid === id);
                });
                intakeRefresh();
            });
        });
    }

    // ── By customer horizontal bar ────────────────────────────────────────
    const custCanvas = document.getElementById('chartCustomerValue');
    if (custCanvas) {
        const custLabels = D.valueByCustomer.map(r => r.customer);
        const custValues = D.valueByCustomer.map(r => r.value);
        const custUnits  = D.valueByCustomer.map(r => r.units);
        const custChart = new Chart(custCanvas, {
            type: 'bar',
            data: { labels: custLabels, datasets: [{ label: 'Value (\u00a3)', data: custValues, backgroundColor: 'rgba(13,202,240,.65)', borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => '\u00a3' + ctx.parsed.x.toLocaleString() } } }, scales: { x: { beginAtZero: true } } },
        });
        toggle(custChart, 'btnCustomerValue', 'btnCustomerUnits', custValues, custUnits, 'x');
    }

    // ── Customer group horizontal bar ─────────────────────────────────────
    const cgCanvas = document.getElementById('chartCustomerGroup');
    if (cgCanvas) {
        const cgLabels = D.byCustomerGroup.map(r => r.group);
        const cgValues = D.byCustomerGroup.map(r => r.value);
        const cgUnits  = D.byCustomerGroup.map(r => r.units);
        const cgChart = new Chart(cgCanvas, {
            type: 'bar',
            data: { labels: cgLabels, datasets: [{ label: 'Value (\u00a3)', data: cgValues, backgroundColor: PALETTE, borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => '\u00a3' + ctx.parsed.x.toLocaleString() } } }, scales: { x: { beginAtZero: true } } },
        });
        toggle(cgChart, 'btnCustGrpValue', 'btnCustGrpUnits', cgValues, cgUnits, 'x');
    }

    // ── By country horizontal bar ─────────────────────────────────────────
    const countryCanvas = document.getElementById('chartCountry');
    if (countryCanvas) {
        const countryLabels = D.byCountry.map(r => r.country);
        const countryValues = D.byCountry.map(r => r.value);
        const countryUnits  = D.byCountry.map(r => r.units);
        const countryChart = new Chart(countryCanvas, {
            type: 'bar',
            data: { labels: countryLabels, datasets: [{ label: 'Value (\u00a3)', data: countryValues, backgroundColor: 'rgba(13,202,240,.5)', borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => '\u00a3' + ctx.parsed.x.toLocaleString() } } }, scales: { x: { beginAtZero: true } } },
        });
        toggle(countryChart, 'btnCountryValue', 'btnCountryUnits', countryValues, countryUnits, 'x');
    }

    // ── Product group doughnut ────────────────────────────────────────────
    const pgCanvas = document.getElementById('chartProductGroup');
    if (pgCanvas) {
        const pgLabels = D.byProductGroup.map(r => r.group);
        const pgValues = D.byProductGroup.map(r => r.value);
        const pgUnits  = D.byProductGroup.map(r => r.units);
        const pgChart = new Chart(pgCanvas, {
            type: 'doughnut',
            data: { labels: pgLabels, datasets: [{ data: pgValues, backgroundColor: PALETTE, borderWidth: 1 }] },
            options: { responsive: true, plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: ctx => ctx.label + ': \u00a3' + ctx.parsed.toLocaleString() } } } },
        });
        toggle(pgChart, 'btnPGValue', 'btnPGUnits', pgValues, pgUnits, null, true);
    }

    // ── Top models horizontal bar ─────────────────────────────────────────
    const modelsCanvas = document.getElementById('chartModels');
    if (modelsCanvas) {
        const modelLabels = D.byModel.map(r => r.model);
        const modelValues = D.byModel.map(r => r.value);
        const modelUnits  = D.byModel.map(r => r.units);
        const modelsChart = new Chart(modelsCanvas, {
            type: 'bar',
            data: { labels: modelLabels, datasets: [{ label: 'Value (\u00a3)', data: modelValues, backgroundColor: 'rgba(255,193,7,.75)', borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => '\u00a3' + ctx.parsed.x.toLocaleString() } } }, scales: { x: { beginAtZero: true } } },
        });
        toggle(modelsChart, 'btnModelsValue', 'btnModelsUnits', modelValues, modelUnits, 'x');
    }

    // ── Order type doughnut ───────────────────────────────────────────────
    const otCanvas = document.getElementById('chartOrderType');
    if (otCanvas) {
        const otLabels = D.byOrderType.map(r => r.order_type);
        const otValues = D.byOrderType.map(r => r.value);
        const otUnits  = D.byOrderType.map(r => r.units);
        const otChart = new Chart(otCanvas, {
            type: 'doughnut',
            data: { labels: otLabels, datasets: [{ data: otValues, backgroundColor: PALETTE, borderWidth: 1 }] },
            options: { responsive: true, plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: ctx => ctx.label + ': \u00a3' + ctx.parsed.toLocaleString() } } } },
        });
        toggle(otChart, 'btnOTValue', 'btnOTUnits', otValues, otUnits, null, true);
    }

    // ── Lead time histogram (no toggle) ──────────────────────────────────
    const ltCanvas = document.getElementById('chartLeadTime');
    if (ltCanvas) {
        new Chart(ltCanvas, {
            type: 'bar',
            data: { labels: D.leadTime.labels, datasets: [{ label: 'Orders', data: D.leadTime.counts, backgroundColor: 'rgba(255,193,7,.75)', borderRadius: 4 }] },
            options: {
                responsive: true,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ctx.parsed.y.toLocaleString() + ' orders' } } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
            },
        });
    }
    } // end if (ORDERS_DASH_DATA)

    // ── Overdue report charts ─────────────────────────────────────────────
    const OR = window.OVERDUE_REPORT_DATA;
    if (OR) {
        const style     = getComputedStyle(document.documentElement);
        const textColor = style.getPropertyValue('--bs-body-color').trim() || '#212529';
        const gridColor = 'rgba(0,0,0,0.06)';
        Chart.defaults.color       = textColor;
        Chart.defaults.font.family = style.getPropertyValue('--bs-body-font-family').trim() || 'system-ui, sans-serif';

        function fmtGbpOverdue(v) {
            return v >= 1000000 ? `\u00a3${(v/1000000).toFixed(1)}m`
                 : v >= 1000    ? `\u00a3${(v/1000).toFixed(0)}k`
                 : `\u00a3${Math.round(v).toLocaleString('en-GB')}`;
        }

        const ageColors = ['rgba(108,117,125,0.80)','rgba(255,193,7,0.85)','rgba(253,126,20,0.85)','rgba(220,53,69,0.80)','rgba(140,10,25,0.88)'];
        const ageCtx = document.getElementById('chartAge');
        if (ageCtx && OR.ageChart) {
            new Chart(ageCtx.getContext('2d'), {
                type: 'bar',
                data: { labels: OR.ageChart.labels, datasets: [{
                    label: 'Orders', data: OR.ageChart.counts,
                    backgroundColor: ageColors,
                    borderColor: ageColors.map(c => c.replace(/[\d.]+\)$/, '1)')),
                    borderWidth: 1, borderRadius: 4,
                }]},
                options: { responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.y} order${ctx.parsed.y !== 1 ? 's' : ''}` } } },
                    scales: { x: { grid: { color: gridColor } }, y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: gridColor }, title: { display: true, text: 'Orders' } } },
                },
            });
        }

        const custCtx = document.getElementById('chartCustomer');
        if (custCtx && OR.valueByCustomer && OR.valueByCustomer.length && typeof ChartDataLabels !== 'undefined') {
            const counts = OR.valueByCustomer.map(d => d.count);
            new Chart(custCtx.getContext('2d'), {
                type: 'bar',
                plugins: [ChartDataLabels],
                data: { labels: OR.valueByCustomer.map(d => d.customer), datasets: [{
                    label: 'Overdue Value', data: OR.valueByCustomer.map(d => d.value),
                    backgroundColor: 'rgba(220,53,69,0.55)', borderColor: '#dc3545', borderWidth: 1, borderRadius: 3,
                }]},
                options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                    layout: { padding: { right: 70 } },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: ctx => ` ${fmtGbpOverdue(ctx.parsed.x)} (${counts[ctx.dataIndex]} order${counts[ctx.dataIndex] !== 1 ? 's' : ''})` } },
                        datalabels: { anchor: 'end', align: 'end', formatter: v => v > 0 ? fmtGbpOverdue(v) : null, font: { size: 10, weight: 'bold' }, color: textColor },
                    },
                    scales: {
                        x: { ticks: { callback: v => fmtGbpOverdue(v), font: { size: 11 } }, grid: { color: gridColor } },
                        y: { ticks: { font: { size: 11 } }, grid: { display: false } },
                    },
                },
            });
        }
    }
});
