import { cosineDistanceNormalized } from './utils.js';

export default class EgoNetworkView {
    constructor(viz) {
        this.viz = viz;
        this.simulation = null;
        this.active = false;
        this.MAX_NEIGHBORS = 50;
        this.MAX_LINKS_PER_NODE = 5; // Limit links to top 5 neighbors per node

        // Caching for stable layout
        this.currentCentralNodeId = null;
        this.cachedNodes = null;
        this.cachedLinks = null;
        this.lastFilteredNodeIds = null;

        // Default initial weights
        this.weights = {
            semantic: 0.5,
            citation: 0.3,
            cluster: 0.2
        };
        // Raw slider values (0-100)
        this.rawWeights = {};
        this.sliders = [];
    }

    /**
     * Sets up the event listeners for the relevance weight sliders.
     * Implements a "zero-sum" logic where adjusting one slider
     * proportionally adjusts the others.
     */
    setupWeightSliders() {
        this.sliders = [
            { key: 'semantic', slider: '#semantic-weight-slider', label: '#semantic-weight-value' },
            { key: 'citation', slider: '#citation-weight-slider', label: '#citation-weight-value' },
            { key: 'cluster', slider: '#cluster-weight-slider', label: '#cluster-weight-value' }
        ];

        // Initialize raw weights from the DOM
        this.sliders.forEach(s => {
            this.rawWeights[s.key] = +d3.select(s.slider).property('value');
        });

        let oldRawWeights = { ...this.rawWeights };

        const updateUI = () => {
            let totalRaw = 0;
            this.sliders.forEach(s => totalRaw += this.rawWeights[s.key]);

            this.sliders.forEach(s => {
                const value = this.rawWeights[s.key];
                const normalized = totalRaw > 0 ? value / totalRaw : 0;
                this.weights[s.key] = normalized;

                d3.select(s.slider).property('value', value);
                d3.select(s.label).text(normalized.toFixed(2));
            });
        };

        const recalculateSimulation = () => {
            if (this.active && this.cachedNodes && this.simulation) {
                this.cachedLinks = this._computePairwiseLinks(this.cachedNodes);

                // Filter links to match currently visible nodes
                const currentNodes = this.simulation.nodes();
                const currentNodeIds = new Set(currentNodes.map(n => n.id));
                const filteredLinks = this.cachedLinks.filter(l => {
                     const s = l.source.id || l.source;
                     const t = l.target.id || l.target;
                     return currentNodeIds.has(s) && currentNodeIds.has(t);
                });

                // Recalculate boost factor for the new links
                let totalStrength = 0;
                filteredLinks.forEach(l => totalStrength += l.value);
                const targetAvgStrength = 0.25;
                const currentAvgStrength = filteredLinks.length > 0 ? totalStrength / filteredLinks.length : 0;
                let boostFactor = 1.0;
                if (currentAvgStrength > 0) {
                    boostFactor = targetAvgStrength / currentAvgStrength;
                }
                boostFactor = Math.max(0.5, Math.min(4.0, boostFactor));

                this.simulation.force("link")
                    .links(filteredLinks)
                    .strength(d => d.value * boostFactor)
                    .distance(d => 300 * (1 - d.value));

                this.simulation.alpha(0.3).restart();
            }
        };

        this.sliders.forEach(s => {
            const sliderElement = d3.select(s.slider);

            sliderElement.on('mousedown', () => {
                oldRawWeights = { ...this.rawWeights };
            });

            sliderElement.on('input', (event) => {
                const changedKey = s.key;
                const newValue = +event.target.value;
                const oldValue = oldRawWeights[changedKey];
                const delta = newValue - oldValue;

                const otherSlidersSumOld = 100 - oldValue;

                if (otherSlidersSumOld > 0) {
                    let sumOfNewOthers = 0;
                    // Distribute the delta proportionally to the other sliders
                    this.sliders.forEach(other_s => {
                        if (other_s.key !== changedKey) {
                            const proportion = oldRawWeights[other_s.key] / otherSlidersSumOld;
                            const newOtherValue = oldRawWeights[other_s.key] - (delta * proportion);
                            this.rawWeights[other_s.key] = Math.max(0, Math.min(100, newOtherValue));
                            sumOfNewOthers += this.rawWeights[other_s.key];
                        }
                    });
                    // Due to clamping, the sum might not be exactly 100-newValue. Adjust.
                    const correctionFactor = (100 - newValue) / sumOfNewOthers;
                     this.sliders.forEach(other_s => {
                        if (other_s.key !== changedKey) {
                           if (sumOfNewOthers > 0) this.rawWeights[other_s.key] *= correctionFactor;
                           else this.rawWeights[other_s.key] = (100 - newValue) / (this.sliders.length -1); // fallback
                        }
                    });


                } else {
                    // This happens if the changed slider was at 100.
                    // Distribute the freed-up amount equally.
                    const amountToDistribute = (100 - newValue) / (this.sliders.length - 1);
                    this.sliders.forEach(other_s => {
                        if (other_s.key !== changedKey) {
                            this.rawWeights[other_s.key] = amountToDistribute;
                        }
                    });
                }

                this.rawWeights[changedKey] = newValue;
                updateUI();
            });

            sliderElement.on('change', () => {
                // Finalize weights and recalculate
                updateUI();
                recalculateSimulation();
            });
        });

        // Initial UI sync
        updateUI();
    }


