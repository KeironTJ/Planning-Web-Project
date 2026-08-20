/**
 * Factory Dashboards — Auth module
 *
 * Handles: auth/login.html — password visibility toggle
 */

'use strict';

document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('toggle-password');
    if (!btn) return;
    btn.addEventListener('click', function () {
        var field = document.getElementById('password-field');
        var icon  = document.getElementById('toggle-icon');
        if (!field) return;
        var isPassword = field.type === 'password';
        field.type = isPassword ? 'text' : 'password';
        icon.classList.toggle('bi-eye',      !isPassword);
        icon.classList.toggle('bi-eye-slash', isPassword);
    });
});
