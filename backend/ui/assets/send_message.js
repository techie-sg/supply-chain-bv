(message, history) => {
    const busy = Boolean(message.trim());
    const update = (props) => ({__type__: 'update', ...props});
    const last = history?.at(-1);
    const content = typeof last?.content === 'string'
        ? last.content
        : (last?.content || []).filter(part => part.type === 'text').map(part => part.text).join('\n');
    const original = content.replace(/\n\n<span class="message-time">[^<]*<\/span>\s*$/, '');
    const retry = last?.role === 'user' && original === message;
    const randomBytes = new Uint8Array(16);
    // randomUUID is unavailable on ordinary HTTP origins. Timing must never
    // prevent a chat from being sent, even in older browsers without Web Crypto.
    let requestId;
    if (typeof globalThis.crypto?.randomUUID === 'function') {
        requestId = globalThis.crypto.randomUUID().replaceAll('-', '');
    } else {
        if (typeof globalThis.crypto?.getRandomValues === 'function') {
            globalThis.crypto.getRandomValues(randomBytes);
        } else {
            for (let index = 0; index < randomBytes.length; index++) {
                randomBytes[index] = Math.floor(Math.random() * 256);
            }
        }
        requestId = Array.from(randomBytes, byte => byte.toString(16).padStart(2, '0')).join('');
    }
    return [
        busy ? message : '',
        busy && !retry ? [...(history || []), {role: 'user', content: [{type: 'text', text: message}]}] : (history || []),
        update({value: '', interactive: !busy}),
        update({interactive: !busy}),
        update({visible: busy}),
        busy ? JSON.stringify({
            request_id: requestId,
            sent_at_ms: Date.now(),
            sent_monotonic_ms: performance.now()
        }) : ''
    ];
}
