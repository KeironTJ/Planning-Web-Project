/**
 * Factory Dashboards — Capacity module
 *
 * Handles:
 *  - capacity/dashboard.html    — single/all-dept charts
 *  - capacity/dept_calendar.html — inline override editing toggle
 *  - capacity/labour_plan.html  — edit modal population
 *
 * Reads: window.CAPACITY_DASH_DATA (dashboard page only)
 */

'use strict';

// ── Inline override editing toggle (dept_calendar.html) ──────────────────────
// Called via onclick="toggleEdit(btn)" in the template.
function toggleEdit(btn) {
    var cell    = btn.closest('td');
    var form    = cell.querySelector('.override-form');
    var editBtn = cell.querySelector('.edit-btn');
    if (form.style.display === 'none') {
        form.style.display = '';
        editBtn.style.display = 'none';
        form.querySelector('input[name=hours]').focus();
    } else {
        form.style.display = 'none';
        editBtn.style.display = '';
    }
}
window.toggleEdit = toggleEdit;

document.addEventListener('DOMContentLoaded', function () {
    if (!window.CAPACITY_DASH_DATA) return;

    const D          = window.CAPACITY_DASH_DATA;
    const gridColour = 'rgba(0,0,0,0.06)';
    const MONTHS     = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

    function isoWeekMonday(iso) {
        const [year, week] = iso.split('-W').map(Number);
        const jan4    = new Date(year, 0, 4);
        const jan4dow = jan4.getDay() || 7;
        const monday  = new Date(jan4);
        monday.setDate(jan4.getDate() - (jan4dow - 1) + (week - 1) * 7);
        return monday;
    }

    function weekLabel(iso) {
        const [, week] = iso.split('-W');
        const d = isoWeekMonday(iso);
        return ['W' + week, d.getDate() + ' ' + MONTHS[d.getMonth()]];
    }

    function weekTitle(iso) {
        const mon = isoWeekMonday(iso);
        const sun = new Date(mon);
        sun.setDate(mon.getDate() + 6);
        const [, week] = iso.split('-W');
        return 'W' + week + '  \u00b7  ' + mon.getDate() + ' ' + MONTHS[mon.getMonth()] + ' \u2014 ' + sun.getDate() + ' ' + MONTHS[sun.getMonth()];
    }

    function deptLabel(name) {
        if (name.length <= 14) return name;
        const mid   = Math.floor(name.length / 2);
        let   split = name.lastIndexOf(' ', mid + 4);
        if (split < 4) split = name.indexOf(' ', mid - 4);
        if (split < 1) return name;
        return [name.substring(0, split), name.substring(split + 1)];
    }

    if (D.selectedDept && D.hasDepartments) {
        // ── Single department chart ───────────────────────────────────────
        const ctx = document.getElementById('deptChart');
        if (ctx && D.chartData.length) {
            const d          = D.chartData[0];
            const weekTitles = D.weeks.map(weekTitle);
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels:   D.weeks.map(weekLabel),
                    datasets: [{ label: 'Available Hours', data: d.rows.map(r => r.avail), backgroundColor: 'rgba(13,110,253,0.35)', borderColor: 'rgba(13,110,253,0.7)', borderWidth: 1 }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { title: items => weekTitles[items[0].dataIndex], label: item => '  Available: ' + item.raw + ' hrs' } } },
                    scales: {
                        y: { beginAtZero: true, title: { display: true, text: 'Hours / week', font: { size: 12 } }, grid: { color: gridColour }, ticks: { callback: v => v + ' h' } },
                        x: { grid: { color: gridColour }, ticks: { maxRotation: 0, font: { size: 11 } } },
                    },
                },
            });
        }
    } else {
        // ── All departments chart ─────────────────────────────────────────
        const ctx2 = document.getElementById('allDeptsChart');
        if (ctx2 && D.chartData.length) {
            const activeData = D.chartData.filter(d => d.rows.some(r => r.avail > 0));
            const fullNames  = activeData.map(d => d.dept.name);
            const labels     = fullNames.map(deptLabel);
            const avails     = activeData.map(d => parseFloat(d.rows.reduce((s, r) => s + r.avail, 0).toFixed(1)));
            new Chart(ctx2, {
                type: 'bar',
                data: { labels, datasets: [{ label: 'Available Hours', data: avails, backgroundColor: 'rgba(13,110,253,0.45)', borderColor: 'rgba(13,110,253,0.7)', borderWidth: 1 }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { title: items => fullNames[items[0].dataIndex], label: item => '  Available: ' + item.raw + ' hrs' } } },
                    scales: {
                        x: { ticks: { maxRotation: 0, font: { size: 11 }, autoSkip: false }, grid: { color: gridColour } },
                        y: { beginAtZero: true, title: { display: true, text: 'Total hours (' + D.numWeeks + '-week period)', font: { size: 12 } }, grid: { color: gridColour }, ticks: { callback: v => v + ' h' } },
                    },
                },
            });
        }
    }

    // ── Labour plan edit modal population (labour_plan.html) ──────────────
    var labourModalEl = document.getElementById('editModal');
    if (labourModalEl) {
        labourModalEl.addEventListener('show.bs.modal', function (e) {
            var btn = e.relatedTarget;
            if (!btn) return;
            document.getElementById('editModalDept').textContent  = btn.dataset.deptName;
            document.getElementById('editModalDate').textContent  = btn.dataset.dateDisplay;
            document.getElementById('editBucketId').value         = btn.dataset.bucketId || '';
            document.getElementById('editDeptId').value           = btn.dataset.deptId;
            document.getElementById('editDate').value             = btn.dataset.date;
            document.getElementById('editAvailableHours').value   = btn.dataset.availableHours;
            document.getElementById('editIsWorkday').checked      = btn.dataset.isWorkday === '1';
            document.getElementById('editDayComplete').checked    = btn.dataset.dayComplete === '1';
            var note = document.getElementById('editManualNote');
            note.textContent = btn.dataset.isManual === '1'
                ? 'Manually overridden \u2014 will not be overwritten by CSV import.'
                : (btn.dataset.bucketId ? 'Imported entry \u2014 saving will mark as manual override.' : 'New entry \u2014 will be marked as manual override.');
        });
    }
});
