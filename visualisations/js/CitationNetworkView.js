import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";

/**
 * Manages the rendering and interactions for the "Citation Network" view.
 */
export default class CitationNetworkView {
    /**
     * @param {Visualization} viz - The main visualization instance.
     */
    constructor(viz) {
        this.viz = viz;
        this.linksToDraw = []; // Store calculated links for detail view
        this.flowLinksToDraw = []; // Store flow links for abstract view
        this.tempHoverNodes = []; // Store nodes temporarily shown on hover
    }

    /**
     * Removes all SVG elements specific to this view.
     */
    clear() {
        this.viz.backgroundLayer.selectAll("rect.citation-rect, g.axis-group").remove();
        this.viz.linkLayer.selectAll("g.citation-links-group, path.citation-link, path.temp-hover-link").remove();
        this.viz.nodeLayer.selectAll("circle.paper-node, circle.temp-hover-node").remove();
        this.linksToDraw = [];
        this.flowLinksToDraw = [];
        this.tempHoverNodes = [];
    }

    /**
     * Main render function.
     */
    render(filteredNodes, checkedClusters) {
        // 1. Auto-switch to Detail View if selection exists
        if (this.viz.selectedPapers.size > 0 && !this.viz.citationDetailView) {
            this.viz.selectedPapers.forEach(paperId => {
                const node = this.viz.allNodesMap.get(paperId);
                if (node) {
                    this.viz.selectedClusterKeys.add(`${node.cluster}-${node.year}`);
                }
            });
            if (this.viz.selectedClusterKeys.size > 0) {
                this.viz.citationDetailView = true;
            }
        }

        this._updateControls();

        // 2. Calculate Abstract Layout (Rectangles)
        const { yearlyData, visibleClusters, startYear, endYear } = this._calculateYearlyData(checkedClusters);

        if (yearlyData.length === 0) {
            this.viz.backgroundLayer.append("text")
                .attr("x", this.viz.VIS_WIDTH / 2)
                .attr("y", this.viz.VIS_HEIGHT / 2)
                .attr("text-anchor", "middle")
                .attr("class", "info-text")
                .text("No yearly citation data for selected criteria.");
            return;
        }

        const { yearScale, rectLayoutMap } = this._calculateLayout(yearlyData, startYear, endYear);
        this.viz.layoutData = Array.from(rectLayoutMap.values()); // Save for global access

        // 3. Draw Base Layer (Rects & Axis)
        this._drawRectangles(rectLayoutMap);
        this._drawAxis(yearScale);


        // 4. Handle View Modes
        if (this.viz.citationDetailView) {
            // --- DETAIL MODE ---
            this.viz.linkLayer.selectAll("g.citation-links-group").remove(); // Remove flow links
            this._calculateDetailLayout(filteredNodes);
        } else {
            // --- ABSTRACT MODE ---
            filteredNodes.forEach(n => { n.x = -1000; n.y = -1000; });
            this.flowLinksToDraw = this._calculateFlowLinks(rectLayoutMap, visibleClusters);
            this._drawFlowLinks(this.flowLinksToDraw);
        }
    }

    /**
     * Called by Visualization._zoomed on every frame.
     */
    onZoom(visibleNodes, k, isZoomedOut) {
        if (!this.viz.citationDetailView) return;
        if (isZoomedOut) {
            this.viz.linkLayer.selectAll("path.citation-link").remove();
            return;
        }

        const visibleIds = new Set(visibleNodes.map(n => n.id));
        const linksToRender = this.linksToDraw.filter(d =>
            visibleIds.has(d.source) || visibleIds.has(d.target)
        );

        this._drawDetailLinks(linksToRender);
    }

