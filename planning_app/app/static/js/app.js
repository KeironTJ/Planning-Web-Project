/**
 * Factory Dashboards — Application JavaScript
 *
 * Responsibilities:
 *  1. Dark/light theme toggle with localStorage persistence.
 *  2. Bootstrap tooltip initialisation.
 *  3. Auto-dismiss alerts after a timeout.
 *  4. Numeric input formatting.
 *  5. UTC → browser-local time formatting (fmtIso).
 *
 * CSRF fetch wrapper lives in api.js (loaded before this file).
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
// 2. Bootstrap tooltips + popovers
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
    document.querySelectorAll('[data-bs-toggle="popover"]').forEach(el => new bootstrap.Popover(el));
});

// -----------------------------------------------------------------------
// 3. Auto-dismiss alerts
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
// 4. Numeric input formatting (comma-separated display)
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('input.num-format').forEach(input => {
        input.addEventListener('blur', () => {
            const val = parseFloat(input.value.replace(/,/g, ''));
            if (!isNaN(val)) {
                input.value = val.toLocaleString();
            }
        });
    });
});

// -----------------------------------------------------------------------
// 5. UTC → browser-local time formatting
// -----------------------------------------------------------------------

/**
 * Format an ISO-8601 timestamp into a human-readable local-time string.
 * Appends 'Z' if the string has no timezone suffix so the browser treats
 * it as UTC rather than local time.
 *
 * @param {string} iso  - ISO-8601 string (with or without timezone)
 * @param {string} fmt  - 'date-only' | 'datetime-year' | '' (default: date + time)
 * @returns {string}
 */
function fmtIso(iso, fmt) {
    if (!iso) return '';
    const normalised = /Z$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z';
    const d = new Date(normalised);
    if (isNaN(d)) return iso;
    const pad = n => String(n).padStart(2, '0');
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const date = `${pad(d.getDate())} ${months[d.getMonth()]}`;
    const year = d.getFullYear();
    if (fmt === 'date-only') return `${date} ${year}`;
    if (fmt === 'datetime-year') return `${date} ${year} ${time}`;
    return `${date} ${time}`;
}

// Apply to every <time class="fmt-utc" data-utc="..."> element on the page.
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('time.fmt-utc[data-utc]').forEach(el => {
        el.textContent = fmtIso(el.dataset.utc, el.dataset.fmt);
    });
});

// Expose so module scripts can call it without reimplementing.
window.fmtIso = fmtIso;
