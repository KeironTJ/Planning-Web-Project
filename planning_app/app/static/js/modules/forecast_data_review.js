(() => {
    const anchored = document.getElementById(window.location.hash.slice(1));
    if (anchored?.matches('#review-list > details')) anchored.open = true;
    const list = document.querySelector('#review-list[data-save-url]');
    const forms = [...(list?.querySelectorAll('form[data-record-id]') ?? [])];
    if (!forms.length) return;

    const button = document.getElementById('save-review-batch');
    const draftCount = document.getElementById('review-draft-count');
    const feedback = document.getElementById('review-feedback');
    const byId = new Map(forms.map(form => [Number(form.dataset.recordId), form]));
    let saving = false;

    const hasDraft = form => Boolean(form.elements.status.value || form.elements.rationale.value);
    function updateDrafts() {
        const count = forms.filter(hasDraft).length;
        draftCount.textContent = count ? `${count} drafted ${count === 1 ? 'review' : 'reviews'} on this page` : 'No drafts on this page.';
        button.disabled = saving || count === 0;
        for (const form of forms) {
            form.closest('details').querySelector('[data-draft-badge]').hidden = !hasDraft(form);
        }
    }

    function setFeedback(message, error = false) {
        feedback.textContent = message;
        feedback.className = error ? 'small text-danger' : 'small text-success';
    }

    async function save(drafts) {
        if (saving || !drafts.length) return;
        for (const form of drafts) {
            const rationale = form.elements.rationale;
            rationale.setCustomValidity(rationale.value.trim() ? '' : 'Enter supporting evidence for this row.');
            if (!form.checkValidity()) {
                form.closest('details').open = true;
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
                status: form.elements.status.value,
                rationale: form.elements.rationale.value.trim(),
                remember: form.elements.remember.checked,
            })),
        };
        saving = true;
        updateDrafts();
        const controls = drafts.flatMap(form => [...form.querySelectorAll('select, textarea, input[type=checkbox], button')]);
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
                const row = form.closest('details');
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
                if (form.contains(document.activeElement)) row.querySelector('summary').focus();
                row.open = false;
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
                progress.querySelector('[data-reviewed]').textContent = count;
                const link = progress.querySelector('[data-unreviewed]');
                link.querySelector('[data-remaining]').textContent = remaining;
                link.hidden = remaining === 0;
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
            const filterHint = !['all', 'findings'].includes(scope) && result.reviews.some(review => review.status !== scope)
                ? ' Reapply the filter to hide rows that no longer match.' : '';
            setFeedback(`Saved ${count} ${count === 1 ? 'review' : 'reviews'}.${filterHint}`);
            if (document.activeElement === document.body) draftCount.focus({ preventScroll: true });
        } catch (error) {
            setFeedback(error.message || 'The save could not be confirmed. Your drafts are still here.', true);
        } finally {
            controls.forEach(control => { control.disabled = false; });
            saving = false;
            updateDrafts();
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
    window.addEventListener('beforeunload', event => {
        if (!forms.some(hasDraft)) return;
        event.preventDefault();
        event.returnValue = '';
    });
    document.getElementById('review-toolbar').hidden = false;
    updateDrafts();
})();
