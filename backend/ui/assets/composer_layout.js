() => {
    const dock = document.getElementById("composer-dock");
    if (!dock || dock.dataset.layoutObserved) return;
    dock.dataset.layoutObserved = "true";
    const update = () => {
        const height = Math.max(80, Math.ceil(dock.getBoundingClientRect().height));
        document.documentElement.style.setProperty("--desk-composer-height", `${height}px`);
    };
    new ResizeObserver(update).observe(dock);
    update();
}
