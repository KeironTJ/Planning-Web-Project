/**
 * Factory Dashboards — Table component
 *
 * Reusable utilities for data tables across all modules:
 *  1. exportTableToCsv  — CSV download with multi-value cell support
 *  2. initPagination    — client-side page-by-page navigation with jump widget
 */

'use strict';

// -----------------------------------------------------------------------
// 1. CSV export
// -----------------------------------------------------------------------

/**
 * Export a <table> to a UTF-8 CSV file download.
 * Supports data-export-cols (pipe-separated column names) on <th> and
 * data-export-vals (pipe-separated values) on <td> for multi-value cells.
 *
 * @param {string} tableId
 * @param {string} filename
 */
function exportTableToCsv(tableId, filename) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const esc = v => '"' + String(v).replace(/"/g, '""') + '"';
    const headerCells = Array.from(table.querySelectorAll('thead th'));
    const hMeta = headerCells.map(th => {
        const text = th.textContent.trim();
        if (!text) return { skip: true, cols: [] };
        const exp = th.getAttribute('data-export-cols');
        return { skip: false, cols: exp ? exp.split('|') : [text] };
    });

    const rows = [];
    rows.push(hMeta.filter(h => !h.skip).flatMap(h => h.cols.map(esc)).join(','));
    table.querySelectorAll('tbody tr').forEach(tr => {
        const cells = Array.from(tr.querySelectorAll('td'));
        const vals = [];
        cells.forEach((td, i) => {
            if (i >= hMeta.length || hMeta[i].skip) return;
            const exp = td.getAttribute('data-export-vals');
            if (exp) exp.split('|').forEach(v => vals.push(esc(v.trim())));
            else vals.push(esc(td.textContent.trim().replace(/\s+/g, ' ')));
        });
        rows.push(vals.join(','));
    });

    const blob = new Blob(['\ufeff' + rows.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const a = Object.assign(document.createElement('a'), {
        href: URL.createObjectURL(blob),
        download: filename,
    });
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// -----------------------------------------------------------------------
// 2. Client-side pagination
// -----------------------------------------------------------------------

/**
 * Add client-side pagination controls to a table.
 * Appends a control bar (info text + page nav + jump input) below the table's card.
 *
 * @param {string} tableId
 * @param {number} pageSize  - initial rows per page
 */
function initPagination(tableId, pageSize) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const rows = Array.from(table.querySelector('tbody').querySelectorAll('tr'));
    if (rows.length <= pageSize) return;

    let page = 1;

    function render() {
        const total = rows.length;
        const pages = Math.ceil(total / pageSize);
        page = Math.max(1, Math.min(page, pages));
        const start = (page - 1) * pageSize;

        rows.forEach((r, i) => {
            r.style.display = (i >= start && i < start + pageSize) ? '' : 'none';
        });

        document.getElementById(tableId + 'Info').textContent =
            'Showing ' + (start + 1) + '\u2013' + Math.min(start + pageSize, total) + ' of ' + total + ' rows';

        const ul = document.getElementById(tableId + 'Pages');
        ul.innerHTML = '';

        function addBtn(label, p, disabled, active, isEllipsis) {
            const li = document.createElement('li');
            li.className = 'page-item' + (disabled ? ' disabled' : '') + (active ? ' active' : '');
            li.innerHTML = isEllipsis
                ? '<span class="page-link border-0 bg-transparent text-muted px-1">\u2026</span>'
                : '<a class="page-link" href="#">' + label + '</a>';
            if (!disabled && !isEllipsis) {
                li.querySelector('a').addEventListener('click', e => { e.preventDefault(); page = p; render(); });
            }
            ul.appendChild(li);
        }

        addBtn('\u00ab', 1, page === 1);
        addBtn('\u2039', page - 1, page === 1);

        const wing = 2;
        let lo = Math.max(2, page - wing);
        let hi = Math.min(pages - 1, page + wing);
        if (page <= wing + 1) hi = Math.min(pages - 1, 1 + wing * 2);
        if (page >= pages - wing) lo = Math.max(2, pages - wing * 2);

        addBtn(1, 1, false, page === 1);
        if (lo > 2) addBtn('', 0, true, false, true);
        for (let p = lo; p <= hi; p++) addBtn(p, p, false, p === page);
        if (hi < pages - 1) addBtn('', 0, true, false, true);
        if (pages > 1) addBtn(pages, pages, false, page === pages);

        addBtn('\u203a', page + 1, page === pages);
        addBtn('\u00bb', pages, page === pages);

        // Jump-to-page input (created once, updated on re-render)
        const jumpId = tableId + 'Jump';
        if (!document.getElementById(jumpId)) {
            const wrap = document.createElement('div');
            wrap.className = 'd-flex align-items-center gap-1 ms-2';
            wrap.innerHTML =
                '<span class="text-muted" style="font-size:.78rem;white-space:nowrap;">Go to</span>' +
                '<input id="' + jumpId + '" type="number" min="1" max="' + pages + '" ' +
                'class="form-control form-control-sm" style="width:60px;font-size:.78rem;" placeholder="#">';
            ul.parentElement.appendChild(wrap);
            document.getElementById(jumpId).addEventListener('change', function () {
                const v = parseInt(this.value, 10);
                if (v >= 1 && v <= pages) { page = v; render(); }
                this.value = '';
            });
        } else {
            document.getElementById(jumpId).max = pages;
        }
    }

    // Insert control bar below the table's card
    const card   = table.closest('.card');
    const footer = card && card.querySelector('.card-footer');
    const ctrl   = document.createElement('div');
    ctrl.className = 'd-flex justify-content-between align-items-center flex-wrap gap-2 px-3 py-2 border-top small';
    ctrl.innerHTML =
        '<span class="text-muted" id="' + tableId + 'Info"></span>' +
        '<div class="d-flex align-items-center"><nav>' +
        '<ul class="pagination pagination-sm mb-0" id="' + tableId + 'Pages"></ul></nav></div>';
    if (footer) footer.before(ctrl);
    else if (card) card.appendChild(ctrl);

    render();
}

window.exportTableToCsv = exportTableToCsv;
window.initPagination   = initPagination;
