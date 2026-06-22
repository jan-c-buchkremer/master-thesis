import { cosineDistanceNormalized } from './utils.js';

self.onmessage = function(e) {
    const { nodes, maxKnn, maxLinkDistance, citationLinks } = e.data;

    if (!nodes) return;

    const allSimilarityLinks = [];
    const allKnnLinks = []; // We'll send this back as an array of [id, neighbors]
    const n = nodes.length;

    // --- HITS Calculation ---
    // Initialize scores
    const nodeMap = new Map();
    nodes.forEach(node => {
        node.auth = 1.0;
        node.hub = 1.0;
        nodeMap.set(node.id, node);
    });

    // Build adjacency list for faster iteration
    const adj = new Map(); // source -> [targets]
    const revAdj = new Map(); // target -> [sources]

    if (citationLinks) {
        citationLinks.forEach(link => {
            if (!adj.has(link.source)) adj.set(link.source, []);
            adj.get(link.source).push(link.target);

            if (!revAdj.has(link.target)) revAdj.set(link.target, []);
            revAdj.get(link.target).push(link.source);
        });
    }

    // Iterate HITS
    const iterations = 20;
    for (let iter = 0; iter < iterations; iter++) {
        // Update Auth
        let norm = 0;
        nodes.forEach(node => {
            let sum = 0;
            const parents = revAdj.get(node.id) || [];
            parents.forEach(parentId => {
                const parent = nodeMap.get(parentId);
                if (parent) sum += parent.hub;
            });
            node.auth = sum;
            norm += sum * sum;
        });
        norm = Math.sqrt(norm);
        nodes.forEach(node => node.auth = norm > 0 ? node.auth / norm : 0);

        // Update Hub
        norm = 0;
        nodes.forEach(node => {
            let sum = 0;
            const children = adj.get(node.id) || [];
            children.forEach(childId => {
                const child = nodeMap.get(childId);
                if (child) sum += child.auth;
            });
            node.hub = sum;
            norm += sum * sum;
        });
        norm = Math.sqrt(norm);
        nodes.forEach(node => node.hub = norm > 0 ? node.hub / norm : 0);
    }

    // Extract HITS scores to send back
    const hitsScores = nodes.map(n => ({ id: n.id, auth: n.auth }));


    // --- Similarity Calculation ---
    for (let i = 0; i < n; i++) {
        const sourceNode = nodes[i];
        const distances = [];

        for (let j = 0; j < n; j++) {
            if (i === j) continue;
            const targetNode = nodes[j];
            
            // Calculate distance
            const dist = cosineDistanceNormalized(sourceNode.embedding, targetNode.embedding);

            // 1. Collect for k-NN (we need to sort later)
            distances.push({
                source: sourceNode.id,
                target: targetNode.id,
                distance: dist
            });

            // 2. Collect for all-pairs similarity (drill-down)
            // Only store if within maxLinkDistance and i < j (undirected unique edges)
            if (i < j && dist < maxLinkDistance) {
                 allSimilarityLinks.push({
                    source: sourceNode.id,
                    target: targetNode.id,
                    distance: dist
                });
            }
        }

        // Sort by distance ascending
        distances.sort((a, b) => a.distance - b.distance);
        
        // Keep top K * 2 (as per original logic)
        const topK = distances.slice(0, maxKnn * 2);
        
        allKnnLinks.push([sourceNode.id, topK]);
    }

    self.postMessage({
        type: 'complete',
        allSimilarityLinks,
        allKnnLinks,
        hitsScores
    });
};