    onNodeHover(hoveredNode) {
        if (!this.viz.citationDetailView || this.viz.selectedPapers.size > 0) return;

        this.viz.graphGroup.classed("mode-highlight", true);

        const rectLayoutMap = new Map(this.viz.layoutData.map(d => [`${d.cluster}-${d.year}`, d]));
        const connectedLinks = [];
        const tempNodes = [];
        const neighborIds = new Set([hoveredNode.id]);

        this.viz.allCitationLinks.forEach(link => {
            if (link.source === hoveredNode.id || link.target === hoveredNode.id) {
                const otherId = link.source === hoveredNode.id ? link.target : link.source;
                neighborIds.add(otherId);
                const otherNode = this.viz.allNodesMap.get(otherId);

                if (otherNode) {
                    let isVisible = otherNode.x > -1000;

                    if (!isVisible) {
                        const rect = rectLayoutMap.get(`${otherNode.cluster}-${otherNode.year}`);
                        if (rect) {
                            if (otherNode.detailOffsetX === undefined) {
                                otherNode.detailOffsetX = Math.random();
                                otherNode.detailOffsetY = Math.random();
                            }
                            otherNode.x = rect.x + 4 + otherNode.detailOffsetX * (rect.width - 8);
                            otherNode.y = rect.y + 4 + otherNode.detailOffsetY * (rect.height - 8);
                            tempNodes.push(otherNode);
                            isVisible = true;
                        }
                    }
                    if (isVisible) {
                        connectedLinks.push(link);
                    }
                }
            }
        });

        this.viz.nodeLayer.selectAll("circle.paper-node")
            .classed("is-highlighted", d => neighborIds.has(d.id));

        this.viz.nodeLayer.selectAll("circle.temp-hover-node")
            .data(tempNodes, d => d.id)
            .join("circle")
            .attr("class", "paper-node temp-hover-node is-highlighted")
            .attr("cx", d => d.x)
            .attr("cy", d => d.y)
            .attr("r", d => this.viz.getNodeRadius(d) / this.viz.currentTransform.k)
            .attr("fill", d => this.viz.colorScale(d.cluster))
            .attr("vector-effect", "non-scaling-stroke");

        this.viz.linkLayer.selectAll("path.temp-hover-link")
            .data(connectedLinks, l => l.source + l.target)
            .join("path")
            .attr("class", "citation-link temp-hover-link is-highlighted")
            .attr("vector-effect", "non-scaling-stroke")
            .attr("fill", "none")
            .attr("stroke", "#444")
            .attr("marker-end", "url(#arrowhead)")
            .attr("d", l => {
                 const s = this.viz.allNodesMap.get(l.source);
                 const t = this.viz.allNodesMap.get(l.target);
                 const dx = t.x - s.x, dy = t.y - s.y;
                 const dr = Math.sqrt(dx * dx + dy * dy) * 1.5;
                 return `M ${s.x},${s.y} A ${dr},${dr} 0 0,1 ${t.x},${t.y}`;
            })
            .attr("stroke-width", this.viz.BASE_HIGHLIGHT_STROKE);

        this.tempHoverNodes = tempNodes;
    }

    onNodeMouseOut() {
        if (!this.viz.citationDetailView || this.viz.selectedPapers.size > 0) return;

        this.viz.graphGroup.classed("mode-highlight", false);
        this.viz.nodeLayer.selectAll(".is-highlighted").classed("is-highlighted", false);
        this.viz.linkLayer.selectAll(".is-highlighted").classed("is-highlighted", false);

        this.viz.linkLayer.selectAll("path.temp-hover-link").remove();
        this.viz.nodeLayer.selectAll("circle.temp-hover-node").remove();

        if (this.tempHoverNodes.length > 0) {
            const activeNodeIds = new Set(this.viz.activeNodes.map(n => n.id));
            this.tempHoverNodes.forEach(n => {
                // If the temporary node is NOT in the main active set, it must be hidden.
                if (!activeNodeIds.has(n.id)) {
                    n.x = -1000;
                    n.y = -1000;
                }
            });
            this.tempHoverNodes = [];
        }
    }

