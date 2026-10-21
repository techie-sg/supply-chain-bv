(message, history) => {
    const busy = Boolean(message.trim());
    const update = (props) => ({__type__: 'update', ...props});
    const last = history?.at(-1);
    const content = typeof last?.content === 'string'
        ? last.content
        : (last?.content || []).filter(part => part.type === 'text').map(part => part.text).join('\n');
    const original = content.replace(/\n\n<span class="message-time">[^<]*<\/span>\s*$/, '');
    const retry = last?.role === 'user' && original === message;
    return [
        busy ? message : '',
        busy && !retry ? [...(history || []), {role: 'user', content: [{type: 'text', text: message}]}] : (history || []),
        update({value: '', interactive: !busy}),
        update({interactive: !busy}),
        update({visible: busy})
    ];
}
