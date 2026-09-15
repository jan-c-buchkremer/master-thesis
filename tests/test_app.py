"""
Route tests against the Flask app via test_client(). Response codes and shapes
follow swagger.yaml. The background processing thread is stubbed out (conftest),
so nothing here touches SPARQL, OpenAlex, OpenRouter or the SPECTER2 model.
"""
import json
import os
import re

from DocumentSetProcessor import DocumentSetProcessor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_metadata(data_dir, docset_hash, **fields):
    d = os.path.join(data_dir, docset_hash)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(fields, f)
    return d


def _touch_result_files(docset_dir, docset_hash):
    for suffix in ("_docset.json", "_topics.json"):
        with open(os.path.join(docset_dir, f"{docset_hash}{suffix}"), "w", encoding="utf-8") as f:
            f.write("[]")


def test_swagger_paths_are_registered_routes(app_module):
    with open(os.path.join(PROJECT_ROOT, "swagger.yaml"), encoding="utf-8") as f:
        spec_paths = set(re.findall(r"^  (/\S+):\s*$", f.read(), flags=re.MULTILINE))
    assert spec_paths == {"/start", "/start_openalex", "/status", "/result", "/visualisations/index.html"}
    app_rules = {rule.rule for rule in app_module.app.url_map.iter_rules()}
    assert spec_paths <= app_rules


def test_error_responses_match_swagger(client):
    # 400: missing required query parameter
    for url in ("/start", "/start_openalex", "/status", "/result"):
        resp = client.get(url)
        assert resp.status_code == 400, url
        assert "error" in resp.get_json()
    # 404: uuid not found
    for url in ("/status", "/result"):
        resp = client.get(url, query_string={"uuid": "does-not-exist"})
        assert resp.status_code == 404, url
        assert resp.get_json() == {"error": "Unknown uuid"}


def test_start_creates_task_mapping_and_pending_metadata(client, tmp_path):
    iri = "http://int.fraunhofer.de/linkeddata/resources/sets/test"
    resp = client.get("/start", query_string={"docset_iri": iri})
    assert resp.status_code == 202
    task_uuid = resp.get_json()["uuid"]
    assert re.fullmatch(r"[0-9a-f-]{36}", task_uuid)

    docset_hash = DocumentSetProcessor.hash_iri(iri)
    with open(tmp_path / "tasks" / f"{task_uuid}.json", encoding="utf-8") as f:
        assert json.load(f) == {"docset_hash": docset_hash}
    with open(tmp_path / docset_hash / "metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta == {"name": iri, "iri": iri, "hash": docset_hash, "status": "pending"}

    status = client.get("/status", query_string={"uuid": task_uuid})
    assert status.status_code == 200
    assert status.get_json() == {"uuid": task_uuid, "status": "pending"}

    # not finished yet -> /result answers 202 per swagger
    result = client.get("/result", query_string={"uuid": task_uuid})
    assert result.status_code == 202
    assert result.get_json()["status"] == "pending"


def test_result_for_finished_and_failed_tasks(client, app_module, tmp_path):
    finished_hash, failed_hash = "f" * 32, "e" * 32
    d = _write_metadata(tmp_path, finished_hash, status="finished")
    _touch_result_files(d, finished_hash)
    _write_metadata(tmp_path, failed_hash, status="error", error="boom")
    app_module.save_task_mapping("uuid-finished", finished_hash)
    app_module.save_task_mapping("uuid-failed", failed_hash)

    ok = client.get("/result", query_string={"uuid": "uuid-finished"})
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["status"] == "finished"
    assert body["url"].endswith(f"/visualisations/index.html?docset={finished_hash}")

    failed = client.get("/result", query_string={"uuid": "uuid-failed"})
    assert failed.status_code == 500
    assert failed.get_json() == {"uuid": "uuid-failed", "status": "error", "error": "boom"}

    status = client.get("/status", query_string={"uuid": "uuid-failed"})
    assert status.status_code == 200
    assert status.get_json()["error"] == "boom"