    _updateControls() {
        d3.select("#knn-controls-container").style("display", 'none');
        d3.select("#node-size-container").style("display", 'flex');
        d3.select("#citation-controls-container").style("display", 'flex');
        d3.select("#timeslice-controls-container").style("display", 'flex');
        d3.select("#link-mode-container").style("display", 'none');

        const showAllPapersChecked = d3.select("#show-all-papers-toggle").property("checked");
        d3.select("#show-citations-label").style("display", showAllPapersChecked ? 'inline-block' : 'none');

        const showDistanceSlider = this.viz.citationDetailView && this.viz.selectedPapers.size > 0;
        d3.select("#distance-controls").style("display", showDistanceSlider ? 'flex' : 'none');
    }

    _calculateYearlyData(checkedClusters) {
        const yearlyData = [];
        const visibleClusters = new Set(checkedClusters.length > 0 ? checkedClusters : this.viz.topicHierarchyRoot.leaves().map(l => l.data.cluster_label));
        const startYear = +d3.select("#start-year-slider").property("value");
        const endYear = +d3.select("#end-year-slider").property("value");

        if(this.viz.topicHierarchyRoot) {
            this.viz.topicHierarchyRoot.descendants().forEach(topic => {
                if (topic.data.yearly_stats && visibleClusters.has(topic.data.cluster_label) && !topic.children) {
                    topic.data.yearly_stats.forEach(stat => {
                        if (stat.year >= startYear && stat.year <= endYear) {
                            yearlyData.push({ ...stat, year: +stat.year, cluster: topic.data.cluster_label, clusterName: topic.data.name });
                        }
                    });
                }
            });
        }
        return { yearlyData, visibleClusters, startYear, endYear };
    }

    _calculateLayout(yearlyData, startYear, endYear) {
        const allYears = Array.from(new Set(yearlyData.map(d => d.year))).sort(d3.ascending);
        const yearScale = d3.scaleBand().domain(allYears).range([this.viz.margin.left, this.viz.VIS_WIDTH - this.viz.margin.right]).padding(0.2);

        const timelineY = this.viz.VIS_HEIGHT / 2;
        const dataByYear = d3.group(yearlyData, d => d.year);
        const rectLayoutMap = new Map();

        const availableHeight = this.viz.VIS_HEIGHT - this.viz.margin.top - this.viz.margin.bottom;
        const citationScale = d3.scaleSqrt().domain([0, d3.max(yearlyData, d => d.total_citations_on_papers)]).range([0, 1]);

        const maxScaledHeight = d3.max(Array.from(dataByYear.values()), yearData => d3.sum(yearData, d => citationScale(d.total_citations_on_papers))) || 0;
        const heightNormalizer = maxScaledHeight > 0 ? availableHeight / maxScaledHeight : 0;

        dataByYear.forEach((yearData, year) => {
            yearData.sort((a, b) => d3.ascending(a.cluster, b.cluster));
            const totalYearHeight = d3.sum(yearData, d => citationScale(d.total_citations_on_papers)) * heightNormalizer;
            let currentY = timelineY - totalYearHeight / 2;

            yearData.forEach(d => {
                const rectHeight = citationScale(d.total_citations_on_papers) * heightNormalizer;
                const rectLayout = { ...d, x: yearScale(year), y: currentY, width: yearScale.bandwidth(), height: rectHeight };
                rectLayoutMap.set(`${d.cluster}-${d.year}`, rectLayout);
                currentY += rectHeight;
            });
        });
        return { yearScale, rectLayoutMap };
    }

    _calculateFlowLinks(rectLayoutMap, visibleClusters) {
        const citationFlowLinks = [];
        rectLayoutMap.forEach(targetRect => {
            const internalCitations = targetRect.citations_from_other_clusters || {};
            for (const sourceClusterId in internalCitations) {
                if (visibleClusters.has(sourceClusterId)) {
                    for (const sourceYear in internalCitations[sourceClusterId]) {
                        const sourceRect = rectLayoutMap.get(`${sourceClusterId}-${sourceYear}`);
                        if (sourceRect) {
                            citationFlowLinks.push({ source: targetRect, target: sourceRect, count: internalCitations[sourceClusterId][sourceYear] });
                        }
                    }
                }
            }
        });
        return citationFlowLinks;
    }

