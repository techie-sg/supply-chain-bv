() => {
    if (!matchMedia('(max-width: 768px)').matches) return;
    if (document.querySelector('#chat-sidebar.open')) {
        document.querySelector('#chat-sidebar .toggle-button')?.click();
    }
}
