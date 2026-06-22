/**
 * Manages the rendering and interactions for the "Topic Landscape" view.
 */
export default class TopicLandscapeView {
    constructor(viz) {
        this.viz = viz;
        this.linksToDraw = []; // Store calculated links here
        this.activeNodeIds = new Set();
    }

    clear() {
        this.viz.linkLayer.selectAll("path.paper-link, path.citation-link").remove();
        this.viz.nodeLayer.selectAll("circle.paper-node").remove();
        this.linksToDraw = [];
    }

    /**
     * Prepares data for rendering.
     * Does NOT draw directly. Calculations are stored for _zoomed to use.
     */
    setup(filteredNodes, filteredNodeIds) {
        this.activeNodeIds = filteredNodeIds;
        this._updateControls(this.viz.selectedPapers.size > 0);

        // Clear any existing links to prevent artifacts when switching modes
        this.viz.linkLayer.selectAll("path.paper-link, path.citation-link").remove();

        // Pre-calculate positions
        filteredNodes.forEach(node => {
            node.x = this.viz.xScale(node.x_2d);
            node.y = this.viz.yScale(node.y_2d);
        });

        // Pre-calculate links (but don't draw yet)
        this._calculateLinks(filteredNodes, filteredNodeIds);
    }

    _calculateLinks(filteredNodes, filteredNodeIds) {
        this.linksToDraw = [];
        this.viz.connectedPapers.clear(); // Clear previous connections
        const isSelectionActive = this.viz.selectedPapers.size > 0;

        if (isSelectionActive) {
            const selectedPaperIds = Array.from(this.viz.selectedPapers);
            let relevantLinks = [];

            if (this.viz.currentLinkMode === 'similarity') {
                const distanceThreshold = +d3.select("#distance-slider").property("value");
                this.viz.strokeWidthScale.domain([0, distanceThreshold]);
                relevantLinks = this.viz.allSimilarityLinks.filter(link => {
                     return (selectedPaperIds.includes(link.source) || selectedPaperIds.includes(link.target))
                            && link.distance < distanceThreshold;
                });
            } else { // 'citations' or 'references'
                 relevantLinks = this.viz.allCitationLinks.filter(link => {
                    const isCitation = this.viz.currentLinkMode === 'citations' && selectedPaperIds.includes(link.target);
                    const isReference = this.viz.currentLinkMode === 'references' && selectedPaperIds.includes(link.source);
                    return isCitation || isReference;
                });
            }

            // Now, populate both linksToDraw and connectedPapers
            relevantLinks.forEach(link => {
                this.linksToDraw.push(link);
                // Add both source and target to connected papers, excluding the selected ones
                if (!this.viz.selectedPapers.has(link.source)) {
                    this.viz.connectedPapers.add(link.source);
                }
                if (!this.viz.selectedPapers.has(link.target)) {
                    this.viz.connectedPapers.add(link.target);
                }
            });

        } else {
            // Global View k-NN
            const k = +d3.select("#knn-slider").property("value");
            const drawnLinks = new Set();
            if (k > 0) {
                filteredNodes.forEach(node => {
                    const allNeighbors = this.viz.allKnnLinks.get(node.id) || [];
                    let visibleNeighborsFound = 0;
                    for (const neighborLink of allNeighbors) {
                        if (visibleNeighborsFound >= k) break;
                        if (filteredNodeIds.has(neighborLink.target)) {
                            const linkKey = [node.id, neighborLink.target].sort().join('--');
                            if (!drawnLinks.has(linkKey)) {
                                this.linksToDraw.push({ ...neighborLink, sourceNode: node, targetNode: this.viz.allNodesMap.get(neighborLink.target) });
                                drawnLinks.add(linkKey);
                            }
                            visibleNeighborsFound++;
                        }
                    }
                });
            }
        }
    }

