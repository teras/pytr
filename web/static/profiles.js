// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// Profile system: selector, boot gate, preferences, favorites

let currentProfile = null;

const AVATAR_COLORS = [
    '#cc0000', '#e67e22', '#f1c40f', '#27ae60', '#2980b9', '#8e44ad',
    '#ffffff', '#b0b0b0', '#555555', '#222222', 'transparent', 'custom',
];
const AVATAR_EMOJIS = [
    '😊', '😎', '🤓', '🦊', '🐱', '🐶', '🐼', '🦁', '🐸', '🦉',
    '🌟', '🎵', '🎮', '🚀', '🌈', '🍕', '⚡', '🎨', '🏀', '🎸',
];
const _pickableColors = AVATAR_COLORS.filter(c => c !== 'transparent' && c !== 'custom');
function randomAvatar() {
    return {
        color: _pickableColors[Math.floor(Math.random() * _pickableColors.length)],
        emoji: AVATAR_EMOJIS[Math.floor(Math.random() * AVATAR_EMOJIS.length)],
    };
}
function emojiColor(bg) {
    if (!bg || bg === 'transparent') return '#fff';
    const hex = bg.replace('#', '');
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? '#000' : '#fff';
}
const isTouchDevice = () => 'ontouchstart' in window || navigator.maxTouchPoints > 0;
const _graphemeSegmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;


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
            profileOverlay.innerHTML = '';
            profileOverlay.classList.add('hidden');
            handleInitialRoute();
        } else if (data.state === 'profile-select') {
            const profiles = data.profiles;
            if (profiles.length === 1) {
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
    if (currentProfile.cookie_mode) {
        localStorage.setItem('cookieMode', currentProfile.cookie_mode);
    }
}

function updateProfileButton() {
    if (!currentProfile || !profileSwitcherBtn) return;
    const display = currentProfile.avatar_emoji || currentProfile.name.charAt(0).toUpperCase();
    profileSwitcherBtn.innerHTML = `<span class="avatar-base profile-avatar-small" style="background:${currentProfile.avatar_color};color:${emojiColor(currentProfile.avatar_color)}">${display}</span>`;
    profileSwitcherBtn.classList.remove('hidden');
}

// ── Profile Selector ───────────────────────────────────────────────────────

function showProfileSelector(profiles) {
    profileOverlay.innerHTML = `
        <div class="profile-selector">
            <h2>Who's watching?</h2>
            <div class="profile-cards">
                ${profiles.map(p => `
                    <div class="profile-card" data-id="${p.id}" data-has-pin="${p.has_pin}">
                        <div class="avatar-base profile-avatar" style="background:${p.avatar_color};color:${emojiColor(p.avatar_color)}">
                            ${p.avatar_emoji || escapeHtml(p.name.charAt(0).toUpperCase())}
                        </div>
                        <div class="profile-name">${escapeHtml(p.name)}</div>
                        ${p.has_pin ? '<div class="profile-pin-icon">PIN</div>' : ''}
                    </div>
                `).join('')}
            </div>
        </div>
    `;
    profileOverlay.classList.remove('hidden');

    // Card click handlers
    profileOverlay.querySelectorAll('.profile-card[data-id]').forEach(card => {
        card.addEventListener('click', () => {
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
        profileOverlay.innerHTML = '';
        profileOverlay.classList.add('hidden');
        stopPlayer();
        handleInitialRoute();
        return true;
    } catch(e) {
        return false;
    }
}

// ── Create Profile Forms ───────────────────────────────────────────────────

function buildAvatarPickerHtml(currentColor = null, currentEmoji = null) {
    const defaults = (!currentColor && !currentEmoji) ? randomAvatar() : null;
    const color = currentColor || (defaults ? defaults.color : AVATAR_COLORS[0]);
    const emoji = currentEmoji || (defaults ? defaults.emoji : AVATAR_EMOJIS[0]);
    const isCustomColor = color !== 'transparent' && color !== 'custom' && !AVATAR_COLORS.includes(color);
    const customValue = isCustomColor ? color : '#8e44ad';
    return `
        <div class="avatar-picker-wrap">
            <div class="avatar-preview-row">
                <div class="avatar-preview" id="avatar-preview" style="background:${color};color:${emojiColor(color)}" title="Click to change emoji">
                    ${emoji}
                </div>
                <input type="text" class="emoji-input" id="emoji-input" value="${emoji}" autocomplete="off">
            </div>
            <div class="emoji-picker-wrap hidden" id="emoji-picker-wrap"></div>
        </div>
        <div class="color-picker">
            ${AVATAR_COLORS.map(c => {
                if (c === 'custom') {
                    return `<label class="color-option color-option-custom${isCustomColor ? ' selected' : ''}">
                        <input type="radio" name="avatar_color" value="${customValue}" ${isCustomColor ? 'checked' : ''}>
                        <span class="color-swatch-custom"${isCustomColor ? ` style="background:${customValue}"` : ''}></span>
                    </label>`;
                }
                return `<label class="color-option${c === color ? ' selected' : ''}">
                    <input type="radio" name="avatar_color" value="${c}" ${c === color ? 'checked' : ''}>
                    <span class="color-swatch" style="background:${c}"></span>
                </label>`;
            }).join('')}
        </div>
        <input type="hidden" name="avatar_emoji" value="${emoji}">
    `;
}

function showCreateFirstProfile() {
    profileOverlay.innerHTML = `
        <div class="profile-selector">
            <h2>Welcome to PYTR</h2>
            <p class="wizard-subtitle">Create your admin profile to get started</p>
            <form id="create-first-profile-form" class="profile-form">
                <input type="text" id="new-profile-name" placeholder="Name" maxlength="30" required autofocus>
                <input type="password" id="setup-pw" placeholder="App password" required autocomplete="new-password">
                <input type="password" id="setup-pw-confirm" placeholder="Confirm password" required autocomplete="new-password">
                <p class="pin-error hidden" id="setup-pw-error"></p>
                ${buildAvatarPickerHtml()}
                <input type="password" id="new-profile-pin" placeholder="4-digit PIN (optional)" maxlength="4" pattern="[0-9]*" inputmode="numeric">
                <button type="submit">Start</button>
            </form>
        </div>
    `;
    profileOverlay.classList.remove('hidden');
    attachFirstRunFormListeners('create-first-profile-form');
}

function showEditProfileForm() {
    if (!currentProfile) return;

    const hasPin = currentProfile.has_pin;
    const hasSB = typeof buildSponsorBlockSettings === 'function';
    const modal = document.createElement('div');
    modal.className = 'pin-modal';
    modal.innerHTML = `
        <div class="pin-modal-content" style="max-width:380px">
            <div class="edit-profile-tabs">
                <button type="button" class="edit-tab active" data-tab="profile">Profile</button>
                ${hasSB ? '<button type="button" class="edit-tab" data-tab="sponsorblock">SponsorBlock</button>' : ''}
            </div>
            <div class="edit-tab-panel" data-panel="profile">
                <form id="edit-profile-form" class="profile-form">
                    <input type="text" id="edit-profile-name" placeholder="Name" maxlength="30" value="${escapeAttr(currentProfile.name)}" required>
                    <p class="pin-error hidden" id="edit-profile-error"></p>
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
            ${hasSB ? `<div class="edit-tab-panel hidden" data-panel="sponsorblock">
                ${buildSponsorBlockSettings()}
                <div class="pin-actions">
                    <button type="button" class="pin-cancel">Close</button>
                </div>
            </div>` : ''}
        </div>
    `;
    document.body.appendChild(modal);
    attachAvatarPickerListeners('edit-profile-form');

    // Tab switching
    modal.querySelectorAll('.edit-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            modal.querySelectorAll('.edit-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            modal.querySelectorAll('.edit-tab-panel').forEach(p => p.classList.add('hidden'));
            modal.querySelector(`[data-panel="${tab.dataset.tab}"]`).classList.remove('hidden');
        });
    });

    if (hasSB) attachSponsorBlockSettingsListeners(modal);

    const pinToggle = modal.querySelector('#edit-pin-toggle');
    const pinInput = modal.querySelector('#edit-pin-input');
    pinToggle.addEventListener('change', () => {
        pinInput.classList.toggle('hidden', !pinToggle.checked);
        if (!pinToggle.checked) pinInput.value = '';
    });

    const editForm = modal.querySelector('#edit-profile-form');

    modal.querySelectorAll('.pin-cancel').forEach(btn => {
        btn.addEventListener('click', () => {
            if (editForm._cleanupEmojiListener) editForm._cleanupEmojiListener();
            modal.remove();
        });
    });

    editForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const form = e.target;
        const errorEl = modal.querySelector('#edit-profile-error');
        errorEl.classList.add('hidden');
        const newName = modal.querySelector('#edit-profile-name').value.trim();
        const color = form.querySelector('input[name="avatar_color"]:checked').value;
        const emoji = form.querySelector('input[name="avatar_emoji"]').value;

        const body = { avatar_color: color, avatar_emoji: emoji };
        if (newName && newName !== currentProfile.name) body.name = newName;

        // PIN logic: absent = no change, null = remove, string = set
        const wantsPin = pinToggle.checked;
        const newPin = pinInput.value.trim();
        if (!wantsPin && hasPin) {
            body.pin = null;
        } else if (wantsPin && newPin) {
            if (newPin.length !== 4 || !/^\d+$/.test(newPin)) {
                errorEl.textContent = 'PIN must be exactly 4 digits';
                errorEl.classList.remove('hidden');
                return;
            }
            body.pin = newPin;
        }

        const resp = await fetch('/api/profiles/edit', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            errorEl.textContent = err.detail || 'Failed to update profile';
            errorEl.classList.remove('hidden');
            return;
        }
        currentProfile = await resp.json();
        updateProfileButton();

        if (form._cleanupEmojiListener) form._cleanupEmojiListener();
        modal.remove();
    });
}


