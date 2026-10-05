/**
 * One work editor shared by tables, hierarchy cards and task boards.
 * Fetch current values on opening; submit only applicable quick-edit fields.
 */
export function initWorkQuickEdit(root) {
    const dialog = root.querySelector('[data-quick-edit-dialog]');
    if (!dialog || typeof dialog.showModal !== 'function') return;
    const form = dialog.querySelector('[data-quick-edit-form]');
    const loading = dialog.querySelector('[data-quick-edit-loading]');
    const errorBox = dialog.querySelector('[data-quick-edit-error]');
    const reload = dialog.querySelector('[data-quick-edit-reload]');
    const save = dialog.querySelector('[data-quick-edit-save]');
    const fullEdit = dialog.querySelector('[data-quick-edit-full]');
    const search = dialog.querySelector('[data-assignee-search]');
    const list = dialog.querySelector('[data-assignee-list]');
    const empty = dialog.querySelector('[data-assignee-empty]');
    const count = dialog.querySelector('[data-assignee-count]');
    const unavailable = dialog.querySelector('[data-unavailable-assignees]');
    const assigneeFields = dialog.querySelector('[data-task-assignees]');
    const closeButtons = [...dialog.querySelectorAll('[data-quick-edit-close]')];
    let trigger;
    let item;
    let controller;
    let saving = false;

    function showError(message, error) {
        errorBox.textContent = message;
        errorBox.hidden = false;
        if (error) console.error('Work quick edit failed.', error);
    }

    async function readResponse(response) {
        if (!response.headers.get('content-type')?.includes('application/json')) {
            throw new Error('The server did not return work data. Your session may have expired. Reload the page and sign in before retrying.');
        }
        return response.json();
    }

    function updatePeople() {
        const query = search.value.trim().toLocaleLowerCase();
        const labels = [...list.querySelectorAll('label')];
        let shown = 0;
        for (const label of labels) {
            label.hidden = !label.dataset.searchName.includes(query);
            if (!label.hidden) shown++;
        }
        empty.hidden = shown !== 0;
        const selected = list.querySelectorAll('input:checked').length;
        count.textContent = `${selected} selected`;
    }

    async function load() {
        controller?.abort();
        const currentController = new AbortController();
        controller = currentController;
        let timedOut = false;
        const timeout = window.setTimeout(() => {
            timedOut = true;
            currentController.abort();
        }, 30000);
        item = undefined;
        loading.hidden = false;
        form.hidden = true;
        errorBox.hidden = true;
        reload.hidden = true;
        assigneeFields.hidden = true;
        assigneeFields.disabled = true;
        fullEdit.href = trigger.href;
        try {
            const response = await window.planningFetch(trigger.dataset.optionsUrl, {
                signal: currentController.signal,
            });
            const data = await readResponse(response);
            if (!response.ok) throw new Error(data.error || `Unable to load work (${response.status}).`);
            if (currentController.signal.aborted || !dialog.open) return;
            item = data.item;
            const label = { projects: 'project', activities: 'activity', tasks: 'task' }[item.kind];
            dialog.querySelector('#quick-edit-title').textContent = `Quick edit ${label}: ${item.name}`;
            assigneeFields.hidden = item.kind !== 'tasks';
            assigneeFields.disabled = item.kind !== 'tasks';
            form.elements.deadline.value = item.deadline || '';
            form.elements.priority.replaceChildren(
                ...data.priorities.map(priority => new Option(
                    priority.charAt(0).toUpperCase() + priority.slice(1), priority,
                    false, priority === item.priority,
                )),
            );
            list.replaceChildren();
            const eligible = new Set(data.assignees.map(user => user.id));
            for (const user of data.assignees) {
                const label = document.createElement('label');
                label.className = 'form-check mb-1';
                label.dataset.searchName = user.name.toLocaleLowerCase();
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'form-check-input';
                checkbox.name = 'assigned_user_ids';
                checkbox.value = String(user.id);
                checkbox.checked = item.assigned_user_ids.includes(user.id);
                const name = document.createElement('span');
                name.className = 'form-check-label';
                name.textContent = user.name;
                label.append(checkbox, name);
                list.append(label);
            }
            const missingAssignees = item.assigned_user_ids.some(id => !eligible.has(id));
            unavailable.hidden = !missingAssignees;
            form.elements.remove_unavailable.checked = false;
            form.elements.remove_unavailable.required = missingAssignees;
            if (missingAssignees) {
                showError('Some existing assignees are inactive or no longer have access. Confirm their removal before saving, or use Edit all fields to review.');
            }
            search.value = '';
            updatePeople();
            form.hidden = false;
            form.elements.deadline.focus();
        } catch (error) {
            if (currentController.signal.aborted && !timedOut) return;
            showError(timedOut ? 'Loading took too long. Retry or open the full edit form.'
                : error instanceof TypeError
                ? 'Unable to reach the server. Your changes have not been saved. Check your connection and retry.'
                : error.message, error);
            reload.hidden = false;
        } finally {
            window.clearTimeout(timeout);
            if (controller === currentController) loading.hidden = true;
        }
    }

    root.addEventListener('click', event => {
        const link = event.target.closest('[data-quick-edit]');
        if (!link || event.defaultPrevented || event.button !== 0
            || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        trigger = link;
        dialog.querySelector('#quick-edit-title').textContent = 'Quick edit work';
        dialog.showModal();
        load();
    });
    for (const button of closeButtons) button.addEventListener('click', () => dialog.close());
    dialog.addEventListener('cancel', event => {
        if (saving) event.preventDefault();
    });
    dialog.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            if (!saving) dialog.close();
        }
    });
    dialog.addEventListener('close', () => {
        controller?.abort();
        trigger?.focus({ preventScroll: true });
    });
    reload.addEventListener('click', load);
    search.addEventListener('input', updatePeople);
    list.addEventListener('change', updatePeople);
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (saving || !item) return;
        const assigned = [...list.querySelectorAll('input:checked')].map(input => Number(input.value));
        if (assigned.length > 100) {
            showError('Select at most 100 assigned users.');
            return;
        }
        saving = true;
        errorBox.hidden = true;
        reload.hidden = true;
        save.textContent = 'Saving...';
        for (const control of form.elements) control.disabled = true;
        for (const button of closeButtons) button.disabled = true;
        fullEdit.setAttribute('aria-disabled', 'true');
        let completed = false;
        const saveController = new AbortController();
        const timeout = window.setTimeout(() => saveController.abort(), 30000);
        try {
            const changes = {
                version: item.version,
                deadline: form.elements.deadline.value || null,
                priority: form.elements.priority.value,
            };
            if (item.kind === 'tasks') changes.assigned_user_ids = assigned;
            const response = await window.planningFetch(trigger.dataset.saveUrl, {
                method: 'PATCH',
                signal: saveController.signal,
                body: JSON.stringify(changes),
            });
            const data = await readResponse(response);
            if (!response.ok) {
                showError(data.error || `Unable to save work (${response.status}).`);
                reload.hidden = response.status !== 409;
                return;
            }
            completed = true;
            save.textContent = 'Saved - refreshing...';
            // Reload reconciles all visible copies, filters and overdue totals.
            window.location.reload();
        } catch (error) {
            showError(error instanceof TypeError || saveController.signal.aborted
                ? 'The save could not be confirmed. Your entries are still here. Check your connection and reload latest values before retrying.'
                : error.message, error);
            reload.hidden = false;
        } finally {
            window.clearTimeout(timeout);
            if (!completed) {
                saving = false;
                for (const control of form.elements) control.disabled = false;
                assigneeFields.disabled = item.kind !== 'tasks';
                for (const button of closeButtons) button.disabled = false;
                fullEdit.removeAttribute('aria-disabled');
                save.textContent = 'Save changes';
                save.focus();
            }
        }
    });
    fullEdit.addEventListener('click', event => {
        if (saving) event.preventDefault();
    });
}
