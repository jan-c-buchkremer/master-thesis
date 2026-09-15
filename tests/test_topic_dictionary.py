import numpy as np

from TopicDictionary import TopicDictionary


def _tree():
    """root(0) -> a(1) -> [a1(3), a2(4)];  root(0) -> b(2)"""
    td = TopicDictionary()
    root = td.add_topic("-1", "root", "", None, [])
    a = td.add_topic("0", "a", "", root, [])
    b = td.add_topic("1", "b", "", root, [])
    td.add_topic("0-0", "a1", "", a, [1, 2])
    td.add_topic("0-1", "a2", "", a, [3])
    return td, root, a, b


def test_add_topic_assigns_sequential_ids_and_navigation_helpers():
    td, root, a, b = _tree()
    assert [t["id"] for t in td.topics.values()] == [0, 1, 2, 3, 4]
    assert td.topic_counter == 5
    assert [t["name"] for t in td.get_ancestor_chain(3)] == ["a", "root"]
    assert td.get_ancestor_chain(root) == []
    assert [t["name"] for t in td.get_siblings(3)] == ["a2"]
    assert [t["name"] for t in td.get_siblings(a)] == ["b"]
    assert td.get_siblings(999) == []


def test_assign_leaf_colors_only_colors_leaves_with_evenly_spaced_hues():
    td, root, a, b = _tree()
    td.assign_leaf_colors()

    leaves = {2, 3, 4}
    for tid, topic in td.topics.items():
        assert ("color" in topic) == (tid in leaves)

    hues = sorted(td.topics[t]["color_h"] for t in leaves)
    np.testing.assert_allclose(hues, [0.0, 1 / 3, 2 / 3])
    colors = {td.topics[t]["color"] for t in leaves}
    assert len(colors) == 3
    assert all(c.startswith("#") and len(c) == 7 for c in colors)