function attachAvatarPickerListeners(formId) {
    const form = document.getElementById(formId);
    const preview = form.querySelector('#avatar-preview');
    const emojiInput = form.querySelector('#emoji-input');
    const emojiHidden = form.querySelector('input[name="avatar_emoji"]');
    const pickerWrap = form.querySelector('#emoji-picker-wrap');

    function updatePreview() {
        const emoji = emojiHidden.value || AVATAR_EMOJIS[0];
        const color = form.querySelector('input[name="avatar_color"]:checked').value;
        if (preview) {
            preview.textContent = emoji;
            preview.style.background = color;
            preview.style.color = emojiColor(color);
        }
    }

    function selectEmoji(emoji) {
        emojiHidden.value = emoji;
        emojiInput.value = emoji;
        updatePreview();
        if (pickerWrap) pickerWrap.classList.add('hidden');
    }

    if (preview) {
        if (isTouchDevice()) {
            // Mobile: use native emoji keyboard
            preview.addEventListener('click', () => {
                emojiInput.value = '';
                emojiInput.focus();
            });
        } else {
            // Desktop: use emoji-picker-element
            preview.addEventListener('click', (e) => {
                e.stopPropagation();
                if (pickerWrap) {
                    if (pickerWrap.classList.contains('hidden')) {
                        // Lazy-create the picker on first open
                        if (!pickerWrap.querySelector('emoji-picker')) {
                            const picker = document.createElement('emoji-picker');
                            picker.classList.add('dark');
                            pickerWrap.appendChild(picker);
                            picker.addEventListener('emoji-click', (ev) => {
                                selectEmoji(ev.detail.unicode);
                            });
                        }
                        pickerWrap.classList.remove('hidden');
                    } else {
                        pickerWrap.classList.add('hidden');
                    }
                }
            });
        }
    }

    if (pickerWrap) {
        pickerWrap.addEventListener('click', (e) => e.stopPropagation());
    }

    if (emojiInput) {
        emojiInput.addEventListener('input', () => {
            const segments = _graphemeSegmenter ? [..._graphemeSegmenter.segment(emojiInput.value)] : [{ segment: emojiInput.value.charAt(0) }];
            if (segments.length > 0) {
                selectEmoji(segments[0].segment);
                emojiInput.blur();
            }
        });
    }

    const closePopup = () => { if (pickerWrap) pickerWrap.classList.add('hidden'); };
    document.addEventListener('click', closePopup);
    form._cleanupEmojiListener = () => document.removeEventListener('click', closePopup);

    form.querySelectorAll('.color-option').forEach(opt => {
        opt.addEventListener('click', () => {
            if (opt.classList.contains('color-option-custom')) return; // handled below
            form.querySelectorAll('.color-option').forEach(o => o.classList.remove('selected'));
            opt.classList.add('selected');
            opt.querySelector('input[type="radio"]').checked = true;
            updatePreview();
        });
    });

    // Custom color picker popup
    const customOpt = form.querySelector('.color-option-custom');
    if (customOpt) {
        const radioInput = customOpt.querySelector('input[type="radio"]');
        const customSwatch = customOpt.querySelector('.color-swatch-custom');

        customSwatch.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            form.querySelectorAll('.color-option').forEach(o => o.classList.remove('selected'));
            customOpt.classList.add('selected');
            radioInput.checked = true;

            // Toggle popup
            let popup = form.querySelector('.color-picker-popup');
            if (popup) {
                popup.remove();
                return;
            }
            popup = document.createElement('div');
            popup.className = 'color-picker-popup';
            popup.addEventListener('click', (ev) => ev.stopPropagation());
            const picker = document.createElement('hex-color-picker');
            picker.setAttribute('color', radioInput.value);
            popup.appendChild(picker);
            const doneBtn = document.createElement('button');
            doneBtn.type = 'button';
            doneBtn.className = 'color-picker-done';
            doneBtn.textContent = 'Done';
            doneBtn.addEventListener('click', () => popup.remove());
            popup.appendChild(doneBtn);
            form.querySelector('.color-picker').appendChild(popup);

            picker.addEventListener('color-changed', (ev) => {
                const hex = ev.detail.value;
                radioInput.value = hex;
                customSwatch.style.background = hex;
                updatePreview();
            });
        });

        // Close color popup on outside click
        const closeColorPopup = () => {
            const popup = form.querySelector('.color-picker-popup');
            if (popup) popup.remove();
        };
        const origCleanup = form._cleanupEmojiListener;
        document.addEventListener('click', closeColorPopup);
        form._cleanupEmojiListener = () => {
            if (origCleanup) origCleanup();
            document.removeEventListener('click', closeColorPopup);
        };
    }
}