    /**
     * Clears the view elements.
     */
    clear() {
        this.active = false; // Mark as inactive
        if (this.simulation) {
            this.simulation.stop();
            this.simulation = null;
        }
        this.viz.graphGroup.selectAll("path.ego-link, circle.paper-node, text.ego-label").remove();
    }

    /**
     * Renders the Ego Network for a specific central paper.
     * @param {Object} centralNode - The paper object to focus on.
     * @returns {Array} The list of nodes currently in the ego network.
     */
    render(centralNode) {
        this.active = true;
        this._updateControls();

        let nodes, links;
        const isSameContext = (centralNode.id === this.currentCentralNodeId);

        if (!isSameContext) {
            this.currentCentralNodeId = centralNode.id;
            this.lastFilteredNodeIds = null; // Reset change tracking

            const neighbors = this._getTopNeighbors(centralNode);
            nodes = [centralNode, ...neighbors];

            links = this._computePairwiseLinks(nodes);

            this.cachedNodes = nodes;
            this.cachedLinks = links;
        } else {
            nodes = this.cachedNodes;
            // Recalculate links in case weights changed while view was inactive
            links = this._computePairwiseLinks(nodes);
            this.cachedLinks = links;
        }

        // Apply filters to the nodes
        const filteredNodes = this._applyFilters(nodes);

        // Filter links to ensure they only connect visible nodes
        // D3 force layout will crash if links reference nodes that are missing from the simulation
        const filteredNodeIds = new Set(filteredNodes.map(n => n.id));
        const filteredLinks = links.filter(l => {
             const s = l.source.id || l.source;
             const t = l.target.id || l.target;
             return filteredNodeIds.has(s) && filteredNodeIds.has(t);
        });

        // Check for topology change
        const newNodeIds = filteredNodeIds;
        let topologyChanged = false;

        if (!this.lastFilteredNodeIds) {
            topologyChanged = true;
        } else if (this.lastFilteredNodeIds.size !== newNodeIds.size) {
            topologyChanged = true;
        } else {
            for (const id of newNodeIds) {
                if (!this.lastFilteredNodeIds.has(id)) {
                    topologyChanged = true;
                    break;
                }
            }
        }
        this.lastFilteredNodeIds = newNodeIds;

        const shouldRestart = !isSameContext || topologyChanged;

        this._runSimulation(filteredNodes, filteredLinks, centralNode, !isSameContext, shouldRestart);

        return filteredNodes;
    }

    _applyFilters(nodes) {
        const sliderVal = +d3.select("#min-citation-slider").property("value");
        const minCitations = this.viz.citationScale ? Math.round(this.viz.citationScale(sliderVal)) : 0;

        const filters = {
            manual: new Set(d3.selectAll("#manual-keyword-list input:checked").nodes().map(n => n.value)),
            author: new Set(d3.selectAll("#author-list input:checked").nodes().map(n => n.value)),
            org: new Set(d3.selectAll("#organization-list input:checked").nodes().map(n => n.value)),
            journal: new Set(d3.selectAll("#journal-list input:checked").nodes().map(n => n.value)),
            cluster: new Set(d3.selectAll("#cluster-list input:checked").nodes().map(n => n.value)),
            startYear: +d3.select("#start-year-slider").property("value"),
            endYear: +d3.select("#end-year-slider").property("value"),
            minCitations: minCitations,
            search: this.viz.savedSearchFilters.filter(f => f.active).map(f => f.term.toLowerCase())
        };

        return nodes.filter(n =>
            (n.year >= filters.startYear && n.year <= filters.endYear) &&
            (n.citations >= filters.minCitations) &&
            (filters.cluster.size === 0 || Array.from(filters.cluster).some(c => n.cluster === c || n.cluster.startsWith(c + "_"))) &&
            (filters.manual.size === 0 || n.manualTags.some(t => filters.manual.has(t))) &&
            (filters.author.size === 0 || n.authors.some(a => filters.author.has(a))) &&
            (filters.org.size === 0 || n.organizations.some(o => filters.org.has(o))) &&
            (filters.journal.size === 0 || n.journals.some(j => filters.journal.has(j))) &&
            (filters.search.length === 0 || filters.search.every(term => n.title.toLowerCase().includes(term) || n.abstract.toLowerCase().includes(term)))
        );
    }