    _calculateDetailLayout(filteredNodes) {
        const rectLayoutMap = new Map(this.viz.layoutData.map(d => [`${d.cluster}-${d.year}`, d]));

        const isStrictBucketMode = (this.viz.selectedClusterKeys.size > 0 && this.viz.selectedPapers.size === 0);

        filteredNodes.forEach(node => {
            const key = `${node.cluster}-${node.year}`;
            const rect = rectLayoutMap.get(key);

            if (isStrictBucketMode && !this.viz.selectedClusterKeys.has(key)) {
                node.x = -1000;
                node.y = -1000;
                return;
            }

            if (rect) {
                if (node.detailOffsetX === undefined) {
                    node.detailOffsetX = Math.random();
                    node.detailOffsetY = Math.random();
                }
                node.x = rect.x + 4 + node.detailOffsetX * (rect.width - 8);
                node.y = rect.y + 4 + node.detailOffsetY * (rect.height - 8);
            } else {
                // This is a connected node outside the visible rectangles.
                // Use its original 2D coordinates.
                node.x = this.viz.xScale(node.x_2d);
                node.y = this.viz.yScale(node.y_2d);
            }
        });

        this.linksToDraw = [];
        const visibleNodeIds = new Set(filteredNodes.map(n => n.id));

        const showAllPapers = d3.select("#show-all-papers-toggle").property("checked");
        const showCitations = d3.select("#show-citations-toggle").property("checked");

        if (showAllPapers && this.viz.selectedPapers.size === 0 && !showCitations) {
            return;
        }

        let relevantLinks = this.viz.allCitationLinks;
        const distanceThreshold = +d3.select("#distance-slider").property("value");

        if (this.viz.selectedPapers.size > 0 && distanceThreshold > 0) {
            relevantLinks = this.viz.allSimilarityLinks.filter(l => l.distance < distanceThreshold);
        }

        relevantLinks.forEach(link => {
            const isSourceVisible = visibleNodeIds.has(link.source);
            const isTargetVisible = visibleNodeIds.has(link.target);
            if (this.viz.selectedPapers.size > 0) {
                const involvesSelection = this.viz.selectedPapers.has(link.source) || this.viz.selectedPapers.has(link.target);
                if (involvesSelection && isSourceVisible && isTargetVisible) {
                    this.linksToDraw.push(link);
                    if (!this.viz.selectedPapers.has(link.source)) this.viz.connectedPapers.add(link.source);
                    if (!this.viz.selectedPapers.has(link.target)) this.viz.connectedPapers.add(link.target);
                }
            } else {
                if (isSourceVisible && isTargetVisible) {
                    this.linksToDraw.push(link);
                }
            }
        });
    }

    _drawRectangles(rectLayoutMap) {
        const rects = this.viz.backgroundLayer.selectAll("rect.citation-rect")
            .data(Array.from(rectLayoutMap.values()), d => `${d.cluster}-${d.year}`);

        rects.exit().remove();

        rects.enter().append("rect")
            .attr("class", "citation-rect")
            .attr("vector-effect", "non-scaling-stroke")
            .on("click", (event, d) => this._handleRectClick(event, d))
            .on("mouseover", (event, d) => this._handleRectMouseover(event, d))
            .on("mouseout", (event, d) => this._handleRectMouseout(event, d))
            .merge(rects)
            .attr("x", d => d.x).attr("y", d => d.y)
            .attr("width", d => d.width).attr("height", d => d.height)
            .attr("fill", d => this.viz.colorScale(d.cluster))
            .attr("stroke", d => this.viz.selectedClusterKeys.has(`${d.cluster}-${d.year}`) ? "black" : "#fff")
            .attr("stroke-width", d => this.viz.selectedClusterKeys.has(`${d.cluster}-${d.year}`) ? 2 : 1)
            .attr("opacity", this.viz.citationDetailView ? 0.1 : 1);
    }