function attachFirstRunFormListeners(formId) {
    attachAvatarPickerListeners(formId);
    const form = document.getElementById(formId);

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = form.querySelector('#new-profile-name').value.trim();
        if (!name) return;
        const color = form.querySelector('input[name="avatar_color"]:checked').value;
        const emoji = form.querySelector('input[name="avatar_emoji"]').value;
        const pin = form.querySelector('#new-profile-pin').value || null;
        if (pin && pin.length !== 4) return;

        const pwInput = form.querySelector('#setup-pw');
        const confirmInput = form.querySelector('#setup-pw-confirm');
        const errorEl = form.querySelector('#setup-pw-error');
        const pw = pwInput.value;
        const confirmValue = confirmInput.value;
        if (pw.length < 4) {
            errorEl.textContent = 'Password must be at least 4 characters';
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
        errorEl.classList.add('hidden');

        if (form._cleanupEmojiListener) form._cleanupEmojiListener();

        try {
            const resp = await fetch('/api/profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, pin, avatar_color: color, avatar_emoji: emoji, password: pw }),
            });
            if (resp.ok) {
                const profile = await resp.json();
                currentProfile = profile;
                applyProfilePrefs();
                updateProfileButton();
                profileOverlay.innerHTML = '';
                profileOverlay.classList.add('hidden');
                handleInitialRoute();
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
    profileSwitcherBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!profileMenu.classList.contains('hidden')) {
            profileMenu.classList.add('hidden');
            return;
        }
        const isAdmin = currentProfile && currentProfile.is_admin;

        // Fetch all profiles to show others for quick switching
        let otherProfiles = [];
        try {
            const resp = await fetch('/api/profiles');
            if (resp.ok) {
                const all = await resp.json();
                otherProfiles = all.filter(p => !currentProfile || p.id !== currentProfile.id);
            }
        } catch(e) {}

        const profileItems = otherProfiles.map(p => {
            const display = p.avatar_emoji || escapeHtml(p.name.charAt(0).toUpperCase());
            return `<div class="profile-menu-profile" data-id="${p.id}" data-has-pin="${p.has_pin}">
                <span class="avatar-base profile-menu-avatar" style="background:${p.avatar_color};color:${emojiColor(p.avatar_color)}">${display}</span>
                <span>${escapeHtml(p.name)}</span>
            </div>`;
        }).join('');

        const isTv = document.body.classList.contains('tv-nav-active');
        profileMenu.innerHTML = `
            ${profileItems}
            ${otherProfiles.length ? '<div class="profile-menu-divider"></div>' : ''}
            ${!isTv ? '<div class="profile-menu-item" data-action="edit-profile">Edit profile</div>' : ''}
            ${isAdmin && !isTv ? '<div class="profile-menu-item" data-action="settings">Options</div>' : ''}
            <div class="profile-menu-item" data-action="tv-mode">${document.body.classList.contains('tv-nav-active') ? 'Desktop Mode' : 'TV Mode'}</div>
            ${!isTv ? `<div class="cookie-toggle-row">
                <span class="cookie-toggle-label">Cookies</span>
                <div class="cookie-toggle-btns" data-action="cookie-toggle">
                    <button class="cookie-btn${getCookieMode() === 'off' ? ' active' : ''}" data-mode="off">Off</button>
                    <button class="cookie-btn${getCookieMode() === 'auto' ? ' active' : ''}" data-mode="auto">Auto</button>
                    <button class="cookie-btn${getCookieMode() === 'on' ? ' active' : ''}" data-mode="on">On</button>
                </div>
            </div>` : ''}
            <div class="profile-menu-divider"></div>
            <div class="profile-menu-item profile-menu-logout" data-action="logout">Logout ${escapeHtml(currentProfile.name)}</div>
        `;
        // Position below the button
        const rect = profileSwitcherBtn.getBoundingClientRect();
        profileMenu.style.top = (rect.bottom + 4) + 'px';
        profileMenu.style.right = (window.innerWidth - rect.right) + 'px';
        profileMenu.classList.remove('hidden');

        // Auto-focus first item in TV mode
        if (isTv && window._tv) {
            const firstItem = profileMenu.querySelector('.profile-menu-profile, .profile-menu-item');
            if (firstItem) window._tv.setFocus(firstItem);
        }

        // Profile switch click handlers
        profileMenu.querySelectorAll('.profile-menu-profile').forEach(item => {
            item.addEventListener('click', () => {
                profileMenu.classList.add('hidden');
                const id = parseInt(item.dataset.id);
                const hasPin = item.dataset.hasPin === 'true';
                if (hasPin) {
                    showMenuPinPrompt(id);
                } else {
                    stopPlayer();
                    selectProfile(id, null);
                }
            });
        });

        // Action menu items
        profileMenu.querySelectorAll('.profile-menu-item').forEach(item => {
            item.addEventListener('click', () => {
                profileMenu.classList.add('hidden');
                const action = item.dataset.action;
                if (action === 'edit-profile') {
                    showEditProfileForm();
                } else if (action === 'settings') {
                    showSettingsModal();
                } else if (action === 'tv-mode') {
                    if (typeof window.toggleTvMode === 'function') window.toggleTvMode();
                } else if (action === 'logout') {
                    window.location.href = '/logout';
                }
            });
        });

        // Cookie toggle buttons
        profileMenu.querySelectorAll('.cookie-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const mode = btn.dataset.mode;
                setCookieMode(mode);
                profileMenu.querySelectorAll('.cookie-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            });
        });
    });
}