    _updateControls() {
        const isSelectionActive = this.viz.selectedPapers.size > 0;
        d3.select("#knn-controls-container").style("display", 'none');
        d3.select("#citation-controls-container").style("display", 'none');
        d3.select("#timeslice-controls-container").style("display", 'flex');
        d3.select("#link-mode-container").style("display", 'flex');
        d3.select("#distance-controls").style("display", isSelectionActive && this.viz.currentLinkMode === 'similarity' ? 'flex' : 'none');

        d3.select("#back-to-global-view-btn").style("display", 'inline-block');
        d3.select("#back-to-abstract-btn").style("display", 'none');
    }

    /**
     * Selects the top X neighbors based on the weighted relevance score.
     */
    _getTopNeighbors(centralNode) {
        const candidates = new Set();

        // Add all nodes as potential candidates
        this.viz.allNodes.forEach(node => {
            if (node.id !== centralNode.id) {
                candidates.add(node.id);
            }
        });

        const scoredCandidates = [];
        candidates.forEach(candId => {
            const node = this.viz.allNodesMap.get(candId);
            if (node) {
                const score = this._calculateRelevance(centralNode, node);
                scoredCandidates.push({ node, score });
            }
        });

        scoredCandidates.sort((a, b) => b.score - a.score);
        return scoredCandidates.slice(0, this.MAX_NEIGHBORS).map(item => item.node);
    }

    /**
     * Calculates the weighted relevance score between two nodes.
     */
    _calculateRelevance(nodeA, nodeB) {
        // 1. Semantic Score
        let semanticScore = 0;
        if (nodeA.embedding && nodeB.embedding) {
            const dist = cosineDistanceNormalized(nodeA.embedding, nodeB.embedding);
            semanticScore = 1.0 - dist;
        }

        // 2. Citation Score
        const isDirect = nodeA.references.includes(nodeB.id) || nodeB.references.includes(nodeA.id);
        const refsA = new Set(nodeA.references);
        const refsB = new Set(nodeB.references);
        let intersection = 0;
        refsA.forEach(r => { if (refsB.has(r)) intersection++; });
        const union = refsA.size + refsB.size - intersection;
        const coCitationScore = union > 0 ? intersection / union : 0;
        const citationScore = isDirect ? 1.0 : coCitationScore;

        // 3. Cluster Score
        const clusterScore = nodeA.cluster === nodeB.cluster ? 1.0 : 0.0;

        // 4. Final Weighted Score
        return (
            this.weights.semantic * semanticScore +
            this.weights.citation * citationScore +
            this.weights.cluster * clusterScore
        );
    }


    /**
     * Computes links between all nodes in the subset for the force layout.
     * Limits to top K links per node to prevent "hairball" effect.
     */
    _computePairwiseLinks(nodes) {
        const links = [];
        const n = nodes.length;

        // For each node, find its top K most relevant neighbors within the subset
        for (let i = 0; i < n; i++) {
            const source = nodes[i];
            const potentialLinks = [];

            for (let j = 0; j < n; j++) {
                if (i === j) continue;
                const target = nodes[j];

                const score = this._calculateRelevance(source, target);
                if (score > 0.05) { // Lower threshold slightly since we filter by top K later
                    potentialLinks.push({
                        source: source.id,
                        target: target.id,
                        value: score
                    });
                }
            }

            // Sort by relevance descending
            potentialLinks.sort((a, b) => b.value - a.value);

            // Take top K
            const topLinks = potentialLinks.slice(0, this.MAX_LINKS_PER_NODE);

            topLinks.forEach(link => links.push(link));
        }

        // Deduplicate links (A-B vs B-A) to ensure consistent physics
        const uniqueLinks = [];
        const linkSet = new Set();
        links.forEach(l => {
            const id = [l.source, l.target].sort().join('-');
            if (!linkSet.has(id)) {
                linkSet.add(id);
                uniqueLinks.push(l);
            }
        });

        return uniqueLinks;
    }

