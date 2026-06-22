import { formatList } from './utils.js';
import TopicLandscapeView from './TopicLandscapeView.js';
import CitationNetworkView from './CitationNetworkView.js';
import EgoNetworkView from './EgoNetworkView.js';

/**
 * Manages the main visualization, including state, data processing,
 * D3 rendering, and user interactions.
 */
export default class Visualization {

    // --- Configuration Constants ---
    // VIS_WIDTH and VIS_HEIGHT are now dynamic, set in constructor
    margin = { top: 40, right: 40, bottom: 40, left: 40 };
    MAX_KEYWORDS = Infinity;
    MAX_AUTHORS = Infinity;
    MAX_ORGS = Infinity;
    MAX_JOURNALS = Infinity;
    MAX_KNN = 5;
    MAX_LINK_DISTANCE = 0.1; // For similarity links

    // Node and link styling constants
    BASE_STROKE_WIDTH_MAX = 3.0;
    BASE_STROKE_WIDTH_MIN = 0.5;
    BASE_HIGHLIGHT_STROKE = 2.5;
    BASE_NORMAL_STROKE = 1.5;
    BASE_SELECTION_STROKE = 2.5;

    // Zoom constraints
    minZoom = 0.5;
    maxZoom = 20.0;
    LOD_THRESHOLD = 0.0; // Zoom level below which details (links) are hidden

    /**
     * Creates a new Visualization instance.
     * @param {string} svgSelector - The CSS selector for the main SVG container.
     * @param {string} tooltipSelector - The CSS selector for the tooltip element.
     */
    constructor(svgSelector, tooltipSelector) {
        this.svgSelector = svgSelector; // Store selector for resizing

        // --- D3 and SVG Setup ---
        // Initial size based on container
        const container = d3.select(svgSelector).node();
        this.VIS_WIDTH = container.getBoundingClientRect().width;
        this.VIS_HEIGHT = container.getBoundingClientRect().height;

        this.svg = d3.select(svgSelector).append("svg")
            .attr("width", "100%")
            .attr("height", "100%")
            .attr("viewBox", `0 0 ${this.VIS_WIDTH} ${this.VIS_HEIGHT}`)
            .attr("preserveAspectRatio", "xMidYMid meet")
            .attr("id", "vis-svg-root");

        this.graphGroup = this.svg.append("g").attr("id", "vis-graph-group");
        this._setupLayers();

        this.tooltip = d3.select(tooltipSelector);

        this._injectStyles(); // [Performance] Inject CSS for hardware accelerated transitions
        this._setupDefs();
        this._setupZoom();

        // Quadtree for O(log n) interaction
        this.quadtree = null;

        // --- Web Worker ---
        this.dataWorker = null;

        // --- View Renderers ---
        this.topicLandscapeView = new TopicLandscapeView(this);
        this.citationNetworkView = new CitationNetworkView(this);
        this.egoNetworkView = new EgoNetworkView(this);

        // --- Application State ---
        this.ctrlKeyPressed = false;
        this.currentTransform = d3.zoomIdentity;
        this.selectedPapers = new Set();
        this.connectedPapers = new Set(); // Papers connected to selection
        this.hoverPaper = null;
        this.currentSearchTerm = "";
        this.savedSearchFilters = [];
        this.savedFilterConfigurations = []; // New state for saved full filter configurations
        this.activeAllowedIds = null; // New state for isolated subset (saved selection)
        this.currentLinkMode = 'similarity';
        this.ignoreFiltersForLinks = false; // New state for "Ignore Filters" checkbox
        this.currentNodeSizeMode = 'citations'; // 'citations', 'normalized', 'authority', 'hub'
        this.currentViewMode = 'topic-landscape';
        this.lastViewMode = 'topic-landscape'; // Track last view mode for zoom reset
        this.previousViewMode = null; // For returning from ego view
        this.activeEgoPaper = null; // The paper for the ego view
        this.citationDetailView = false;
        this.selectedClusterKeys = new Set();
        this.selectionContextNodes = null;

        // --- Data Storage ---
        this.allNodes = [];
        this.activeNodes = []; // [Performance] Stores currently filtered nodes (before culling)
        this.allNodesMap = new Map();
        this.allSimilarityLinks = [];
        this.allCitationLinks = [];
        this.allKnnLinks = new Map();
        this.layoutData = [];
        this.colorScale = null;
        this.xScale = null;
        this.yScale = null;
        this.radiusScale = null;
        this.normalizedRadiusScale = null;
        this.authorityRadiusScale = null;
        this.hubRadiusScale = null;
        this.strokeWidthScale = null;
        this.topicHierarchyRoot = null;
        this.citationScale = null; // Log scale for citation filter

        // Interaction Setup
        this._setupEventListeners();
        this._setupInfoCard();
        this._setupResizeListener(); // Add resize listener

        d3.select("body").on("keydown", this.handleKeyDown.bind(this));
        d3.select("body").on("keyup", this.handleKeyUp.bind(this));
    }

    _setupResizeListener() {
        window.addEventListener("resize", () => {
            const container = d3.select(this.svgSelector).node();
            if (!container) return;
            
            this.VIS_WIDTH = container.getBoundingClientRect().width;
            this.VIS_HEIGHT = container.getBoundingClientRect().height;

            this.svg.attr("viewBox", `0 0 ${this.VIS_WIDTH} ${this.VIS_HEIGHT}`);
            
            // Re-calculate scales if data is loaded
            if (this.allNodes.length > 0) {
                this._updateScales();
                this.update();
            }
        });
    }

    _updateScales() {
        const xExtent = d3.extent(this.allNodes, d => d.x_2d);
        const yExtent = d3.extent(this.allNodes, d => d.y_2d);
        
        this.xScale = d3.scaleLinear()
            .domain([xExtent[0] - (xExtent[1] - xExtent[0]) * 0.1, xExtent[1] + (xExtent[1] - xExtent[0]) * 0.1])
            .range([this.margin.left, this.VIS_WIDTH - this.margin.right]);
            
        this.yScale = d3.scaleLinear()
            .domain([yExtent[0] - (yExtent[1] - yExtent[0]) * 0.1, yExtent[1] + (yExtent[1] - yExtent[0]) * 0.1])
            .range([this.VIS_HEIGHT - this.margin.bottom, this.margin.top]);

        // Update node positions
        this.allNodes.forEach(node => {
            node.x = this.xScale(node.x_2d);
            node.y = this.yScale(node.y_2d);
        });
        
        // Re-setup zoom extent
        this.zoom.translateExtent([[-200, -200], [this.VIS_WIDTH + 200, this.VIS_HEIGHT + 200]]);
        this.svg.call(this.zoom);
    }

    _setupLayers() {
        this.backgroundLayer = this.graphGroup.append("g").attr("class", "background-layer");
        this.linkLayer = this.graphGroup.append("g").attr("class", "link-layer");
        this.nodeLayer = this.graphGroup.append("g").attr("class", "node-layer");
    }

    /**
     * [Performance] Inject CSS styles to handle state changes via classes
     * rather than JS iteration.
     */
    _injectStyles() {
        const styleId = "vis-dynamic-styles";
        if (document.getElementById(styleId)) return;

        const css = `
            /* Base Transitions */
            .paper-node, .paper-link, .citation-link, .ego-link {
                transition: opacity 0.1s ease, stroke-opacity 0.1s ease;
            }

            /* --- LOD (Level of Detail) --- */
            /* When zoomed out, hide links to improve performance */
            #vis-graph-group.zoomed-out .paper-link,
            #vis-graph-group.zoomed-out .citation-link {
                display: none;
            }

            /* --- Mode: Highlight (Hover) or Selection --- */
            /* When the container has these classes, fade everything out by default */
            #vis-graph-group.mode-highlight .paper-node,
            #vis-graph-group.mode-selection .paper-node,
            #vis-graph-group.mode-highlight path,
            #vis-graph-group.mode-selection path {
                opacity: 0.1; 
                stroke-opacity: 0.05;
            }

            /* Exception: The specific items we want to see */
            #vis-graph-group .paper-node.is-highlighted,
            #vis-graph-group .paper-node.is-selected,
            #vis-graph-group .paper-node.is-connected,
            #vis-graph-group path.is-highlighted,
            #vis-graph-group path.is-selected-link {
                opacity: 1 !important;
                stroke-opacity: 0.6 !important;
            }
            
            /* Specific Styles */
            .paper-node.search-match { stroke: #000; stroke-width: 2px; }
            .paper-node.is-selected { stroke: #000; stroke-width: 2px; }
            .paper-node.is-ego-center { stroke: #000 !important; stroke-width: 3px !important; opacity: 1 !important; }
        `;

        d3.select("head").append("style").attr("id", styleId).text(css);
    }

    // --- Core Logic ---

    _setupZoom() {
        this.zoom = d3.zoom()
            .scaleExtent([this.minZoom, this.maxZoom])
            .translateExtent([[-200, -200], [this.VIS_WIDTH + 200, this.VIS_HEIGHT + 200]]) // Constrain pan
            .on("zoom", this._zoomed.bind(this));
        this.svg.call(this.zoom);
    }

    /**
     * [Performance] Optimized Zoom Handler with Culling & LOD
     */
    _zoomed(event) {
        this.currentTransform = event.transform;
        this.graphGroup.attr("transform", this.currentTransform);
        const k = this.currentTransform.k;

        // 1. Level of Detail (LOD) Check
        const isZoomedOut = k < this.LOD_THRESHOLD;
        this.graphGroup.classed("zoomed-out", isZoomedOut);

        let visibleNodes;

        if (this.quadtree) {
            // 2. Viewport Culling (Virtualization)
            // Calculate the visible bounding box in data coordinates
            const [minX, minY] = this.currentTransform.invert([0, 0]);
            const [maxX, maxY] = this.currentTransform.invert([this.VIS_WIDTH, this.VIS_HEIGHT]);

            // Add buffer to prevent "popping" at edges
            const buffer = 100 / k;

            // Filter activeNodes to find what is actually visible
            visibleNodes = this._getVisibleNodesFromQuadtree(minX - buffer, minY - buffer, maxX + buffer, maxY + buffer);
        } else {
            // No quadtree (e.g., in Ego Network view), so all active nodes are considered "visible" for rendering.
            visibleNodes = this.activeNodes;
        }

        // 3. Render Visible Nodes
        window.requestAnimationFrame(() => {
            // Draw nodes
            this.drawPaperNodes(visibleNodes);

            // Draw links (Delegate to current view, pass LOD state)
            const currentView = this._getCurrentViewObj();
            if (currentView && typeof currentView.onZoom === 'function') {
                currentView.onZoom(visibleNodes, k, isZoomedOut);
            }
        });

        // Semantic Zoom (scaling radius) - applied to currently visible nodes
        if (this.currentViewMode !== 'citation-network') {
             this.graphGroup.selectAll("circle.paper-node")
                .attr("r", d => this.getNodeRadius(d) / k);
        }
    }

