export function initWorkOrdering(root) {
    let draggedItem;
    let sourceList;
    let dragHandle;
    let originalOrder;
    let pointerId;
    let startX;
    let startY;
    let isDragging = false;

    function directItems(list) {
        return [...list.children].filter(item => item.matches('[data-order-item]'));
    }

    function orderPayload(list) {
        const scope = list.dataset;
        return {
            parent_kind: scope.parentKind || null,
            parent_id: scope.parentId ? Number(scope.parentId) : null,
            root_kind: scope.rootKind || null,
            items: directItems(list).map(item => {
                const [kind, id] = item.dataset.workKey.split(':');
                return { kind, id: Number(id) };
            }),
        };
    }

    function announce(list, message, isError = false) {
        let status = list.querySelector(':scope > [data-order-status]');
        if (!status) {
            status = document.createElement('li');
            status.className = 'small mt-2';
            status.dataset.orderStatus = '';
            status.setAttribute('role', 'status');
            list.prepend(status);
        }
        status.textContent = message;
        status.classList.toggle('text-danger', isError);
        status.classList.toggle('text-muted', !isError);
    }

    async function save(list, previousOrder) {
        const restoreOrder = [...previousOrder];
        const controls = list.querySelectorAll('[data-order-move], [data-order-handle]');
        controls.forEach(control => { control.disabled = true; });
        list.setAttribute('aria-busy', 'true');
        try {
            const response = await window.planningFetch('/projects/api/order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(orderPayload(list)),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `Unable to save order (${response.status}).`);
            window.location.reload();
        } catch (error) {
            const items = new Map(directItems(list).map(item => [item.dataset.workKey, item]));
            for (const key of restoreOrder) list.append(items.get(key));
            announce(list, error.message, true);
            console.error('Work ordering failed.', error);
        } finally {
            list.removeAttribute('aria-busy');
            controls.forEach(control => { control.disabled = false; });
        }
    }

    root.addEventListener('click', event => {
        const handle = event.target.closest('[data-order-handle]');
        if (handle) {
            event.preventDefault();
            event.stopPropagation();
            return;
        }
        const button = event.target.closest('[data-order-move]');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        const item = button.closest('[data-order-item]');
        const list = item?.parentElement;
        if (!item || !list?.matches('[data-order-list]')) return;
        const items = directItems(list);
        const index = items.indexOf(item);
        const neighbour = items[index + (button.dataset.orderMove === 'up' ? -1 : 1)];
        if (!neighbour) return;
        originalOrder = items.map(entry => entry.dataset.workKey);
        if (button.dataset.orderMove === 'up') list.insertBefore(item, neighbour);
        else list.insertBefore(neighbour, item);
        save(list, originalOrder);
    });

    function restoreOrder() {
        const items = new Map(directItems(sourceList).map(item => [item.dataset.workKey, item]));
        for (const key of originalOrder) sourceList.append(items.get(key));
    }

    function finishDrag(saveOrder) {
        if (isDragging && saveOrder) {
            const nextOrder = directItems(sourceList).map(item => item.dataset.workKey);
            if (nextOrder.some((key, index) => key !== originalOrder[index])) {
                save(sourceList, originalOrder);
            }
        } else if (isDragging) {
            restoreOrder();
        }
        draggedItem?.classList.remove('work-order-dragging');
        dragHandle?.removeAttribute('aria-grabbed');
        draggedItem = null;
        sourceList = null;
        dragHandle = null;
        originalOrder = null;
        pointerId = undefined;
        isDragging = false;
    }

    root.addEventListener('pointerdown', event => {
        const handle = event.target.closest('[data-order-handle]');
        if (!handle || event.button !== 0 || !event.isPrimary) return;
        draggedItem = handle.closest('[data-order-item]');
        sourceList = draggedItem?.parentElement;
        if (!draggedItem || !sourceList?.matches('[data-order-list]')) {
            draggedItem = null;
            sourceList = null;
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        dragHandle = handle;
        pointerId = event.pointerId;
        startX = event.clientX;
        startY = event.clientY;
        originalOrder = directItems(sourceList).map(item => item.dataset.workKey);
        handle.setPointerCapture(pointerId);
    });

    root.addEventListener('pointermove', event => {
        if (!draggedItem || event.pointerId !== pointerId) return;
        if (!isDragging) {
            if (Math.hypot(event.clientX - startX, event.clientY - startY) < 5) return;
            isDragging = true;
            draggedItem.classList.add('work-order-dragging');
            dragHandle.setAttribute('aria-grabbed', 'true');
        }
        const target = root.ownerDocument.elementFromPoint(event.clientX, event.clientY)
            ?.closest('[data-order-item]');
        if (!target || target === draggedItem || target.parentElement !== sourceList) return;
        const rect = target.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        sourceList.insertBefore(draggedItem, before ? target : target.nextSibling);
    });

    root.addEventListener('pointerup', event => {
        if (draggedItem && event.pointerId === pointerId) finishDrag(true);
    });

    root.addEventListener('pointercancel', event => {
        if (draggedItem && event.pointerId === pointerId) finishDrag(false);
    });
}
