/**
 * Tab-local workspace preferences. Opt in with data-state-key on disclosures,
 * data-work-filters on a GET form, and data-work-reset on its reset link.
 */

export function initWorkspaceState(root, namespace) {
    const storageKey = `planning-workspace:${namespace}:v1`;
    const url = new URL(window.location.href);
    const pageUrl = new URL(url);
    pageUrl.searchParams.delete('return_to');
    pageUrl.searchParams.sort();
    const pageKey = pageUrl.pathname + pageUrl.search;
    let state = { pages: {}, filters: {} };
    let available = true;
    let ready = false;
    let resetting = false;
    let scrollTimer;

    function isRecord(value) {
        return value !== null && typeof value === 'object' && !Array.isArray(value);
    }

    function isPosition(value) {
        return isRecord(value) && Number.isFinite(value.x) && Number.isFinite(value.y);
    }

    function isPageState(page) {
        return isRecord(page) && isRecord(page.disclosures) && isPosition(page.scroll)
            && Object.values(page.disclosures).every(open => typeof open === 'boolean')
            && (page.containerScroll === undefined || (isRecord(page.containerScroll)
                && Object.values(page.containerScroll).every(isPosition)));
    }

    function reportStorageError(error) {
        available = false;
        history.scrollRestoration = 'auto';
        console.warn('Workspace preferences could not be stored or restored.', error);
        const notice = document.createElement('div');
        notice.className = 'alert alert-warning';
        notice.setAttribute('role', 'status');
        notice.textContent = 'Your browser could not remember this workspace. You can still use all work screens normally.';
        (root.querySelector('main') || root).prepend(notice);
    }

    try {
        const saved = window.sessionStorage.getItem(storageKey);
        if (saved) {
            const parsed = JSON.parse(saved);
            if (!isRecord(parsed) || !isRecord(parsed.pages) || !isRecord(parsed.filters)
                || !Object.values(parsed.pages).every(isPageState)
                || Object.values(parsed.filters).some(filters => typeof filters !== 'string'
                    || (filters !== '' && !filters.startsWith('?')))) {
                throw new TypeError('Invalid workspace preferences.');
            }
            state = parsed;
        }
    } catch (error) {
        if (!(error instanceof DOMException || error instanceof SyntaxError || error instanceof TypeError)) throw error;
        reportStorageError(error);
    }

    const filterForm = root.querySelector('[data-work-filters]');
    const filterNames = new Set(['page', 'per_page', 'view']);
    if (filterForm) {
        for (const control of filterForm.elements) {
            if (control.name) filterNames.add(control.name);
        }
        const savedFilters = state.filters[url.pathname];
        if (available && !url.search && typeof savedFilters === 'string' && savedFilters.startsWith('?')) {
            window.location.replace(url.pathname + savedFilters);
            return;
        }
    }

    const disclosures = [...root.querySelectorAll('details[data-state-key]')];
    const scrollContainers = [...root.querySelectorAll('[data-scroll-key]')];
    const page = state.pages[pageKey];
    if (page && page.disclosures && typeof page.disclosures === 'object') {
        for (const disclosure of disclosures) {
            const open = page.disclosures[disclosure.dataset.stateKey];
            if (typeof open === 'boolean') disclosure.open = open;
        }
    }

    function save() {
        if (!available || !ready || resetting) return;
        const expanded = {};
        for (const disclosure of disclosures) {
            expanded[disclosure.dataset.stateKey] = disclosure.open;
        }
        const containerScroll = {};
        for (const container of scrollContainers) {
            containerScroll[container.dataset.scrollKey] = {
                x: container.scrollLeft, y: container.scrollTop,
            };
        }
        // Reinserting makes this the most recently used page; bound tab storage.
        delete state.pages[pageKey];
        state.pages[pageKey] = {
            disclosures: expanded,
            scroll: { x: window.scrollX, y: window.scrollY },
            containerScroll,
        };
        for (const key of Object.keys(state.pages).slice(0, -40)) delete state.pages[key];
        if (filterForm) {
            const filters = new URLSearchParams();
            for (const [name, value] of url.searchParams) {
                if (filterNames.has(name) && value) filters.append(name, value);
            }
            state.filters[url.pathname] = filters.size ? `?${filters}` : '';
        }
        try {
            window.sessionStorage.setItem(storageKey, JSON.stringify(state));
        } catch (error) {
            if (!(error instanceof DOMException)) throw error;
            reportStorageError(error);
        }
    }

    for (const disclosure of disclosures) disclosure.addEventListener('toggle', save);
    for (const container of scrollContainers) container.addEventListener('scroll', save, { passive: true });
    root.addEventListener('submit', save);
    root.addEventListener('click', (event) => {
        const link = event.target.closest('a');
        if (!link || event.defaultPrevented || event.button !== 0
            || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        if (link.hasAttribute('data-work-reset')) {
            save();
            resetting = true;
            delete state.filters[url.pathname];
            for (const key of Object.keys(state.pages)) {
                if (key === url.pathname || key.startsWith(url.pathname + '?')) delete state.pages[key];
            }
            if (available) {
                try {
                    window.sessionStorage.setItem(storageKey, JSON.stringify(state));
                } catch (error) {
                    if (!(error instanceof DOMException)) throw error;
                    reportStorageError(error);
                }
            }
        } else {
            save();
        }
    });
    window.addEventListener('scroll', () => {
        window.clearTimeout(scrollTimer);
        scrollTimer = window.setTimeout(save, 150);
    }, { passive: true });
    window.addEventListener('pagehide', save);
    if (available && page && page.scroll && !url.hash) history.scrollRestoration = 'manual';
    window.addEventListener('pageshow', (event) => {
        if (event.persisted) {
            ready = true;
            return;
        }
        window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
            for (const container of scrollContainers) {
                const position = page && page.containerScroll && page.containerScroll[container.dataset.scrollKey];
                if (position && Number.isFinite(position.x) && Number.isFinite(position.y)) {
                    container.scrollLeft = position.x;
                    container.scrollTop = position.y;
                }
            }
            if (available && !url.hash && page && page.scroll
                && Number.isFinite(page.scroll.x) && Number.isFinite(page.scroll.y)) {
                window.scrollTo({ left: page.scroll.x, top: page.scroll.y, behavior: 'instant' });
            }
            ready = true;
            save();
        }));
    });
}
