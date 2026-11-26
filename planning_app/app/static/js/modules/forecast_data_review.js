(() => {
    const table = document.getElementById('review-table-scroll');
    const scrollTop = document.getElementById('review-scroll-top');
    const tableElement = table.querySelector('table');
    function updateHorizontalScroll() {
        scrollTop.firstElementChild.style.width = `${tableElement.offsetWidth}px`;
        scrollTop.hidden = table.scrollWidth <= table.clientWidth;
        scrollTop.scrollLeft = table.scrollLeft;
    }
    scrollTop.addEventListener('scroll', () => { table.scrollLeft = scrollTop.scrollLeft; });
    table.addEventListener('scroll', () => { scrollTop.scrollLeft = table.scrollLeft; });
    window.addEventListener('resize', updateHorizontalScroll);
    if (window.ResizeObserver) {
        const observer = new ResizeObserver(updateHorizontalScroll);
        observer.observe(table);
        observer.observe(tableElement);
    }
    updateHorizontalScroll();

    function setExpanded(row, expanded) {
        const details = row.querySelector('[data-row-details]');
        const detailRow = row.querySelector('[data-review-detail-row]');
        const toggle = row.querySelector('[data-review-toggle]');
        detailRow.hidden = !expanded;
        details.open = expanded;
        toggle.setAttribute('aria-expanded', String(expanded));
        toggle.textContent = expanded ? 'Hide' : 'View';
        toggle.setAttribute('aria-label', toggle.getAttribute('aria-label').replace(/^(View|Hide)/, expanded ? 'Hide' : 'View'));
    }
    for (const row of table.querySelectorAll('[data-review-row]')) {
        const details = row.querySelector('[data-row-details]');
        const toggle = row.querySelector('[data-review-toggle]');
        details.querySelector(':scope > summary').hidden = true;
        toggle.hidden = false;
        setExpanded(row, false);
        toggle.addEventListener('click', () => setExpanded(row, !details.open));
    }
    const anchored = document.getElementById(window.location.hash.slice(1));
    if (anchored?.matches('[data-review-row]')) setExpanded(anchored, true);
    for (const form of document.querySelectorAll('[data-adjustment-form]')) {
        const values = form.querySelector('[data-adjustment-values]');
        const action = form.elements.action;
        const updateValues = () => { values.disabled = ['exclude', 'clear'].includes(action.value); };
        action.addEventListener('change', updateValues);
        updateValues();
    }
    const list = document.querySelector('#review-list[data-save-url]');
    const forms = [...(list?.querySelectorAll('form[data-record-id]') ?? [])];
    if (!list) return;

    const button = document.getElementById('save-review-batch');
    const draftCount = document.getElementById('review-draft-count');
    const feedback = document.getElementById('review-feedback');
    const byId = new Map(forms.map(form => [Number(form.dataset.recordId), form]));
    const toolbar = document.getElementById('review-toolbar');
    const selectedForm = document.getElementById('selected-review');
    const selections = [...list.querySelectorAll('[data-review-select]')];
    const selectAll = document.getElementById('select-page-reviews');
    const selectedCount = document.getElementById('selected-review-count');
    const selectedButton = document.getElementById('save-selected-reviews');
    const confirmSelection = document.getElementById('selected-review-confirm');
    const exclusionForm = document.getElementById('selected-exclusion');
    const exclusionButton = document.getElementById('save-selected-exclusions');
    const exclusionConfirm = document.getElementById('selected-exclusion-confirm');
    const exclusionSuggestions = document.getElementById('confirm-row-exclusion-suggestions');
    let saving = false;
    let submittingExclusion = false;

    const hasDraft = form => Boolean(form.elements.status.value || form.elements.rationale.value || form.elements.remember.checked);
    function updateSelection() {
        const selected = selections.filter(input => input.checked);
        const count = selected.length;
        const nonReviewable = selected.some(input => !byId.has(Number(input.dataset.reviewSelect)));
        selectedCount.textContent = `${count} ${count === 1 ? 'row' : 'rows'} selected on this page.${nonReviewable ? ' Some selected rows have no findings and cannot receive a review.' : ''}`;
        selectAll.checked = selections.length > 0 && count === selections.length;
        selectAll.indeterminate = count > 0 && count < selections.length;
        selectAll.disabled = saving;
        selections.forEach(input => { input.disabled = saving; });
        selectedButton.disabled = saving || count === 0 || nonReviewable;
        if (exclusionButton) exclusionButton.disabled = saving || count === 0;
    }

    function updateDrafts() {
        const count = forms.filter(hasDraft).length;
        draftCount.textContent = count ? `${count} drafted ${count === 1 ? 'review' : 'reviews'} on this page` : 'No drafts on this page.';
        button.disabled = saving || count === 0;
        for (const form of forms) {
            form.closest('[data-review-row]').querySelector('[data-draft-badge]').hidden = !hasDraft(form);
        }
    }

    function setFeedback(message, error = false) {
        feedback.textContent = message;
        feedback.className = error ? 'small text-danger' : 'small text-success';
    }

    async function save(drafts, shared = null) {
        if (saving || !drafts.length) return;
        for (const form of shared ? [] : drafts) {
            const rationale = form.elements.rationale;
            rationale.setCustomValidity(rationale.value.trim() ? '' : 'Enter supporting evidence for this row.');
            if (!form.checkValidity()) {
                setExpanded(form.closest('[data-review-row]'), true);
                form.reportValidity();
                setFeedback('Complete the outcome and evidence for each draft before saving.', true);
                return;
            }
        }
        const first = drafts[0].elements;
        const payload = {
            capture_identity: first.capture_identity.value,
            reviews: drafts.map(form => ({
                record_id: Number(form.dataset.recordId),
                status: shared ? shared.status : form.elements.status.value,
                rationale: shared ? shared.rationale : form.elements.rationale.value.trim(),
                remember: shared ? shared.remember : form.elements.remember.checked,
            })),
        };
        saving = true;
        updateDrafts();
        updateSelection();
        const controls = drafts.flatMap(form => [...form.querySelectorAll('select, textarea, input[type=checkbox], button')]);
        if (shared) controls.push(...selectedForm.querySelectorAll('select, textarea, input[type=checkbox]'));
        controls.forEach(control => { control.disabled = true; });
        setFeedback('Saving reviews...');
        try {
            const response = await fetch(list.dataset.saveUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': first.csrf_token.value },
                body: JSON.stringify(payload),
            });
            if (response.redirected) throw new Error('Your session may have expired. Keep a copy of your drafts before signing in again.');
            const result = response.headers.get('Content-Type')?.includes('application/json')
                ? await response.json() : null;
            if (!response.ok || !result) {
                throw new Error(result?.error || 'The server did not confirm the save. Your drafts are still here.');
            }
            for (const review of result.reviews) {
                const form = byId.get(review.record_id);
                const row = form.closest('[data-review-row]');
                const badge = row.querySelector('[data-latest-review]');
                badge.textContent = review.label;
                badge.className = `badge ${review.badge} me-2`;
                const history = row.querySelector('[data-review-history]');
                const entries = history.querySelector('ol');
                entries.insertAdjacentHTML('afterbegin', review.history_entry);
                const time = entries.firstElementChild.querySelector('time[data-utc]');
                if (time && window.fmtIso) time.textContent = window.fmtIso(time.dataset.utc, time.dataset.fmt);
                history.hidden = false;
                row.querySelector('[data-suggestion]')?.remove();
                row.querySelector('[data-suggestion-badge]')?.remove();
                form.reset();
                if (form.contains(document.activeElement)) row.querySelector('[data-review-toggle]').focus();
                setExpanded(row, false);
            }
            let reviewed = 0;
            for (const count of document.querySelectorAll('[data-review-count]')) {
                const value = result.counts[count.dataset.reviewCount] || 0;
                count.textContent = value;
                reviewed += value;
            }
            const unreviewed = document.getElementById('unreviewed-count');
            unreviewed.textContent = Number(unreviewed.dataset.flagged) - reviewed;
            if (result.gbp_counts) {
                for (const group of document.querySelectorAll('[data-gbp-review-group]')) {
                    const counts = result.gbp_counts[group.dataset.gbpReviewGroup];
                    for (const cell of group.querySelectorAll('[data-gbp-review-status]')) {
                        cell.textContent = (counts[cell.dataset.gbpReviewStatus] || 0).toLocaleString('en-GB');
                    }
                }
            }
            for (const progress of document.querySelectorAll('[data-finding-progress]')) {
                const count = result.finding_counts[progress.dataset.findingProgress] || 0;
                const remaining = Number(progress.dataset.total) - count;
                progress.querySelector('[data-reviewed]').textContent = count.toLocaleString('en-GB');
                const link = progress.querySelector('[data-unreviewed]');
                link.querySelector('[data-remaining]').textContent = remaining.toLocaleString('en-GB');
                link.hidden = remaining === 0;
                progress.querySelector('[data-no-unreviewed]').hidden = remaining !== 0;
                progress.dataset.state = remaining ? 'attention' : 'reviewed';
                progress.querySelector('[data-finding-status]').textContent = remaining
                    ? 'Some rows not reviewed. ' : 'All affected rows reviewed. ';
            }
            document.getElementById('extract-delete')?.remove();
            const suggestionForm = document.getElementById('confirm-suggestions');
            if (suggestionForm) {
                const remaining = document.querySelectorAll('input[name=suggestion][form=confirm-suggestions]').length;
                if (!remaining) suggestionForm.remove();
                else suggestionForm.querySelector('strong').textContent = `Previously reviewed: ${remaining} suggestion(s) on this page.`;
            }
            const count = result.reviews.length;
            const scope = list.dataset.scope;
            const filterHint = !['all', 'findings', 'exclusion_suggestions'].includes(scope) && result.reviews.some(review => review.status !== scope)
                ? ' Reapply the filter to hide rows that no longer match.' : '';
            setFeedback(`Saved ${count} ${count === 1 ? 'review' : 'reviews'}.${filterHint}`);
            if (document.activeElement === document.body) draftCount.focus({ preventScroll: true });
            return true;
        } catch (error) {
            setFeedback(error.message || 'The save could not be confirmed. Your evidence is still here.', true);
        } finally {
            controls.forEach(control => { control.disabled = false; });
            saving = false;
            updateDrafts();
            updateSelection();
        }
    }

    list.addEventListener('input', event => {
        if (event.target.name === 'rationale') event.target.setCustomValidity('');
        updateDrafts();
    });
    for (const form of forms) {
        form.addEventListener('submit', event => {
            event.preventDefault();
            save([form]);
        });
    }
    button.addEventListener('click', () => save(forms.filter(hasDraft)));
    for (const input of selections) {
        input.hidden = false;
        input.addEventListener('click', event => event.stopPropagation());
        input.addEventListener('change', () => {
            confirmSelection.checked = false;
            if (exclusionConfirm) exclusionConfirm.checked = false;
            updateSelection();
        });
    }
    selectAll.addEventListener('change', () => {
        selections.forEach(input => { input.checked = selectAll.checked; });
        confirmSelection.checked = false;
        if (exclusionConfirm) exclusionConfirm.checked = false;
        updateSelection();
    });
    selectedForm.elements.rationale.addEventListener('input', event => event.target.setCustomValidity(''));
    selectedForm.addEventListener('submit', async event => {
        event.preventDefault();
        const evidence = selectedForm.elements.rationale;
        evidence.setCustomValidity(evidence.value.trim() ? '' : 'Enter supporting evidence for the selected rows.');
        if (!selectedForm.reportValidity()) return;
        const selected = selections.filter(input => input.checked).map(input => byId.get(Number(input.dataset.reviewSelect)));
        if (!selected.length || selected.some(form => !form)) {
            setFeedback('Select only rows with findings to save a review.', true);
            return;
        }
        const drafted = selected.find(hasDraft);
        if (drafted) {
            setExpanded(drafted.closest('[data-review-row]'), true);
            drafted.elements.status.focus();
            setFeedback('Save or clear individual drafts on the selected rows first.', true);
            return;
        }
        const saved = await save(selected, {
            status: selectedForm.elements.status.value,
            rationale: evidence.value.trim(),
            remember: selectedForm.elements.remember.checked,
        });
        if (saved) {
            selections.forEach(input => { input.checked = false; });
            selectedForm.reset();
            updateSelection();
        }
    });
    exclusionForm?.addEventListener('submit', event => {
        if (forms.some(hasDraft) || selectedForm.elements.rationale.value || selectedForm.elements.status.value
            || document.querySelector('input[name=suggestion][form=confirm-row-exclusion-suggestions]:checked')) {
            event.preventDefault();
            setFeedback('Save or clear review drafts and selected exclusion suggestions before saving row exclusions.', true);
            return;
        }
        submittingExclusion = true;
    });
    exclusionSuggestions?.addEventListener('submit', event => {
        if (forms.some(hasDraft) || selections.some(input => input.checked)
            || selectedForm.elements.rationale.value || selectedForm.elements.status.value
            || exclusionForm?.elements.rationale.value || exclusionForm?.elements.action.value
            || document.querySelector('input[name=suggestion][form=confirm-suggestions]:checked')) {
            event.preventDefault();
            document.getElementById('row-exclusion-suggestion-feedback').textContent =
                'Save or clear other review and exclusion drafts before confirming suggestions.';
            return;
        }
        submittingExclusion = true;
    });
    window.addEventListener('beforeunload', event => {
        if (submittingExclusion) return;
        if (!forms.some(hasDraft) && !selections.some(input => input.checked)
            && !selectedForm.elements.rationale.value && !selectedForm.elements.status.value
            && !exclusionForm?.elements.rationale.value && !exclusionForm?.elements.action.value
            && !document.querySelector('input[name=suggestion][form=confirm-row-exclusion-suggestions]:checked')) return;
        event.preventDefault();
        event.returnValue = '';
    });
    toolbar.hidden = false;
    document.getElementById('review-controls-link').hidden = false;
    updateDrafts();
    updateSelection();
})();