    _getCurrentViewObj() {
        if (this.currentViewMode === 'topic-landscape') return this.topicLandscapeView;
        if (this.currentViewMode === 'citation-network') return this.citationNetworkView;
        if (this.currentViewMode === 'ego-network') return this.egoNetworkView;
        return null;
    }

    /**
     * [Performance] Efficiently query the Quadtree for nodes within a bounding box.
     * Much faster than Array.filter for large datasets during zoom/pan.
     */
    _getVisibleNodesFromQuadtree(x0, y0, x3, y3) {
        const visible = [];
        if (!this.quadtree) return [];
        
        this.quadtree.visit((node, x1, y1, x2, y2) => {
            if (!node.length) {
                do {
                    const d = node.data;
                    if (d.x >= x0 && d.x <= x3 && d.y >= y0 && d.y <= y3) {
                        visible.push(d);
                    }
                } while (node = node.next);
            }
            // Prune children that are completely outside the bounding box
            return x1 > x3 || x2 < x0 || y1 > y3 || y2 < y0;
        });
        return visible;
    }

    // --- Event Listeners (Quadtree) ---

    _setupEventListeners() {
        // [Performance] Single listener on SVG instead of N listeners on circles
        this.svg.on("mousemove", (event) => {
            const [mx, my] = d3.pointer(event, this.graphGroup.node());
            let closest = null;

            if (this.quadtree) {
                // Use quadtree for efficient search in most views
                const searchRadius = 20 / this.currentTransform.k;
                closest = this.quadtree.find(mx, my, searchRadius);
            } else if (this.currentViewMode === 'ego-network') {
                // Manual search for Ego Network view (no quadtree)
                let minDistanceSq = Infinity;
                for (const node of this.activeNodes) {
                    const dx = node.x - mx;
                    const dy = node.y - my;
                    const distSq = dx * dx + dy * dy;
                    const radius = this.getNodeRadius(node) / this.currentTransform.k;
                    if (distSq < radius * radius && distSq < minDistanceSq) {
                        minDistanceSq = distSq;
                        closest = node;
                    }
                }
            }

            if (closest) {
                if (this.hoverPaper !== closest.id) {
                    if (this.hoverPaper) {
                        this._onNodeMouseOut();
                    }
                    this.hoverPaper = closest.id;
                    this._onNodeHover(closest, event);
                } else {
                    this._updateTooltip(closest, event);
                }
            } else {
                if (this.hoverPaper) {
                    this.hoverPaper = null;
                    this._onNodeMouseOut();
                }
            }
        });

        this.svg.on("click", (event) => {
            let closest = null;
            if (this.quadtree) {
                const [mx, my] = d3.pointer(event, this.graphGroup.node());
                const searchRadius = 20 / this.currentTransform.k;
                closest = this.quadtree.find(mx, my, searchRadius);
            } else { // No quadtree, likely ego-network view
                const targetElement = d3.select(event.target);
                if (targetElement.classed('paper-node')) {
                    closest = targetElement.datum();
                }
            }

            if (closest) {
                this.togglePaperIsolation(event, closest);
            } else {
                // Background Click
                if (this.currentViewMode === 'citation-network') {
                    // If there's a paper selection, the first priority is to clear it.
                    if (this.selectedPapers.size > 0) {
                        this.selectedPapers.clear();
                        this.update();
                    }
                    // If no papers are selected, but we are in detail view, revert to abstract.
                    else if (this.citationDetailView) {
                        this.citationDetailView = false;
                        d3.select("#show-all-papers-toggle").property("checked", false);
                        this.update();
                    }
                    // If we are already in abstract view, a click clears the rectangle selection.
                    else {
                        this.selectedClusterKeys.clear();
                        this.update();
                    }
                } else {
                    // Default behavior for other views (like Topic Landscape)
                    this.selectedPapers.clear();
                    this.update();
                }
            }
        });

        this.svg.on("contextmenu", (event) => {
             event.preventDefault();
             let closest = null;
             if (this.quadtree) {
                const [mx, my] = d3.pointer(event, this.graphGroup.node());
                const searchRadius = 20 / this.currentTransform.k;
                closest = this.quadtree.find(mx, my, searchRadius);
             } else { // No quadtree, likely ego-network view
                const targetElement = d3.select(event.target);
                if (targetElement.classed('paper-node')) {
                    closest = targetElement.datum();
                }
             }

             if (closest) {
                this.showInfoCard(closest);
             }
        });
    }

    _onNodeHover(d, event) {
        this.tooltip.transition().duration(100).style("opacity", 1);
        this.tooltip.html(`<b>${d.title}</b><br>(${formatList(d.authors, 3)}, ${d.year || 'N/A'}) ${d.citations} citations.<br>Norm. In-Degree: ${d.normalizedInDegree.toFixed(4)}<br>Auth: ${d.authScore ? d.authScore.toFixed(4) : 'N/A'}, Hub: ${d.hubScore ? d.hubScore.toFixed(4) : 'N/A'}<br>Cluster: ${d.cluster_name}`)
        this._updateTooltip(d, event);

        const currentView = this._getCurrentViewObj();
        if (currentView && typeof currentView.onNodeHover === 'function') {
            currentView.onNodeHover(d);
        }

        // Defer to view-specific hover logic, but provide a default for other views
        if (this.currentViewMode !== 'ego-network' && this.currentViewMode !== 'citation-network') {
            this.highlightPaperNeighbors(d);
        }
    }

    _updateTooltip(d, event) {
        const [x, y] = d3.pointer(event, document.body);
        this.tooltip.style("left", `${x + 15}px`).style("top", `${y + 15}px`);
    }

    _onNodeMouseOut() {
        this.tooltip.transition().duration(200).style("opacity", 0);

        const currentView = this._getCurrentViewObj();
        if (currentView && typeof currentView.onNodeMouseOut === 'function') {
            currentView.onNodeMouseOut();
        }

        // Always clear generic highlights unless the view is handling it
        if (this.currentViewMode !== 'ego-network' && this.currentViewMode !== 'citation-network') {
            this.clearHighlights();
        }
    }

