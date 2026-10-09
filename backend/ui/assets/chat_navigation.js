() => {
    const list = document.querySelector('#history-list');
    if (!list || list.dataset.navigationReady) return;
    list.dataset.navigationReady = 'true';
    const showTitle = (event) => {
        const label = event.target.closest('label');
        if (label) label.title = label.querySelector('span')?.textContent.trim() || '';
    };
    list.addEventListener('pointerover', showTitle);
    list.addEventListener('focusin', showTitle);
    list.addEventListener('click', (event) => {
        if (!event.target.closest('label')) return;
        // Radio input doesn't fire again when the already selected chat is clicked.
        document.querySelector(
            '#workspace-tabs [role="tab"][data-tab-id="assistant"]'
        )?.click();
    });
}
