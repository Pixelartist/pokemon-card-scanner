// Pokemon Card Scanner JavaScript

// Tab Navigation
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        // Remove active class from all tabs and content
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        
        // Add active class to clicked tab
        tab.classList.add('active');
        const tabId = tab.dataset.tab + '-tab';
        document.getElementById(tabId).classList.add('active');
    });
});

// Drop Zone
const dropZone = document.getElementById('drop-zone');
const imageInput = document.getElementById('image-input');
const preview = document.getElementById('preview');
const previewImg = document.querySelector('#preview img');
const clearBtn = document.getElementById('clear-btn');
const scanOptions = document.querySelector('.scan-options'); // class, not id
const identifyBtn = document.getElementById('identify-btn');

let currentImage = null;

// Click to browse
if (dropZone) {
    dropZone.addEventListener('click', () => imageInput.click());
}
if (imageInput) {
    imageInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });
}

// Drag and drop
if (dropZone) {
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    });
}

function handleFile(file) {
    if (!file.type.startsWith('image/')) {
        alert('Please upload an image file');
        return;
    }
    
    currentImage = file;
    const reader = new FileReader();
    reader.onload = (e) => {
        previewImg.src = e.target.result;
        preview.classList.remove('hidden');
        scanOptions.classList.remove('hidden');
        dropZone.classList.add('hidden');
    };
    reader.readAsDataURL(file);
}

clearBtn?.addEventListener('click', () => {
    currentImage = null;
    imageInput.value = '';
    preview?.classList.add('hidden');
    scanOptions?.classList.add('hidden');
    dropZone?.classList.remove('hidden');
    document.getElementById('scan-result').innerHTML = '';
});

// Identify card button
identifyBtn?.addEventListener('click', () => {
    if (currentImage) {
        scanBtn?.click();
    }
});

function getAuthToken() {
    return authToken || sessionStorage.getItem('access_token') || null;
}

// --- Session management (access token in memory + short-lived sessionStorage;
// --- durable login lives in the httpOnly refresh cookie the server sets) ---
let authToken = null;

function setAuthToken(token) {
    authToken = token || null;
    if (authToken) {
        sessionStorage.setItem('access_token', authToken);
    } else {
        sessionStorage.removeItem('access_token');
    }
}

// One-time migration: drop tokens persisted by the old localStorage scheme
localStorage.removeItem('auth_token');

let refreshInFlight = null;

async function refreshAccessToken() {
    // Deduplicate concurrent refreshes (several tabs' fetches can 401 at once)
    if (!refreshInFlight) {
        refreshInFlight = (async () => {
            try {
                const response = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'same-origin' });
                if (!response.ok) return false;
                const data = await response.json();
                setAuthToken(data.access_token);
                if (data.user?.username) {
                    localStorage.setItem('username', data.user.username);
                    updateUserInfo(data.user.username);
                }
                return true;
            } catch {
                return false;
            } finally {
                refreshInFlight = null;
            }
        })();
    }
    return refreshInFlight;
}

// Global fetch wrapper: attach the bearer token to API calls, pick up silently
// renewed tokens (X-Renew-Access-Token), and retry once via /refresh on 401.
const nativeFetch = window.fetch.bind(window);
window.fetch = async function (input, init = {}) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isApi = url.includes('/api/');
    const requestInit = { credentials: 'same-origin', ...init };

    if (isApi && !url.includes('/api/auth/')) {
        const token = getAuthToken();
        if (token) {
            const headers = new Headers(requestInit.headers || {});
            if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
            requestInit.headers = headers;
        }
    }

    let response = await nativeFetch(input, requestInit);

    const renewed = response.headers.get('X-Renew-Access-Token');
    if (renewed) setAuthToken(renewed);

    if (isApi && !url.includes('/api/auth/') && response.status === 401) {
        if (await refreshAccessToken()) {
            const token = getAuthToken();
            const headers = new Headers(requestInit.headers || {});
            if (token) headers.set('Authorization', `Bearer ${token}`);
            requestInit.headers = headers;
            response = await nativeFetch(input, requestInit);
        } else {
            setAuthToken(null);
        }
    }
    return response;
};

function requireAuth() {
    const token = getAuthToken();
    if (!token) {
        const modal = document.getElementById('login-modal');
        if (modal) {
            modal.classList.remove('hidden');
        }
        return false;
    }
    return token;
}

async function verifyAuth(token) {
    if (!token) return false;
    try {
        const response = await nativeFetch('/api/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        return response.ok;
    } catch (error) {
        return false;
    }
}

// Close modal handlers
document.querySelectorAll('[data-close="true"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
        const modal = e.target.closest('.modal');
        if (modal) {
            modal.classList.add('hidden');
        }
    });
});

// Close on outside click
document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.add('hidden');
        }
    });
});

// Auth tab switching
document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        const formType = tab.dataset.auth;
        document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(`${formType}-form`).classList.add('active');
    });
});

