async (payload) => {
    // Wait for the Chatbot update to reach the screen before acknowledging it.
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    let timing = {};
    try { timing = JSON.parse(payload || '{}'); } catch (_) {}
    return [
        {__type__: 'update', visible: false},
        {__type__: 'update', interactive: true},
        {__type__: 'update', interactive: true},
        JSON.stringify({
            request_id: timing.request_id,
            rendered_at_ms: Date.now(),
            rendered_monotonic_ms: performance.now()
        })
    ];
}
