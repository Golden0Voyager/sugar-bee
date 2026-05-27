(function() {
    // ========== 健康助手 JS ==========
    let chatSessionId = '';
    let chatStreaming = false;

    // -- 拖拽调宽 --
    (function initResize() {
        const handle = document.getElementById('chatResizeHandle');
        const panel = document.getElementById('chatPanel');
        let dragging = false, startX = 0, startW = 0;

        handle.addEventListener('mousedown', e => {
            dragging = true; startX = e.clientX;
            startW = panel.offsetWidth;
            handle.classList.add('dragging');
            panel.style.transition = 'none';  // 拖拽时关闭过渡动画
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            e.preventDefault();
        });
        document.addEventListener('mousemove', e => {
            if (!dragging) return;
            // 左边缘向左拖 → 变宽
            const newW = startW + (startX - e.clientX);
            const minW = 320, maxW = window.innerWidth * 0.8;
            panel.style.width = Math.min(maxW, Math.max(minW, newW)) + 'px';
        });
        document.addEventListener('mouseup', () => {
            if (!dragging) return;
            dragging = false;
            handle.classList.remove('dragging');
            panel.style.transition = '';
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        });
    })();

    // -- 面板开关 --
    window.toggleChatPanel = function(show) {
        const panel = document.getElementById('chatPanel');
        const overlay = document.getElementById('chatOverlay');
        const fab = document.getElementById('chatFab');
        const isOpen = panel.classList.contains('open');
        const shouldOpen = show !== undefined ? show : !isOpen;

        if (shouldOpen) {
            panel.classList.add('open');
            overlay.classList.add('active');
            fab.classList.add('hidden');
            // 首次打开 → 加载历史
            if (!chatSessionId) chatLoadHistory();
        } else {
            panel.classList.remove('open');
            overlay.classList.remove('active');
            fab.classList.remove('hidden');
        }
    };

    // -- 加载历史 --
    async function chatLoadHistory(sid) {
        try {
            const url = sid ? `/api/chat/history?session_id=${encodeURIComponent(sid)}` : '/api/chat/history';
            const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const json = await res.json();
            if (json.status !== 'success') return;

            const d = json.data;
            chatSessionId = d.session_id || '';

            // 渲染消息
            const container = document.getElementById('chatMessages');
            container.innerHTML = '';

            if (d.messages && d.messages.length > 0) {
                d.messages.forEach(m => appendChatMsg(m.role, m.content, false));
                chatScrollBottom();
            } else {
                container.innerHTML = `
                    <div id="chatWelcome" class="chat-welcome">
                        <div class="chat-welcome-title">你好，有什么可以帮你的？</div>
                        <div class="chat-welcome-subtitle">基于你的健康数据，为你提供个性化建议</div>
                        <div class="chat-quick-chips">
                            <button class="chat-chip" onclick="chatSendQuick('我的血糖控制得怎么样？')">我的血糖控制得怎么样？</button>
                            <button class="chat-chip" onclick="chatSendQuick('推荐今天的运动方案')">推荐今天的运动方案</button>
                            <button class="chat-chip" onclick="chatSendQuick('晚餐吃什么好？')">晚餐吃什么好？</button>
                            <button class="chat-chip" onclick="chatSendQuick('帮我总结一下这周的健康状况')">总结这周健康状况</button>
                        </div>
                    </div>`;
            }

            // 渲染会话列表
            renderSessionList(d.sessions || [], chatSessionId);
        } catch (e) {
            console.error('chatLoadHistory error:', e);
        }
    }

    let _chatSessions = [];  // 缓存会话列表

    function renderSessionList(sessions, activeId) {
        _chatSessions = sessions;
        const bar = document.getElementById('chatSessionBar');
        const list = document.getElementById('chatSessionList');
        const btn = document.getElementById('chatHistoryBtn');

        // 没有会话时隐藏
        if (!sessions.length) {
            bar.style.display = 'none';
            btn.style.display = 'none';
            return;
        }
        btn.style.display = '';

        // 仅在 bar 可见时更新内容
        list.innerHTML = sessions.map(s =>
            `<div class="chat-session-item${s.session_id === activeId ? ' active' : ''}"
                  onclick="chatSwitchSession('${s.session_id}')">
                <span class="chat-session-title" title="${s.title}">${s.title}</span>
                <button class="chat-session-delete" onclick="event.stopPropagation(); chatDeleteSession('${s.session_id}')" title="删除对话">
                    <i class="bi bi-trash3"></i>
                </button>
            </div>`
        ).join('');
    }

    // -- 历史对话列表切换 --
    window.chatToggleHistory = function() {
        const bar = document.getElementById('chatSessionBar');
        const btn = document.getElementById('chatHistoryBtn');
        const visible = bar.style.display !== 'none';
        bar.style.display = visible ? 'none' : 'block';
        btn.classList.toggle('active', !visible);
    };

    window.chatSwitchSession = function(sid) {
        if (sid === chatSessionId || chatStreaming) return;
        chatLoadHistory(sid);
        // 切换后自动收起列表
        document.getElementById('chatSessionBar').style.display = 'none';
        document.getElementById('chatHistoryBtn').classList.remove('active');
    };

    // -- 删除会话 --
    window.chatDeleteSession = async function(sid) {
        if (chatStreaming) return;
        if (!confirm('确定删除这个对话吗？')) return;
        try {
            const res = await fetch(`/api/chat/session/${encodeURIComponent(sid)}`, {
                method: 'DELETE',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            const json = await res.json();
            if (json.status === 'success') {
                // 如果删的是当前会话，切到下一个或显示空白
                if (sid === chatSessionId) {
                    const remaining = _chatSessions.filter(s => s.session_id !== sid);
                    if (remaining.length) {
                        chatLoadHistory(remaining[0].session_id);
                    } else {
                        chatSessionId = '';
                        chatLoadHistory();
                    }
                } else {
                    // 从列表中移除
                    _chatSessions = _chatSessions.filter(s => s.session_id !== sid);
                    renderSessionList(_chatSessions, chatSessionId);
                }
            }
        } catch (e) {
            console.error('chatDeleteSession error:', e);
        }
    };

    // -- 新建对话 --
    window.chatNewSession = async function() {
        if (chatStreaming) return;
        try {
            const res = await fetch('/api/chat/new_session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            });
            const json = await res.json();
            if (json.status === 'success') {
                chatSessionId = json.data.session_id;
                // 清空面板显示欢迎
                chatLoadHistory(chatSessionId);
            }
        } catch (e) {
            console.error('chatNewSession error:', e);
        }
    };

    // -- 发送消息 --
    window.chatSend = async function() {
        const input = document.getElementById('chatInput');
        const text = input.value.trim();
        if (!text || chatStreaming) return;

        // 确保有 session
        if (!chatSessionId) {
            try {
                const res = await fetch('/api/chat/new_session', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                });
                const json = await res.json();
                if (json.status === 'success') chatSessionId = json.data.session_id;
            } catch(e) { return; }
        }

        // 隐藏欢迎区
        const welcome = document.getElementById('chatWelcome');
        if (welcome) welcome.remove();

        input.value = '';
        input.style.height = 'auto';
        chatToggleSend(false);

        // 追加用户消息
        appendChatMsg('user', text, false);
        chatScrollBottom();

        // 追加 AI 占位 + 打字指示器
        const aiEl = appendChatMsg('assistant', '', true);
        chatScrollBottom();

        // SSE 流式
        chatStreaming = true;
        chatToggleInput(false);

        try {
            const res = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify({ message: text, session_id: chatSessionId }),
            });

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let fullText = '';
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                // 解析 SSE
                const lines = buffer.split('\n');
                buffer = lines.pop();  // 保留不完整的行

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const payload = line.slice(6);
                    if (payload === '[DONE]') continue;
                    try {
                        const obj = JSON.parse(payload);
                        if (obj.error) {
                            fullText += `\n\n**出错了**: ${obj.error}`;
                        } else if (obj.content) {
                            fullText += obj.content;
                        }
                    } catch(e) {}
                }

                // 更新 AI 消息（流式时显示纯文本，避免 markdown 半渲染闪烁）
                const contentEl = aiEl.querySelector('.chat-msg-content');
                contentEl.textContent = fullText;
                chatScrollBottom();
            }

            // 流式完成，用 marked 渲染最终 Markdown
            const contentEl = aiEl.querySelector('.chat-msg-content');
            if (typeof marked !== 'undefined' && fullText) {
                contentEl.innerHTML = marked.parse(fullText);
            }
            // 移除打字指示器
            const typing = aiEl.querySelector('.chat-typing');
            if (typing) typing.remove();

        } catch (e) {
            console.error('chatSend stream error:', e);
            const contentEl = aiEl.querySelector('.chat-msg-content');
            contentEl.textContent = '连接出错，请稍后重试';
        } finally {
            chatStreaming = false;
            chatToggleInput(true);
            chatScrollBottom();
        }
    };

    window.chatSendQuick = function(text) {
        document.getElementById('chatInput').value = text;
        chatSend();
    };

    // -- DOM helpers --
    function appendChatMsg(role, content, isStreaming) {
        const container = document.getElementById('chatMessages');
        const div = document.createElement('div');
        div.className = `chat-msg ${role}`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'chat-msg-content';

        if (role === 'assistant' && content && typeof marked !== 'undefined') {
            contentDiv.innerHTML = marked.parse(content);
        } else {
            contentDiv.textContent = content;
        }
        div.appendChild(contentDiv);

        // 打字指示器
        if (isStreaming && role === 'assistant') {
            const typing = document.createElement('div');
            typing.className = 'chat-typing';
            typing.innerHTML = '<span class="chat-typing-dot"></span><span class="chat-typing-dot"></span><span class="chat-typing-dot"></span>';
            div.appendChild(typing);
        }

        container.appendChild(div);
        return div;
    }

    function chatScrollBottom() {
        const c = document.getElementById('chatMessages');
        requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
    }

    function chatToggleSend(enabled) {
        document.getElementById('chatSendBtn').disabled = !enabled;
    }

    function chatToggleInput(enabled) {
        const input = document.getElementById('chatInput');
        const btn = document.getElementById('chatSendBtn');
        input.disabled = !enabled;
        btn.disabled = !enabled;
        if (enabled) input.focus();
    }

    // -- 输入交互 --
    window.chatInputKeydown = function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatSend();
        }
    };

    window.chatAutoResize = function(el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 120) + 'px';
        chatToggleSend(el.value.trim().length > 0);
    };
})();
