// NOVA-7 Dashboard — WebSocket client
(function () {
    'use strict';

    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${location.host}/ws/dashboard`;
    let ws = null;
    let pollInterval = null;

    // ── localStorage session isolation ──────────────────────
    const LS_KEY = 'nova7_my_channels';

    function getMyChannels() {
        try {
            return JSON.parse(localStorage.getItem(LS_KEY)) || [];
        } catch { return []; }
    }

    function removeMyChannel(ch) {
        const chs = getMyChannels().filter(c => c !== ch);
        localStorage.setItem(LS_KEY, JSON.stringify(chs));
    }

    // ── WebSocket Connection ──────────────────────────────────
    function connect() {
        ws = new WebSocket(wsUrl);

        ws.onopen = function () {
            console.log('Dashboard WebSocket connected');
            addEvent('System', 'Dashboard connected', 'nominal');
        };

        ws.onmessage = function (event) {
            try {
                const data = JSON.parse(event.data);
                handleMessage(data);
            } catch (e) {
                console.error('Failed to parse WS message:', e);
            }
        };

        ws.onclose = function () {
            console.log('Dashboard WebSocket disconnected — reconnecting in 3s');
            setTimeout(connect, 3000);
        };

        ws.onerror = function () {
            ws.close();
        };
    }

    function handleMessage(data) {
        if (data.type === 'status_update') {
            updateServices(data.services || {}, data.chaos);
            updateCountdown(data.countdown || {});
        } else if (data.type === 'countdown') {
            updateCountdown(data);
        } else if (data.type === 'event') {
            addEvent(data.source || 'System', data.message || '', data.level || 'info');
        }
    }

    // ── Service Status Updates ────────────────────────────────
    function updateServices(services, chaosData) {
        const myChannels = getMyChannels();
        let hasWarning = false;
        let hasCritical = false;

        // Cleanup: remove stale channels from localStorage
        if (chaosData && myChannels.length > 0) {
            const stale = myChannels.filter(ch => !chaosData[ch] || chaosData[ch].state !== 'ACTIVE');
            stale.forEach(ch => removeMyChannel(ch));
        }

        for (const [name, info] of Object.entries(services)) {
            const card = document.getElementById('svc-' + name);
            if (!card) continue;

            const statusEl = card.querySelector('.svc-status');

            // Re-derive status based on session's channels
            let status;
            if (myChannels.length === 0) {
                // No channels triggered in this session → all nominal
                status = 'NOMINAL';
            } else {
                const myActive = (info.active_faults || []).filter(f => myChannels.includes(f));
                const myCascade = (info.cascade_faults || []).filter(f => myChannels.includes(f));
                if (myActive.length > 0) {
                    status = 'CRITICAL';
                } else if (myCascade.length > 0) {
                    status = 'WARNING';
                } else {
                    status = 'NOMINAL';
                }
            }

            // Remove all status classes
            card.classList.remove('nominal', 'warning', 'critical');
            statusEl.classList.remove('nominal', 'advisory', 'caution', 'warning', 'critical');

            if (status === 'CRITICAL') {
                card.classList.add('critical');
                statusEl.classList.add('critical');
                statusEl.textContent = 'CRITICAL';
                hasCritical = true;
            } else if (status === 'WARNING') {
                card.classList.add('warning');
                statusEl.classList.add('warning');
                statusEl.textContent = 'WARNING';
                hasWarning = true;
            } else {
                card.classList.add('nominal');
                statusEl.classList.add('nominal');
                statusEl.textContent = 'NOMINAL';
            }
        }

        // Update overall status
        const overallEl = document.getElementById('overall-status');
        const dotEl = document.querySelector('.status-dot');
        if (hasCritical) {
            overallEl.textContent = 'ANOMALY DETECTED';
            dotEl.className = 'status-dot critical';
        } else if (hasWarning) {
            overallEl.textContent = 'ADVISORY';
            dotEl.className = 'status-dot warning';
        } else {
            overallEl.textContent = 'ALL SYSTEMS NOMINAL';
            dotEl.className = 'status-dot nominal';
        }
    }

    // ── Countdown Updates ─────────────────────────────────────
    function updateCountdown(data) {
        const clockEl = document.getElementById('countdown-clock');
        if (!clockEl) return;

        if (data.display) {
            clockEl.textContent = data.display;
        }

        if (data.running === false) {
            clockEl.classList.add('hold');
        } else {
            clockEl.classList.remove('hold');
        }
    }

    // ── Event Feed ────────────────────────────────────────────
    function addEvent(source, message, level) {
        const feed = document.getElementById('event-feed');
        if (!feed) return;

        const entry = document.createElement('div');
        entry.className = 'feed-entry ' + (level || '');
        const now = new Date().toLocaleTimeString('en-US', { hour12: false });
        entry.innerHTML = `<span class="feed-time">${now}</span>[${source}] ${message}`;

        feed.insertBefore(entry, feed.firstChild);

        // Keep max 100 entries
        while (feed.children.length > 100) {
            feed.removeChild(feed.lastChild);
        }
    }

    // ── HTTP Polling Fallback ─────────────────────────────────
    function pollStatus() {
        fetch('/api/status')
            .then(r => r.json())
            .then(data => {
                updateServices(data.services || {}, data.chaos);
                updateCountdown(data.countdown || {});
            })
            .catch(() => { /* ignore */ });
    }

    // ── Countdown Controls ────────────────────────────────────
    window.countdownAction = function (action) {
        fetch(`/api/countdown/${action}`, { method: 'POST' })
            .then(() => pollStatus())
            .catch(e => console.error('Countdown action failed:', e));
    };

    window.setSpeed = function (speed) {
        fetch('/api/countdown/speed', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ speed: parseFloat(speed) }),
        })
            .then(() => pollStatus())
            .catch(e => console.error('Speed set failed:', e));
    };

    // ── Initialize ────────────────────────────────────────────
    connect();
    pollStatus();
    pollInterval = setInterval(pollStatus, 2000);
})();
