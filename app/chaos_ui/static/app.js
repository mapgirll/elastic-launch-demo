// NOVA-7 Chaos Controller UI
(function () {
    'use strict';

    let selectedChannel = null;
    let channelData = {};

    // ── localStorage session isolation ──────────────────────
    const LS_KEY = 'nova7_my_channels';

    function getMyChannels() {
        try {
            return JSON.parse(localStorage.getItem(LS_KEY)) || [];
        } catch { return []; }
    }

    function addMyChannel(ch) {
        const chs = getMyChannels();
        if (!chs.includes(ch)) {
            chs.push(ch);
            localStorage.setItem(LS_KEY, JSON.stringify(chs));
        }
    }

    function removeMyChannel(ch) {
        const chs = getMyChannels().filter(c => c !== ch);
        localStorage.setItem(LS_KEY, JSON.stringify(chs));
    }

    // ── Initialize ────────────────────────────────────────────
    function init() {
        fetchChannels();
        setInterval(fetchStatus, 2000);
        // Auto-populate email from X-Forwarded-User header
        fetch('/api/user/info')
            .then(r => r.json())
            .then(data => {
                if (data.email) {
                    document.getElementById('user-email').value = data.email;
                }
            })
            .catch(() => { /* ignore */ });
    }

    function fetchChannels() {
        fetch('/api/chaos/status')
            .then(r => r.json())
            .then(data => {
                channelData = data;
                populateDropdown(data);
                updateActiveChannels(data);
            })
            .catch(e => console.error('Failed to fetch channels:', e));
    }

    function fetchStatus() {
        fetch('/api/chaos/status')
            .then(r => r.json())
            .then(data => {
                channelData = data;
                updateActiveChannels(data);
                if (selectedChannel && data[selectedChannel]) {
                    updateChannelInfo(selectedChannel, data[selectedChannel]);
                }
                // Cleanup: remove channels from localStorage that are no longer ACTIVE
                const mine = getMyChannels();
                const stale = mine.filter(ch => !data[ch] || data[ch].state !== 'ACTIVE');
                stale.forEach(ch => removeMyChannel(ch));
            })
            .catch(() => { /* ignore */ });
    }

    function populateDropdown(data) {
        const select = document.getElementById('channel-select');
        // Clear existing options except first
        while (select.options.length > 1) select.remove(1);

        const sortedIds = Object.keys(data).map(Number).sort((a, b) => a - b);
        for (const id of sortedIds) {
            const ch = data[id];
            const opt = document.createElement('option');
            opt.value = id;
            const stateTag = ch.state === 'ACTIVE' ? ' [ACTIVE]' : '';
            opt.textContent = `CH-${String(id).padStart(2, '0')}: ${ch.name}${stateTag}`;
            select.appendChild(opt);
        }
    }

    // ── Channel Selection ─────────────────────────────────────
    window.selectChannel = function (value) {
        selectedChannel = value ? parseInt(value) : null;
        const infoEl = document.getElementById('channel-info');
        const btnInject = document.getElementById('btn-inject');
        const btnResolve = document.getElementById('btn-resolve');

        if (!selectedChannel || !channelData[selectedChannel]) {
            infoEl.classList.add('hidden');
            btnInject.disabled = true;
            btnResolve.disabled = true;
            return;
        }

        infoEl.classList.remove('hidden');
        updateChannelInfo(selectedChannel, channelData[selectedChannel]);
    };

    function updateChannelInfo(id, ch) {
        document.getElementById('info-channel').textContent = 'CH-' + String(id).padStart(2, '0');
        document.getElementById('info-name').textContent = ch.name;
        document.getElementById('info-subsystem').textContent = (ch.subsystem || '').toUpperCase();
        document.getElementById('info-section').textContent = (ch.vehicle_section || '').replace(/_/g, ' ').toUpperCase();
        document.getElementById('info-error-type').textContent = ch.error_type || '';
        document.getElementById('info-affected').textContent = (ch.affected_services || []).join(', ');
        document.getElementById('info-description').textContent = ch.description || '';

        const statusEl = document.getElementById('info-status');
        statusEl.textContent = ch.state;
        statusEl.style.color = ch.state === 'ACTIVE' ? '#ff0000' : '#00ff41';

        const btnInject = document.getElementById('btn-inject');
        const btnResolve = document.getElementById('btn-resolve');
        btnInject.disabled = ch.state === 'ACTIVE';
        btnResolve.disabled = ch.state !== 'ACTIVE';
    }

    // ── Trigger / Resolve ─────────────────────────────────────
    window.triggerFault = function () {
        if (!selectedChannel) return;
        const mode = document.querySelector('input[name="fault-mode"]:checked').value;
        const userEmail = document.getElementById('user-email').value.trim();
        const callbackUrl = window.location.origin;

        fetch('/api/chaos/trigger', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel: selectedChannel,
                mode: mode,
                se_name: channelData[selectedChannel]?.name || '',
                callback_url: callbackUrl,
                user_email: userEmail,
            }),
        })
            .then(r => r.json())
            .then(result => {
                if (result.status === 'triggered') addMyChannel(selectedChannel);
                fetchStatus();
            })
            .catch(e => console.error('Trigger failed:', e));
    };

    window.resolveFault = function () {
        if (!selectedChannel) return;

        fetch('/api/chaos/resolve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel: selectedChannel }),
        })
            .then(r => r.json())
            .then(result => {
                if (result.status === 'resolved') removeMyChannel(selectedChannel);
                fetchStatus();
            })
            .catch(e => console.error('Resolve failed:', e));
    };

    window.resolveChannel = function (channel) {
        fetch('/api/chaos/resolve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel: channel }),
        })
            .then(r => r.json())
            .then(result => {
                if (result.status === 'resolved') removeMyChannel(channel);
                fetchStatus();
            })
            .catch(e => console.error('Resolve failed:', e));
    };

    // ── Active Channels Display ───────────────────────────────
    function updateActiveChannels(data) {
        const container = document.getElementById('active-channels');
        const activeIds = Object.keys(data)
            .map(Number)
            .filter(id => data[id].state === 'ACTIVE')
            .sort((a, b) => a - b);

        if (activeIds.length === 0) {
            container.innerHTML = '<div class="no-active">No active faults</div>';
            return;
        }

        container.innerHTML = activeIds.map(id => {
            const ch = data[id];
            const elapsed = ch.triggered_at ? Math.round((Date.now() / 1000) - ch.triggered_at) : 0;
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            return `
                <div class="active-channel-card">
                    <div class="ac-header">
                        <span class="ac-channel">CH-${String(id).padStart(2, '0')}</span>
                        <span class="ac-time">${mins}m ${secs}s ago</span>
                    </div>
                    <div class="ac-name">${ch.name}</div>
                    <div class="ac-subsystem">${ch.subsystem} | ${(ch.affected_services || []).join(', ')}</div>
                    <button class="ac-resolve-btn" onclick="resolveChannel(${id})">RESOLVE</button>
                </div>
            `;
        }).join('');

        // Update dropdown to show active state
        populateDropdown(data);
    }

    // ── Start ─────────────────────────────────────────────────
    init();
})();
