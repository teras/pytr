// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// Profile system: selector, boot gate, preferences, favorites

let currentProfile = null;

const AVATAR_COLORS = [
    '#cc0000', '#e67e22', '#f1c40f', '#27ae60', '#2980b9', '#8e44ad',
    '#ffffff', '#b0b0b0', '#555555', '#222222', '#e84393', 'transparent',
];
const DEFAULT_EMOJI = '\ud83d\ude0a';
const isTouchDevice = () => 'ontouchstart' in window || navigator.maxTouchPoints > 0;
const _graphemeSegmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });

// Emoji palette for the desktop picker — grouped by category
const EMOJI_PALETTE = {
    'Smileys': [
        '\ud83d\ude00','\ud83d\ude03','\ud83d\ude04','\ud83d\ude01','\ud83d\ude0a','\ud83d\ude0d',
        '\ud83e\udd29','\ud83d\ude0e','\ud83e\udd13','\ud83e\udd17','\ud83e\udd73','\ud83d\ude0b',
        '\ud83d\ude1c','\ud83e\udd2a','\ud83d\ude08','\ud83d\udc7b','\ud83d\udc7e','\ud83e\udd16',
        '\ud83d\udc80','\ud83d\udca9','\ud83e\uddd9','\ud83e\udddb','\ud83e\uddd1\u200d\ud83c\udfa4','\ud83e\uddd1\u200d\ud83d\ude80',
    ],
    'Animals': [
        '\ud83d\udc31','\ud83d\udc36','\ud83d\udc3b','\ud83d\udc3c','\ud83e\udd8a','\ud83e\udd81',
        '\ud83d\udc2f','\ud83d\udc35','\ud83d\udc27','\ud83e\udd89','\ud83e\udd8b','\ud83d\udc22',
        '\ud83d\udc19','\ud83e\udd88','\ud83d\udc33','\ud83e\udd84','\ud83d\udc32','\ud83d\udc09',
    ],
    'Things': [
        '\ud83c\udfb8','\ud83c\udfae','\ud83c\udfa8','\ud83c\udfac','\ud83c\udfb5','\ud83d\udcda',
        '\ud83d\ude80','\ud83c\udfaf','\ud83c\udfc0','\u26bd','\ud83c\udfc4','\ud83c\udfbf',
        '\ud83d\udcf7','\ud83d\udd2d','\ud83c\udf54','\ud83c\udf55','\ud83c\udf70','\u2615',
    ],
    'Nature': [
        '\ud83c\udf1f','\ud83c\udf0a','\ud83c\udf3b','\ud83c\udf40','\ud83c\udf08','\ud83c\udf1e',
        '\ud83c\udf19','\u2b50','\ud83d\udd25','\u2744\ufe0f','\ud83c\udf3a','\ud83c\udf32',
        '\ud83c\udf35','\ud83c\udf44','\ud83c\udf3f','\ud83c\udf38',
    ],
};

const profileOverlay = document.getElementById('profile-overlay');
const profileSwitcherBtn = document.getElementById('profile-switcher-btn');

// ── Boot Gate ──────────────────────────────────────────────────────────────

async function checkProfile() {
    try {
        const resp = await fetch('/api/profiles/boot');
        if (!resp.ok) throw new Error('Boot failed');
        const data = await resp.json();

        if (data.state === 'login-required') {
            window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
            return;
        } else if (data.state === 'first-run') {
            showCreateFirstProfile();
        } else if (data.state === 'ready') {
            currentProfile = data.profile;
            applyProfilePrefs();
            updateProfileButton();
            profileOverlay.classList.add('hidden');
            handleInitialRoute();
        } else if (data.state === 'profile-select') {
            const profiles = data.profiles;
            if (profiles.length === 1 && !profiles[0].has_pin) {
                await selectProfile(profiles[0].id, null);
            } else {
                showProfileSelector(profiles);
            }
        }
    } catch (err) {
        console.error('Boot check failed:', err);
    }
}

function applyProfilePrefs() {
    if (!currentProfile) return;
    if (currentProfile.preferred_quality) {
        preferredQuality = currentProfile.preferred_quality;
        localStorage.setItem('preferredQuality', preferredQuality);
    }
    if (currentProfile.subtitle_lang) {
        localStorage.setItem('subtitle_lang', currentProfile.subtitle_lang);
    }
}

