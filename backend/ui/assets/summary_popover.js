() => {
    const panel = document.querySelector('#chat-summary-panel');
    const dock = document.querySelector('#composer-dock');
    if (!panel || !dock || panel.dataset.popoverReady) return;
    panel.dataset.popoverReady = 'true';
    panel.popover = 'auto';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-labelledby', 'chat-summary-title');
    panel.tabIndex = -1;
    const prepare = (button) => {
        if (!button) return;
        button.setAttribute('popovertarget', panel.id);
        button.setAttribute('aria-controls', panel.id);
        button.setAttribute('aria-haspopup', 'dialog');
        button.title = 'Conversation summary';
        button.setAttribute('aria-expanded', String(panel.matches(':popover-open')));
    };
    const position = () => {
        const bounds = dock.getBoundingClientRect();
        const width = Math.min(560, innerWidth - 24);
        panel.style.left = `${Math.max(12, Math.min(bounds.right - width, innerWidth - width - 12))}px`;
        panel.style.bottom = `${Math.max(12, innerHeight - bounds.top + 12)}px`;
    };
    prepare(document.querySelector('#summary-trigger'));
    for (const event of ['focusin', 'pointerover', 'click']) {
        document.addEventListener(event, (e) => {
            const button = e.target.closest('#summary-trigger');
            if (!button) return;
            prepare(button);
            position();
        }, true);
    }
    panel.addEventListener('toggle', () => {
        prepare(document.querySelector('#summary-trigger'));
        if (panel.matches(':popover-open')) panel.focus({preventScroll: true});
    });
    const reposition = () => {
        if (panel.matches(':popover-open')) position();
    };
    new ResizeObserver(reposition).observe(dock);
    window.addEventListener('resize', reposition);
}
