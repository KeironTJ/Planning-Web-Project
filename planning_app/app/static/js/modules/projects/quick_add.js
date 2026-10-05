/** Shared contextual creator for projects, activities and tasks. */
export function initWorkQuickAdd(root) {
    const dialog = root.querySelector('[data-quick-add-dialog]');
    if (!dialog || typeof dialog.showModal !== 'function') return;
    const form = dialog.querySelector('[data-create-form]');
    const loading = dialog.querySelector('[data-create-loading]');
    const errorBox = dialog.querySelector('[data-create-error]');
    const success = dialog.querySelector('[data-create-success]');
    const retry = dialog.querySelector('[data-create-retry]');
    const fullForm = dialog.querySelector('[data-create-full]');
    const parentField = dialog.querySelector('[data-create-parent-field]');
    const closeButtons = [...dialog.querySelectorAll('[data-create-close]')];
    const kinds = { projects: 'project', activities: 'activity', tasks: 'task' };
    let trigger;
    let kind;
    let controller;
    let busy = false;
    let created = false;
    let parents = [];

    function message(text, error) {
        errorBox.textContent = text;
        errorBox.hidden = false;
        if (error) console.error('Work creation failed.', error);
    }
    function endpoint(base) {
        return base.replace('/projects/api/projects', `/projects/api/${kind}`);
    }
    async function readResponse(response) {
        if (!response.headers.get('content-type')?.includes('application/json')) {
            throw new Error('The server did not return work data. Your session may have expired. Reload and sign in before retrying.');
        }
        return response.json();
    }
    function updateFullForm() {
        const url = new URL(trigger.href);
        url.searchParams.delete('project_id');
        url.searchParams.delete('activity_id');
        const [parentKind, id] = form.elements.parent.value.split(':');
        if (id) url.searchParams.set(parentKind === 'projects' ? 'project_id' : 'activity_id', id);
        fullForm.href = url.href;
    }
    async function load() {
        controller?.abort();
        controller = new AbortController();
        const current = controller;
        let timedOut = false;
        const timeout = window.setTimeout(() => { timedOut = true; current.abort(); }, 30000);
        loading.hidden = false;
        form.hidden = true;
        retry.hidden = true;
        errorBox.hidden = true;
        const origin = new URL(trigger.href);
        const url = new URL(endpoint(dialog.dataset.optionsBase), origin);
        for (const key of ['project_id', 'activity_id']) {
            if (origin.searchParams.get(key)) url.searchParams.set(key, origin.searchParams.get(key));
        }
        try {
            const response = await window.planningFetch(url.href, { signal: current.signal });
            const data = await readResponse(response);
            if (!response.ok) throw new Error(data.error || `Unable to load defaults (${response.status}).`);
            if (current.signal.aborted || !dialog.open) return;
            parents = data.parents;
            const defaults = data.defaults;
            form.reset();
            form.elements.parent.replaceChildren(new Option(`Standalone ${kinds[kind]}`, ''));
            for (const parent of parents) form.elements.parent.add(new Option(parent.label, parent.value));
            if (defaults.parent && !parents.some(parent => parent.value === defaults.parent)) {
                throw new Error('This parent is closed. Reopen it before adding child work.');
            }
            form.elements.parent.value = defaults.parent;
            parentField.hidden = kind === 'projects';
            form.elements.department.value = defaults.department;
            form.elements.owner_id.replaceChildren(...data.users.map(user => new Option(
                user.name, String(user.id), false, user.id === defaults.owner_id,
            )));
            form.elements.priority.replaceChildren(...data.priorities.map(value => new Option(
                value.charAt(0).toUpperCase() + value.slice(1), value, false, value === defaults.priority,
            )));
            updateFullForm();
            form.hidden = false;
            form.elements.name.focus();
        } catch (error) {
            if (current.signal.aborted && !timedOut) return;
            message(timedOut ? 'Loading took too long. Retry or use the full form.'
                : error instanceof TypeError ? 'Unable to reach the server. Check your connection and retry.'
                : error.message, error);
            retry.hidden = false;
        } finally {
            window.clearTimeout(timeout);
            if (current === controller) loading.hidden = true;
        }
    }
    root.addEventListener('click', event => {
        const link = event.target.closest('[data-quick-add]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey
            || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        trigger = link;
        kind = link.dataset.kind;
        created = false;
        success.hidden = true;
        fullForm.href = link.href;
        dialog.querySelector('#quick-add-title').textContent = `Add ${kinds[kind]}`;
        dialog.showModal();
        load();
    });
    form.elements.parent.addEventListener('change', () => {
        const parent = parents.find(row => row.value === form.elements.parent.value);
        if (parent) form.elements.department.value = parent.department;
        updateFullForm();
    });
    retry.addEventListener('click', load);
    function close() {
        if (!busy) dialog.close();
    }
    for (const button of closeButtons) button.addEventListener('click', close);
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); close(); }
    });
    dialog.addEventListener('close', () => {
        controller?.abort();
        if (created) window.location.reload();
        else trigger?.focus({ preventScroll: true });
    });
    fullForm.addEventListener('click', event => { if (busy) event.preventDefault(); });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        const another = event.submitter?.value === 'another';
        const values = {
            name: form.elements.name.value,
            department: form.elements.department.value,
            owner_id: Number(form.elements.owner_id.value),
            priority: form.elements.priority.value,
            deadline: form.elements.deadline.value || null,
        };
        const [parentKind, id] = form.elements.parent.value.split(':');
        if (id) values[parentKind === 'projects' ? 'project_id' : 'activity_id'] = Number(id);
        busy = true;
        errorBox.hidden = true;
        success.hidden = true;
        for (const control of form.elements) control.disabled = true;
        for (const button of closeButtons) button.disabled = true;
        fullForm.setAttribute('aria-disabled', 'true');
        const saveController = new AbortController();
        const timeout = window.setTimeout(() => saveController.abort(), 30000);
        let completed = false;
        try {
            const response = await window.planningFetch(endpoint(dialog.dataset.apiBase), {
                method: 'POST', signal: saveController.signal, body: JSON.stringify(values),
            });
            const data = await readResponse(response);
            if (!response.ok) { message(data.error || `Unable to create work (${response.status}).`); return; }
            created = true;
            if (another) {
                success.textContent = `Created ${data.reference} - ${data.name}. You can add another ${kinds[kind]}.`;
                success.hidden = false;
                form.elements.name.value = '';
            } else {
                completed = true;
                window.location.reload();
            }
        } catch (error) {
            message(error instanceof TypeError || saveController.signal.aborted
                ? 'Creation could not be confirmed. Your entries are still here. Check the work list before retrying to avoid creating a duplicate.'
                : error.message, error);
        } finally {
            window.clearTimeout(timeout);
            if (!completed) {
                busy = false;
                for (const control of form.elements) control.disabled = false;
                for (const button of closeButtons) button.disabled = false;
                fullForm.removeAttribute('aria-disabled');
                form.elements.name.focus();
            }
        }
    });
}
