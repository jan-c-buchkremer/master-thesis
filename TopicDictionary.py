import json
import os
from collections import defaultdict, deque
from typing import Dict, List, Optional
import logging

import numpy as np
from matplotlib.colors import to_hex, hsv_to_rgb

logger = logging.getLogger(__name__)

class NumpyEncoder(json.JSONEncoder):
    """ Special json encoder for numpy types """
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)

class TopicDictionary:
    """Manages the hierarchical topic dictionary structure."""

    def __init__(self):
        self.topics = {}
        self.topic_counter = 0

    def add_topic(self, cluster_label: str, name: str, description: str,
                  parent_id: Optional[int], papers: List[int]) -> int:
        """Add a topic to the dictionary and return its ID."""
        topic_id = self.topic_counter
        self.topics[topic_id] = {
            "id": topic_id,
            "cluster_label": cluster_label,
            "name": name,
            "description": description,
            "parent": parent_id,
            "papers": papers
        }
        self.topic_counter += 1
        return topic_id

    def get_topic(self, topic_id: int) -> Optional[Dict]:
        """Retrieve a topic by its ID."""
        return self.topics.get(topic_id)

    def save_to_json(self, filepath: str):
        """Save the topic dictionary to a JSON file."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(list(self.topics.values()), f, indent=2, ensure_ascii=False, cls=NumpyEncoder)
        logger.info(f"Topic dictionary saved to {filepath}")

    def assign_leaf_colors(self,
                           use_tree_ordering: bool = True,
                           saturation: float = 0.75,
                           value: float = 0.92):
        """Assign leaf colors by evenly spacing hues."""
        logger.info("Assigning colors to leaf nodes...")

        if not self.topics:
            logger.warning("Topic dictionary is empty, cannot assign colors.")
            return

        # Build children/adjacency maps
        children_map = defaultdict(list)
        adj_list = defaultdict(list)
        for topic_id, topic in self.topics.items():
            parent = topic.get("parent")
            children_map[parent].append(topic_id)
            if parent is not None:
                adj_list[parent].append(topic_id)
                adj_list[topic_id].append(parent)

        # Leaves: topics that do not appear as a parent key in children_map
        leaf_ids = [tid for tid in self.topics if tid not in children_map]
        n_leaves = len(leaf_ids)
        if n_leaves == 0:
            logger.info("No leaf nodes found to color.")
            return

        logger.info(f"Found {n_leaves} leaf nodes.")

        # Compute all-pairs shortest tree distances between leaves (BFS from each leaf)
        leaf_distances = defaultdict(dict)
        for i, start_node in enumerate(leaf_ids):
            queue = deque([(start_node, 0)])
            visited = {start_node: 0}
            while queue:
                current_node, dist = queue.popleft()
                for neighbor in adj_list[current_node]:
                    if neighbor not in visited:
                        visited[neighbor] = dist + 1
                        queue.append((neighbor, dist + 1))
            for j, end_node in enumerate(leaf_ids[i:], start=i):
                if end_node in visited:
                    d = visited[end_node]
                else:
                    d = n_leaves * 2
                leaf_distances[start_node][end_node] = d
                leaf_distances[end_node][start_node] = d

        # Build a 1-D ordering of leaves
        if use_tree_ordering and n_leaves > 1:
            ordered = []
            remaining = set(leaf_ids)
            start = min(leaf_ids, key=lambda x: len(adj_list[x]) if x in adj_list else 0)
            current = start
            ordered.append(current)
            remaining.remove(current)
            while remaining:
                next_leaf = min(remaining, key=lambda x: leaf_distances[current].get(x, n_leaves * 2))
                ordered.append(next_leaf)
                remaining.remove(next_leaf)
                current = next_leaf
            leaf_order = ordered
        else:
            leaf_order = list(leaf_ids)

        # Evenly spaced hues
        hues = np.linspace(0.0, 1.0, n_leaves, endpoint=False)

        # Map hues to leaves
        leaf_to_hsv = {}
        for idx, leaf_id in enumerate(leaf_order):
            h = float(hues[idx])
            s = float(saturation)
            v = float(value)
            leaf_to_hsv[leaf_id] = (h, s, v)

        # Convert HSV -> RGB -> hex
        hsv_array = np.array([leaf_to_hsv[leaf_id] for leaf_id in leaf_ids])
        rgb_array = hsv_to_rgb(hsv_array)
        hex_colors = [to_hex(rgb) for rgb in rgb_array]

        # Store colors back into topics
        for i, leaf_id in enumerate(leaf_ids):
            h, s, v = leaf_to_hsv[leaf_id]
            rgb = rgb_array[i]
            self.topics[leaf_id]['color'] = hex_colors[i]
            self.topics[leaf_id]['color_h'] = h
            self.topics[leaf_id]['color_s'] = s
            self.topics[leaf_id]['color_v'] = v
            self.topics[leaf_id].setdefault('color_x', None)
            self.topics[leaf_id].setdefault('color_y', None)

        logger.info(f"Assigned colors to {n_leaves} leaf nodes.")

    def is_empty(self):
        """Check if the topic dictionary is empty."""
        return not self.topics
