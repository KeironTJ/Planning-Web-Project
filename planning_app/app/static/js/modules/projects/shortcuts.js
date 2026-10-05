/** Small navigation shortcuts that never intercept input or dialog keys. */
export function initWorkShortcuts(root) {
    const dialog = root.querySelector('[data-work-shortcuts]');
    const openButton = root.querySelector('[data-shortcuts-open]');
    if (!dialog || !openButton || typeof dialog.showModal !== 'function') return;

    const closeButton = dialog.querySelector('[data-shortcuts-close]');
    const destinations = {
        d: root.querySelector('[data-work-destination="dashboard"]')?.href,
        p: root.querySelector('[data-work-destination="projects"]')?.href,
        a: root.querySelector('[data-work-destination="activities"]')?.href,
        t: root.querySelector('[data-work-destination="tasks"]')?.href,
    };
    let waitingForDestination = false;
    let prefixTimer;
    let returnFocus;

    openButton.hidden = false;
    function showHelp() {
        returnFocus = document.activeElement === document.body
            ? openButton
            : document.activeElement;
        dialog.showModal();
        closeButton.focus();
    }
    function closeHelp() {
        dialog.close();
    }
    openButton.addEventListener('click', showHelp);
    closeButton.addEventListener('click', closeHelp);
    dialog.addEventListener('close', () => returnFocus?.focus({ preventScroll: true }));
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeHelp(); });

    function typingTarget(target) {
        return target instanceof Element && (
            target.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]')
            || target.closest('dialog[open]')
        );
    }

    document.addEventListener('keydown', event => {
        if (event.defaultPrevented || event.repeat || event.ctrlKey || event.altKey || event.metaKey
            || typingTarget(event.target)) {
            if (event.key === 'Escape') waitingForDestination = false;
            return;
        }
        if (waitingForDestination) {
            window.clearTimeout(prefixTimer);
            waitingForDestination = false;
            const destination = destinations[event.key.toLowerCase()];
            if (destination) {
                event.preventDefault();
                window.location.assign(destination);
            }
            return;
        }
        if (event.key === '?' ) {
            event.preventDefault();
            showHelp();
        } else if (event.key === '/') {
            const search = root.querySelector('#work-search');
            if (search) {
                event.preventDefault();
                search.focus();
            }
        } else if (event.key.toLowerCase() === 'g' && event.key.length === 1) {
            waitingForDestination = true;
            window.clearTimeout(prefixTimer);
            prefixTimer = window.setTimeout(() => { waitingForDestination = false; }, 1200);
        } else if (event.key.toLowerCase() === 'n' && event.key.length === 1) {
            const addLinks = [...root.querySelectorAll('[data-quick-add]')];
            if (addLinks.length === 1) {
                event.preventDefault();
                addLinks[0].click();
            }
        }
    });
}