    /**
     * Draws the visible links based on the current link mode.
     */
    _drawVisibleLinks(nodes) {
        const isSelectionActive = this.viz.selectedPapers.size > 0;

        if (!isSelectionActive) {
            this.viz.graphGroup.selectAll("circle.paper-node").attr("opacity", 1.0);
            this.viz.graphGroup.selectAll("path.ego-link").remove();
            return;
        }

        const nodeIds = new Set(nodes.map(d => d.id));
        const selectedPaperIds = Array.from(this.viz.selectedPapers);
        let rawVisibleLinks = [];
        const allNeighbors = new Set(selectedPaperIds);

        if (this.viz.currentLinkMode === 'similarity') {
            const distanceThreshold = +d3.select("#distance-slider").property("value");
            this.viz.strokeWidthScale.domain([0, distanceThreshold]);

            rawVisibleLinks = this.viz.allSimilarityLinks.filter(link => {
                const s = link.source.id || link.source;
                const t = link.target.id || link.target;

                const isRelated = (selectedPaperIds.includes(s) || selectedPaperIds.includes(t)) && link.distance < distanceThreshold;
                if (isRelated) {
                    allNeighbors.add(s);
                    allNeighbors.add(t);
                }
                return isRelated && nodeIds.has(s) && nodeIds.has(t);
            });
        } else { // 'citations' or 'references'
            rawVisibleLinks = this.viz.allCitationLinks.filter(link => {
                const s = link.source.id || link.source;
                const t = link.target.id || link.target;

                const isCitation = this.viz.currentLinkMode === 'citations' && selectedPaperIds.includes(t);
                const isReference = this.viz.currentLinkMode === 'references' && selectedPaperIds.includes(s);
                if (isCitation) allNeighbors.add(s);
                if (isReference) allNeighbors.add(t);
                return (isCitation || isReference) && nodeIds.has(s) && nodeIds.has(t);
            });
        }

        this.viz.connectedPapers.clear();
        allNeighbors.forEach(id => {
            if (!this.viz.selectedPapers.has(id)) {
                this.viz.connectedPapers.add(id);
            }
        });

        // Manually apply classes for immediate feedback (before next zoom/tick)
        this.viz.graphGroup.selectAll("circle.paper-node")
            .classed("is-connected", d => this.viz.connectedPapers.has(d.id))
            .classed("is-selected", d => this.viz.selectedPapers.has(d.id))
            .attr("opacity", null); // Clear inline opacity so CSS takes over

        const simulationNodesMap = new Map(nodes.map(node => [node.id, node]));
        const visibleLinks = rawVisibleLinks.map(link => ({
            source: simulationNodesMap.get(link.source.id || link.source),
            target: simulationNodesMap.get(link.target.id || link.target),
            value: link.value,
            distance: link.distance
        }));

        const linkSelection = this.viz.graphGroup.selectAll("path.ego-link")
            .data(visibleLinks, d => `${d.source.id}-${d.target.id}`);

        linkSelection.exit().remove();

        linkSelection.enter().append("path")
            .attr("class", "ego-link is-selected-link") // Add class for visibility
            .attr("fill", "none")
            .attr("stroke", "#999")
            .attr("vector-effect", "non-scaling-stroke")
            .merge(linkSelection)
            .classed("is-selected-link", true) // Ensure merged selection has it
            .attr("stroke-opacity", 0.6)
            .attr("stroke-width", d => {
                if (this.viz.currentLinkMode === 'similarity') {
                    return this.viz.strokeWidthScale(d.distance);
                }
                return this.viz.BASE_HIGHLIGHT_STROKE;
            })
            .attr("marker-end", this.viz.currentLinkMode === 'citations' || this.viz.currentLinkMode === 'references' ? "url(#arrowhead)" : null)
            .attr("d", d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const dr = Math.sqrt(dx * dx + dy * dy) * 1.5;
                return `M ${d.source.x},${d.source.y} A ${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
            });
    }

    /**
     * Runs the D3 force simulation.
     */
    _runSimulation(nodes, simulationLinks, centralNode, isNewView, shouldRestart) {
        const width = this.viz.VIS_WIDTH;
        const height = this.viz.VIS_HEIGHT;
        const center = { x: width / 2, y: height / 2 };
        const radius = Math.min(width, height) / 2 - 50; // Radius for spherical boundary

        // Deterministic seeding based on ID hash
        const seedRandom = (str) => {
            let hash = 0;
            for (let i = 0; i < str.length; i++) {
                hash = str.charCodeAt(i) + ((hash << 5) - hash);
            }
            return function() {
                const x = Math.sin(hash++) * 10000;
                return x - Math.floor(x);
            };
        };

        if (isNewView) {
            const rng = seedRandom(centralNode.id);
            nodes.forEach(d => {
                if (d.id === centralNode.id) {
                    d.fx = center.x;
                    d.fy = center.y;
                } else {
                    d.fx = null;
                    d.fy = null;
                    // Use deterministic random position
                    d.x = center.x + (rng() - 0.5) * 100;
                    d.y = center.y + (rng() - 0.5) * 100;
                }
            });
        } else {
            nodes.forEach(d => {
                if (d.id === centralNode.id) {
                    d.fx = center.x;
                    d.fy = center.y;
                }
            });
        }

        // Normalization of Link Forces
        // We want the simulation to have a consistent "tightness" regardless of the absolute values of the weights.
        // If the average link strength is low, we boost it so the nodes don't drift apart too easily.
        let totalStrength = 0;
        simulationLinks.forEach(l => totalStrength += l.value);

        // Target average strength per link.
        // A value of 0.25 implies we want links to be moderately strong on average.
        const targetAvgStrength = 0.25;
        const currentAvgStrength = simulationLinks.length > 0 ? totalStrength / simulationLinks.length : 0;

        let boostFactor = 1.0;
        if (currentAvgStrength > 0) {
            boostFactor = targetAvgStrength / currentAvgStrength;
        }
        // Clamp the boost to prevent extreme behavior
        // Min 0.5 (don't weaken strong links too much)
        // Max 4.0 (don't make weak links super stiff)
        boostFactor = Math.max(0.5, Math.min(4.0, boostFactor));

        if (!this.simulation) {
            this.simulation = d3.forceSimulation(nodes)
                .force("link", d3.forceLink(simulationLinks).id(d => d.id)
                    .strength(d => d.value * boostFactor)
                    .distance(d => 300 * (1 - d.value))
                )
                .force("charge", d3.forceManyBody().strength(-300))
                // Use standard centering forces instead of custom velocity manipulation
                // This ensures the simulation settles ("stops") naturally.
                .force("x", d3.forceX(center.x).strength(0.05))
                .force("y", d3.forceY(center.y).strength(0.05))
                .force("collide", d3.forceCollide().radius(d => this.viz.getNodeRadius(d) / this.viz.currentTransform.k + 5));
        } else {
            this.simulation.nodes(nodes);
            this.simulation.force("link").links(simulationLinks);
            // Update strength with new boost factor
            this.simulation.force("link").strength(d => d.value * boostFactor);

            // Ensure centering forces are present if re-using simulation
             this.simulation.force("x", d3.forceX(center.x).strength(0.05));
             this.simulation.force("y", d3.forceY(center.y).strength(0.05));
             // Remove the old custom force if it exists
             this.simulation.force("center_gravity", null);
        }

        this.viz.drawPaperNodes(nodes);

        this.viz.graphGroup.selectAll("circle.paper-node")
            .classed("is-ego-center", d => d.id === centralNode.id);

        this.viz.graphGroup.selectAll("text.ego-label").remove();

        this._drawVisibleLinks(nodes);

        if (shouldRestart) {
            this.simulation.alpha(0.3).restart();
        }

        this.simulation.on("tick", () => {
            // Constrain nodes to spherical boundary
            nodes.forEach(d => {
                const dx = d.x - center.x;
                const dy = d.y - center.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                const r = this.viz.getNodeRadius(d);

                if (distance > radius - r) {
                    const angle = Math.atan2(dy, dx);
                    d.x = center.x + (radius - r) * Math.cos(angle);
                    d.y = center.y + (radius - r) * Math.sin(angle);
                }
            });

            this.viz.graphGroup.selectAll("path.ego-link").attr("d", d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const dr = Math.sqrt(dx * dx + dy * dy) * 1.5;
                return `M ${d.source.x},${d.source.y} A ${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
            });

            this.viz.graphGroup.selectAll("circle.paper-node")
                .attr("cx", d => d.x)
                .attr("cy", d => d.y);
        });
    }
}