function showMenuPinPrompt(profileId) {
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
    document.body.appendChild(modal);

    const input = modal.querySelector('.pin-input');
    const error = modal.querySelector('.pin-error');
    input.focus();

    const submit = async () => {
        const pin = input.value;
        if (pin.length !== 4) return;
        stopPlayer();
        const ok = await selectProfile(profileId, pin);
        if (ok) {
            modal.remove();
        } else {
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
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
}

// Menu closing handled by consolidated listener in app.js

// ── Settings Modal (admin) ──────────────────────────────────────────────────

async function showSettingsModal() {
    // Fetch current settings and profiles in parallel
    let allowEmbed = false;
    let allProfiles = [];
    try {
        const [settingsResp, profilesResp] = await Promise.all([
            fetch('/api/profiles/settings'),
            fetch('/api/profiles'),
        ]);
        if (settingsResp.ok) {
            const data = await settingsResp.json();
            allowEmbed = !!data.allow_embed;
        }
        if (profilesResp.ok) {
            allProfiles = await profilesResp.json();
        }
    } catch(e) {}

    const overlay = document.createElement('div');
    overlay.className = 'pin-modal';
    document.body.appendChild(overlay);

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });

    // ── Settings view ──
    function showSettingsView() {
        function renderProfileList() {
            return allProfiles.map(p => {
                const display = p.avatar_emoji || escapeHtml(p.name.charAt(0).toUpperCase());
                const deleteBtn = !p.is_admin ? `<button type="button" class="settings-profile-delete" data-id="${p.id}" title="Delete">×</button>` : '';
                return `<div class="settings-profile-row">
                    <span class="avatar-base profile-menu-avatar" style="background:${p.avatar_color};color:${emojiColor(p.avatar_color)}">${display}</span>
                    <span class="settings-profile-name">${escapeHtml(p.name)}${p.is_admin ? ' <span class="settings-hint">(admin)</span>' : ''}</span>
                    ${deleteBtn}
                </div>`;
            }).join('');
        }

        overlay.innerHTML = `
            <div class="pin-modal-content" style="max-width:400px">
                <h3>Options</h3>
                <form id="settings-form" class="profile-form">
                    <div class="settings-profiles-header">
                        <label class="settings-label">Profiles</label>
                        <button type="button" id="settings-add-profile" class="settings-add-profile-btn" title="Add profile">+</button>
                    </div>
                    <div id="settings-profile-list" class="settings-profile-list">
                        ${renderProfileList()}
                    </div>

                    <div class="profile-menu-divider" style="margin:8px 0"></div>

                    <label class="settings-label">App Password</label>
                    <input type="password" id="settings-password" placeholder="New password (leave empty to keep)" minlength="4" autocomplete="new-password">

                    <label class="settings-label" style="margin-top:16px">
                        <input type="checkbox" id="settings-allow-embed" ${allowEmbed ? 'checked' : ''}>
                        Allow embed access
                        <span class="settings-hint">(no auth required, for LibRedirect)</span>
                    </label>

                    <div class="pin-actions">
                        <button type="button" class="pin-cancel">Cancel</button>
                        <button type="submit">Save</button>
                    </div>
                </form>
            </div>
        `;

        const form = overlay.querySelector('#settings-form');
        const profileListEl = overlay.querySelector('#settings-profile-list');

        function attachProfileDeleteHandlers() {
            profileListEl.querySelectorAll('.settings-profile-delete').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const id = parseInt(btn.dataset.id);
                    const row = btn.closest('.settings-profile-row');
                    const name = row.querySelector('.settings-profile-name').textContent.trim();
                    if (await nativeConfirm(`Delete profile "${name}"?`)) {
                        await fetch(`/api/profiles/profile/${id}`, { method: 'DELETE' });
                        const resp = await fetch('/api/profiles');
                        if (resp.ok) {
                            allProfiles = await resp.json();
                            profileListEl.innerHTML = renderProfileList();
                            attachProfileDeleteHandlers();
                        }
                    }
                });
            });
        }
        attachProfileDeleteHandlers();

        overlay.querySelector('#settings-add-profile').addEventListener('click', () => showAddProfileView());
        overlay.querySelector('.pin-cancel').addEventListener('click', () => overlay.remove());

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const newPw = overlay.querySelector('#settings-password').value;
            const embedToggle = overlay.querySelector('#settings-allow-embed');

            if (newPw) {
                if (newPw.length < 4) {
                    nativeAlert('Password must be at least 4 characters');
                    return;
                }
                const resp = await fetch('/api/profiles/settings/password', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: newPw }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    nativeAlert(err.detail || 'Failed to update password');
                    return;
                }
            }

            const newEmbed = embedToggle.checked;
            if (newEmbed !== allowEmbed) {
                const resp = await fetch('/api/profiles/settings/allow-embed', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ allow_embed: newEmbed }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    nativeAlert(err.detail || 'Failed to update embed setting');
                    return;
                }
            }

            overlay.remove();
        });
    }

    // ── Add profile view ──
    function showAddProfileView() {
        overlay.innerHTML = `
            <div class="pin-modal-content" style="max-width:380px">
                <h3>New Profile</h3>
                <form id="settings-create-profile-form" class="profile-form">
                    <input type="text" id="new-profile-name" placeholder="Name" maxlength="30" required autofocus>
                    ${buildAvatarPickerHtml()}
                    <input type="password" id="new-profile-pin" placeholder="4-digit PIN (optional)" maxlength="4" pattern="[0-9]*" inputmode="numeric">
                    <div class="pin-actions">
                        <button type="button" class="pin-cancel">Cancel</button>
                        <button type="submit">Create</button>
                    </div>
                </form>
            </div>
        `;
        attachAvatarPickerListeners('settings-create-profile-form');

        const createForm = overlay.querySelector('#settings-create-profile-form');

        overlay.querySelector('.pin-cancel').addEventListener('click', () => {
            if (createForm._cleanupEmojiListener) createForm._cleanupEmojiListener();
            showSettingsView();
        });

        createForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = e.target;
            const name = form.querySelector('#new-profile-name').value.trim();
            if (!name) return;
            const color = form.querySelector('input[name="avatar_color"]:checked').value;
            const emoji = form.querySelector('input[name="avatar_emoji"]').value;
            const pin = form.querySelector('#new-profile-pin').value || null;
            if (pin && pin.length !== 4) return;

            if (form._cleanupEmojiListener) form._cleanupEmojiListener();

            try {
                const resp = await fetch('/api/profiles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, pin, avatar_color: color, avatar_emoji: emoji }),
                });
                if (resp.ok) {
                    const listResp = await fetch('/api/profiles');
                    if (listResp.ok) allProfiles = await listResp.json();
                    showSettingsView();
                } else {
                    const err = await resp.json();
                    nativeAlert(err.detail || 'Failed to create profile');
                }
            } catch(e) {
                nativeAlert('Failed to create profile');
            }
        });
    }

    showSettingsView();
}