function updateProfileButton() {
    if (!currentProfile || !profileSwitcherBtn) return;
    const display = currentProfile.avatar_emoji || currentProfile.name.charAt(0).toUpperCase();
    profileSwitcherBtn.innerHTML = `<span class="profile-avatar-small" style="background:${currentProfile.avatar_color}">${display}</span>`;
    profileSwitcherBtn.classList.remove('hidden');
}

// ── Profile Selector ───────────────────────────────────────────────────────

function showProfileSelector(profiles) {
    const isAdmin = currentProfile && currentProfile.is_admin;
    profileOverlay.innerHTML = `
        <div class="profile-selector">
            <h2>Who's watching?</h2>
            <div class="profile-cards">
                ${profiles.map(p => `
                    <div class="profile-card" data-id="${p.id}" data-has-pin="${p.has_pin}">
                        <div class="profile-avatar" style="background:${p.avatar_color}">
                            ${p.avatar_emoji || escapeHtml(p.name.charAt(0).toUpperCase())}
                        </div>
                        <div class="profile-name">${escapeHtml(p.name)}</div>
                        ${p.has_pin ? '<div class="profile-pin-icon">PIN</div>' : ''}
                        ${isAdmin && !p.is_admin ? `<button class="profile-delete-btn" data-id="${p.id}" title="Delete profile">x</button>` : ''}
                    </div>
                `).join('')}
                ${isAdmin || profiles.length === 0 ? `
                    <div class="profile-card profile-add-card" id="profile-add-btn">
                        <div class="profile-avatar profile-avatar-add">+</div>
                        <div class="profile-name">Add</div>
                    </div>
                ` : ''}
            </div>
        </div>
    `;
    profileOverlay.classList.remove('hidden');

    // Card click handlers
    profileOverlay.querySelectorAll('.profile-card[data-id]').forEach(card => {
        card.addEventListener('click', (e) => {
            if (e.target.closest('.profile-delete-btn')) return;
            const id = parseInt(card.dataset.id);
            const hasPin = card.dataset.hasPin === 'true';
            const isCurrentProfile = currentProfile && currentProfile.id === id;
            if (hasPin && !isCurrentProfile) {
                showPinPrompt(id);
            } else {
                selectProfile(id, null);
            }
        });
    });

    // Delete buttons
    profileOverlay.querySelectorAll('.profile-delete-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.id);
            const name = btn.closest('.profile-card').querySelector('.profile-name').textContent;
            if (await nativeConfirm(`Delete profile "${name}"?`)) {
                await fetch(`/api/profiles/profile/${id}`, { method: 'DELETE' });
                const resp = await fetch('/api/profiles');
                const updated = await resp.json();
                showProfileSelector(updated);
            }
        });
    });

    // Add button
    const addBtn = document.getElementById('profile-add-btn');
    if (addBtn) {
        addBtn.addEventListener('click', () => showCreateProfileForm());
    }
}

function showPinPrompt(profileId) {
    const card = profileOverlay.querySelector(`.profile-card[data-id="${profileId}"]`);
    if (!card) return;

    const existing = profileOverlay.querySelector('.pin-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.className = 'pin-modal';
    modal.innerHTML = `
        <div class="pin-modal-content">
            <h3>Enter PIN</h3>
            <input type="password" class="pin-input" maxlength="4" pattern="[0-9]*" inputmode="numeric" autofocus>
            <p class="pin-error hidden">Wrong PIN</p>
            <div class="pin-actions">
                <button class="pin-cancel">Cancel</button>
                <button class="pin-submit">OK</button>
            </div>
        </div>
    `;
    profileOverlay.querySelector('.profile-selector').appendChild(modal);

    const input = modal.querySelector('.pin-input');
    const error = modal.querySelector('.pin-error');
    input.focus();

    const submit = async () => {
        const pin = input.value;
        if (pin.length !== 4) return;
        const ok = await selectProfile(profileId, pin);
        if (!ok) {
            error.classList.remove('hidden');
            input.value = '';
            input.focus();
        }
    };

    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') submit();
    });
    modal.querySelector('.pin-submit').addEventListener('click', submit);
    modal.querySelector('.pin-cancel').addEventListener('click', () => modal.remove());
}