    handleKeyDown(event) {
        if (event.key === "Control" || event.key === "Meta") {
            this.ctrlKeyPressed = true;
        }

        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
            event.preventDefault();
            this.activeNodes.forEach(n => this.selectedPapers.add(n.id));
            this.update();
        }
    }

    handleKeyUp(event) {
        if (event.key === "Control" || event.key === "Meta") {
            this.ctrlKeyPressed = false;
            if (this.currentViewMode === 'citation-network' && !this.citationDetailView && this.selectedClusterKeys.size > 0) {
                this.citationDetailView = true;
                this.update();
            }
        }
    }

    _setupDefs() {
        const defs = this.svg.append("defs");
        defs.append("marker")
            .attr("id", "arrowhead")
            .attr("viewBox", "0 0 10 10")
            .attr("refX", 10).attr("refY", 5)
            .attr("markerWidth", 3).attr("markerHeight", 3)
            .attr("orient", "auto-start-reverse")
            .append("path").attr("d", "M 0 0 L 10 5 L 0 10 z").attr("fill", "#555");
    }

    /**
     * Clears detail view positions from nodes.
     */
    _clearDetailPositions() {
        this.allNodes.forEach(node => {
            node.detailOffsetX = undefined;
            node.detailOffsetY = undefined;
        });
    }

    /**
     * Main entry point to start the application.
     */
    loadDocsetIndex() {
        // Parse docset from URL query parameter
        const urlParams = new URLSearchParams(window.location.search);
        const docsetHash = urlParams.get('docset');

        if (docsetHash) {
            // Fetch index to find the human readable name
            d3.json("/data/index.json").then(docsetList => {
                let docsetName = docsetHash; // Default to hash if not found
                if (docsetList && Array.isArray(docsetList)) {
                    const entry = docsetList.find(d => d.hash === docsetHash);
                    if (entry) {
                        docsetName = entry.name;
                    }
                }
                d3.select("#current-docset-label").text(docsetName);
                this.loadAndVisualizeDocset(docsetHash, docsetName);
            }).catch(error => {
                console.error("Failed to load /data/index.json", error);
                // Fallback to just using the hash
                d3.select("#current-docset-label").text(docsetHash);
                this.loadAndVisualizeDocset(docsetHash, docsetHash);
            });
        } else {
            // If no docset specified, show error
            this.graphGroup.append("text")
                .attr("x", this.VIS_WIDTH / 2)
                .attr("y", this.VIS_HEIGHT / 2)
                .attr("text-anchor", "middle")
                .attr("class", "error-message")
                .text("No document set specified. Please select one from the Home page.");
        }

        // Setup View Mode Selector
        d3.select("#view-mode-selector").on("change", (event) => {
            this.currentViewMode = d3.select(event.currentTarget).property("value");
            this.citationDetailView = false;
            this._clearDetailPositions();
            this.svg.call(this.zoom.transform, d3.zoomIdentity);
            this.update();
        });

        // Setup other global controls
        d3.select("#back-to-global-view-btn").on("click", () => {
            this.currentViewMode = this.previousViewMode || 'topic-landscape';
            this.activeEgoPaper = null;
            this.previousViewMode = null;
            this.svg.call(this.zoom.transform, d3.zoomIdentity);
            this.update();
        });
    }

    /**
     * Resets the visualization for a new docset.
     */
    resetVisualization() {
        console.log("Resetting visualization...");
        if (this.dataWorker) {
            this.dataWorker.terminate();
            this.dataWorker = null;
        }
        this.graphGroup.selectAll("*").remove();
        this.graphGroup.attr("class", ""); // Reset any mode classes
        this._setupLayers();

        this.svg.select("defs").selectAll("*").remove();
        this._setupDefs();
        ["#cluster-list", "#manual-keyword-list", "#auto-keyword-list", "#author-list", "#organization-list", "#journal-list", "#search-filter-list", "#saved-filters-list"].forEach(sel => d3.select(sel).html(""));
        d3.select("#paper-search-input").property("value", "");
        this.allNodes = [];
        this.activeNodes = [];
        this.quadtree = null;
        this.allNodesMap.clear();
        this.allSimilarityLinks = [];
        this.allCitationLinks = [];
        this.allKnnLinks.clear();
        this.topicHierarchyRoot = null;
        this.layoutData = [];
        this.selectedPapers.clear();
        this.connectedPapers.clear();
        this.hoverPaper = null;
        this.currentSearchTerm = "";
        this.savedSearchFilters = [];
        this.savedFilterConfigurations = [];
        this.activeAllowedIds = null;
        this.currentLinkMode = 'similarity';
        this.ignoreFiltersForLinks = false;
        this.currentNodeSizeMode = 'citations';
        this.citationDetailView = false;
        this.selectedClusterKeys.clear();
        this.selectionContextNodes = null;
        this.activeEgoPaper = null;
        this.previousViewMode = null;
        this.currentTransform = d3.zoomIdentity;
        this.currentViewMode = 'topic-landscape';
        this.lastViewMode = 'topic-landscape';
        this.citationScale = null;
        this.svg.call(this.zoom.transform, d3.zoomIdentity);
        d3.select("#paper-info-card").classed("active", false);
        d3.select("#saved-filters-group").style("display", "none");
    }

    /**
     * Loads and visualizes a specific docset.
     * @param {string} docsetHash - The hash of the docset (used for file paths).
     * @param {string} docsetName - The human-readable name (used for display).
     */
    loadAndVisualizeDocset(docsetHash, docsetName) {
        this.resetVisualization();

        // Show loading indicator
        this.graphGroup.append("text")
            .attr("id", "loading-indicator")
            .attr("x", this.VIS_WIDTH / 2)
            .attr("y", this.VIS_HEIGHT / 2)
            .attr("text-anchor", "middle")
            .style("font-size", "24px")
            .text(`Loading ${docsetName}...`);

        const paperDataPath = `/data/${docsetHash}/${docsetHash}_docset.json`;
        const topicDataPath = `/data/${docsetHash}/${docsetHash}_topics.json`;

        Promise.all([
            d3.json(paperDataPath).catch(err => { console.error(`Error loading ${paperDataPath}`, err); return null; }),
            d3.json(topicDataPath).catch(err => { console.error(`Error loading ${topicDataPath}`, err); return null; })
        ]).then(([rawData, topicData]) => {
            // Remove loading indicator
            d3.select("#loading-indicator").remove();

            if (!rawData || rawData.length === 0) {
                this.graphGroup.append("text").attr("x", this.VIS_WIDTH / 2).attr("y", this.VIS_HEIGHT / 2).attr("text-anchor", "middle").attr("class", "error-message").text(`Failed to load data for ${docsetName}.`);
                return;
            }
            if (!topicData) topicData = [];
            this._processAndDraw(rawData, topicData);
        });
    }

    /**
     * Processes data and initializes the visualization.
     */
    _processAndDraw(rawData, topicData) {
        // --- Topic Hierarchy ---
        this.topicHierarchyRoot = d3.stratify().id(d => d.id).parentId(d => d.parent)(topicData.length > 0 ? topicData : [{id: 'root', parent: '', name: 'All', papers: [], cluster_label: 'root'}]);

        // --- Data Pre-processing ---
        const manualKeywordCounts = new Map(), autoKeywordCounts = new Map(), authorCounts = new Map(), organizationCounts = new Map(), journalCounts = new Map();
        const totalPapers = rawData.length;
        const currentYear = new Date().getFullYear();

        rawData.forEach(p => {
            if (p.paper && p.x_2d != null && p.y_2d != null && p.cluster != null && p.embedding) {
                const citations = p.hasTimesCited ? +p.hasTimesCited : 0;
                const year = p.fromYear ? parseInt(p.fromYear, 10) : null;

                let normalizedInDegree = 0;
                if (year) {
                    const age = Math.max(1, currentYear - year);
                    normalizedInDegree = citations / age;
                }

                const nodeData = {
                    id: p.paper, title: p.title || "No Title", abstract: p.abstract || "",
                    year: year, citations: citations,
                    normalizedInDegree: normalizedInDegree,
                    authScore: 0, hubScore: 0, // Initialize HITS scores
                    x_2d: +p.x_2d, y_2d: +p.y_2d, cluster: String(p.cluster), cluster_name: p.cluster_name || String(p.cluster),
                    embedding: p.embedding, authors: p.Author || [], manualTags: p.hasManualTag || [], autoTags: p.hasAutoTag || [],
                    organizations: p.Organization || [], journals: p.Journal || [], references: p.PublicationRef || []
                };
                this.allNodesMap.set(p.paper, nodeData);
                nodeData.manualTags.forEach(tag => manualKeywordCounts.set(tag, (manualKeywordCounts.get(tag) || 0) + 1));
                nodeData.autoTags.forEach(tag => autoKeywordCounts.set(tag, (autoKeywordCounts.get(tag) || 0) + 1));
                nodeData.authors.forEach(author => authorCounts.set(author, (authorCounts.get(author) || 0) + 1));
                nodeData.organizations.forEach(org => organizationCounts.set(org, (organizationCounts.get(org) || 0) + 1));
                nodeData.journals.forEach(journal => journalCounts.set(journal, (journalCounts.get(journal) || 0) + 1));
            }
        });
        this.allNodes = Array.from(this.allNodesMap.values());

        // --- Citation Links ---
        this.allNodes.forEach(sourceNode => {
            sourceNode.references.forEach(targetId => {
                if (this.allNodesMap.has(targetId)) {
                    this.allCitationLinks.push({ source: sourceNode.id, target: targetId });
                }
            });
        });
        console.log(`Processed ${this.allCitationLinks.length} citation links.`);

        // --- Offload heavy calculations to Web Worker ---
        this.dataWorker = new Worker('./js/dataWorker.js', { type: 'module' });
        this.dataWorker.onmessage = (e) => {
            console.log("Received data from worker.");
            const { allSimilarityLinks, allKnnLinks, hitsScores } = e.data;
            this.allSimilarityLinks = allSimilarityLinks;
            this.allKnnLinks = new Map(allKnnLinks);

            // Apply HITS scores
            if (hitsScores) {
                hitsScores.forEach(s => {
                    const node = this.allNodesMap.get(s.id);
                    if (node) {
                        node.authScore = s.auth;
                        node.hubScore = s.hub;
                    }
                });

                // Initialize scales for HITS
                this.authorityRadiusScale = d3.scaleSqrt().domain([0, d3.max(this.allNodes, d => d.authScore)]).range([4, 25]);
                this.hubRadiusScale = d3.scaleSqrt().domain([0, d3.max(this.allNodes, d => d.hubScore)]).range([4, 25]);
            }

            console.log(`Calculated ${this.allSimilarityLinks.length} similarity links and k-NN for ${this.allKnnLinks.size} papers.`);
            this.update(); // Re-render with link data
        };
        console.log("Starting Web Worker for link calculations...");
        const workerNodes = this.allNodes.map(n => ({ id: n.id, embedding: n.embedding }));
        // Pass citation links to worker for HITS calculation
        this.dataWorker.postMessage({
            nodes: workerNodes,
            maxKnn: this.MAX_KNN,
            maxLinkDistance: this.MAX_LINK_DISTANCE,
            citationLinks: this.allCitationLinks
        });

        // --- D3 Scales ---
        this._updateScales(); // Initial scale setup

        this.radiusScale = d3.scaleSqrt().domain([0, d3.max(this.allNodes, d => d.citations)]).range([4, 25]);
        this.normalizedRadiusScale = d3.scaleSqrt().domain([0, d3.max(this.allNodes, d => d.normalizedInDegree)]).range([4, 25]);
        // Initialize HITS scales with dummy domain, updated when worker returns
        this.authorityRadiusScale = d3.scaleSqrt().domain([0, 1]).range([4, 25]);
        this.hubRadiusScale = d3.scaleSqrt().domain([0, 1]).range([4, 25]);

        const colorMap = new Map(this.topicHierarchyRoot.leaves().map(node => [node.data.cluster_label, node.data.color || '#ccc']));
        this.colorScale = (clusterId) => colorMap.get(clusterId) || '#ccc';
        this.strokeWidthScale = d3.scaleLinear().range([this.BASE_STROKE_WIDTH_MAX, this.BASE_STROKE_WIDTH_MIN]);

        // --- UI Initialization ---
        this.setupKnnSlider();
        this.setupDistanceSlider(this.MAX_LINK_DISTANCE, 0.0);
        this.setupTimeSliceFilter();
        this.setupCitationCountFilter(); // Initialize the new filter
        this.setupSidebarToggles();
        this.setupSearchFilter();
        this.setupSavedFilterListeners(); // Setup the saved filters section
        this.setupManualKeywordFilter(manualKeywordCounts);
        // this.setupAutoKeywordFilter(autoKeywordCounts); // Removed as requested
        this.setupAuthorFilter(authorCounts);
        this.setupOrganizationFilter(organizationCounts);
        this.setupJournalFilter(journalCounts);
        this.setupClusterFilter(this.topicHierarchyRoot);
        this.egoNetworkView.setupWeightSliders(); // Setup for Ego view
        this.setupResetFiltersButton(); // Setup reset button
        this.setupReloadButtons(); // Setup reload buttons
        this.setupSaveFiltersButton(); // Setup the main save button
        this.setupDocsButton(); // Setup the documentation button

        d3.select("#show-all-papers-toggle").on("change", (event) => {
            this.selectedClusterKeys = event.currentTarget.checked ? new Set(this.layoutData.map(d => `${d.cluster}-${d.year}`)) : new Set();
            this.citationDetailView = event.currentTarget.checked;
            // When turning off "Show All Papers", also turn off "Show Citations"
            if (!this.citationDetailView) {
                d3.select("#show-citations-toggle").property("checked", false);
            }
            this.update();
        });

        d3.select("#show-citations-toggle").on("change", () => {
            this.update();
        });

        d3.select("#link-mode-selector").on("change", (event) => {
            this.currentLinkMode = event.currentTarget.value;
            this.update();
        });
        d3.select("#ignore-filters-checkbox").on("change", (event) => {
            this.ignoreFiltersForLinks = event.currentTarget.checked;
            this.update();
        });
        d3.select("#node-size-selector").on("change", (event) => {
            const previousMode = this.currentNodeSizeMode;
            this.currentNodeSizeMode = event.currentTarget.value;

            // --- DEBUG PRINT ---
            if (this.activeNodes.length > 0) {
                const randomNode = this.activeNodes[Math.floor(Math.random() * this.activeNodes.length)];

                // Temporarily switch back to get previous radius
                this.currentNodeSizeMode = previousMode;
                const prevRadius = this.getNodeRadius(randomNode);

                // Switch to new mode
                this.currentNodeSizeMode = event.currentTarget.value;
                const newRadius = this.getNodeRadius(randomNode);

                console.log(`Switched from ${previousMode} to ${this.currentNodeSizeMode}. Random Node (${randomNode.id}) Radius: ${prevRadius.toFixed(2)} -> ${newRadius.toFixed(2)}`);
            }

            this.update();
        });

        this.update(); // Initial draw (without links)
    }

    setupDocsButton() {
        d3.select("#show-docs-btn").on("click", () => {
            const markdownContent = `
# Interactive Visualization Tool: User Guide

## 1. Introduction

### Core Purpose

This tool is an interactive visualization platform designed for the exploration and analysis of academic paper collections. It helps you understand complex relationships, identify key research trends, and discover influential papers within a specific domain. By representing papers and their connections visually, it allows you to navigate and comprehend large bodies of literature more effectively than with traditional search and list-based methods.

### Mental Model

The interface is a **state-driven, interactive graph visualization**. The core concept is that you manipulate a series of filter controls, which collectively define the "state" of the data being viewed. Any change to this state (e.g., adjusting a slider, checking a box) immediately triggers a re-rendering of the main visualization canvas.

The tool fluidly combines three distinct views:
- **\`Topic Landscape\`:** A 2D map where papers are positioned based on their semantic similarity, revealing thematic clusters.
- **\`Citation Network\`:** A high-level view showing how topics (clusters) influence each other over time through citations.
- **\`Ego Network\`:** A focused, force-directed graph showing the local neighborhood of a single selected paper.

---

## 2. Interface Anatomy

The interface is composed of four primary regions:

1.  **Header Bar:** Displays the current dataset and the main view switcher.
2.  **Filter Sidebar:** Contains all controls for filtering, searching, and saving states.
3.  **Visualization Area:** The main canvas where the interactive visualization is rendered, along with its context-sensitive controls.
4.  **Info Card:** A panel that appears on the right to show detailed information about a selected paper.


### Common Interactions

-   **Mouse Hover:** Reveals a tooltip with basic paper information and highlights the hovered node and its immediate neighbors.
-   **Left Click (on Node):** Selects a single paper, deselecting any others. This is the primary action for focusing on a node.
-   **\`Ctrl/Meta + Left Click\` (on Node):** Toggles the selection state of a paper, allowing for multi-select.
-   **Left Click (on Background):** Clears the current selection of all papers or cluster buckets.
-   **Right Click (on Node):** Opens the **Info Card** with detailed metadata for that paper.
-   **Zoom/Pan:** Use the mouse wheel or trackpad to zoom and pan across the visualization canvas.
-   **\`Ctrl/Meta + 'A'\`:** Selects all currently visible nodes within the active filters.

---

## 3. The Header Bar

The Header Bar contains two key elements:

-   **Current Docset Label:** Displays the name of the dataset you are currently viewing.
-   **View Mode Selector:** A dropdown menu to switch between the three main visualization modes.

### View Modes Explained

1.  **\`Topic Landscape\` (Default View)**
    -   **What it is:** A 2D scatterplot where each dot (node) is a paper. The position is determined by the paper's embedding, so papers with similar content appear closer together.
    -   **Use for:** Getting an overview of the entire collection, identifying thematic clusters, and finding papers based on semantic similarity.

2.  **\`Citation Network\`**
    -   **What it is:** An abstracted view showing rectangles that represent a topic cluster for a specific year. Lines between rectangles show the flow of citations from one topic-year to another.
    -   **Use for:** Analyzing the influence of topics over time and understanding how ideas from one research area are cited by another.

3.  **\`Ego Network\`**
    -   **What it is:** A local, force-directed graph centered on a single paper (the "ego"). It shows the most relevant neighboring papers.
    -   **Use for:** Deep-diving into a specific paper's context and understanding its relationship to other papers based on a mix of semantic, citation, and cluster similarity.

---

## 4. The Filter Sidebar

This is the primary control center for refining what data you see. All filter groups can be collapsed or expanded by clicking on their title.

### State Management & Search

-   **Save/Reset Controls:**
    -   **Save Dropdown:** Choose what you want to save:
        -   \`Save Filters\`: Saves the current state of all filter controls (sliders, checkboxes, etc.) as a named preset.
        -   \`Save Selection\`: Saves the specific set of papers you have \`Ctrl+Clicked\` as a named preset.
        -   \`Save Visible\`: Saves the currently selected papers *plus* their connected neighbors (the full highlighted network) as a named preset.
    -   **Save Button:** Prompts for a name and saves the chosen configuration.
    -   **Reset Button:** Clears all active filters, selections, and searches, returning the view to its default state.

-   **Saved Filters:**
    -   Lists all your saved presets.
    -   Check a box to apply a saved filter set. Applying one will automatically deselect any other.
    -   Click the \`×\` icon to delete a saved preset.

-   **Search:**
    -   Type a term in the input box to filter papers by their title or abstract.
    -   Click \`Add\` or press \`Enter\` to add the search term as a persistent filter. You can add multiple search filters.

### Attribute Filters

-   **Timespan:** A double-ended range slider to filter papers by their publication year.
-   **Min. Citations:** A slider to hide papers with fewer citations than the selected value. The slider is logarithmic to provide finer control over lower citation counts.
-   **Filter by Cluster:** A hierarchical checklist of topic clusters. Checking a parent topic will automatically select all its children.
-   **Categorical Filters:** Checklists to filter papers by:
    -   \`Author\`
    -   \`Organization\`
    -   \`Journal\`
    -   \`Manual Keyword\`
    -   **Reload Icon (↻):** Next to the title of these filters, this icon re-sorts the list based on the frequency of items in the *current view*, bringing the most relevant authors/journals to the top.

### Ego Network-Specific Filter

-   **Relevance Weights:** These sliders only appear in the \`Ego Network\` view. They allow you to control the "relevance" score that determines which neighbors are shown and how strong the connection is.
    -   \`Semantic\`: Prioritizes papers with similar abstract/title content.
    -   \`Citation\`: Prioritizes papers that are directly or indirectly cited.
    -   \`Cluster\`: Prioritizes papers belonging to the same topic cluster.
    The sliders are zero-sum; increasing one will proportionally decrease the others.

---

## 5. The Visualization Area & Its Controls

This is the main canvas where the data is visualized. Above it are controls that change depending on the selected \`View Mode\`.

### Topic Landscape View Controls

-   **Nearest Neighbours (k-NN):** Shows links to the *k*-nearest neighbors for all visible papers. Useful for seeing the overall structure of the data. This disappears when you select a paper.
-   **Node Size:** Changes how the size of each paper node is determined.
    -   \`Total Citations\`: Size based on the absolute citation count.
    -   \`Norm. In-Degree\`: Citation count normalized by the paper's age. Highlights newer, impactful papers.
    -   \`Authority (HITS)\`: A score indicating importance as a source of information (highly cited).
-   **Link Mode (when a paper is selected):**
    -   \`Cosine Similarity\`: Shows links to semantically similar papers. Use the \`Cosine Distance\` slider to control the similarity threshold.
    -   \`Citations\`: Shows links to papers that **cite** the selection.
    -   \`References\`: Shows links to papers that the selection **references**.
-   **Ignore Filters (checkbox):** When checked, link mode will show connections to papers even if they are hidden by the current sidebar filters. This is powerful for discovery.

### Citation Network View Controls

-   **Show All Papers:** In "Detail View" (after clicking a rectangle), this shows all papers within the selected bucket(s).
-   **Show Citations:** When \`Show All Papers\` is active, this displays all citation links *between* the currently visible papers.

### Ego Network View Controls

-   **Back to Global View:** Returns you to the view you were in before entering the Ego Network.
-   The \`Link Mode\` and \`Node Size\` controls function similarly to the Topic Landscape view but operate on the local neighborhood of the ego paper.

---

## 6. The Info Card

The Info Card is a panel that slides out from the right, providing detailed information about a single paper.

-   **How to Open:** **Right-click** on any paper node in any view.
-   **What it Shows:**
    -   Title and Abstract.
    -   Authors, Organizations, Journal, and Keywords. Each of these is a checklist that is synced with the main Filter Sidebar, allowing you to quickly filter by an author or journal you see in the card.
    -   Year and Citation Count.
    -   The paper's topic cluster.
-   **Actions:**
    -   **\`Open Ego Network View\` button:** Immediately switches to the \`Ego Network\` view with this paper as the central "ego".
    -   **\`×\` button:** Closes the Info Card.

`;
            const newTab = window.open();
            const html = marked.parse(markdownContent);
            newTab.document.write(`
                <!DOCTYPE html>
                <html lang="en">
                <head>
                    <meta charset="UTF-8">
                    <title>Documentation</title>
                    <style>
                        body { font-family: sans-serif; line-height: 1.6; padding: 2em; max-width: 800px; margin: 0 auto; }
                        h1, h2, h3 { border-bottom: 1px solid #ddd; padding-bottom: 0.3em; }
                        code { background-color: #f4f4f4; padding: 2px 4px; border-radius: 3px; }
                        pre { background-color: #f4f4f4; padding: 1em; border-radius: 3px; white-space: pre-wrap; }
                    </style>
                </head>
                <body>
                    ${html}
                </body>
                </html>
            `);
            newTab.document.close();
        });
    }

    // --- Filter Setup Functions ---
    setupManualKeywordFilter(counts) { this._setupFilterList("#manual-keyword-list", counts, (d, c) => `<span class="filter-item-placeholder"></span><input type="checkbox" id="mkw-${d}" value="${d}"><label for="mkw-${d}">${d} (${c})</label>`); }
    setupAutoKeywordFilter(counts) { this._setupFilterList("#auto-keyword-list", counts, (d, c) => `<input type="checkbox" id="akw-${d}" value="${d}"><label for="akw-${d}">${d} (${c})</label>`); }
    setupAuthorFilter(counts) { this._setupFilterList("#author-list", counts, (d, c) => `<input type="checkbox" id="au-${d}" value="${d}"><label for="au-${d}">${d} (${c})</label>`); }
    setupOrganizationFilter(counts) { this._setupFilterList("#organization-list", counts, (d, c) => `<input type="checkbox" id="org-${d}" value="${d}"><label for="org-${d}">${d} (${c})</label>`); }
    setupJournalFilter(counts) { this._setupFilterList("#journal-list", counts, (d, c) => `<input type="checkbox" id="jrn-${d}" value="${d}"><label for="jrn-${d}">${d} (${c})</label>`); }
    _setupFilterList(selector, counts, htmlFactory) {
        const sortedItems = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(d => d[0]);
        d3.select(selector).selectAll(".filter-item").data(sortedItems).join("div").attr("class", "filter-item")
            .html(d => htmlFactory(d, counts.get(d)))
            .selectAll("input").on("change", () => this.update());
    }

    setupReloadButtons() {
        d3.selectAll(".reload-icon").on("click", (event) => {
            event.stopPropagation(); // Prevent toggling the accordion
            const target = event.target.dataset.target;
            this.reorderFilterList(target);
        });
    }

    reorderFilterList(target) {
        let counts = new Map();
        let selector = "";
        let htmlFactory = null;

        // Calculate counts based on currently active nodes
        this.activeNodes.forEach(node => {
            if (target === "author") {
                node.authors.forEach(item => counts.set(item, (counts.get(item) || 0) + 1));
                selector = "#author-list";
                htmlFactory = (d, c) => `<input type="checkbox" id="au-${d}" value="${d}"><label for="au-${d}">${d} (${c})</label>`;
            } else if (target === "organization") {
                node.organizations.forEach(item => counts.set(item, (counts.get(item) || 0) + 1));
                selector = "#organization-list";
                htmlFactory = (d, c) => `<input type="checkbox" id="org-${d}" value="${d}"><label for="org-${d}">${d} (${c})</label>`;
            } else if (target === "journal") {
                node.journals.forEach(item => counts.set(item, (counts.get(item) || 0) + 1));
                selector = "#journal-list";
                htmlFactory = (d, c) => `<input type="checkbox" id="jrn-${d}" value="${d}"><label for="jrn-${d}">${d} (${c})</label>`;
            } else if (target === "manual-keyword") {
                node.manualTags.forEach(item => counts.set(item, (counts.get(item) || 0) + 1));
                selector = "#manual-keyword-list";
                htmlFactory = (d, c) => `<span class="filter-item-placeholder"></span><input type="checkbox" id="mkw-${d}" value="${d}"><label for="mkw-${d}">${d} (${c})</label>`;
            }
        });

        if (selector && htmlFactory) {
            // Get currently checked items to preserve selection state
            const checkedItems = new Set(d3.selectAll(`${selector} input:checked`).nodes().map(n => n.value));

            // Re-render the list
            this._setupFilterList(selector, counts, htmlFactory);

            // Restore checked state
            d3.selectAll(`${selector} input`).property("checked", function() {
                return checkedItems.has(this.value);
            });
        }
    }

    setupKnnSlider() {
        const slider = d3.select("#knn-slider");
        const valueLabel = d3.select("#knn-value");
        slider.attr("max", this.MAX_KNN)
            .on("input", () => valueLabel.text(slider.property("value")))
            .on("change", () => this.update());
        valueLabel.text(slider.property("value"));
    }

    setupDistanceSlider(max, initial) {
        const slider = d3.select("#distance-slider");
        const valueLabel = d3.select("#distance-value");
        slider.attr("min", 0).attr("max", max).attr("step", 0.001).attr("value", initial)
            .on("input", () => valueLabel.text((+slider.property("value")).toFixed(2)))
            .on("change", () => this.update());
        valueLabel.text((+slider.property("value")).toFixed(2));
    }

    setupTimeSliceFilter() {
        const yearExtent = d3.extent(this.allNodes, d => d.year);
        if (!yearExtent[0]) return;

        const startYearSlider = d3.select("#start-year-slider");
        const endYearSlider = d3.select("#end-year-slider");
        const startYearValue = d3.select("#start-year-value");
        const endYearValue = d3.select("#end-year-value");
        const rangeFill = d3.select("#timeslice-controls-container .slider-range-fill");

        const min = yearExtent[0];
        const max = yearExtent[1];

        const updateRangeFill = () => {
            const start = +startYearSlider.property("value");
            const end = +endYearSlider.property("value");
            const startPercent = ((start - min) / (max - min)) * 100;
            const endPercent = ((end - min) / (max - min)) * 100;
            rangeFill.style("left", `${startPercent}%`);
            rangeFill.style("width", `${endPercent - startPercent}%`);
        };

        startYearSlider.attr("min", min).attr("max", max).attr("value", min)
            .on("input", () => {
                let start = +startYearSlider.property("value"), end = +endYearSlider.property("value");
                if (start > end) { startYearSlider.property("value", end); start = end; }
                startYearValue.text(start);
                updateRangeFill();
            }).on("change", () => this.update());

        endYearSlider.attr("min", min).attr("max", max).attr("value", max)
            .on("input", () => {
                let start = +startYearSlider.property("value"), end = +endYearSlider.property("value");
                if (end < start) { endYearSlider.property("value", start); end = start; }
                endYearValue.text(end);
                updateRangeFill();
            }).on("change", () => this.update());

        startYearValue.text(min);
        endYearValue.text(max);
        updateRangeFill();
    }

    setupCitationCountFilter() {
        const maxCitations = d3.max(this.allNodes, d => d.citations) || 100;

        // Create a log scale for the slider
        // Domain: [0, 100] (slider input range)
        // Range: [0, maxCitations] (actual citation values)
        // We use a power scale (exponent > 1) to mimic log behavior while allowing 0
        // Or strictly log scale with a small offset to handle 0.
        // Let's use a power scale for smoother interaction near 0.
        this.citationScale = d3.scalePow().exponent(3)
            .domain([0, 100])
            .range([0, maxCitations]);

        const slider = d3.select("#min-citation-slider");
        const valueLabel = d3.select("#min-citation-value");

        slider.attr("min", 0)
              .attr("max", 100)
              .attr("step", 1)
              .attr("value", 0)
              .on("input", () => {
                  const sliderVal = +slider.property("value");
                  const actualVal = Math.round(this.citationScale(sliderVal));
                  valueLabel.text(actualVal);
              })
              .on("change", () => this.update());

        valueLabel.text(0);
    }

    setupSidebarToggles() {
        d3.selectAll('.filter-title').on('click', function() {
            const parentGroup = this.closest('.filter-group');
            parentGroup.classList.toggle('collapsed');
        });
    }

    setupClusterFilter(rootNode) {
        rootNode.sort((a, b) => d3.ascending(a.data.cluster_label, b.data.cluster_label));
        const listContainer = d3.select("#cluster-list");
        listContainer.selectAll("*").remove();

        if (!rootNode.children || rootNode.children.length === 0) {
            listContainer.html("<em>Topic data not available.</em>");
            return;
        }

        const vizInstance = this;

        function buildTree(parentSelection, nodesData) {
            const nodes = parentSelection.selectAll("div.cluster-node")
                .data(nodesData, d => d.data.id)
                .join("div")
                .attr("class", "cluster-node");

            const filterItem = nodes.append("div").attr("class", "filter-item");

            // 1. Checkbox
            filterItem.append("input").attr("type", "checkbox").attr("id", d => `cluster-${d.data.id}`)
                .attr("value", d => d.data.cluster_label)
                .on("change", vizInstance.onClusterCheckboxChange.bind(vizInstance));

            // 2. Swatch
            filterItem.append("span").attr("class", "cluster-color-swatch")
                .style("background-color", d => vizInstance.colorScale(d.data.cluster_label));

            // 3. Label
            filterItem.append("label").attr("for", d => `cluster-${d.data.id}`)
                .text(d => `${d.data.name} (${d.data.papers.length})`);

            // 4. Toggle (Arrow)
            filterItem.each(function(d) {
                if (d.children) {
                    d3.select(this).append("span")
                        .attr("class", "toggle-arrow")
                        .style("margin-left", "auto") // Push to right
                        .style("cursor", "pointer")
                        // Removed inline padding to avoid layout issues
                        .on("click", (event) => {
                            event.stopPropagation();
                            const parentNode = event.currentTarget.closest(".cluster-node");
                            d3.select(parentNode).classed("collapsed", !d3.select(parentNode).classed("collapsed"));
                        });
                } else {
                     // Placeholder to keep alignment if needed, or just nothing
                     d3.select(this).append("span")
                        .style("width", "10px")
                        .style("margin-left", "auto");
                }
            });

            nodes.each(function(d) {
                if (d.children) {
                    const childrenContainer = d3.select(this).append("div").attr("class", "cluster-children");
                    buildTree(childrenContainer, d.children);
                }
            });
        }
        buildTree(listContainer, rootNode.children);
    }

    onClusterCheckboxChange(event) {
        const changedElement = event.currentTarget;
        const clusterLabel = changedElement.value;
        const isChecked = changedElement.checked;
        const node = this.topicHierarchyRoot.find(n => n.data.cluster_label === clusterLabel);
        if (!node) return;

        node.descendants().forEach(desc => {
            const checkbox = d3.select(`#cluster-${desc.data.id}`);
            if (!checkbox.empty()) checkbox.property("checked", isChecked);
        });

        if (isChecked) this.updateParentStates(node);
        else {
            node.ancestors().slice(1).forEach(anc => {
                const checkbox = d3.select(`#cluster-${anc.data.id}`);
                if (!checkbox.empty()) checkbox.property("checked", false);
            });
        }
        this.update();
    }

    updateParentStates(node) {
        if (!node.parent || node.parent.data.cluster_label === 'root') return;
        const parent = node.parent;
        const allChildrenChecked = parent.children.every(child => {
            const checkbox = d3.select(`#cluster-${child.data.id}`);
            return checkbox.empty() || checkbox.property("checked");
        });
        const parentCheckbox = d3.select(`#cluster-${parent.data.id}`);
        if (!parentCheckbox.empty()) parentCheckbox.property("checked", allChildrenChecked);
        this.updateParentStates(parent);
    }

    setupResetFiltersButton() {
        d3.select("#reset-filters-btn").on("click", () => {
            // 1. Reset Search
            d3.select("#paper-search-input").property("value", "");
            this.currentSearchTerm = "";
            this.savedSearchFilters = [];
            this.updateSearchFilterList();
            this.graphGroup.selectAll(".paper-node").classed("search-match", false);

            // 2. Reset Timespan
            const yearExtent = d3.extent(this.allNodes, d => d.year);
            if (yearExtent[0]) {
                d3.select("#start-year-slider").property("value", yearExtent[0]);
                d3.select("#end-year-slider").property("value", yearExtent[1]);
                d3.select("#start-year-value").text(yearExtent[0]);
                d3.select("#end-year-value").text(yearExtent[1]);
                // Trigger input event to update the visual fill
                d3.select("#start-year-slider").dispatch("input");
            }

            // 3. Reset Min Citations
            d3.select("#min-citation-slider").property("value", 0);
            d3.select("#min-citation-value").text(0);

            // 4. Reset Checkboxes (Cluster, Manual, Auto, Author, Org, Journal)
            d3.selectAll(".filter-sidebar input[type='checkbox']").property("checked", false);

            // 5. Uncheck saved filters
            d3.selectAll("#saved-filters-list input[type='checkbox']").property("checked", false);

            // 6. Reset Selection
            this.selectedPapers.clear();
            this.connectedPapers.clear();
            this.activeAllowedIds = null;

            // 7. Update Visualization
            this.update();
        });
    }

    setupSaveFiltersButton() {
        d3.select("#perform-save-btn").on("click", () => {
            const saveMode = d3.select("#save-dropdown").property("value");

            if (saveMode === "filters") {
                const filterName = prompt("Enter a name for this filter configuration:");
                if (filterName) {
                    this.saveCurrentFilterConfiguration(filterName);
                }
            } else if (saveMode === "selection") {
                if (this.selectedPapers.size === 0) {
                    alert("Please select at least one paper before saving the selection.");
                    return;
                }
                const selectionName = prompt("Enter a name for this selection:");
                if (selectionName) {
                    this.saveCurrentSelection(selectionName);
                }
            } else if (saveMode === "visible") {
                if (this.selectedPapers.size === 0) {
                    alert("Please select at least one paper before saving visible nodes based on selection.");
                    return;
                }
                const visibleName = prompt("Enter a name for this visible selection:");
                if (visibleName) {
                    this.saveCurrentVisible(visibleName);
                }
            }
        });
    }

    saveCurrentFilterConfiguration(name) {
        // Collect current state
        const config = {
            id: Date.now(), // simple unique ID
            name: name,
            active: true,
            type: 'filter',
            // Search
            searchFilters: JSON.parse(JSON.stringify(this.savedSearchFilters)),
            // Timespan
            startYear: +d3.select("#start-year-slider").property("value"),
            endYear: +d3.select("#end-year-slider").property("value"),
            // Min Citations
            minCitations: +d3.select("#min-citation-slider").property("value"),
            // Checkboxes
            manual: Array.from(d3.selectAll("#manual-keyword-list input:checked").nodes()).map(n => n.value),
            author: Array.from(d3.selectAll("#author-list input:checked").nodes()).map(n => n.value),
            org: Array.from(d3.selectAll("#organization-list input:checked").nodes()).map(n => n.value),
            journal: Array.from(d3.selectAll("#journal-list input:checked").nodes()).map(n => n.value),
            cluster: Array.from(d3.selectAll("#cluster-list input:checked").nodes()).map(n => n.value)
        };

        this.savedFilterConfigurations.push(config);
        this.updateSavedFiltersList();

        // Show the group if it was hidden
        d3.select("#saved-filters-group").style("display", "block");
    }

    saveCurrentSelection(name) {
        const config = {
            id: Date.now(),
            name: name,
            active: true,
            type: 'filter', // Save as a regular filter
            // Reset search and standard filters when saving just a selection to ensure
            // when we load it, it strictly applies the allowedIds without being restricted
            // by unrelated side filters that might have changed.
            // Wait, the prompt says "The filter should exist solely on its own so that it can be entirely deselected with a simple click. Currently when deselecting the filter I have to uncheck multiple boxes which is cumbersome."
            // This means a saved SELECTION should NOT enforce the checkboxes/sliders. It should JUST set allowedIds.
            // Let's modify the saved object for 'selection'.

            searchFilters: [],
            startYear: d3.extent(this.allNodes, d => d.year)[0] || 1900,
            endYear: d3.extent(this.allNodes, d => d.year)[1] || 2024,
            minCitations: 0,
            manual: [],
            author: [],
            org: [],
            journal: [],
            cluster: [],

            allowedIds: Array.from(this.selectedPapers),
            isPureSelection: true // Flag to know it's a pure selection
        };

        this.savedFilterConfigurations.push(config);
        this.updateSavedFiltersList();
        d3.select("#saved-filters-group").style("display", "block");
    }

    saveCurrentVisible(name) {
        // "Visible" nodes are the selected papers PLUS the connected papers
        const allVisibleIds = new Set([...this.selectedPapers, ...this.connectedPapers]);

        const config = {
            id: Date.now(),
            name: name,
            active: true,
            type: 'filter',
            searchFilters: [],
            startYear: d3.extent(this.allNodes, d => d.year)[0] || 1900,
            endYear: d3.extent(this.allNodes, d => d.year)[1] || 2024,
            minCitations: 0,
            manual: [],
            author: [],
            org: [],
            journal: [],
            cluster: [],
            allowedIds: Array.from(allVisibleIds),
            isPureSelection: true
        };

        this.savedFilterConfigurations.push(config);
        this.updateSavedFiltersList();
        d3.select("#saved-filters-group").style("display", "block");
    }

    setupSavedFilterListeners() {
        d3.select("#saved-filters-list")
            .on("change", this.handleSavedFilterToggle.bind(this))
            .on("click", this.handleSavedFilterRemove.bind(this));
    }

    updateSavedFiltersList() {
        d3.select("#saved-filters-list")
            .selectAll(".filter-item")
            .data(this.savedFilterConfigurations, d => d.id)
            .join("div")
            .attr("class", "filter-item")
            .html(d => `
                <input type="checkbox" id="saved-filter-${d.id}" value="${d.id}" ${d.active ? "checked" : ""}>
                <label for="saved-filter-${d.id}">${d.name}</label>
                <span class="remove-search-filter" data-id="${d.id}">&times;</span>
            `);
    }

    handleSavedFilterToggle(event) {
        if (event.target.type !== 'checkbox') return;
        const configId = +event.target.value;
        const config = this.savedFilterConfigurations.find(c => c.id === configId);

        if (config) {
            config.active = event.target.checked;

            if (config.active) {
                // Uncheck other saved filters
                this.savedFilterConfigurations.forEach(c => {
                    if (c.id !== configId) c.active = false;
                });
                this.updateSavedFiltersList();

                this.applyFilterConfiguration(config);
            } else {
                // When unchecking, we want to clear the isolated subset
                this.activeAllowedIds = null;

                // Only reset the UI if it was NOT a pure selection,
                // because a pure selection didn't check any UI boxes in the first place.
                if (!config.isPureSelection) {
                    d3.selectAll(".filter-sidebar input[type='checkbox']").property("checked", false);
                    d3.select("#paper-search-input").property("value", "");
                    this.currentSearchTerm = "";
                    this.savedSearchFilters = [];
                    this.updateSearchFilterList();

                    const yearExtent = d3.extent(this.allNodes, d => d.year);
                    if (yearExtent[0]) {
                        d3.select("#start-year-slider").property("value", yearExtent[0]).dispatch("input");
                        d3.select("#end-year-slider").property("value", yearExtent[1]).dispatch("input");
                    }
                    d3.select("#min-citation-slider").property("value", 0).dispatch("input");
                }

                // Ensure the saved filters list itself reflects the unchecked state
                // (it already does from the event, but let's be safe)
                this.updateSavedFiltersList();

                this.update();
            }
        }
    }

    applyFilterConfiguration(config) {
        this.selectedPapers.clear();
        this.connectedPapers.clear();
        this.activeAllowedIds = config.allowedIds ? new Set(config.allowedIds) : null;

        // 1. Search
        this.savedSearchFilters = JSON.parse(JSON.stringify(config.searchFilters || []));
        this.updateSearchFilterList();
        this.currentSearchTerm = ""; // Clear active typing
        d3.select("#paper-search-input").property("value", "");

        // 2. Timespan
        d3.select("#start-year-slider").property("value", config.startYear).dispatch("input");
        d3.select("#end-year-slider").property("value", config.endYear).dispatch("input");

        // 3. Min Citations
        d3.select("#min-citation-slider").property("value", config.minCitations).dispatch("input");

        // 4. Reset all checkboxes first
        d3.selectAll(".filter-sidebar input[type='checkbox']").property("checked", false);
        // Re-check the saved filter checkbox itself (since we just wiped all)
        d3.select(`#saved-filter-${config.id}`).property("checked", true);

        // 5. Re-apply specific checkboxes
        if (config.manual) config.manual.forEach(v => d3.select(`#manual-keyword-list input[value="${v}"]`).property("checked", true));
        if (config.author) config.author.forEach(v => d3.select(`#author-list input[value="${v}"]`).property("checked", true));
        if (config.org) config.org.forEach(v => d3.select(`#organization-list input[value="${v}"]`).property("checked", true));
        if (config.journal) config.journal.forEach(v => d3.select(`#journal-list input[value="${v}"]`).property("checked", true));

        // Clusters need special handling to ensure hierarchy is respected or triggers updates
        if (config.cluster) {
             config.cluster.forEach(v => {
                 const cb = d3.select(`#cluster-list input[value="${v}"]`);
                 if (!cb.empty()) {
                     cb.property("checked", true);
                     // We might need to manually trigger hierarchy logic or just let update() handle the leaves
                     // The update() logic looks at checked inputs, so just checking them is enough for filtering.
                     // However, visual hierarchy check state (parent/child) won't update automatically unless we call the handler.
                     // For simplicity, we just check them. The visual state of parents might be out of sync.
                     // If we want perfect sync, we'd need to simulate the change event or call onClusterCheckboxChange.
                 }
             });
        }

        this.update();
    }

    handleSavedFilterRemove(event) {
        if (!event.target.classList.contains('remove-search-filter')) return;
        const configId = +event.target.dataset.id;

        this.savedFilterConfigurations = this.savedFilterConfigurations.filter(c => c.id !== configId);
        this.updateSavedFiltersList();

        if (this.savedFilterConfigurations.length === 0) {
            d3.select("#saved-filters-group").style("display", "none");
        }
    }

    /**
     * Helper: Assigns CSS classes for styling instead of inline attributes.
     */
    getNodeClass(d) {
        let classes = "paper-node";
        const isSearchMatch = this.currentSearchTerm.length > 1 && (d.title.toLowerCase().includes(this.currentSearchTerm) || d.abstract.toLowerCase().includes(this.currentSearchTerm));
        if (this.selectedPapers.has(d.id)) classes += " is-selected";
        if (this.connectedPapers.has(d.id)) classes += " is-connected";
        if (isSearchMatch) classes += " search-match";

        if (this.currentViewMode === 'ego-network' && this.activeEgoPaper && d.id === this.activeEgoPaper.id) {
            classes += " is-ego-center";
        }

        return classes;
    }

    /**
     * Helper: Returns the radius of a node based on the current size mode.
     */
    getNodeRadius(d) {
        if (this.currentNodeSizeMode === 'normalized') {
            return this.normalizedRadiusScale(d.normalizedInDegree);
        } else if (this.currentNodeSizeMode === 'authority') {
            return this.authorityRadiusScale(d.authScore || 0);
        } else if (this.currentNodeSizeMode === 'hub') {
            return this.hubRadiusScale(d.hubScore || 0);
        }
        return this.radiusScale(d.citations);
    }

    /**
     * Main update function. Filters data and calls the appropriate view renderer.
     */
    update() {
        // --- 1. Filter nodes based on UI controls ---
        const sliderVal = +d3.select("#min-citation-slider").property("value");
        const minCitations = this.citationScale ? Math.round(this.citationScale(sliderVal)) : 0;

        const filters = {
            manual: new Set(d3.selectAll("#manual-keyword-list input:checked").nodes().map(n => n.value)),
            author: new Set(d3.selectAll("#author-list input:checked").nodes().map(n => n.value)),
            org: new Set(d3.selectAll("#organization-list input:checked").nodes().map(n => n.value)),
            journal: new Set(d3.selectAll("#journal-list input:checked").nodes().map(n => n.value)),
            cluster: new Set(d3.selectAll("#cluster-list input:checked").nodes().map(n => n.value)),
            startYear: +d3.select("#start-year-slider").property("value"),
            endYear: +d3.select("#end-year-slider").property("value"),
            minCitations: minCitations,
            search: this.savedSearchFilters.filter(f => f.active).map(f => f.term.toLowerCase())
        };

        // Store result in this.activeNodes (The "Global" set of valid nodes)
        this.activeNodes = this.allNodes.filter(n =>
            (this.activeAllowedIds ? this.activeAllowedIds.has(n.id) : true) &&
            (n.year >= filters.startYear && n.year <= filters.endYear) &&
            (n.citations >= filters.minCitations) &&
            (filters.cluster.size === 0 || Array.from(filters.cluster).some(c => n.cluster === c || n.cluster.startsWith(c + "_"))) &&
            (filters.manual.size === 0 || n.manualTags.some(t => filters.manual.has(t))) &&
            (filters.author.size === 0 || n.authors.some(a => filters.author.has(a))) &&
            (filters.org.size === 0 || n.organizations.some(o => filters.org.has(o))) &&
            (filters.journal.size === 0 || n.journals.some(j => filters.journal.has(j))) &&
            (filters.search.length === 0 || filters.search.every(term => n.title.toLowerCase().includes(term) || n.abstract.toLowerCase().includes(term)))
        );
        const activeNodeIds = new Set(this.activeNodes.map(d => d.id));

        // --- 3. Reset View State ---
        d3.select("#back-to-global-view-btn").style("display", 'none');
        d3.select("#relevance-weights-filter").style("display", "none");

        this.graphGroup.classed("mode-selection", this.selectedPapers.size > 0);
        this.graphGroup.classed("mode-highlight", false);
        this.connectedPapers.clear();

        // --- 4. Delegate to View Renderer ---
        if (this.currentViewMode === 'topic-landscape') {
            this.citationNetworkView.clear();
            this.egoNetworkView.clear();
            this.topicLandscapeView.setup(this.activeNodes, activeNodeIds);

            if (this.ignoreFiltersForLinks && this.selectedPapers.size > 0) {
                const missingNodes = [];
                const papersToMakeVisible = new Set([...this.selectedPapers, ...this.connectedPapers]);

                papersToMakeVisible.forEach(id => {
                    if (!activeNodeIds.has(id)) {
                        const node = this.allNodesMap.get(id);
                        if (node) {
                            missingNodes.push(node);
                            activeNodeIds.add(id); // Keep activeNodeIds in sync
                        }
                    }
                });
                this.activeNodes = this.activeNodes.concat(missingNodes);
            }

        } else if (this.currentViewMode === 'citation-network') {
            this.topicLandscapeView.clear();
            this.egoNetworkView.clear();

            if (this.selectedPapers.size > 0) {
                const tempConnectedIds = new Set();
                let relevantLinks = this.allCitationLinks;
                const distanceThreshold = +d3.select("#distance-slider").property("value");
                if (this.currentLinkMode === 'similarity' && distanceThreshold > 0) {
                    relevantLinks = this.allSimilarityLinks.filter(l => l.distance < distanceThreshold);
                }

                relevantLinks.forEach(link => {
                    const sourceId = link.source.id || link.source;
                    const targetId = link.target.id || link.target;
                    if (this.selectedPapers.has(sourceId)) tempConnectedIds.add(targetId);
                    if (this.selectedPapers.has(targetId)) tempConnectedIds.add(sourceId);
                });

                const currentActiveIds = new Set(this.activeNodes.map(n => n.id));
                const missingNodes = [];
                tempConnectedIds.forEach(id => {
                    if (!currentActiveIds.has(id)) {
                        const node = this.allNodesMap.get(id);
                        if (node) {
                            missingNodes.push(node);
                        }
                    }
                });
                this.activeNodes = this.activeNodes.concat(missingNodes);
            }

            this.citationNetworkView.render(this.activeNodes, Array.from(filters.cluster));

        } else if (this.currentViewMode === 'ego-network') {
            if (!this.activeEgoPaper && this.selectedPapers.size === 1) {
                const paperId = this.selectedPapers.values().next().value;
                this.activeEgoPaper = this.allNodesMap.get(paperId);
                this.previousViewMode = this.lastViewMode;
            }

            if (this.activeEgoPaper) {
                this.topicLandscapeView.clear();
                this.egoNetworkView.clear();
                const egoNodes = this.egoNetworkView.render(this.activeEgoPaper);
                this.activeNodes = egoNodes;
                d3.select("#back-to-global-view-btn").style("display", 'inline-block');
                d3.select("#relevance-weights-filter").style("display", "block");
            } else {
                this.currentViewMode = 'topic-landscape';
                this.update();
                return;
            }
        }

        if (this.currentViewMode !== 'ego-network') {
            this.quadtree = d3.quadtree()
                .x(d => d.x)
                .y(d => d.y)
                .addAll(this.activeNodes);
        } else {
            this.quadtree = null; // Explicitly nullify it for ego view
        }

        this._zoomed({ transform: this.currentTransform });
    }

    /**
     * Draws paper nodes using D3's join method.
     * [Performance] Uses vector-effect for strokes and assigns IDs for fast DOM lookup.
     */
    drawPaperNodes(nodes) {
        const nodeCircles = this.nodeLayer.selectAll("circle.paper-node")
            .data(nodes, d => d.id);

        nodeCircles.exit().remove();

        const enterNodes = nodeCircles.enter().append("circle")
            .attr("id", d => `node-${d.id}`) // ID for fast CSS selector lookup
            .attr("class", d => this.getNodeClass(d))
            .attr("vector-effect", "non-scaling-stroke") // GPU handles stroke scaling
            .attr("cx", d => d.x)
            .attr("cy", d => d.y);

        nodeCircles.merge(enterNodes)
            .attr("r", d => this.getNodeRadius(d) / this.currentTransform.k)
            .attr("fill", d => this.colorScale(d.cluster))
            .attr("opacity", 0.6) // [Visuals] Slight transparency to show density without strokes
            .attr("stroke", null) // [Performance] Remove default stroke to improve FPS
            .attr("stroke-width", null)
            .attr("class", d => this.getNodeClass(d));
    }

    // --- Interaction Functions ---

    /**
     * [Performance] Uses CSS classes to highlight neighbors instead of iterating attributes.
     */
    highlightPaperNeighbors(hoveredPaper) {
        if (this.selectedPapers.size > 0) {
            // Don't enter full highlight mode, just make the hovered node opaque.
            const el = document.getElementById(`node-${hoveredPaper.id}`);
            if (el) d3.select(el).classed("is-highlighted", true);
            return;
        }

        // 1. Set Container to "Highlight Mode" (fades everything out via CSS)
        this.graphGroup.classed("mode-highlight", true);

        // 2. Identify neighbors
        const hoverNeighbors = new Set([hoveredPaper.id]);

        // [Performance Note] This iterates DOM elements. For massive graphs, consider an adjacency list lookup.
        // Optimally, we should use a pre-calculated adjacency map,
        // but for now, we scan the DOM data which is O(L) where L = visible links.
        const visibleLinks = this.linkLayer.selectAll("path.paper-link, path.ego-link, path.citation-link");

        // Mark links as highlighted
        visibleLinks.classed("is-highlighted", function(d) {
             const sourceId = d.source.id || d.source;
             const targetId = d.target.id || d.target;
             const isConnected = (sourceId === hoveredPaper.id || targetId === hoveredPaper.id);
             if (isConnected) {
                 hoverNeighbors.add(sourceId);
                 hoverNeighbors.add(targetId);
             }
             return isConnected;
        });

        // Special handling for Citation Network (where links might be hidden)
        if (this.currentViewMode === 'citation-network') {
             this.allCitationLinks.forEach(link => {
                 if (link.source === hoveredPaper.id) hoverNeighbors.add(link.target);
                 if (link.target === hoveredPaper.id) hoverNeighbors.add(link.source);
             });
        }

        // 3. Mark specific nodes as highlighted (bring opacity back to 1 via CSS)
        hoverNeighbors.forEach(id => {
             const element = document.getElementById(`node-${id}`);
             if (element) {
                 d3.select(element).classed("is-highlighted", true);
             }
        });
    }

    clearHighlights() {
        // Remove styling classes
        this.graphGroup.classed("mode-highlight", false);
        this.graphGroup.selectAll(".is-highlighted").classed("is-highlighted", false);
    }

    togglePaperIsolation(event, d) {
        const paperId = d.id;
        const isCtrl = event.ctrlKey || event.metaKey;

        if (isCtrl) {
            this.selectedPapers.has(paperId) ? this.selectedPapers.delete(paperId) : this.selectedPapers.add(paperId);
        } else {
            if (this.selectedPapers.size === 1 && this.selectedPapers.has(paperId)) {
                this.selectedPapers.clear();
            } else {
                this.selectedPapers.clear();
                this.selectedPapers.add(paperId);
            }
        }

        if (this.currentViewMode === 'ego-network') {
            // Prevent the click from un-fixing the node by re-fixing it at its current position.
            // This counteracts the default d3.drag behavior which unpins nodes on click.
            d.fx = d.x;
            d.fy = d.y;
        }

        this.update();
    }

    // --- Search Filter Functions (simplified) ---
    setupSearchFilter() {
        d3.select("#paper-search-input").on("input", this.handleSearchInput.bind(this)).on("keydown", this.handleSearchEnter.bind(this));
        d3.select("#add-search-filter-btn").on("click", this.addSearchFilter.bind(this));
        d3.select("#search-filter-list").on("change", this.handleSearchFilterToggle.bind(this)).on("click", this.handleSearchFilterRemove.bind(this));
    }

    handleSearchInput(event) {
        this.currentSearchTerm = event.target.value.trim().toLowerCase();
        // Update styling classes instead of attributes
        this.graphGroup.selectAll(".paper-node").classed("search-match", d => {
             return this.currentSearchTerm.length > 1 &&
                    (d.title.toLowerCase().includes(this.currentSearchTerm) || d.abstract.toLowerCase().includes(this.currentSearchTerm));
        });
    }

    handleSearchEnter(event) { if (event.key === 'Enter') { event.preventDefault(); this.addSearchFilter(); } }
    addSearchFilter() {
        const term = d3.select("#paper-search-input").property("value").trim();
        if (term.length > 1 && !this.savedSearchFilters.some(f => f.term === term)) {
            this.savedSearchFilters.push({ term: term, active: true });
            this.updateSearchFilterList();
            this.update();
        }
        d3.select("#paper-search-input").property("value", "");
        this.currentSearchTerm = "";
        // Clear search highlight classes
        this.graphGroup.selectAll(".paper-node").classed("search-match", false);
    }
    updateSearchFilterList() {
        d3.select("#search-filter-list").selectAll(".filter-item").data(this.savedSearchFilters, d => d.term).join("div")
            .attr("class", "filter-item")
            .html(d => `<input type="checkbox" id="search-${d.term.replace(/\W/g, '_')}" value="${d.term}" ${d.active ? "checked" : ""}><label for="search-${d.term.replace(/\W/g, '_')}">${d.term}</label><span class="remove-search-filter" data-term="${d.term}">&times;</span>`);
    }
    handleSearchFilterToggle(event) {
        if (event.target.type !== 'checkbox') return;
        const filter = this.savedSearchFilters.find(f => f.term === event.target.value);
        if (filter) filter.active = event.target.checked;
        this.update();
    }
    handleSearchFilterRemove(event) {
        if (!event.target.classList.contains('remove-search-filter')) return;
        this.savedSearchFilters = this.savedSearchFilters.filter(f => f.term !== event.target.dataset.term);
        this.updateSearchFilterList();
        this.update();
    }

    // --- Info Card Functions ---
    _setupInfoCard() {
        d3.select(".close-btn").on("click", () => {
            d3.select("#paper-info-card").classed("active", false);
        });

        d3.select("#open-ego-network-btn").on("click", () => {
            if (this.activeEgoPaper) {
                this.previousViewMode = this.currentViewMode;
                this.currentViewMode = 'ego-network';

                this.selectedPapers.clear();
                this.selectedPapers.add(this.activeEgoPaper.id);

                this.svg.call(this.zoom.transform, d3.zoomIdentity);
                this.update();
                d3.select("#paper-info-card").classed("active", false);
            }
        });
    }

    showInfoCard(paper) {
        this.activeEgoPaper = paper; // Set active paper for Ego View transition
        const card = d3.select("#paper-info-card");

        card.select("#info-title").text(paper.title);
        card.select("#info-abstract").text(paper.abstract || "No abstract available.");
        card.select("#info-year").text(paper.year || "N/A");
        card.select("#info-citations").text(paper.citations);

        const viz = this;

        /**
         * Generic helper to create a checkbox list in the info card.
         * @param {string} containerSelector - The CSS selector for the list container (e.g., "#info-authors").
         * @param {Array<string>} items - The list of strings to display.
         * @param {string} filterType - The type of filter, used for IDs (e.g., "au", "org").
         * @param {string} mainListSelector - The CSS selector for the main filter list in the sidebar.
         */
        function createInfoCardCheckboxList(containerSelector, items, filterType, mainListSelector) {
            const listContainer = card.select(containerSelector);
            listContainer.html(""); // Clear previous items
            items.forEach(item => {
                const li = listContainer.append("li");

                // Use attribute selector for safety with special characters in values
                const mainCheckboxSelector = `${mainListSelector} input[value="${CSS.escape(item)}"]`;
                const mainCheckbox = d3.select(mainCheckboxSelector);
                const isChecked = !mainCheckbox.empty() && mainCheckbox.property("checked");

                li.append("input")
                    .attr("type", "checkbox")
                    .attr("id", `info-${filterType}-${item.replace(/\W/g, '_')}`)
                    .attr("value", item)
                    .property("checked", isChecked)
                    .on("change", (event) => {
                        const isChecked = event.target.checked;
                        if (!mainCheckbox.empty()) {
                            mainCheckbox.property("checked", isChecked);
                            // If it's a cluster, we need to trigger the hierarchical update
                            if (filterType === 'cluster') {
                                viz.onClusterCheckboxChange({ currentTarget: mainCheckbox.node() });
                            } else {
                                viz.update();
                            }
                        }
                    });
                li.append("label").attr("for", `info-${filterType}-${item.replace(/\W/g, '_')}`).text(item);
            });
        }

        // Populate lists using the helper
        createInfoCardCheckboxList("#info-authors", paper.authors, "au", "#author-list");
        createInfoCardCheckboxList("#info-orgs", paper.organizations, "org", "#organization-list");
        createInfoCardCheckboxList("#info-journal", paper.journals, "jrn", "#journal-list");
        createInfoCardCheckboxList("#info-manual-keywords", paper.manualTags, "mkw", "#manual-keyword-list");

        // Special handling for Cluster
        const clusterContainer = card.select("#info-cluster-container");
        clusterContainer.html(""); // Clear previous content
        const clusterCheckboxId = `cluster-${paper.cluster.replace(/\W/g, '_')}`;
        const mainClusterCheckboxSelector = `#cluster-list input[value="${CSS.escape(paper.cluster)}"]`;
        const mainClusterCheckbox = d3.select(mainClusterCheckboxSelector);
        const isClusterChecked = !mainClusterCheckbox.empty() && mainClusterCheckbox.property("checked");

        const clusterLi = clusterContainer.append("div").attr("class", "filter-item");
        clusterLi.append("strong").text("Cluster:").style("margin-right", "5px");
        clusterLi.append("input")
            .attr("type", "checkbox")
            .attr("id", `info-${clusterCheckboxId}`)
            .attr("value", paper.cluster)
            .property("checked", isClusterChecked)
            .on("change", (event) => {
                if (!mainClusterCheckbox.empty()) {
                    mainClusterCheckbox.property("checked", event.target.checked);
                    // Trigger the hierarchical update logic
                    viz.onClusterCheckboxChange({ currentTarget: mainClusterCheckbox.node() });
                }
            });
        clusterLi.append("label")
            .attr("for", `info-${clusterCheckboxId}`)
            .text(paper.cluster_name);


        card.classed("active", true);
    }

} // End of Visualization class