/**
 * Factory Dashboards — API helpers
 *
 * Responsibilities:
 *  1. CSRF-aware fetch() wrapper (planningFetch).
 *
 * Loaded on every page via base.html. All modules that make API calls
 * should use planningFetch() rather than raw fetch().
 */

'use strict';

// -----------------------------------------------------------------------
// 1. CSRF token injection for fetch()
// -----------------------------------------------------------------------

/**
 * Wraps fetch() with automatic CSRF token injection for non-GET requests.
 * Token is read from <meta name="csrf-token"> set in base.html.
 *
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<Response>}
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

// Expose globally so module scripts and legacy inline scripts can call it.
window.planningFetch = planningFetch;
