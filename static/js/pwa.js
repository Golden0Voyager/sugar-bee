(function() {
    var isStandalone = window.navigator.standalone || window.matchMedia('(display-mode: standalone)').matches;
    if (isStandalone) {
        document.documentElement.classList.add('standalone');
        // 顶部安全区 padding
        var style = document.createElement('style');
        style.textContent = '.standalone body { padding-top: env(safe-area-inset-top, 20px); }' +
            '.standalone .container:first-child { padding-top: env(safe-area-inset-top, 0px); }';
        document.head.appendChild(style);
    }
    // 监听 display-mode 变化（从浏览器切换到 standalone）
    window.matchMedia('(display-mode: standalone)').addEventListener('change', function(e) {
        if (e.matches) document.documentElement.classList.add('standalone');
    });
})();
