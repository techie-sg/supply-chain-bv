(manager) => {
    if (!manager) return;
    const url = new URL(window.location.href);
    url.searchParams.set('manager', manager);
    window.history.replaceState(null, '', url);
}
