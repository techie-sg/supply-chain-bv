(chat) => {
    if (!chat) return;
    const url = new URL(window.location.href);
    if (chat.id) url.searchParams.set('chat', chat.id);
    else url.searchParams.delete('chat');
    window.history.replaceState(null, '', url);
    const title = chat.title ? `${chat.title} | DispatchDesk` : 'DispatchDesk';
    document.documentElement.dataset.chatTitle = title;
    if (!url.searchParams.has('view') || url.searchParams.get('view') === 'assistant') {
        document.title = title;
    }
}