// Login form handler (Enter key and button click both submit the form)
async function handleLoginSubmit(e) {
    e.preventDefault();
    const username = document.getElementById('login-username')?.value?.trim();
    const password = document.getElementById('login-password')?.value;
    const errorEl = document.getElementById('login-error');
    const submitBtn = document.getElementById('login-btn');

    if (!username || !password) {
        if (errorEl) errorEl.textContent = 'Please fill in all fields';
        return;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });

        if (response.ok) {
            const data = await response.json();
            setAuthToken(data.access_token);
            const uname = data.user?.username || data.username;
            if (uname) localStorage.setItem('username', uname);
            updateUserInfo(uname);
            document.getElementById('login-modal')?.classList.add('hidden');
            document.getElementById('login-password').value = '';
            if (errorEl) errorEl.textContent = '';
            // Re-initialize app to load collection for logged-in user
            initializeApp();
        } else {
            const error = await response.json().catch(() => ({}));
            if (errorEl) errorEl.textContent = error.detail || 'Login failed';
        }
    } catch (error) {
        if (errorEl) errorEl.textContent = 'Network error';
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

document.getElementById('login-form')?.addEventListener('submit', handleLoginSubmit);

// Register form handler
async function handleRegisterSubmit(e) {
    e.preventDefault();
    const username = document.getElementById('register-username')?.value?.trim();
    const email = document.getElementById('register-email')?.value?.trim();
    const password = document.getElementById('register-password')?.value;
    const errorEl = document.getElementById('register-error');
    const submitBtn = document.getElementById('register-btn');

    if (!username || !email || !password) {
        if (errorEl) errorEl.textContent = 'Please fill in all fields';
        return;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
        const response = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, email, password })
        });

        if (response.ok) {
            const data = await response.json();
            setAuthToken(data.access_token);
            const uname = data.user?.username || data.username;
            if (uname) localStorage.setItem('username', uname);
            updateUserInfo(uname);
            document.getElementById('login-modal')?.classList.add('hidden');
            if (errorEl) errorEl.textContent = '';
            initializeApp();
        } else {
            const error = await response.json().catch(() => ({}));
            if (errorEl) errorEl.textContent = error.detail || 'Registration failed';
        }
    } catch (error) {
        if (errorEl) errorEl.textContent = 'Network error';
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

document.getElementById('register-form')?.addEventListener('submit', handleRegisterSubmit);

function updateUserInfo(username) {
    const statusEl = document.getElementById('user-status');
    if (statusEl && username) {
        statusEl.innerHTML = `<span class="status-dot"></span><span class="username">${username}</span>`;
    }
}



const scanBtn = document.getElementById('scan-btn');
if (scanBtn) {
    scanBtn.addEventListener('click', async function() {
        if (!currentImage) return;
        
        const resultDiv = document.getElementById('scan-result');
        resultDiv.innerHTML = '<div class="loading"><div class="spinner"></div><p>Identifying card...</p></div>';
        
        const formData = new FormData();
        formData.append('image', currentImage);
        
        const setHint = document.getElementById('set-hint').value;
        const region = document.getElementById('region').value;
        
        if (setHint) formData.append('set_hint', setHint);
        if (region) formData.append('region', region);
        
        try {
            const token = getAuthToken();
            const response = await fetch('/api/scan', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`
                },
                body: formData,
                signal: AbortSignal.timeout(60000) // 60 second timeout
            });

            if (!response.ok) {
                throw new Error(`Server responded with status ${response.status}`);
            }

            const data = await response.json();
            // Unwrap the API response envelope
            const result = data.data || data;
            displayScanResult(result, resultDiv);
        } catch (error) {
            if (error.name === 'AbortError') {
                resultDiv.innerHTML = '<p class="error">Scan timed out after 60 seconds. Please try again.</p>';
            } else if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
                resultDiv.innerHTML = '<p class="error">Server connection failed. Please check if the server is running and try again.</p>';
            } else {
                resultDiv.innerHTML = '<p class="error">Error scanning card: ' + error.message + '</p>';
            }
        }
    });
}

function displayScanResult(data, container) {
    // Normalize: API returns {data, meta} envelope on success, flat {error,...} on failure
    data = data.data || data;
    if (data.error) {
        let errorMsg = data.error;
        let actionButton = '';

        // Improve API key error messaging
        if (data.error.includes('No API key') || data.error.includes('401')) {
            errorMsg = 'API key is missing or invalid. Add a real Pokemon TCG API key to your .env file (get one free at https://pokemontcgapi.com/). The scanner will work in demo mode but cannot identify cards without a valid key.';
        } else if (data.error.includes('Invalid API key') || data.error.includes('403')) {
            errorMsg = 'Invalid API key. Get a valid key from https://pokemontcgapi.com/ and update your .env file.';
        } else if (data.error.includes('Email verification required')) {
            errorMsg = '📧 Email verification required!\n\nYour API key is valid but your account needs email verification to unlock 5 free trial card recognitions.\n\nPlease verify your email at: https://pokemontcgapi.com/account\n\nAfter verification, try scanning again!';
            actionButton = '<button class="btn btn-primary" onclick="window.open(\'https://pokemontcgapi.com/account\', \'_blank\')">Verify Email Now</button>';
        } else if (data.error.includes('Trial recognitions exhausted')) {
            errorMsg = '⚠️ Free trial exhausted\n\nYou have used all 5 free trial card recognitions. Card recognition continues from the Growth plan.\n\nUpgrade at: https://pokemontcgapi.com/pricing\n\nYour API key is valid and working - you just need to upgrade your plan.';
            actionButton = '<button class="btn btn-primary" onclick="window.open(\'https://pokemontcgapi.com/pricing\', \'_blank\')">View Pricing</button>';
        } else if (data.error.includes('VERIFY') || (data.solution === 'verify_email')) {
            errorMsg = 'Please verify your email at https://pokemontcgapi.com/account to unlock free card recognition.';
            actionButton = '<button class="btn btn-primary" onclick="window.open(\'https://pokemontcgapi.com/account\', \'_blank\')">Verify Email</button>';
        }
        container.innerHTML = `<div class="card-result"><h3>Error</h3><p>${errorMsg}</p>${actionButton}</div>`;
        return;
    }
    
    if (data.decision === 'no_match') {
        container.innerHTML = '<div class="card-result"><h3>No Match Found</h3><p>No matching card found in the database. Try a clearer image or specify the set.</p></div>';
        return;
    }
    
    let html = '';
    
    // Main match
    if (data.decision === 'match' && data.candidates.length > 0) {
        const card = data.candidates[0];
        html += `
            <div class="card-result">
                <h3>Card Identified</h3>
                <div class="card-main">
                    <div class="card-image">
                        <img src="${card.card?.images?.large || card.image_url || ''}" alt="${card.name}">
                    </div>
                    <div class="card-details">
                        <div class="card-name">${card.name}</div>
                        <div class="card-meta">
                            <span>${card.number || 'N/A'}</span>
                            <span>${card.rarity || 'N/A'}</span>
                            <span>${(card.set && card.set.name) || (card.set && card.set !== 'N/A') || 'Unknown Set'}</span>
                        </div>
                        <div class="card-actions">
                            <button class="btn btn-primary" onclick="addToCollection('${card.id}')">Add to Collection</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }
    
    // Candidates
    if (data.candidates.length > 1) {
        html += '<div class="candidates"><h4>Other Matches</h4><div class="candidate-list">';
        data.candidates.forEach(candidate => {
            html += `
                <div class="candidate" onclick="showCandidateDetail('${candidate.id}')">
                    <img src="${candidate.card?.images?.small || candidate.image_url || ''}" alt="${candidate.name}">
                    <div class="candidate-name">${candidate.name}</div>
                    <div class="candidate-number">${candidate.number || ''}</div>
                </div>
            `;
        });
        html += '</div></div>';
    }
    
    container.innerHTML = html;
}

async function addToCollection(cardId) {
    // Open add modal
    const modal = document.getElementById('add-modal');
    const modalBody = document.getElementById('add-modal-body');
    
    // Fetch card details
    const token = getAuthToken();
    const response = await fetch('/api/cards/' + cardId, {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });
    const card = await response.json();
    
    modalBody.innerHTML = `
        <h2>Add ${card.name} to Collection</h2>
        <form id="add-form" onsubmit="submitAddCollection(event)">
            <div class="form-group">
                <label>Condition</label>
                <select name="condition">
                    <option value="Mint">Mint</option>
                    <option value="Near Mint" selected>Near Mint</option>
                    <option value="Light Play">Light Play</option>
                    <option value="Moderate Play">Moderate Play</option>
                    <option value="Heavy Play">Heavy Play</option>
                    <option value="Damaged">Damaged</option>
                </select>
            </div>
            <div class="form-group">
                <label>Language</label>
                <select name="language">
                    <option value="EN" selected>English</option>
                    <option value="DE">German</option>
                    <option value="FR">French</option>
                    <option value="IT">Italian</option>
                    <option value="ES">Spanish</option>
                    <option value="JA">Japanese</option>
                    <option value="ZH">Chinese</option>
                </select>
            </div>
            <div class="form-group">
                <label>Quantity</label>
                <input type="number" name="quantity" value="1" min="1" max="99">
            </div>
            <div class="form-group">
                <label>Notes</label>
                <textarea name="notes" rows="3" placeholder="Optional notes..."></textarea>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" name="is_reverse_holo"> Reverse Holo
                </label>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" name="is_first_edition"> First Edition
                </label>
            </div>
            <input type="hidden" name="card_id" value="${cardId}">
            <button type="submit" class="btn btn-primary">Add to Collection</button>
        </form>
    `;
    
    modal.classList.remove('hidden');
}

async function submitAddCollection(e) {
    e.preventDefault();
    const form = e.target;
    const formData = new FormData(form);
    
    try {
        const token = getAuthToken();
        const response = await fetch('/api/collection/add', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            },
            body: formData
        });
        
        if (response.ok) {
            alert('Card added to collection!');
            document.getElementById('add-modal').classList.add('hidden');
            initBinder();
        } else {
            alert('Error adding card');
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

async function showCandidateDetail(cardId) {
    const token = getAuthToken();
    const response = await fetch('/api/cards/' + cardId, {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });
    const card = await response.json();
    
    const modal = document.getElementById('card-modal');
    const modalBody = document.getElementById('modal-body');
    
    modalBody.innerHTML = `
        <h2>${card.name}</h2>
        <div class="card-main" style="margin-top: 20px;">
            <div class="card-image">
                <img src="${card.images?.large || ''}" alt="${card.name}">
            </div>
            <div class="card-details">
                <div class="card-meta">
                    <span>Number: ${card.number || 'N/A'}</span>
                    <span>Rarity: ${card.rarity || 'N/A'}</span>
                    <span>Set: ${card.set_name || 'Unknown'}</span>
                </div>
                <div class="card-actions" style="margin-top: 20px;">
                    <button class="btn btn-primary" onclick="addToCollection('${card.id}')">Add to Collection</button>
                </div>
            </div>
        </div>
    `;
    
    modal.classList.remove('hidden');
}

// Modal close handlers
document.querySelectorAll('[data-close="true"]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));
    });
});

// Close modal on outside click
document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.add('hidden');
        }
    });
});

