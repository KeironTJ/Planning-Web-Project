/** Transfer unsaved work through a CSRF-protected preview, not a save or URL draft. */
export function openFullWorkForm(destination, values) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = destination;
    const url = new URL(destination);
    const fields = {
        ...values,
        form_action: 'preview',
        csrf_token: document.querySelector('meta[name="csrf-token"]').content,
        return_to: url.searchParams.get('return_to') || '',
    };
    for (const [name, value] of Object.entries(fields)) {
        for (const entry of Array.isArray(value) ? value : [value]) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = name;
            input.value = String(entry);
            form.append(input);
        }
    }
    document.body.append(form);
    form.submit();
    form.remove();
}
