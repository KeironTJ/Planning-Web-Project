/** Searchable checkbox lists; filtering never changes the selected values. */
export function initPeoplePickers(root) {
    for (const picker of root.querySelectorAll('[data-people-picker]')) {
        const search = picker.querySelector('[data-people-search]');
        const labels = [...picker.querySelectorAll('[data-person-name]')];
        const count = picker.querySelector('[data-people-count]');
        search.hidden = false;
        picker.querySelector('[data-people-search-label]').hidden = false;
        function update() {
            const query = search.value.trim().toLocaleLowerCase();
            let shown = 0;
            for (const label of labels) {
                label.hidden = !label.dataset.personName.toLocaleLowerCase().includes(query);
                if (!label.hidden) shown++;
            }
            count.textContent = `${picker.querySelectorAll('input[type=checkbox]:checked').length} selected / ${shown} people shown`;
        }
        search.addEventListener('input', update);
        picker.addEventListener('change', update);
        update();
    }
}