// Collection loading
async function loadCollection() {
    const container = document.getElementById('collection-grid');
    container.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading collection...</p></div>';

    try {
        const token = getAuthToken();
        console.log('[loadCollection] Token:', token ? 'present (' + token.length + ' chars)' : 'MISSING');

        if (!token) {
            throw new Error('No authentication token found. Please login first.');
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000); // 10s timeout

        const response = await fetch('/api/collection?limit=50', {
            headers: {
                'Authorization': `Bearer ${token}`
            },
            signal: controller.signal
        });
        clearTimeout(timeoutId);

        console.log('[loadCollection] Status:', response.status);

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP ${response.status}: ${errorText}`);
        }

        const data = await response.json();
        console.log('[loadCollection] Data:', data);

        if (!data.items) {
            throw new Error('Invalid response format: missing items array');
        }

        if (data.items.length === 0) {
            container.innerHTML = '<p>Your collection is empty. Scan some cards to get started!</p>';
            return;
        }

        container.innerHTML = data.items.map(item => `
            <div class="collection-item">
                <img src="${item.card?.images?.large || ''}" alt="${item.card?.name || 'Unknown'}">
                <div class="collection-item-info">
                    <div class="collection-item-name">${item.card?.name || 'Unknown'}</div>
                    <div class="collection-item-meta">
                        ${item.condition} • ${item.language} • Qty: ${item.quantity}
                    </div>
                    <div class="collection-item-actions">
                        <button class="btn btn-secondary btn-small" onclick="deleteItem(${item.id})">Delete</button>
                    </div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('[loadCollection] Error:', error);
        if (error.name === 'AbortError') {
            container.innerHTML = '<p>Error: Request timed out. Please check your connection.</p>';
        } else {
            container.innerHTML = '<p>Error loading collection: ' + error.message + '</p>';
        }
    }
}