// ── Cookie Mode ───────────────────────────────────────────────────────────

function getCookieMode() {
    return localStorage.getItem('cookieMode') || 'auto';
}

function setCookieMode(mode) {
    if (!['off', 'auto', 'on'].includes(mode)) mode = 'auto';
    localStorage.setItem('cookieMode', mode);
    savePreference('cookie_mode', mode);
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

function _getActiveQueue() {
    return typeof window._getQueue === 'function' ? window._getQueue() : null;
}

async function checkFavoriteStatus(videoId) {
    if (!currentProfile) return;
    const queue = _getActiveQueue();
    const checkId = queue ? queue.playlistId : videoId;
    try {
        const resp = await fetch(`/api/profiles/favorites/${encodeURIComponent(checkId)}/status`);
        if (resp.ok) {
            const data = await resp.json();
            updateFavoriteButton(data.is_favorite);
        }
    } catch(e) {}
}

function updateFavoriteButton(isFavorite) {
    const btn = document.getElementById('favorite-btn');
    if (!btn) return;
    const queue = _getActiveQueue();
    const label = queue ? (isFavorite ? '★ Playlist Saved' : '☆ Save Playlist') : (isFavorite ? '★ Saved' : '☆ Save');
    btn.dataset.favorited = isFavorite ? 'true' : 'false';
    btn.textContent = label;
    btn.classList.toggle('favorited', isFavorite);
}

async function toggleFavorite() {
    if (!currentProfile || !currentVideoId) return;
    const btn = document.getElementById('favorite-btn');
    if (!btn) return;

    const queue = _getActiveQueue();
    const isFav = btn.dataset.favorited === 'true';

    if (queue) {
        // Queue mode: save/remove the playlist/mix
        const favId = queue.playlistId;
        if (isFav) {
            await fetch(`/api/profiles/favorites/${encodeURIComponent(favId)}`, { method: 'DELETE' });
            updateFavoriteButton(false);
        } else {
            const itemType = favId.startsWith('RD') ? 'mix' : 'playlist';
            const firstVideoId = (queue.videos[0] && queue.videos[0].id) || currentVideoId;
            const thumbnail = `https://img.youtube.com/vi/${firstVideoId}/hqdefault.jpg`;
            await fetch(`/api/profiles/favorites/${encodeURIComponent(favId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: queue.title || '',
                    channel: videoChannel.textContent || '',
                    thumbnail,
                    item_type: itemType,
                    playlist_id: favId,
                    first_video_id: firstVideoId,
                    video_count: String(queue.videos.length),
                }),
            });
            updateFavoriteButton(true);
        }
    } else {
        // Regular video mode
        if (isFav) {
            await fetch(`/api/profiles/favorites/${currentVideoId}`, { method: 'DELETE' });
            updateFavoriteButton(false);
        } else {
            const title = videoTitle.textContent || '';
            const channel = videoChannel.textContent || '';
            const thumbnail = videoPlayer.poster || `https://img.youtube.com/vi/${currentVideoId}/hqdefault.jpg`;
            const duration = parseInt(videoPlayer.dataset.expectedDuration) || 0;
            const body = { title, channel, thumbnail, duration, duration_str: formatDuration(duration) };
            if (isLiveStream) body.item_type = 'live';
            await fetch(`/api/profiles/favorites/${currentVideoId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            updateFavoriteButton(true);
        }
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
                duration_str: formatDuration(duration),
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
                return;
            }
        }
        // No saved position — try SponsorBlock highlight as fallback
        if (typeof seekToHighlight === 'function') seekToHighlight();
    } catch(e) {}
}
