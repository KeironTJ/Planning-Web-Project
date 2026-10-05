/** UK text entry with an optional native calendar; API dates stay ISO. */
export function ukDate(iso) {
    if (!iso) return '';
    const [year, month, day] = iso.split('-');
    return `${day}/${month}/${year}`;
}

export function isoDate(value) {
    if (!value) return null;
    const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
    if (!match) throw new Error('Enter a valid date as dd/mm/yyyy.');
    const [, day, month, year] = match;
    const leap = Number(year) % 4 === 0 && (Number(year) % 100 !== 0 || Number(year) % 400 === 0);
    const maxDay = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][Number(month) - 1];
    if (Number(year) < 1 || Number(month) < 1 || Number(month) > 12
        || Number(day) < 1 || Number(day) > maxDay) {
        throw new Error('Enter a valid date as dd/mm/yyyy.');
    }
    return `${year}-${month}-${day}`;
}

export function initUKDateInputs(root) {
    for (const input of root.querySelectorAll('[data-uk-date]')) {
        function validate() {
            input.setCustomValidity('');
            try { isoDate(input.value); }
            catch (error) { input.setCustomValidity(error.message); }
        }
        input.addEventListener('input', validate);
        input.addEventListener('change', validate);
        input.form?.addEventListener('reset', () => input.setCustomValidity(''));
        validate();
        const picker = document.createElement('input');
        picker.type = 'date';
        if (typeof picker.showPicker !== 'function') continue;
        picker.className = 'visually-hidden';
        picker.tabIndex = -1;
        picker.setAttribute('aria-hidden', 'true');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm btn-outline-secondary mt-1';
        button.textContent = 'Choose date';
        button.setAttribute('aria-label', `Choose ${input.labels[0]?.textContent || 'date'} from calendar`);
        button.addEventListener('click', () => {
            try { picker.value = isoDate(input.value) || ''; }
            catch { picker.value = ''; }
            picker.showPicker();
        });
        picker.addEventListener('change', () => {
            input.value = ukDate(picker.value);
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.focus();
        });
        input.after(button, picker);
    }
}