async function deleteItem(itemId) {
    if (!confirm('Remove this card from your collection?')) return;
    
    try {
        const token = getAuthToken();
        await fetch('/api/collection/' + itemId, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        loadCollection();
    } catch (error) {
        alert('Error deleting item: ' + error.message);
    }
}

// Stats loading
async function loadStats() {
    const container = document.getElementById('stats-grid');
    
    try {
        const token = getAuthToken();
        const response = await fetch('/api/stats', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        const stats = await response.json();
        
        container.innerHTML = `
            <div class="stat-card">
                <div class="stat-number">${stats.total_items}</div>
                <div class="stat-label">Total Cards</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">${stats.unique_cards}</div>
                <div class="stat-label">Unique Cards</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">${stats.total_cards}</div>
                <div class="stat-label">Total Items</div>
            </div>
            <div class="stat-card" style="grid-column: span 2;">
                <div class="stat-label" style="margin-bottom: 15px;">Cards by Set</div>
                ${stats.by_set.map(s => `<div>${s.set}: ${s.count}</div>`).join('')}
            </div>
        `;
    } catch (error) {
        container.innerHTML = '<p>Error loading stats: ' + error.message + '</p>';
    }
}

// Initialize
async function initializeApp() {
    // Try the stored access token first; if missing/expired, the httpOnly
    // refresh cookie silently re-authenticates (survives server restarts).
    let token = getAuthToken();

    if (token) {
        const isValid = await verifyAuth(token);
        if (!isValid) {
            setAuthToken(null);
            token = null;
        }
    }

    if (!token) {
        token = (await refreshAccessToken()) ? getAuthToken() : null;
    }

    // Setup user status click handler (logout) — once, since initializeApp
    // also runs after login and would otherwise stack handlers
    const userStatus = document.getElementById('user-status');
    if (userStatus && !userStatus.dataset.handlerBound) {
        userStatus.dataset.handlerBound = '1';
        userStatus.style.cursor = 'pointer';
        userStatus.title = 'Currently signed in — click to sign out';
        userStatus.addEventListener('click', async () => {
            if (!getAuthToken()) {
                document.getElementById('login-modal')?.classList.remove('hidden');
                return;
            }
            if (!confirm('Sign out of your account?')) return;
            try {
                await fetch('/api/auth/logout', { method: 'POST' });
            } catch { /* offline logout still clears local state */ }
            setAuthToken(null);
            localStorage.removeItem('username');
            updateUserInfo('Guest');
            document.getElementById('login-modal')?.classList.remove('hidden');
        });
    }

    // If still no valid session, show login
    if (!token) {
        const modal = document.getElementById('login-modal');
        if (modal) {
            modal.classList.remove('hidden');
        }
        return;
    }

    // Show username in the header
    const savedName = localStorage.getItem('username');
    if (savedName) updateUserInfo(savedName);

    // Load collection on tab switch
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', async () => {
            // Verify token before loading collection
            const currentToken = getAuthToken();
            if (!currentToken) {
                const modal = document.getElementById('login-modal');
                if (modal) {
                    modal.classList.remove('hidden');
                }
                return;
            }

            if (tab.dataset.tab === 'collection') {
                await loadCollection();
            } else if (tab.dataset.tab === 'binder') {
                await initBinder();
            } else if (tab.dataset.tab === 'gallery') {
                await initGallery();
            } else if (tab.dataset.tab === 'stats') {
                await loadStats();
            }
        });
    });
}

// Camera functionality
let cameraStream = null;
let currentMode = 'upload'; // 'upload' or 'camera'

// Mode toggle buttons
document.getElementById('mode-upload').addEventListener('click', () => switchMode('upload'));
document.getElementById('mode-camera').addEventListener('click', () => switchMode('camera'));

function switchMode(mode) {
    currentMode = mode;
    
    // Update button states
    document.getElementById('mode-upload').classList.toggle('active', mode === 'upload');
    document.getElementById('mode-camera').classList.toggle('active', mode === 'camera');
    
    // Show/hide appropriate sections
    const dropZone = document.getElementById('drop-zone');
    const cameraSection = document.getElementById('camera-section');
    
    if (mode === 'upload') {
        dropZone.classList.remove('hidden');
        cameraSection.classList.add('hidden');
        stopCamera();
    } else {
        dropZone.classList.add('hidden');
        cameraSection.classList.remove('hidden');
        startCamera();
    }
}

async function startCamera() {
    const cameraSection = document.getElementById('camera-section');
    const video = document.getElementById('camera-preview');

    // Pre-check: verify mediaDevices is available
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        cameraSection.innerHTML = `
            <div class="camera-prompt">
                <p>📷 Camera API not available. Your browser does not support webcam access.</p>
                <button class="btn btn-secondary" onclick="switchMode('upload')">Use Upload Instead</button>
            </div>
        `;
        return;
    }

    try {
        // Request camera access
        cameraStream = await navigator.mediaDevices.getUserMedia({
            video: {
                facingMode: 'environment', // Use back camera on mobile
                width: { ideal: 1280 },
                height: { ideal: 720 }
            },
            audio: false
        });
        
        video.srcObject = cameraStream;
        video.play();

        // Show capture button
        document.getElementById('capture-btn').classList.remove('hidden');

        // Start auto-detection if enabled
        if (document.getElementById('auto-detect-toggle').checked) {
            setTimeout(() => {
                if (document.getElementById('auto-detect-toggle').checked) {
                    startAutoDetect();
                }
            }, 1000);
        }

    } catch (error) {
        console.error('Camera error:', error);
        let message = 'Could not access camera. ';

        // Check if mediaDevices is available first
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            message += 'Camera API not available. Your browser does not support getUserMedia. Use upload mode instead.';
        } else if (error.name === 'NotAllowed') {
            message += 'Please allow camera permission to scan cards.';
        } else if (error.name === 'NotFoundError') {
            message += 'No camera found on this device.';
        } else if (error.name === 'SecurityError' || error.message?.includes('SecureContext')) {
            message += 'Camera requires HTTPS or localhost. If accessing via IP address, use https:// or switch to upload mode.';
        } else {
            message += error.message || 'Please try uploading an image instead.';
        }
        
        cameraSection.innerHTML = `
            <div class="camera-prompt">
                <p>${message}</p>
                <button class="btn btn-secondary" onclick="switchMode('upload')">Use Upload Instead</button>
            </div>
        `;
    }
}

function stopCamera() {
    if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        cameraStream = null;
    }
}

// Capture photo from camera
document.getElementById('capture-btn')?.addEventListener('click', () => {
    const video = document.getElementById('camera-preview');
    const canvas = document.getElementById('camera-canvas');
    if (!video || !canvas) return;
    
    const ctx = canvas.getContext('2d');
    
    // Set canvas size to match video
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    
    // Draw video frame to canvas
    ctx.drawImage(video, 0, 0);
    
    // Convert canvas to blob
    canvas.toBlob((blob) => {
        if (blob) {
            currentImage = blob;
            
            // Show preview
            const reader = new FileReader();
            reader.onload = (e) => {
                const previewImg = document.querySelector('#preview img');
                const preview = document.getElementById('preview');
                if (previewImg) previewImg.src = e.target.result;
                if (preview) preview.classList.remove('hidden');
            };
            reader.readAsDataURL(blob);
            
            // Hide camera section
            const cameraSection = document.getElementById('camera-section');
            if (cameraSection) cameraSection.classList.add('hidden');
            
            // Stop camera
            stopCamera();
        }
    }, 'image/jpeg', 0.9);
});

// Stop camera button
document.getElementById('stop-camera-btn').addEventListener('click', () => {
    stopCamera();
    switchMode('upload');
});

// Auto-detect card functionality
let autoDetectInterval = null;
let isAutoDetecting = false;

// Start auto-detection when camera is active
function startAutoDetect() {
    if (isAutoDetecting) return;
    isAutoDetecting = true;
    
    autoDetectInterval = setInterval(async () => {
        if (!document.getElementById('auto-detect-toggle').checked) {
            stopAutoDetect();
            return;
        }

        const result = await detectCardInFrame();

        if (result && result.found && (result.confidence || result.score) > 0.6) {
            // Card detected with high confidence - stop and capture
            stopAutoDetect();
            setTimeout(() => capturePhoto(), 300);
        }
    }, 4000); // Detection takes ~20s on the NAS CPU; polling faster just queues work
}

