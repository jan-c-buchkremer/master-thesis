import Visualization from './visualisation.js';

/**
 * Main application entry point.
 * Waits for the DOM to be fully loaded before initializing.
 */
document.addEventListener('DOMContentLoaded', function() {

    // 1. Define the selectors for the main SVG and tooltip elements.
    const svgSelector = "#visualization";
    const tooltipSelector = ".tooltip";

    // 2. Create the main visualization instance.
    const viz = new Visualization(svgSelector, tooltipSelector);

    // 3. Kick off the application by loading the docset index.
    // The Visualization class will handle all subsequent setup.
    viz.loadDocsetIndex();

});