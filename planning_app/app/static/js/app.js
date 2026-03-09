/**
 * Planning Hub — Application JavaScript
 *
 * Responsibilities:
 *  1. Dark/light theme toggle with localStorage persistence.
 *  2. Bootstrap tooltip initialisation.
 *  3. CSRF header injection for fetch() calls.
 *  4. Auto-dismiss alerts after a timeout.
 *  5. Confirm-before-submit for destructive forms.
 */

'use strict';

// -----------------------------------------------------------------------
// 1. Theme toggle
// -----------------------------------------------------------------------

const THEME_KEY = 'planning-theme';
const htmlRoot = document.getElementById('html-root');
const themeToggle = document.getElementById('theme-toggle');
const themeIcon = document.getElementById('theme-icon');

function applyTheme(theme) {
    htmlRoot.setAttribute('data-bs-theme', theme);
    if (themeIcon) {
        themeIcon.className = theme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-stars-fill';
    }
    try { localStorage.setItem(THEME_KEY, theme); } catch (_) {}
}

// Load saved theme on page load
(function initTheme() {
    let saved;
    try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
    // Default to OS preference if no saved preference
    if (!saved) {
        saved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    applyTheme(saved);
})();

if (themeToggle) {
    themeToggle.addEventListener('click', () => {
        const current = htmlRoot.getAttribute('data-bs-theme') || 'light';
        applyTheme(current === 'dark' ? 'light' : 'dark');
    });
}

// -----------------------------------------------------------------------
// 2. Bootstrap tooltips
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    const tooltipEls = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipEls.forEach(el => new bootstrap.Tooltip(el));
});

// -----------------------------------------------------------------------
// 3. CSRF token injection for fetch()
// -----------------------------------------------------------------------

/**
 * Read the CSRF token from the <meta> tag and attach it to all non-GET
 * fetch requests automatically.  Use this wrapper instead of raw fetch().
 *
 * @param {string} url
 * @param {RequestInit} options
 */
function planningFetch(url, options = {}) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
    const method = (options.method || 'GET').toUpperCase();
    if (csrfToken && method !== 'GET' && method !== 'HEAD') {
        options.headers = {
            'X-CSRFToken': csrfToken,
            'Content-Type': 'application/json',
            ...options.headers,
        };
    }
    return fetch(url, options);
}

// Expose globally so inline scripts can use it
window.planningFetch = planningFetch;

// -----------------------------------------------------------------------
// 4. Auto-dismiss alerts
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    const alerts = document.querySelectorAll('.alert.alert-dismissible');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            if (bsAlert) bsAlert.close();
        }, 6000); // 6 seconds
    });
});

// -----------------------------------------------------------------------
// 5. Numeric input formatting (comma-separated display)
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Add class "num-format" to inputs to get live comma formatting on blur
    document.querySelectorAll('input.num-format').forEach(input => {
        input.addEventListener('blur', () => {
            const val = parseFloat(input.value.replace(/,/g, ''));
            if (!isNaN(val)) {
                input.value = val.toLocaleString();
            }
        });
    });
});
