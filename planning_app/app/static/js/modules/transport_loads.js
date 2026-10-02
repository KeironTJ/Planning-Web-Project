/**
 * Read-only transport snapshot. Page refresh does not trigger an Epicor sync.
 */
'use strict';

document.addEventListener('DOMContentLoaded', function () {
    var refresh = document.getElementById('loadAutoRefresh');
    if (refresh) {
        refresh.checked = sessionStorage.getItem('transportAutoRefresh') !== 'off';
        refresh.addEventListener('change', function () {
            sessionStorage.setItem('transportAutoRefresh', refresh.checked ? 'on' : 'off');
        });
        window.setInterval(function () {
            var editing = document.activeElement && document.activeElement.closest('.load-filters');
            if (refresh.checked && !document.hidden && !editing && !document.querySelector('details[open]')) {
                window.location.reload();
            }
        }, 60000);
    }

    var canvas = document.getElementById('loadShipTimeline');
    var dataElement = document.getElementById('loadTimelineData');
    if (!canvas || !dataElement) return;
    if (typeof Chart === 'undefined') {
        var message = document.createElement('p');
        message.className = 'text-danger small';
        message.textContent = 'The chart could not load. Use the calendar counts table below.';
        canvas.replaceWith(message);
        return;
    }
    var data = JSON.parse(dataElement.textContent);
    var chart;
    function renderChart() {
        var styles = getComputedStyle(document.documentElement);
        var colours = {
            planned: styles.getPropertyValue('--bs-secondary').trim(),
            scheduled: styles.getPropertyValue('--bs-primary').trim(),
            packed: styles.getPropertyValue('--bs-warning').trim(),
            shipped: styles.getPropertyValue('--bs-success').trim(),
            other: styles.getPropertyValue('--bs-danger').trim(),
        };
        var textColour = styles.getPropertyValue('--bs-body-color').trim();
        var gridColour = styles.getPropertyValue('--bs-border-color').trim();
        if (chart) chart.destroy();
        chart = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: data.labels,
                datasets: data.datasets.map(function (dataset) {
                    return { label: dataset.label, data: dataset.data, backgroundColor: colours[dataset.style], borderRadius: 3 };
                }),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { color: textColour, boxWidth: 12 } },
                    tooltip: { callbacks: { label: function (context) {
                        return context.dataset.label + ': ' + context.raw + (context.raw === 1 ? ' load' : ' loads');
                    }}},
                },
                scales: {
                    x: { stacked: true, ticks: { color: textColour }, grid: { display: false } },
                    y: { stacked: true, beginAtZero: true, ticks: { precision: 0, color: textColour },
                        grid: { color: gridColour }, title: { display: true, text: 'Loads', color: textColour } },
                },
            },
        });
    }
    renderChart();
    new MutationObserver(renderChart).observe(document.documentElement, {
        attributes: true, attributeFilter: ['data-bs-theme'],
    });
});
