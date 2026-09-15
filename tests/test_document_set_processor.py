import hashlib
import json

from DocumentSetProcessor import DocumentSetProcessor


def _bare_processor(metadata_path):
    """Instance without running __init__ (which would try to load the model)."""
    p = DocumentSetProcessor.__new__(DocumentSetProcessor)
    p.metadata_path = str(metadata_path)
    return p


def test_hash_iri_is_md5_of_iri():
    iri = "http://int.fraunhofer.de/linkeddata/resources/sets/abc"
    assert DocumentSetProcessor.hash_iri(iri) == hashlib.md5(iri.encode("utf-8")).hexdigest()
    assert len(DocumentSetProcessor.hash_iri(iri)) == 32
    assert DocumentSetProcessor.hash_iri(iri) != DocumentSetProcessor.hash_iri(iri + "x")


def test_hash_query_is_canonical():
    h = DocumentSetProcessor.hash_query
    assert h(search="a", filters={"x": "1", "y": "2"}) == h(search="a", filters={"y": "2", "x": "1"})
    assert h(search="a") == h(search="a", filters={})
    assert h(search="a") != h(search="b")
    assert h(search="a", raw_filter="type:article") != h(search="a")


def test_update_status_writes_status_and_clears_stale_error(tmp_path):
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"name": "n", "status": "error", "error": "boom"}), encoding="utf-8")
    p = _bare_processor(meta)

    p.update_status("running")
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert "error" not in data

    p.update_status("error", "failed again")
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data == {"name": "n", "status": "error", "error": "failed again"}
