'use strict';

document.addEventListener('DOMContentLoaded', function () {
    var printButton = document.getElementById('overviewPrint');
    if (printButton) {
        printButton.addEventListener('click', function () {
            window.print();
        });
    }
});
