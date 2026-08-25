/**
 * Planning Board — client-side behaviour
 * Used by: templates/planning/workorder_plan/workspace.html
 *
 * Sections:
 *   1. Board drag-scroll
 *   2. Override modal population + week-picker
 *   3. Order-grouped detail table (toggle rows)
 *   4. Order-level bulk-override modal
 *   5. Jobs search filter
 */

'use strict';

// ---------------------------------------------------------------------------
// 1. Board drag-scroll
// ---------------------------------------------------------------------------
(function initBoardScroll() {
  const el = document.getElementById('boardScroll');
  if (!el) return;

  let isDown = false, startX, startY, scrollLeft, scrollTop;

  el.addEventListener('mousedown', function (e) {
    if (e.target.closest('input,button,a,select,textarea')) return;
    isDown = true;
    el.classList.add('is-dragging');
    startX     = e.pageX - el.offsetLeft;
    startY     = e.pageY - el.offsetTop;
    scrollLeft = el.scrollLeft;
    scrollTop  = el.scrollTop;
    e.preventDefault();
  });

  function stopDrag() {
    isDown = false;
    el.classList.remove('is-dragging');
  }
  el.addEventListener('mouseleave', stopDrag);
  el.addEventListener('mouseup', stopDrag);

  el.addEventListener('mousemove', function (e) {
    if (!isDown) return;
    const walkX = (e.pageX - el.offsetLeft - startX) * 1.2;
    const walkY = (e.pageY - el.offsetTop  - startY) * 1.2;
    el.scrollLeft = scrollLeft - walkX;
    el.scrollTop  = scrollTop  - walkY;
  });
}());


// ---------------------------------------------------------------------------
// 2. Override modal — job-level
// ---------------------------------------------------------------------------
(function initOverrideModal() {
  const modal = document.getElementById('overrideModal');
  if (!modal) return;

  modal.addEventListener('show.bs.modal', function (e) {
    const btn = e.relatedTarget;
    if (!btn) return;
    document.getElementById('ov_job_num').value  = btn.dataset.job  || '';
    document.getElementById('ov_asm').value      = btn.dataset.asm  || '0';
    document.getElementById('ov_modal_title').textContent =
      btn.dataset.job + (parseInt(btn.dataset.asm) > 0 ? '/' + btn.dataset.asm : '');
    document.getElementById('ov_plnwk').value    = btn.dataset.plnwk || '';
    document.getElementById('ov_due').value      = btn.dataset.due   || '';
    document.getElementById('ov_notes').value    = btn.dataset.notes || '';
    document.getElementById('ov_plnwk_picker').value = '';
  });

  // Date picker → ISO week
  const picker = document.getElementById('ov_plnwk_picker');
  if (picker) {
    picker.addEventListener('change', function () {
      if (!this.value) return;
      document.getElementById('ov_plnwk').value = dateToIsoWeek(this.value);
    });
  }
}());


// ---------------------------------------------------------------------------
// 3. Order-group override modal — applies to ALL jobs in an order
// ---------------------------------------------------------------------------
(function initOrderModal() {
  const modal = document.getElementById('orderOverrideModal');
  if (!modal) return;

  modal.addEventListener('show.bs.modal', function (e) {
    const btn = e.relatedTarget;
    if (!btn) return;
    document.getElementById('oom_order_num').value   = btn.dataset.orderNum  || '';
    document.getElementById('oom_job_count').textContent = btn.dataset.jobCount || '?';
    document.getElementById('oom_plnwk').value       = btn.dataset.plnwk     || '';
    document.getElementById('oom_due').value         = btn.dataset.due       || '';
    document.getElementById('oom_notes').value       = btn.dataset.notes     || '';
    document.getElementById('oom_plnwk_picker').value = '';
  });

  const picker = document.getElementById('oom_plnwk_picker');
  if (picker) {
    picker.addEventListener('change', function () {
      if (!this.value) return;
      document.getElementById('oom_plnwk').value = dateToIsoWeek(this.value);
    });
  }
}());


// ---------------------------------------------------------------------------
// 4. Order-grouped detail table
//    toggleOrder() is defined inline in the template (guaranteed availability)
//    External script just handles search filter.
// ---------------------------------------------------------------------------


// ---------------------------------------------------------------------------
// 5. Jobs search filter
// ---------------------------------------------------------------------------
(function initOrderSearch() {
  const input = document.getElementById('orderSearch');
  if (!input) return;
  input.addEventListener('input', function () {
    const q = this.value.toLowerCase();
    document.querySelectorAll('.order-group-row').forEach(function (row) {
      const match = (row.dataset.search || '').toLowerCase().includes(q);
      row.style.display = match ? '' : 'none';
      // Also hide job sub-rows of hidden groups
      const orderNum = row.dataset.orderNum;
      document.querySelectorAll(`.order-job-row[data-order-num="${orderNum}"]`)
        .forEach(function (jr) { jr.style.display = match ? (jr.classList.contains('visible') ? '' : 'none') : 'none'; });
    });
  });
}());


// ---------------------------------------------------------------------------
// Utility: convert YYYY-MM-DD → ISO week label (2026-W35)
// ---------------------------------------------------------------------------
function dateToIsoWeek(dateStr) {
  const d   = new Date(dateStr + 'T12:00:00');
  const dow = (d.getDay() + 6) % 7;           // Mon=0
  const thu = new Date(d);
  thu.setDate(d.getDate() - dow + 3);
  const jan1 = new Date(thu.getFullYear(), 0, 1);
  const week = Math.ceil(((thu - jan1) / 86400000 + 1) / 7);
  return thu.getFullYear() + '-W' + String(week).padStart(2, '0');
}