function stopAutoDetect() {
    isAutoDetecting = false;
    if (autoDetectInterval) {
        clearInterval(autoDetectInterval);
        autoDetectInterval = null;
    }
    
    // Clear overlay
    const overlay = document.getElementById('detection-overlay');
    if (overlay) {
        const ctx = overlay.getContext('2d');
        ctx.clearRect(0, 0, overlay.width, overlay.height);
    }
    
    // Clear frame state
    const cardFrame = document.getElementById('card-frame');
    if (cardFrame) {
        cardFrame.classList.remove('active', 'detected', 'misaligned');
    }

    const statusElement = document.getElementById('detection-status');
    if (statusElement) {
        statusElement.textContent = 'Point camera at a card';
        statusElement.className = 'detection-status';
    }
    
    const debugSection = document.getElementById('detection-debug');
    if (debugSection) {
        debugSection.classList.add('hidden');
    }
}

// Toggle auto-detect
document.getElementById('auto-detect-toggle').addEventListener('change', (e) => {
    if (e.target.checked) {
        startAutoDetect();
    } else {
        stopAutoDetect();
    }
});

// Search functionality (only if elements exist)
const searchBtn = document.getElementById('search-btn');
const searchInput = document.getElementById('search-input');
if (searchBtn) {
    searchBtn.addEventListener('click', performSearch);
}
if (searchInput) {
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') performSearch();
    });
}

async function performSearch() {
    const query = document.getElementById('search-input').value.trim();
    const resultsDiv = document.getElementById('search-results');
    
    if (!query) {
        resultsDiv.innerHTML = '<p>Please enter a search term</p>';
        return;
    }
    
    resultsDiv.innerHTML = '<div class="loading"><div class="spinner"></div><p>Searching...</p></div>';
    
    try {
        const response = await fetch(`/api/collection/search?query=${encodeURIComponent(query)}`);
        const data = await response.json();
        
        if (data.results.length === 0) {
            resultsDiv.innerHTML = '<p>No cards found matching your search.</p>';
            return;
        }
        
        resultsDiv.innerHTML = data.results.map(item => `
            <div class="collection-item">
                <img src="${item.card?.images?.large || ''}" alt="${item.card?.name || 'Unknown'}">
                <div class="collection-item-info">
                    <div class="collection-item-name">${item.card?.name || 'Unknown'}</div>
                    <div class="collection-item-meta">
                        ${item.card?.set_name || ''} • ${item.card?.number || ''} • ${item.condition}
                    </div>
                    <div class="collection-item-actions">
                        <span>Qty: ${item.quantity}</span>
                    </div>
                </div>
            </div>
        `).join('');
        
    } catch (error) {
        resultsDiv.innerHTML = '<p>Error searching: ' + error.message + '</p>';
    }
}

// Export functionality
const exportCsvBtn = document.getElementById('export-csv');
if (exportCsvBtn) {
    exportCsvBtn.addEventListener('click', exportCollection);
}

