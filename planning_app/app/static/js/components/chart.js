/**
 * Factory Dashboards — Chart component
 *
 * Shared Chart.js utilities used across all module dashboards.
 * Must be loaded before any module that creates charts.
 */

'use strict';

/** Shared colour palette — consistent across all module charts. */
const CHART_PALETTE = [
    'rgba(13,110,253,.75)', 'rgba(25,135,84,.75)',  'rgba(255,193,7,.75)',
    'rgba(220,53,69,.75)',  'rgba(13,202,240,.75)',  'rgba(111,66,193,.75)',
    'rgba(253,126,20,.75)', 'rgba(32,201,151,.75)',  'rgba(102,16,242,.75)',
    'rgba(214,51,132,.75)', 'rgba(0,128,128,.75)',   'rgba(165,42,42,.75)',
    'rgba(70,130,180,.75)', 'rgba(240,128,128,.75)', 'rgba(144,238,144,.75)',
];

/**
 * Wire a value / units toggle button pair onto an existing Chart.js instance.
 *
 * @param {Chart}   chart       - existing Chart.js instance
 * @param {string}  valueBtnId  - id of the "Value (£)" toggle button
 * @param {string}  unitsBtnId  - id of the "Units" toggle button
 * @param {Array}   valueData   - dataset array for value mode
 * @param {Array}   unitsData   - dataset array for units mode
 * @param {string}  [axis]      - Chart.js parsed axis key: 'y' (default) or 'x' for horizontal bars
 * @param {boolean} [isDoughnut] - true for doughnut/pie (tooltip uses ctx.label not ctx.parsed[axis])
 * @param {{buttonId: string, totalValue: number, unitsButtonId?: string, totalUnits?: number}} [percentage] - optional value and units share modes
 */
function wireValueUnitsToggle(chart, valueBtnId, unitsBtnId, valueData, unitsData, axis, isDoughnut, percentage) {
    const valBtn = document.getElementById(valueBtnId);
    const uniBtn = document.getElementById(unitsBtnId);
    if (!valBtn || !uniBtn) return;
    const a = axis || 'y';
    const percentBtn = percentage ? document.getElementById(percentage.buttonId) : null;
    const unitsPercentBtn = percentage && percentage.unitsButtonId
        ? document.getElementById(percentage.unitsButtonId) : null;
    const originalTickCallback = percentage ? chart.options.scales[a].ticks.callback : null;

    function makeTooltip(useUnits) {
        if (isDoughnut) {
            return useUnits
                ? { label: ctx => ctx.label + ': ' + ctx.parsed.toLocaleString() + ' units' }
                : { label: ctx => ctx.label + ': \u00a3' + ctx.parsed.toLocaleString() };
        }
        return useUnits
            ? { label: ctx => ctx.parsed[a].toLocaleString() + ' units' }
            : { label: ctx => '\u00a3' + ctx.parsed[a].toLocaleString() };
    }

    function activate(useUnits, usePercent = false) {
        const data = useUnits ? unitsData : valueData;
        const total = usePercent ? (useUnits ? percentage.totalUnits : percentage.totalValue) : null;
        chart.data.datasets[0].data = usePercent
            ? data.map(value => total === 0 ? 0 : value / total * 100)
            : data;
        chart.data.datasets[0].label = usePercent
            ? (useUnits ? 'Units (%)' : 'Value (%)')
            : useUnits ? 'Units' : 'Value (\u00a3)';
        chart.options.plugins.tooltip = {
            callbacks: usePercent
                ? { label: ctx => ctx.parsed[a].toFixed(1) + (useUnits ? '% of open units' : '% of open order value') }
                : makeTooltip(useUnits),
        };
        if (percentage) {
            chart.options.scales[a].ticks.callback = usePercent
                ? value => Number(value).toLocaleString() + '%'
                : originalTickCallback;
        }
        chart.update();
        valBtn.classList.toggle('active', !useUnits && !usePercent);
        uniBtn.classList.toggle('active', useUnits && !usePercent);
        if (percentBtn) percentBtn.classList.toggle('active', usePercent && !useUnits);
        if (unitsPercentBtn) unitsPercentBtn.classList.toggle('active', usePercent && useUnits);
    }

    valBtn.addEventListener('click', () => activate(false));
    uniBtn.addEventListener('click', () => activate(true));
    if (percentBtn) percentBtn.addEventListener('click', () => activate(false, true));
    if (unitsPercentBtn) unitsPercentBtn.addEventListener('click', () => activate(true, true));
}

window.CHART_PALETTE        = CHART_PALETTE;
window.wireValueUnitsToggle = wireValueUnitsToggle;