    /**
     * Called by Visualization._zoomed on every frame.
     * Handles Culling and LOD for links.
     */
    onZoom(visibleNodes, k, isZoomedOut) {
        // LOD Check: If zoomed out too far, don't draw ANY links.
        if (isZoomedOut) {
            this.viz.linkLayer.selectAll("path.paper-link, path.citation-link").remove();
            return;
        }

        // Culling Check: Only draw links if BOTH source and target are visible
        // (This is an optimization. You could also draw if just ONE is visible).
        const visibleIds = new Set(visibleNodes.map(n => n.id));

        const visibleLinks = this.linksToDraw.filter(d => {
            const s = d.sourceNode ? d.sourceNode.id : d.source;
            const t = d.targetNode ? d.targetNode.id : d.target;
            
            // If selection is active and we are NOT ignoring filters, 
            // ensure both ends of the link are in the filtered set.
            if (this.viz.selectedPapers.size > 0 && !this.viz.ignoreFiltersForLinks) {
                if (!this.activeNodeIds.has(s) || !this.activeNodeIds.has(t)) {
                    return false;
                }
            }

            // Draw if at least one end is on screen (prevents lines disappearing at edges)
            return visibleIds.has(s) || visibleIds.has(t);
        });

        const isSelectionActive = this.viz.selectedPapers.size > 0;

        if (isSelectionActive) {
            if (this.viz.currentLinkMode === 'similarity') {
                this._drawSimilarityLinks(visibleLinks);
            } else {
                this._drawCitationLinks(visibleLinks);
            }
        } else {
             // Standard k-NN Links
             this.viz.linkLayer.selectAll("path.paper-link")
                .data(visibleLinks, d => [d.source, d.target].sort().join('--'))
                .join("path")
                .attr("class", "paper-link")
                .attr("vector-effect", "non-scaling-stroke")
                .attr("fill", "none")
                .attr("stroke", "#444444")
                .attr("stroke-opacity", 0.3)
                .attr("d", d => {
                     // Ensure we have node objects
                     const s = d.sourceNode || this.viz.allNodesMap.get(d.source);
                     const t = d.targetNode || this.viz.allNodesMap.get(d.target);
                     const dr = Math.sqrt(Math.pow(t.x - s.x, 2) + Math.pow(t.y - s.y, 2)) * 1.5;
                     return `M ${s.x},${s.y} A ${dr},${dr} 0 0,1 ${t.x},${t.y}`;
                })
                .attr("stroke-width", 1.5);
        }
    }

    _drawSimilarityLinks(links) {
        this.viz.linkLayer.selectAll("path.paper-link")
            .data(links, d => d.source + d.target)
            .join("path")
            .attr("class", "paper-link is-selected-link")
            .attr("vector-effect", "non-scaling-stroke")
            .attr("fill", "none")
            .attr("stroke", "#444444")
            .attr("stroke-opacity", 0.5)
            .attr("d", d => {
                const s = this.viz.allNodesMap.get(d.source);
                const t = this.viz.allNodesMap.get(d.target);
                const dr = Math.sqrt(Math.pow(t.x - s.x, 2) + Math.pow(t.y - s.y, 2)) * 1.5;
                return `M ${s.x},${s.y} A ${dr},${dr} 0 0,1 ${t.x},${t.y}`;
            })
            .attr("stroke-width", d => this.viz.strokeWidthScale(d.distance));
    }

    _drawCitationLinks(links) {
        this.viz.linkLayer.selectAll("path.citation-link")
            .data(links, d => d.source + d.target)
            .join("path")
            .attr("class", "citation-link is-selected-link")
            .attr("vector-effect", "non-scaling-stroke")
            .attr("fill", "none")
            .attr("stroke", "#444444")
            .attr("stroke-opacity", 0.5)
            .attr("marker-end", "url(#arrowhead)")
            .attr("d", d => {
                const s = this.viz.allNodesMap.get(d.source);
                const t = this.viz.allNodesMap.get(d.target);
                const dr = Math.sqrt(Math.pow(t.x - s.x, 2) + Math.pow(t.y - s.y, 2)) * 1.5;
                return `M ${s.x},${s.y} A ${dr},${dr} 0 0,1 ${t.x},${t.y}`;
            })
            .attr("stroke-width", this.viz.BASE_HIGHLIGHT_STROKE);
    }

    _updateControls(isSelectionActive) {
        // Nearest Neighbours was hidden on selection in the previous iteration by mistake, 
        // reverting it to always be 'flex' unless the user intended to hide it.
        // Actually, if a selection is active, K-NN links are replaced by similarity/citation links, 
        // so hiding KNN controls during selection makes sense! I'll keep it hidden during selection.
        d3.select("#knn-controls-container").style("display", isSelectionActive ? 'none' : 'flex');
        d3.select("#node-size-container").style("display", 'flex');
        d3.select("#citation-controls-container").style("display", 'none');
        
        // This was the bug: hiding #timeslice-controls-container hides the content of the sidebar filter
        d3.select("#timeslice-controls-container").style("display", 'flex'); 

        d3.select("#back-to-abstract-btn").style("display", 'none');
        d3.select("#link-mode-container").style("display", isSelectionActive ? 'flex' : 'none');
        d3.select("#distance-controls").style("display", isSelectionActive && this.viz.currentLinkMode === 'similarity' ? 'flex' : 'none');
    }
}