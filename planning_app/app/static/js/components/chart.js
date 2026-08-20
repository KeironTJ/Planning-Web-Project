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
 */
function wireValueUnitsToggle(chart, valueBtnId, unitsBtnId, valueData, unitsData, axis, isDoughnut) {
    const valBtn = document.getElementById(valueBtnId);
    const uniBtn = document.getElementById(unitsBtnId);
    if (!valBtn || !uniBtn) return;
    const a = axis || 'y';

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

    function activate(useUnits) {
        chart.data.datasets[0].data  = useUnits ? unitsData : valueData;
        chart.data.datasets[0].label = useUnits ? 'Units' : 'Value (\u00a3)';
        chart.options.plugins.tooltip = { callbacks: makeTooltip(useUnits) };
        chart.update();
        valBtn.classList.toggle('active', !useUnits);
        uniBtn.classList.toggle('active', useUnits);
    }

    valBtn.addEventListener('click', () => activate(false));
    uniBtn.addEventListener('click', () => activate(true));
}

window.CHART_PALETTE        = CHART_PALETTE;
window.wireValueUnitsToggle = wireValueUnitsToggle;