async function selectProfile(id, pin) {
    try {
        const resp = await fetch(`/api/profiles/select/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin }),
        });
        if (!resp.ok) return false;
        const data = await resp.json();
        currentProfile = data.profile;
        applyProfilePrefs();
        updateProfileButton();
        profileOverlay.classList.add('hidden');
        // Always go to home (watch history) on profile select/switch
        stopPlayer();
        history.replaceState({ view: 'history' }, '', '/');
        showListView();
        loadHistory();
        return true;
    } catch {
        return false;
    }
}

// ── Create Profile Forms ───────────────────────────────────────────────────

function buildEmojiPickerPopupHtml() {
    let html = '<div class="emoji-picker-popup hidden" id="emoji-picker-popup">';
    for (const [category, emojis] of Object.entries(EMOJI_PALETTE)) {
        html += `<div class="emoji-category-label">${category}</div>`;
        html += '<div class="emoji-grid">';
        for (const e of emojis) {
            html += `<span class="emoji-cell" data-emoji="${e}">${e}</span>`;
        }
        html += '</div>';
    }
    html += '</div>';
    return html;
}

function buildAvatarPickerHtml(currentColor = null, currentEmoji = null) {
    const color = currentColor || AVATAR_COLORS[0];
    const emoji = currentEmoji || DEFAULT_EMOJI;
    return `
        <div class="avatar-picker-wrap">
            <div class="avatar-preview-row">
                <div class="avatar-preview" id="avatar-preview" style="background:${color}" title="Click to change emoji">
                    ${emoji}
                </div>
                <input type="text" class="emoji-input" id="emoji-input" value="${emoji}" autocomplete="off">
            </div>
            ${buildEmojiPickerPopupHtml()}
        </div>
        <div class="color-picker">
            ${AVATAR_COLORS.map(c => `
                <label class="color-option${c === color ? ' selected' : ''}">
                    <input type="radio" name="avatar_color" value="${c}" ${c === color ? 'checked' : ''}>
                    <span class="color-swatch" style="background:${c}"></span>
                </label>
            `).join('')}
        </div>
        <input type="hidden" name="avatar_emoji" value="${emoji}">
    `;
}

function showCreateFirstProfile() {
    profileOverlay.innerHTML = `
        <div class="profile-selector">
            <h2>Welcome to YTP</h2>
            <p class="wizard-subtitle">Create your admin profile to get started</p>
            <form id="create-first-profile-form" class="profile-form">
                <input type="text" id="new-profile-name" placeholder="Name" maxlength="20" pattern="[a-zA-Z0-9_]+" title="Letters, numbers and underscores only" required autofocus>
                ${buildAvatarPickerHtml()}
                <input type="password" id="new-profile-pin" placeholder="4-digit PIN (optional)" maxlength="4" pattern="[0-9]*" inputmode="numeric">
                <button type="submit">Next</button>
            </form>
        </div>
    `;
    profileOverlay.classList.remove('hidden');
    attachCreateFormListeners('create-first-profile-form', true);
}

function showSetupPassword(profile) {
    profileOverlay.innerHTML = `
        <div class="profile-selector">
            <h2>Set App Password</h2>
            <p class="wizard-subtitle">This password protects access to YTP</p>
            <form id="setup-password-form" class="profile-form">
                <input type="password" id="setup-pw" placeholder="Password" required autofocus autocomplete="new-password">
                <input type="password" id="setup-pw-confirm" placeholder="Confirm password" required autocomplete="new-password">
                <p class="pin-error hidden" id="setup-pw-error"></p>
                <button type="submit">Finish Setup</button>
            </form>
        </div>
    `;
    profileOverlay.classList.remove('hidden');

    const form = document.getElementById('setup-password-form');
    const pwInput = document.getElementById('setup-pw');
    const confirmInput = document.getElementById('setup-pw-confirm');
    const errorEl = document.getElementById('setup-pw-error');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const pw = pwInput.value;
        const confirmValue = confirmInput.value;

        if (pw.length < 1) {
            errorEl.textContent = 'Password is required';
            errorEl.classList.remove('hidden');
            return;
        }
        if (pw !== confirmValue) {
            errorEl.textContent = 'Passwords do not match';
            errorEl.classList.remove('hidden');
            confirmInput.value = '';
            confirmInput.focus();
            return;
        }

        try {
            const resp = await fetch('/api/profiles/settings/password', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pw }),
            });
            if (resp.ok) {
                // Select the profile and proceed
                await selectProfile(profile.id, null);
            } else {
                errorEl.textContent = 'Failed to save password';
                errorEl.classList.remove('hidden');
            }
        } catch {
            errorEl.textContent = 'Network error';
            errorEl.classList.remove('hidden');
        }
    });
}

function showCreateProfileForm() {
    const existing = profileOverlay.querySelector('.pin-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.className = 'pin-modal';
    modal.innerHTML = `
        <div class="pin-modal-content" style="max-width:380px">
            <h3>New Profile</h3>
            <form id="create-profile-form" class="profile-form">
                <input type="text" id="new-profile-name" placeholder="Name" maxlength="20" pattern="[a-zA-Z0-9_]+" title="Letters, numbers and underscores only" required autofocus>
                ${buildAvatarPickerHtml()}
                <input type="password" id="new-profile-pin" placeholder="4-digit PIN (optional)" maxlength="4" pattern="[0-9]*" inputmode="numeric">
                <div class="pin-actions">
                    <button type="button" class="pin-cancel">Cancel</button>
                    <button type="submit">Create</button>
                </div>
            </form>
        </div>
    `;
    profileOverlay.querySelector('.profile-selector').appendChild(modal);
    attachCreateFormListeners('create-profile-form');
    modal.querySelector('.pin-cancel').addEventListener('click', () => {
        const form = document.getElementById('create-profile-form');
        if (form && form._cleanupEmojiListener) form._cleanupEmojiListener();
        modal.remove();
    });
}

function showEditProfileForm() {
    if (!currentProfile) return;

    const hasPin = currentProfile.has_pin;
    const modal = document.createElement('div');
    modal.className = 'pin-modal';
    modal.innerHTML = `
        <div class="pin-modal-content" style="max-width:380px">
            <h3>Edit Profile</h3>
            <form id="edit-profile-form" class="profile-form">
                ${buildAvatarPickerHtml(currentProfile.avatar_color, currentProfile.avatar_emoji)}
                <div class="edit-pin-section">
                    <label class="edit-pin-label">
                        <input type="checkbox" id="edit-pin-toggle" ${hasPin ? 'checked' : ''}>
                        PIN lock
                    </label>
                    <input type="password" id="edit-pin-input" class="${hasPin ? '' : 'hidden'}" placeholder="${hasPin ? 'New PIN (leave empty to keep)' : '4-digit PIN'}" maxlength="4" pattern="[0-9]*" inputmode="numeric">
                </div>
                <div class="pin-actions">
                    <button type="button" class="pin-cancel">Cancel</button>
                    <button type="submit">Save</button>
                </div>
            </form>
        </div>
    `;
    document.body.appendChild(modal);
    attachAvatarPickerListeners('edit-profile-form');

    const pinToggle = modal.querySelector('#edit-pin-toggle');
    const pinInput = modal.querySelector('#edit-pin-input');
    pinToggle.addEventListener('change', () => {
        pinInput.classList.toggle('hidden', !pinToggle.checked);
        if (!pinToggle.checked) pinInput.value = '';
    });

    modal.querySelector('.pin-cancel').addEventListener('click', () => {
        const form = document.getElementById('edit-profile-form');
        if (form && form._cleanupEmojiListener) form._cleanupEmojiListener();
        modal.remove();
    });

    document.getElementById('edit-profile-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const form = e.target;
        const color = form.querySelector('input[name="avatar_color"]:checked').value;
        const emoji = form.querySelector('input[name="avatar_emoji"]').value;
        if (form._cleanupEmojiListener) form._cleanupEmojiListener();

        // Save avatar
        try {
            const resp = await fetch('/api/profiles/avatar', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ avatar_color: color, avatar_emoji: emoji }),
            });
            if (resp.ok) {
                currentProfile = await resp.json();
                updateProfileButton();
            }
        } catch {}

        // Save PIN changes
        const wantsPin = pinToggle.checked;
        const newPin = pinInput.value.trim();
        if (!wantsPin && hasPin) {
            // Remove PIN
            await fetch('/api/profiles/pin', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: null }) });
            currentProfile.has_pin = false;
        } else if (wantsPin && newPin) {
            if (newPin.length === 4 && /^\d+$/.test(newPin)) {
                await fetch('/api/profiles/pin', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: newPin }) });
                currentProfile.has_pin = true;
            }
        }

        modal.remove();
    });
}


function attachAvatarPickerListeners(formId) {
    const form = document.getElementById(formId);
    const preview = form.querySelector('#avatar-preview');
    const emojiInput = form.querySelector('#emoji-input');
    const emojiHidden = form.querySelector('input[name="avatar_emoji"]');
    const emojiPopup = form.querySelector('#emoji-picker-popup');

    function updatePreview() {
        const emoji = emojiHidden.value || DEFAULT_EMOJI;
        const color = form.querySelector('input[name="avatar_color"]:checked').value;
        if (preview) {
            preview.textContent = emoji;
            preview.style.background = color;
        }
    }

    function selectEmoji(emoji) {
        emojiHidden.value = emoji;
        emojiInput.value = emoji;
        updatePreview();
        if (emojiPopup) emojiPopup.classList.add('hidden');
    }

    if (preview) {
        if (isTouchDevice()) {
            preview.addEventListener('click', () => {
                emojiInput.value = '';
                emojiInput.focus();
            });
        } else {
            preview.addEventListener('click', (e) => {
                e.stopPropagation();
                if (emojiPopup) emojiPopup.classList.toggle('hidden');
            });
        }
    }

    if (emojiPopup) {
        emojiPopup.addEventListener('click', (e) => e.stopPropagation());
        emojiPopup.querySelectorAll('.emoji-cell').forEach(cell => {
            cell.addEventListener('click', () => selectEmoji(cell.dataset.emoji));
        });
    }

    if (emojiInput) {
        emojiInput.addEventListener('input', () => {
            const segments = [..._graphemeSegmenter.segment(emojiInput.value)];
            if (segments.length > 0) {
                selectEmoji(segments[0].segment);
                emojiInput.blur();
            }
        });
    }

    const closePopup = () => { if (emojiPopup) emojiPopup.classList.add('hidden'); };
    document.addEventListener('click', closePopup);
    form._cleanupEmojiListener = () => document.removeEventListener('click', closePopup);

    form.querySelectorAll('.color-option').forEach(opt => {
        opt.addEventListener('click', () => {
            form.querySelectorAll('.color-option').forEach(o => o.classList.remove('selected'));
            opt.classList.add('selected');
            opt.querySelector('input[type="radio"]').checked = true;
            updatePreview();
        });
    });
}

function attachCreateFormListeners(formId, isFirstRun = false) {
    attachAvatarPickerListeners(formId);
    const form = document.getElementById(formId);

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('new-profile-name').value.trim();
        if (!name) return;
        const color = form.querySelector('input[name="avatar_color"]:checked').value;
        const emoji = form.querySelector('input[name="avatar_emoji"]').value;
        const pin = document.getElementById('new-profile-pin').value || null;
        if (pin && pin.length !== 4) return;

        if (form._cleanupEmojiListener) form._cleanupEmojiListener();

        try {
            const resp = await fetch('/api/profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, pin, avatar_color: color, avatar_emoji: emoji }),
            });
            if (resp.ok) {
                const profile = await resp.json();
                if (isFirstRun) {
                    // First-run wizard: go to password setup step
                    showSetupPassword(profile);
                } else {
                    // Show updated profile list so user can choose
                    const listResp = await fetch('/api/profiles');
                    const profiles = await listResp.json();
                    showProfileSelector(profiles);
                }
            } else {
                const err = await resp.json();
                nativeAlert(err.detail || 'Failed to create profile');
            }
        } catch (err) {
            nativeAlert('Failed to create profile');
        }
    });
}

// ── Profile Switcher ───────────────────────────────────────────────────────

const profileMenu = document.createElement('div');
profileMenu.id = 'profile-menu';
profileMenu.className = 'profile-menu hidden';
document.body.appendChild(profileMenu);

if (profileSwitcherBtn) {
    profileSwitcherBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!profileMenu.classList.contains('hidden')) {
            profileMenu.classList.add('hidden');
            return;
        }
        const isAdmin = currentProfile && currentProfile.is_admin;
        profileMenu.innerHTML = `
            <div class="profile-menu-item" data-action="history">Watch History</div>
            <div class="profile-menu-item" data-action="favorites">Favorites</div>
            <div class="profile-menu-divider"></div>
            <div class="profile-menu-item" data-action="edit-profile">Edit Profile</div>
            ${isAdmin ? '<div class="profile-menu-item" data-action="settings">Settings</div>' : ''}
            <div class="profile-menu-divider"></div>
            <div class="profile-menu-item" data-action="switch">Switch Profile</div>
            <div class="profile-menu-item profile-menu-logout" data-action="logout">Logout ${escapeHtml(currentProfile.name)}</div>
        `;
        // Position below the button
        const rect = profileSwitcherBtn.getBoundingClientRect();
        profileMenu.style.top = (rect.bottom + 4) + 'px';
        profileMenu.style.right = (window.innerWidth - rect.right) + 'px';
        profileMenu.classList.remove('hidden');

        profileMenu.querySelectorAll('.profile-menu-item').forEach(item => {
            item.addEventListener('click', async () => {
                profileMenu.classList.add('hidden');
                const action = item.dataset.action;
                if (action === 'history') {
                    navigateToHistory();
                } else if (action === 'favorites') {
                    navigateToFavorites();
                } else if (action === 'edit-profile') {
                    showEditProfileForm();
                } else if (action === 'settings') {
                    showSettingsModal();
                } else if (action === 'switch') {
                    stopPlayer();
                    const resp = await fetch('/api/profiles');
                    const profiles = await resp.json();
                    showProfileSelector(profiles);
                } else if (action === 'logout') {
                    window.location.href = '/logout';
                }
            });
        });
    });
}

// Menu closing handled by consolidated listener in app.js

function navigateToHistory() {
    cacheListView();
    history.pushState({ view: 'history' }, '', '/history');
    showListView();
    loadHistory();
}

function navigateToFavorites() {
    cacheListView();
    history.pushState({ view: 'favorites' }, '', '/favorites');
    showListView();
    loadFavorites();
}

// ── Settings Modal (admin) ──────────────────────────────────────────────────

async function showSettingsModal() {
    // Fetch current settings
    let hasPassword = false;
    let cookiesBrowser = '';
    let allowEmbed = false;
    try {
        const resp = await fetch('/api/profiles/settings');
        if (resp.ok) {
            const data = await resp.json();
            hasPassword = data.has_password;
            cookiesBrowser = data.cookies_browser || '';
            allowEmbed = !!data.allow_embed;
        }
    } catch {}

    const overlay = document.createElement('div');
    overlay.className = 'pin-modal';
    overlay.innerHTML = `
        <div class="pin-modal-content" style="max-width:400px">
            <h3>Settings</h3>
            <form id="settings-form" class="profile-form">
                <label class="settings-label">App Password <span class="settings-hint">${hasPassword ? '(currently set)' : '(none)'}</span></label>
                <input type="password" id="settings-password" placeholder="${hasPassword ? 'New password (leave empty to keep)' : 'Set a password'}" autocomplete="new-password">

                <label class="settings-label" style="margin-top:16px">
                    <input type="checkbox" id="settings-allow-embed" ${allowEmbed ? 'checked' : ''}>
                    Allow embed access
                    <span class="settings-hint">(no auth required, for LibRedirect)</span>
                </label>

                <label class="settings-label" style="margin-top:16px">
                    Browser Cookies
                    <span class="settings-hint">for age-restricted videos</span>
                </label>
                <select id="settings-cookies-browser" class="settings-select">
                    <option value="">Disabled</option>
                    <option value="chrome" ${cookiesBrowser === 'chrome' ? 'selected' : ''}>Chrome</option>
                    <option value="firefox" ${cookiesBrowser === 'firefox' ? 'selected' : ''}>Firefox</option>
                    <option value="chromium" ${cookiesBrowser === 'chromium' ? 'selected' : ''}>Chromium</option>
                    <option value="brave" ${cookiesBrowser === 'brave' ? 'selected' : ''}>Brave</option>
                    <option value="edge" ${cookiesBrowser === 'edge' ? 'selected' : ''}>Edge</option>
                </select>

                <div class="pin-actions">
                    <button type="button" class="pin-cancel">Cancel</button>
                    <button type="submit">Save</button>
                </div>
            </form>
        </div>
    `;
    document.body.appendChild(overlay);

    const form = overlay.querySelector('#settings-form');
    const pwInput = overlay.querySelector('#settings-password');
    const cookiesSelect = overlay.querySelector('#settings-cookies-browser');
    const embedToggle = overlay.querySelector('#settings-allow-embed');

    overlay.querySelector('.pin-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const newPw = pwInput.value;
        const newCookies = cookiesSelect.value;

        // Save password if changed
        if (newPw) {
            await fetch('/api/profiles/settings/password', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: newPw }),
            });
        }

        // Save embed setting if changed
        const newEmbed = embedToggle.checked;
        if (newEmbed !== allowEmbed) {
            await fetch('/api/profiles/settings/allow-embed', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ allow_embed: newEmbed }),
            });
        }

        // Save cookies browser if changed
        if (newCookies !== cookiesBrowser) {
            await fetch('/api/profiles/settings/cookies-browser', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cookies_browser: newCookies || null }),
            });
        }

        overlay.remove();
    });
}

// ── Preference Saving ──────────────────────────────────────────────────────

function savePreference(key, value) {
    if (!currentProfile) return;
    const body = {};
    body[key] = value;
    fetch('/api/profiles/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).catch(() => {});
}

// ── Favorites ──────────────────────────────────────────────────────────────

async function checkFavoriteStatus(videoId) {
    if (!currentProfile) return;
    try {
        const resp = await fetch(`/api/profiles/favorites/${videoId}/status`);
        if (resp.ok) {
            const data = await resp.json();
            updateFavoriteButton(data.is_favorite);
        }
    } catch {}
}

function updateFavoriteButton(isFavorite) {
    const btn = document.getElementById('favorite-btn');
    if (!btn) return;
    btn.dataset.favorited = isFavorite ? 'true' : 'false';
    btn.textContent = isFavorite ? '★ Saved' : '☆ Save';
    btn.classList.toggle('favorited', isFavorite);
}

async function toggleFavorite() {
    if (!currentProfile || !currentVideoId) return;
    const btn = document.getElementById('favorite-btn');
    if (!btn) return;

    const isFav = btn.dataset.favorited === 'true';
    if (isFav) {
        await fetch(`/api/profiles/favorites/${currentVideoId}`, { method: 'DELETE' });
        updateFavoriteButton(false);
    } else {
        const title = videoTitle.textContent || '';
        const channel = videoChannel.textContent || '';
        const thumbnail = videoPlayer.poster || `https://img.youtube.com/vi/${currentVideoId}/hqdefault.jpg`;
        const duration = parseInt(videoPlayer.dataset.expectedDuration) || 0;
        await fetch(`/api/profiles/favorites/${currentVideoId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, channel, thumbnail, duration }),
        });
        updateFavoriteButton(true);
    }
}

// ── Position Save/Restore via API ──────────────────────────────────────────

async function savePositionToAPI() {
    if (!currentProfile || !currentVideoId || !videoPlayer.currentTime) return;
    const dur = videoPlayer.duration || 0;
    // Don't save if near the end
    if (dur > 0 && (videoPlayer.currentTime > dur - 30 || videoPlayer.currentTime / dur > 0.95)) {
        // Save position 0 to clear it
        fetch('/api/profiles/position', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_id: currentVideoId, position: 0 }),
        }).catch(() => {});
        return;
    }
    if (videoPlayer.currentTime > 5) {
        const title = videoTitle.textContent || '';
        const channel = videoChannel.textContent || '';
        const thumbnail = videoPlayer.poster || '';
        const duration = parseInt(videoPlayer.dataset.expectedDuration) || 0;
        fetch('/api/profiles/position', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_id: currentVideoId,
                position: parseFloat(videoPlayer.currentTime.toFixed(1)),
                title, channel, thumbnail, duration,
            }),
        }).catch(() => {});
    }
}

async function restorePositionFromAPI(videoId) {
    if (!currentProfile) return;
    try {
        const resp = await fetch(`/api/profiles/position/${videoId}`);
        if (resp.ok) {
            const data = await resp.json();
            if (data.position && data.position > 5) {
                videoPlayer.currentTime = data.position;
            }
        }
    } catch {}
}