    _drawFlowLinks(citationFlowLinks) {
        const linkWidthScale = d3.scaleSqrt().domain([0, d3.max(citationFlowLinks, d => d.count) || 1]).range([1, 15]);
        const pathGenerator = d => `M ${d.source.x + d.source.width},${d.source.y + d.source.height / 2} C ${d.source.x + d.source.width + 50},${d.source.y + d.source.height / 2} ${d.target.x - 50},${d.target.y + d.target.height / 2} ${d.target.x},${d.target.y + d.target.height / 2}`;

        // --- 1. Define Gradients ---
        const defs = this.viz.svg.select("defs");
        const uniquePairs = new Set();
        citationFlowLinks.forEach(d => {
             // Create a unique key for the gradient based on cluster colors
             uniquePairs.add(`${d.source.cluster}|${d.target.cluster}`);
        });

        const gradientData = Array.from(uniquePairs).map(pair => {
            const [sourceCluster, targetCluster] = pair.split("|");
            return {
                id: `grad-${sourceCluster.replace(/\s+/g, '_')}-${targetCluster.replace(/\s+/g, '_')}`, // Sanitize IDs
                sourceColor: this.viz.colorScale(sourceCluster),
                targetColor: this.viz.colorScale(targetCluster)
            };
        });

        const gradients = defs.selectAll("linearGradient.flow-gradient")
            .data(gradientData, d => d.id);
        
        gradients.exit().remove();

        const gradientsEnter = gradients.enter().append("linearGradient")
            .attr("class", "flow-gradient")
            .attr("id", d => d.id)
            .attr("gradientUnits", "objectBoundingBox");
        
        gradientsEnter.merge(gradients)
            .attr("x1", "0%").attr("y1", "0%")
            .attr("x2", "100%").attr("y2", "0%");

        gradientsEnter.append("stop").attr("offset", "0%").attr("class", "start");
        gradientsEnter.append("stop").attr("offset", "100%").attr("class", "end");
        
        // Update stops
        const mergedGradients = gradientsEnter.merge(gradients);
        mergedGradients.select("stop.start").attr("stop-color", d => d.sourceColor);
        mergedGradients.select("stop.end").attr("stop-color", d => d.targetColor);

        // --- 2. Draw Links ---
        const linkGroup = this.viz.linkLayer.selectAll("g.citation-links-group").data([null]);
        const linkGroupEnter = linkGroup.enter().append("g").attr("class", "citation-links-group");

        const links = linkGroup.merge(linkGroupEnter).selectAll("path.link-main")
            .data(citationFlowLinks, d => `${d.source.cluster}-${d.source.year}-to-${d.target.cluster}-${d.target.year}`);

        links.exit().remove();

        links.enter().append("path").attr("class", "link-main")
             .attr("vector-effect", "non-scaling-stroke")
             .style("pointer-events", "none")
             .merge(links)
             .attr("d", pathGenerator)
             .attr("fill", "none")
             .attr("stroke", d => `url(#grad-${d.source.cluster.replace(/\s+/g, '_')}-${d.target.cluster.replace(/\s+/g, '_')})`)
             .attr("stroke-width", d => linkWidthScale(d.count))
             .attr("stroke-opacity", 0);
    }

    _drawDetailLinks(links) {
        const isSelectionActive = this.viz.selectedPapers.size > 0;
        const linkSelection = this.viz.linkLayer.selectAll("path.citation-link")
            .data(links, d => d.source + d.target);

        linkSelection.exit().remove();

        linkSelection.enter().append("path")
            .attr("class", isSelectionActive ? "citation-link is-selected-link" : "citation-link")
            .attr("vector-effect", "non-scaling-stroke")
            .attr("fill", "none")
            .attr("stroke", "#444")
            .attr("stroke-opacity", 0.5)
            .attr("marker-end", "url(#arrowhead)")
            .merge(linkSelection)
            .attr("class", isSelectionActive ? "citation-link is-selected-link" : "citation-link")
            .attr("d", d => {
                const s = this.viz.allNodesMap.get(d.source);
                const t = this.viz.allNodesMap.get(d.target);
                if (!s || !t || s.x === -1000 || t.x === -1000) return "";
                const dx = t.x - s.x, dy = t.y - s.y;
                const dr = Math.sqrt(dx * dx + dy * dy) * 1.5;
                return `M ${s.x},${s.y} A ${dr},${dr} 0 0,1 ${t.x},${t.y}`;
            })
            .attr("stroke-width", this.viz.BASE_HIGHLIGHT_STROKE);
    }