async function exportCollection() {
    try {
        const token = getAuthToken();
        const response = await fetch('/api/collection/export?format=csv', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'pokemon_collection.csv';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    } catch (error) {
        alert('Export failed: ' + error.message);
    }
}

// Enhanced card detail display
async function showEnhancedCardDetail(cardId) {
    const token = getAuthToken();
    const response = await fetch('/api/cards/' + cardId + '/details', {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });
    const card = await response.json();
    
    const modal = document.getElementById('card-modal');
    const modalBody = document.getElementById('modal-body');
    
    // Extract set info
    const setData = card.basic_info?.set || {};
    const setCode = setData.code || card.basic_info?.set?.code || 'N/A';
    
    // Format types
    const types = (card.basic_info?.types || []).map(t => 
        `<span class="type-badge">${t}</span>`
    ).join('');
    
    // Format legalities
    const legalities = card.legalities || {};
    const legalBadges = Object.entries(legalities)
        .filter(([_, v]) => v === "Legal")
        .map(([k, _]) => `<span class="legal-badge">${k}</span>`)
        .join('');
    
    // Format prices if available
    let priceHtml = '';
    if (card.prices) {
        const prices = card.prices;
        priceHtml = `
            <div class="price-section">
                <h4>Market Prices</h4>
                <div class="price-table">
                    ${prices.tcgplayer?.marketPrice ? `
                        <div class="price-item">
                            <div class="price-label">TCGPlayer Market</div>
                            <div class="price-value">$${prices.tcgplayer.marketPrice}</div>
                        </div>
                    ` : ''}
                    ${prices.cardmarket?.avg30 ? `
                        <div class="price-item">
                            <div class="price-label">Cardmarket 30d Avg</div>
                            <div class="price-value">€${prices.cardmarket.avg30}</div>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }
    
    modalBody.innerHTML = `
        <h2>${card.basic_info.name}</h2>
        <div class="enhanced-card-grid">
            <div class="card-image">
                <img src="${card.basic_info.images?.large || ''}" alt="${card.basic_info.name}">
            </div>
            <div class="card-details">
                <div class="card-meta">
                    <span>Set: ${setData.name || 'Unknown'}</span>
                    <span>Number: ${card.basic_info.number || 'N/A'}</span>
                    <span>Rarity: ${card.basic_info.rarity || 'N/A'}</span>
                </div>
                <div class="card-types-badge">
                    ${types}
                </div>
                ${card.basic_info.hp ? `<div style="margin-top: 10px;">HP: ${card.basic_info.hp}</div>` : ''}
                ${card.basic_info.artist ? `<div style="color: var(--text-secondary); margin-top: 10px;">Artist: ${card.basic_info.artist}</div>` : ''}
                <div class="legal-badges">
                    <div style="color: var(--text-secondary); margin-bottom: 5px;">Legal in:</div>
                    ${legalBadges || '<span style="color: var(--text-secondary);">Not checked</span>'}
                </div>
                ${priceHtml}
                <div class="card-actions" style="margin-top: 20px;">
                    <button class="btn btn-primary" onclick="addToCollection('${cardId}')">Add to Collection</button>
                </div>
            </div>
        </div>
    `;
    
    modal.classList.remove('hidden');
}

// Alias used by binder/gallery clicks
async function showCardDetail(cardId, _el) {
    await showEnhancedCardDetail(cardId);
}

// Update displayScanResult to use enhanced view for top match
const originalDisplayScanResult = displayScanResult;
displayScanResult = async function(data, container) {
    // Normalize: API returns {data, meta} envelope on success, flat {error,...} on failure
    data = data.data || data;
    if (data.decision === 'match' && data.candidates.length > 0) {
        const card = data.candidates[0];
        const cardId = card.card?.id || card.id;
        
        // Fetch enhanced details
        try {
            const token = getAuthToken();
            const response = await fetch('/api/cards/' + cardId + '/details', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            const details = await response.json();
            const detailImg = details.basic_info.images?.large
                || details.basic_info.images?.small
                || card.image_url
                || card.card?.images?.large
                || '';
            
            container.innerHTML = `
                <div class="card-result">
                    <h3>Card Identified: ${details.basic_info.name}</h3>
                    <div class="enhanced-card-grid">
                        <div class="card-image">
                            <img src="${detailImg}" alt="${details.basic_info.name}">
                        </div>
                        <div class="card-details">
                            <div class="card-meta">
                                <span>${details.basic_info.number || 'N/A'}</span>
                                <span>${details.basic_info.rarity || 'N/A'}</span>
                                <span>${details.basic_info.set?.name || details.basic_info.set || 'Unknown Set'}</span>
                            </div>
                            <div class="card-types-badge">
                                ${(details.basic_info.types || []).map(t => `<span class="type-badge">${t}</span>`).join('')}
                            </div>
                            ${details.basic_info.hp ? `<div style="margin-top: 10px;">HP: ${details.basic_info.hp}</div>` : ''}
                            <div class="card-actions" style="margin-top: 20px;">
                                <button class="btn btn-primary" onclick="addToCollection('${cardId}')">Add to Collection</button>
                                <button class="btn btn-secondary" onclick="showEnhancedCardDetail('${cardId}')">View Details</button>
                            </div>
                        </div>
                    </div>
                    ${details.prices ? `
                        <div class="price-section">
                            <h4>Market Prices</h4>
                            <div class="price-table">
                                ${details.prices.tcgplayer?.marketPrice ? `
                                    <div class="price-item">
                                        <div class="price-label">TCGPlayer</div>
                                        <div class="price-value">$${details.prices.tcgplayer.marketPrice}</div>
                                    </div>
                                ` : ''}
                                ${details.prices.cardmarket?.avg30 ? `
                                    <div class="price-item">
                                        <div class="price-label">Cardmarket (30d)</div>
                                        <div class="price-value">€${details.prices.cardmarket.avg30}</div>
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    ` : ''}
                </div>
            `;
            
            // Show other candidates
            if (data.candidates.length > 1) {
                container.innerHTML += '<div class="candidates"><h4>Other Matches</h4><div class="candidate-list">';
                data.candidates.slice(1).forEach(candidate => {
                    container.innerHTML += `
                        <div class="candidate" onclick="showCandidateDetail('${candidate.id}')">
                            <img src="${candidate.card?.images?.small || candidate.image_url || ''}" alt="${candidate.name}">
                            <div class="candidate-name">${candidate.name}</div>
                            <div class="candidate-number">${candidate.number || ''}</div>
                        </div>
                    `;
                });
                container.innerHTML += '</div></div>';
            }
            
            return;
        } catch (error) {
            console.error('Error fetching enhanced details:', error);
        }
    }
    
    // Fall back to original display
    originalDisplayScanResult(data, container);
};


// Card detection functions
async function detectCardInFrame() {
    const video = document.getElementById('camera-preview');
    const canvas = document.getElementById('camera-canvas');
    const overlay = document.getElementById('detection-overlay');
    const debugSection = document.getElementById('detection-debug');
    const debugCanvas = document.getElementById('debug-canvas');
    const scoreElement = document.getElementById('detection-score');
    const statusElement = document.getElementById('detection-status');
    
    if (!video || !canvas || !overlay) return null;
    
    // Draw current video frame to canvas
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0);
    
    // Convert to blob for API call
    canvas.toBlob(async (blob) => {
        if (!blob) return;
        
        const formData = new FormData();
        formData.append('image', blob, 'frame.jpg');

        try {
            const response = await fetch('/api/detect', {
                method: 'POST',
                body: formData,
                signal: AbortSignal.timeout(120000) // 120 second timeout for slow CPU
            });
            
            const result = await response.json();
            
            if (result.found && result.bbox) {
                const [x, y, w, h] = result.bbox;
                const confidence = result.confidence || result.score || 0;
                
                // Clear overlay
                const overlayCtx = overlay.getContext('2d');
                overlay.width = video.videoWidth;
                overlay.height = video.videoHeight;
                overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
                
                // Draw bounding box
                overlayCtx.strokeStyle = confidence > 0.7 ? '#4ecca3' : confidence > 0.5 ? '#ffa500' : '#e94560';
                overlayCtx.lineWidth = 4;
                overlayCtx.strokeRect(x, y, w, h);
                
                // Draw corner markers
                const markerSize = 20;
                overlayCtx.lineWidth = 4;
                
                // Top-left
                overlayCtx.beginPath();
                overlayCtx.moveTo(x, y + markerSize);
                overlayCtx.lineTo(x, y);
                overlayCtx.lineTo(x + markerSize, y);
                overlayCtx.stroke();
                
                // Top-right
                overlayCtx.beginPath();
                overlayCtx.moveTo(x + w - markerSize, y);
                overlayCtx.lineTo(x + w, y);
                overlayCtx.lineTo(x + w, y + markerSize);
                overlayCtx.stroke();
                
                // Bottom-left
                overlayCtx.beginPath();
                overlayCtx.moveTo(x, y + h - markerSize);
                overlayCtx.lineTo(x, y + h);
                overlayCtx.lineTo(x + markerSize, y + h);
                overlayCtx.stroke();
                
                // Bottom-right
                overlayCtx.beginPath();
                overlayCtx.moveTo(x + w - markerSize, y + h);
                overlayCtx.lineTo(x + w, y + h);
                overlayCtx.lineTo(x + w, y + h - markerSize);
                overlayCtx.stroke();
                
                // Add confidence label
                overlayCtx.fillStyle = confidence > 0.7 ? '#4ecca3' : confidence > 0.5 ? '#ffa500' : '#e94560';
                overlayCtx.font = 'bold 24px Inter, sans-serif';
                overlayCtx.fillText(`${Math.round(confidence * 100)}%`, x, y - 10);
                
                // Update status
                if (statusElement) {
                    statusElement.textContent = `Card detected: ${Math.round(confidence * 100)}% confidence`;
                    statusElement.className = 'detection-status found';
                }
                
                // Update frame position to match detected card
                const cardFrame = document.getElementById('card-frame');
                if (cardFrame) {
                    const frameStyle = cardFrame.style;
                    const scaleX = 100 / result.image_width;
                    const scaleY = 100 / result.image_height;
                    const left = (x + w / 2) * scaleX;
                    const top = (y + h / 2) * scaleY;
                    const width = w * scaleX;
                    const height = h * scaleY;
                    
                    frameStyle.left = `${left}%`;
                    frameStyle.top = `${top}%`;
                    frameStyle.width = `${Math.min(width, 95)}%`;
                    frameStyle.height = `${Math.min(height, 95)}%`;
                    frameStyle.transform = 'translate(-50%, -50%)';
                    
                    cardFrame.classList.add('detected');
                    cardFrame.classList.remove('active', 'misaligned');
                }
                
                // Show debug info
                if (debugSection && result.debug_image) {
                    debugSection.classList.remove('hidden');
                    debugCanvas.src = 'data:image/png;base64,' + result.debug_image;
                    
                    if (scoreElement) {
                        scoreElement.textContent = `Detection Score: ${Math.round(confidence * 100)}%`;
                        scoreElement.className = 'detection-score ' + 
                            (confidence > 0.7 ? 'high' : confidence > 0.5 ? 'medium' : 'low');
                    }
                }
                
                return result;
            } else {
                // No card detected
                const overlayCtx = overlay.getContext('2d');
                overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
                
                // Update frame state - no card
                const cardFrame = document.getElementById('card-frame');
                if (cardFrame) {
                    cardFrame.classList.remove('detected');
                    cardFrame.classList.add('active');
                }

                if (statusElement) {
                    statusElement.textContent = 'Point camera at a card';
                    statusElement.className = 'detection-status detecting';
                }
                
                if (debugSection) {
                    debugSection.classList.add('hidden');
                }
            }
        } catch (err) {
            console.error('Detection error:', err);
        }
    }, 'image/jpeg', 0.9);
    
    return null;
}

// ============================================
// BINDER (PAGE FLIP) FUNCTIONALITY
// ============================================
let pageFlip = null;
let collectionData = [];
const CARDS_PER_PAGE = 4;

async function loadBinderData() {
    try {
        const token = getAuthToken();
        if (!token) return [];
        
        const response = await fetch('/api/collection?limit=200', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        
        if (!response.ok) return [];
        
        const data = await response.json();
        return data.items || data.data || data.collection || [];
    } catch (error) {
        console.error('Failed to load binder data:', error);
        return [];
    }
}

// Normalize a /api/collection item to flat fields for binder/gallery rendering
function normalizeCollectionItem(raw) {
    const c = raw.card || {};
    return {
        id: raw.id,
        card_id: raw.card_id,
        ptcg_id: c.ptcg_id || '',
        card_name: c.name || raw.card_name || 'Unknown',
        image_url: c.images?.large || c.images?.small || raw.scan_image_path || '',
        set_id: c.ptcg_id ? c.ptcg_id.split('-')[0] : '',
        set_info: c.set_name || '',
    };
}

function buildBinderPages(cards) {
    const container = document.getElementById('flipbook');
    container.innerHTML = '';
    
    // Cover page
    const coverPage = document.createElement('div');
    coverPage.className = 'page';
    coverPage.setAttribute('data-density', 'hard');
    coverPage.innerHTML = `
        <div class="page-content cover">
            <div class="cover-inner">
                <h2>My Collection</h2>
                <p>${cards.length} card${cards.length !== 1 ? 's' : ''} in binder</p>
                <button id="start-scanning" class="btn btn-primary" onclick="switchTab('scan')">Start Scanning</button>
            </div>
        </div>
    `;
    container.appendChild(coverPage);
    
    // Data pages (4 cards per page)
    for (let i = 0; i < cards.length; i += CARDS_PER_PAGE) {
        const pageCards = cards.slice(i, i + CARDS_PER_PAGE);
        const page = document.createElement('div');
        page.className = 'page';
        page.setAttribute('data-density', 'soft');
        
        let cardsHTML = '<div class="page-content">';
        pageCards.forEach(raw => {
            const card = normalizeCollectionItem(raw);
            const cardName = card.card_name;
            const imgUrl = card.image_url;
            const detailId = card.ptcg_id || card.card_id || card.id;
            cardsHTML += `
                <div class="binder-card" onclick="showCardDetail('${detailId}', this)">
                    <img src="${imgUrl}" alt="${cardName}" loading="lazy">
                    <div class="binder-card-overlay">${cardName}</div>
                </div>
            `;
        });
        
        // Fill empty slots
        for (let j = pageCards.length; j < CARDS_PER_PAGE; j++) {
            cardsHTML += '<div class="empty-page">Empty Slot</div>';
        }
        
        cardsHTML += '</div>';
        page.innerHTML = cardsHTML;
        container.appendChild(page);
    }
    
    // Add blank pages if needed
    const totalPages = 1 + Math.ceil(cards.length / CARDS_PER_PAGE);
    if (totalPages % 2 === 1) {
        const blankPage = document.createElement('div');
        blankPage.className = 'page';
        blankPage.setAttribute('data-density', 'soft');
        blankPage.innerHTML = '<div class="page-content"><div class="empty-page"></div></div>';
        container.appendChild(blankPage);
    }
    
    return totalPages;
}

async function initBinder() {
    const container = document.getElementById('flipbook');
    if (!container) return;
    
    // Destroy existing instance
    if (pageFlip) {
        pageFlip.destroy();
        pageFlip = null;
    }
    
    // Clear pages
    container.innerHTML = '<div class="page"><div class="loading"><div class="spinner"></div><p>Loading binder...</p></div></div>';
    
    // Load cards
    collectionData = await loadBinderData();
    
    if (collectionData.length === 0) {
        container.innerHTML = `
            <div class="page" data-density="hard">
                <div class="page-content cover">
                    <div class="cover-inner">
                        <h2>My Binder</h2>
                        <p>Your collection is empty</p>
                        <button class="btn btn-primary" onclick="switchTab('scan')">Scan Your First Card</button>
                    </div>
                </div>
            </div>
        `;
    } else {
        buildBinderPages(collectionData);
    }
    
    // Initialize StPageFlip
    const bookEl = document.getElementById('flipbook');
    const pages = bookEl.querySelectorAll('.page');
    
    // Calculate page size based on container
    const containerWidth = document.getElementById('book-container').offsetWidth - 60;
    const pageWidth = Math.min(containerWidth / 2, 320);
    const pageHeight = pageWidth * (3.5 / 2.5); // Pokemon card ratio
    
    pageFlip = new St.PageFlip(bookEl, {
        width: pageWidth,
        height: pageHeight,
        size: 'fixed',
        minWidth: 200,
        maxWidth: 400,
        minHeight: 280,
        maxHeight: 560,
        drawShadow: true,
        flippingTime: 800,
        usePortrait: false,
        startZIndex: 50,
        maxShadowOpacity: 0.5,
        showCover: true,
        mobileScrollSupport: true
    });
    
    pageFlip.loadFromHtml(pages);
    
    // Update page info
    updatePageInfo();
    
    // Bind navigation
    document.getElementById('prev-page')?.addEventListener('click', () => {
        pageFlip?.flipPrev();
    });
    
    document.getElementById('next-page')?.addEventListener('click', () => {
        pageFlip?.flipNext();
    });
    
    // Listen for page turn events
    pageFlip?.on('flip', (e) => {
        updatePageInfo();
    });
}

function updatePageInfo() {
    const pageInfo = document.getElementById('page-info');
    if (pageFlip && pageInfo) {
        const current = pageFlip.getCurrentPageIndex() + 1;
        const total = pageFlip.getPageCount();
        pageInfo.textContent = `Page ${current} of ${total}`;
    }
}

// ============================================
// GALLERY (INFINITE SCROLL) FUNCTIONALITY
// ============================================
let galleryPage = 1;
let galleryLoading = false;
let galleryHasMore = true;
let gallerySearchQuery = '';
let gallerySortOrder = 'newest';

async function loadGalleryPage() {
    if (galleryLoading || !galleryHasMore) return;
    
    galleryLoading = true;
    const grid = document.getElementById('gallery-grid');
    
    // Show loading indicator
    if (galleryPage === 1) {
        grid.innerHTML = '<div class="gallery-loading"><div class="spinner"></div><p>Loading cards...</p></div>';
    }
    
    try {
        const token = getAuthToken();
        if (!token) {
            grid.innerHTML = '<div class="gallery-loading"><p>Please login to view your gallery</p></div>';
            galleryLoading = false;
            return;
        }
        
        const limit = 20;
        const offset = (galleryPage - 1) * limit;
        
        const response = await fetch(`/api/collection?limit=${limit}&offset=${offset}&sort=${gallerySortOrder}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        
        if (!response.ok) {
            throw new Error('Failed to load gallery');
        }
        
        const data = await response.json();
        const cards = (data.items || data.data || data.collection || []).map(normalizeCollectionItem);
        
        // First page: clear the loading placeholder before inserting cards
        if (galleryPage === 1) {
            grid.innerHTML = '';
        }
        
        if (cards.length === 0 && galleryPage === 1) {
            grid.innerHTML = '<div class="gallery-end"><p>Your collection is empty. Start scanning cards!</p></div>';
            galleryHasMore = false;
        } else if (cards.length < limit) {
            galleryHasMore = false;
        }
        
        // Render cards
        cards.forEach(card => {
            const item = createGalleryItem(card);
            grid.appendChild(item);
        });
        
        galleryPage++;
        
    } catch (error) {
        console.error('Gallery load error:', error);
        if (galleryPage === 1) {
            grid.innerHTML = '<div class="gallery-loading"><p>Error loading gallery. Please try again.</p></div>';
        }
    } finally {
        galleryLoading = false;
    }
}

function createGalleryItem(card) {
    const item = document.createElement('div');
    item.className = 'gallery-item';
    
    const cardName = card.card_name || card.name || 'Unknown';
    const imgUrl = card.image_url || card.scanned_image_url || '';
    const setId = card.set_id || '';
    const setInfo = card.set_info || '';
    
    item.innerHTML = `
        <img src="${imgUrl}" alt="${cardName}" loading="lazy">
        <div class="gallery-item-info">
            <div class="gallery-item-name">${cardName}</div>
            <div class="gallery-item-meta">${setId || setInfo || 'Unknown Set'}</div>
        </div>
    `;
    
    item.addEventListener('click', () => {
        showCardDetail(card.ptcg_id || card.card_id || card.id, item);
    });
    
    return item;
}

function initGallery() {
    galleryPage = 1;
    galleryHasMore = true;
    galleryLoading = false;
    
    const grid = document.getElementById('gallery-grid');
    if (grid) {
        grid.innerHTML = '';
    }
    
    loadGalleryPage();
    
    // Setup infinite scroll
    window.removeEventListener('scroll', handleGalleryScroll);
    window.addEventListener('scroll', handleGalleryScroll);
    
    // Setup search
    const searchInput = document.getElementById('gallery-search');
    if (searchInput) {
        let debounceTimer;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                gallerySearchQuery = e.target.value.toLowerCase();
                resetGallery();
            }, 300);
        });
    }
    
    // Setup sort
    const sortSelect = document.getElementById('gallery-sort');
    if (sortSelect) {
        sortSelect.addEventListener('change', (e) => {
            gallerySortOrder = e.target.value;
            resetGallery();
        });
    }
}

