import json

import numpy as np
import pandas as pd

import DataHandler


def test_format_iri_makes_fraunhofer_iris_human_readable():
    assert DataHandler._format_iri("http://x/person/Doe_Jane_123") == "Doe"
    assert DataHandler._format_iri("http://x/org/Fraunhofer_IAIS") == "Fraunhofer IAIS"
    assert DataHandler._format_iri("http://x/journal/Nature%20Physics") == "Nature Physics"
    assert DataHandler._format_iri("plain text") == "plain text"
    assert DataHandler._format_iri(None) is None


def test_collapse_metadata_one_row_per_paper():
    df = pd.DataFrame([
        {"paper": "p1", "title": "T1", "hasAutoTag": "a", "PublicationRef": "p2"},
        {"paper": "p1", "title": "T1", "hasAutoTag": "b", "PublicationRef": "p2"},
        {"paper": "p2", "title": "T2", "hasAutoTag": None, "PublicationRef": None},
    ])
    out = DataHandler._collapse_metadata(df).set_index("paper")
    assert len(out) == 2
    assert out.loc["p1", "title"] == "T1"
    assert out.loc["p1", "hasAutoTag"] == ["a", "b"]
    assert out.loc["p1", "PublicationRef"] == ["p2"]
    assert out.loc["p2", "PublicationRef"] == []


def test_save_and_load_docset_round_trip(tmp_path):
    path = tmp_path / "h" / "h_docset.json"
    df = pd.DataFrame({
        "paper": ["p1", "p2"],
        "title": ["A", None],
        "embedding": [np.array([0.1, 0.2]), np.array([0.3, 0.4])],
    })
    DataHandler.save_docset_to_json(df, str(path), human_readable=False)

    records = json.loads(path.read_text(encoding="utf-8"))
    assert records[0]["embedding"] == [0.1, 0.2]
    assert records[1]["title"] is None  # NaN/None serialised as null, not "NaN"

    loaded = DataHandler.load_docset_from_file(str(path))
    assert list(loaded["paper"]) == ["p1", "p2"]
    assert isinstance(loaded["embedding"].iloc[0], np.ndarray)
    np.testing.assert_allclose(loaded["embedding"].iloc[1], [0.3, 0.4])


def test_update_docset_index_lists_only_complete_docsets(tmp_path):
    def make(hash_, name, complete=True):
        d = tmp_path / hash_
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({"name": name, "iri": f"iri:{hash_}"}), encoding="utf-8")
        (d / f"{hash_}_docset.json").write_text("[]", encoding="utf-8")
        if complete:
            (d / f"{hash_}_topics.json").write_text("[]", encoding="utf-8")

    make("bbb", "Zeta")
    make("aaa", "Alpha")
    make("ccc", "Incomplete", complete=False)

    DataHandler.update_docset_index(str(tmp_path))
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index == [
        {"hash": "aaa", "name": "Alpha", "iri": "iri:aaa"},
        {"hash": "bbb", "name": "Zeta", "iri": "iri:bbb"},
    ]