    _drawAxis(yearScale) {
        let axisGroup = this.viz.backgroundLayer.select("g.axis-group");
        if (axisGroup.empty()) {
            axisGroup = this.viz.backgroundLayer.append("g").attr("class", "axis-group");
        }
        const timelineY = this.viz.VIS_HEIGHT / 2;
        axisGroup.attr("transform", `translate(0, ${timelineY})`)
            .call(d3.axisBottom(yearScale).tickFormat(d3.format("d")));
    }

    _handleRectClick(event, d) {
        // Check for fuzzy node selection first
        const [mx, my] = d3.pointer(event, this.viz.graphGroup.node());
        const searchRadius = 20 / this.viz.currentTransform.k;
        const closest = this.viz.quadtree ? this.viz.quadtree.find(mx, my, searchRadius) : null;

        if (closest) {
            this.viz.togglePaperIsolation(event, closest);
            event.stopPropagation();
            return;
        }

        // If we clicked blank space (rectangle background) and have a selection, clear it.
        if (this.viz.selectedPapers.size > 0) {
             this.viz.selectedPapers.clear();
             this.viz.connectedPapers.clear();
             this.viz.update();
             event.stopPropagation();
             return;
        }

        event.stopPropagation();

        const key = `${d.cluster}-${d.year}`;
        const isCtrl = this.viz.ctrlKeyPressed || event.ctrlKey || event.metaKey;

        if (isCtrl) {
            this.viz.selectedClusterKeys.has(key) ? this.viz.selectedClusterKeys.delete(key) : this.viz.selectedClusterKeys.add(key);
        } else {
            if (this.viz.selectedClusterKeys.size === 1 && this.viz.selectedClusterKeys.has(key)) {
                this.viz.selectedClusterKeys.clear();
                this.viz.citationDetailView = false;
            } else {
                this.viz.selectedClusterKeys.clear();
                this.viz.selectedClusterKeys.add(key);
                this.viz.citationDetailView = true;
            }
        }
        this.viz.update();
    }

    _handleRectMouseover(event, d) {
        if (this.viz.citationDetailView) return;

        this.viz.tooltip
            .html(`<b>Cluster:</b> ${d.clusterName}<br><b>Year:</b> ${d.year}<br><b>Citations:</b> ${d.total_citations_on_papers}<br><b>Papers:</b> ${d.paper_count}`)
            .style("left", `${event.pageX + 10}px`)
            .style("top", `${event.pageY + 10}px`);

        this.viz.tooltip.transition().duration(200).style("opacity", 1);

        d3.select(event.currentTarget)
            .attr("stroke", "black")
            .attr("stroke-width", 2);

        this.viz.linkLayer.selectAll(".link-main")
            .transition().duration(200)
            .attr("stroke-opacity", link_d => (link_d.source === d || link_d.target === d) ? 0.9 : 0);
    }

    _handleRectMouseout(event, d) {
        if (this.viz.citationDetailView) return;

        this.viz.tooltip.transition().duration(500).style("opacity", 0);

        if (!this.viz.selectedClusterKeys.has(`${d.cluster}-${d.year}`)) {
            d3.select(event.currentTarget)
                .attr("stroke", "#fff")
                .attr("stroke-width", 1);
        }

        this.viz.linkLayer.selectAll(".link-main")
            .transition().duration(200)
            .attr("stroke-opacity", 0);
    }
}