function handleGalleryScroll() {
    if (galleryLoading || !galleryHasMore) return;
    
    const scrollBottom = window.innerHeight + window.scrollY;
    const docHeight = document.documentElement.scrollHeight;
    
    if (scrollBottom >= docHeight - 200) {
        loadGalleryPage();
    }
}

function resetGallery() {
    galleryPage = 1;
    galleryHasMore = true;
    const grid = document.getElementById('gallery-grid');
    if (grid) {
        grid.innerHTML = '';
    }
    loadGalleryPage();
}

// ============================================
// TAB NAVIGATION (Updated)
// ============================================
function switchTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    
    // Remove active from all buttons
    document.querySelectorAll('.tab').forEach(btn => {
        btn.classList.remove('active');
    });
    
    // Show selected tab
    const tabContent = document.getElementById(`${tabName}-tab`);
    if (tabContent) {
        tabContent.classList.add('active');
    }
    
    // Activate button
    const tabBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
    if (tabBtn) {
        tabBtn.classList.add('active');
    }
    
    // Handle tab-specific initialization
    if (tabName === 'binder') {
        initBinder();
    } else if (tabName === 'gallery') {
        initGallery();
    } else if (tabName === 'collection') {
        // Keep old collection functionality if needed
        loadCollection();
    }
}
// Initialize app on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('[App] DOMContentLoaded fired');
    try {
        initializeApp();
        console.log('[App] initializeApp completed');
    } catch(e) {
        console.error('[App] initializeApp error:', e);
    }
});
