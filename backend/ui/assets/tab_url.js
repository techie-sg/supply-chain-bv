async () => {
    await new Promise(requestAnimationFrame);
    const tab = document.querySelector(
        '#workspace-tabs > .tab-wrapper [role="tab"][aria-selected="true"]'
    );
    const view = tab?.dataset.tabId;
    if (!['assistant', 'settings', 'handover', 'demo'].includes(view)) return;
    const navigation = {
        assistant: 'new-chat',
        settings: 'edit-settings',
        handover: 'sidebar-handover',
        demo: 'sidebar-demo',
    };
    const titles = {settings: 'Settings', handover: 'Handover', demo: 'Scenarios'};
    for (const [name, id] of Object.entries(navigation)) {
        const button = document.getElementById(id);
        if (name === view) button?.setAttribute('aria-current', 'page');
        else button?.removeAttribute('aria-current');
    }
    const url = new URL(window.location.href);
    url.searchParams.set('view', view);
    window.history.replaceState(null, '', url);
    document.title = view === 'assistant'
        ? (document.documentElement.dataset.chatTitle || 'DispatchDesk')
        : `${titles[view]} | DispatchDesk`;
}
