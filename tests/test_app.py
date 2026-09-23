"""
Route tests against the Flask app via test_client(). Response codes and shapes
follow swagger.yaml. The app only queues jobs (the worker runs them), so nothing
here touches SPARQL, OpenAlex, OpenRouter or the SPECTER2 model.
"""
import os
import re

import DocsetHash

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _add_docset(db, docset_hash, status, error=None):
    with db.get_engine().begin() as conn:
        conn.execute(db.docsets.insert().values(hash=docset_hash, name=docset_hash, source="openalex",
                                                status=status, error=error))


def _touch_result_files(data_dir, docset_hash):
    d = os.path.join(data_dir, docset_hash)
    os.makedirs(d, exist_ok=True)
    for suffix in ("_docset.json", "_topics.json"):
        with open(os.path.join(d, f"{docset_hash}{suffix}"), "w", encoding="utf-8") as f:
            f.write("[]")


def _jobs(db):
    with db.get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(db.jobs.select().order_by(db.jobs.c.id)).mappings()]


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


def test_start_queues_a_job_and_reports_pending(client, db):
    iri = "http://int.fraunhofer.de/linkeddata/resources/sets/test"
    resp = client.get("/start", query_string={"docset_iri": iri})
    assert resp.status_code == 202
    task_uuid = resp.get_json()["uuid"]
    assert re.fullmatch(r"[0-9a-f-]{36}", task_uuid)

    docset_hash = DocsetHash.hash_iri(iri)
    assert db.get_task_docset_hash(task_uuid) == docset_hash
    docset = db.get_docset(docset_hash)
    assert (docset["name"], docset["iri"], docset["source"], docset["status"]) == (iri, iri, "fraunhofer", "pending")
    [job] = _jobs(db)
    assert (job["docset_hash"], job["kind"], job["status"], job["params"]) == (
        docset_hash, "iri", "queued", {"docset_iri": iri})

    status = client.get("/status", query_string={"uuid": task_uuid})
    assert status.status_code == 200
    assert status.get_json() == {"uuid": task_uuid, "status": "pending"}

    # not finished yet -> /result answers 202 per swagger
    result = client.get("/result", query_string={"uuid": task_uuid})
    assert result.status_code == 202
    assert result.get_json()["status"] == "pending"


def test_start_openalex_queues_query_params(client, db):
    resp = client.get("/start_openalex", query_string={"search": "graphs", "filter": "type:article", "min_size": 50})
    assert resp.status_code == 202
    [job] = _jobs(db)
    assert job["kind"] == "openalex"
    assert job["docset_hash"] == DocsetHash.hash_query(search="graphs", raw_filter="type:article")
    assert job["params"] == {"search": "graphs", "raw_filter": "type:article", "docset_name": None,
                             "min_size": 50, "max_size": 5000}


def test_repeated_start_reuses_queued_and_finished_runs(client, db, tmp_path):
    iri = "http://example.org/set"
    uuids = [client.get("/start", query_string={"docset_iri": iri}).get_json()["uuid"] for _ in range(2)]
    assert len(set(uuids)) == 2
    assert len(_jobs(db)) == 1  # the second request joined the queued run

    # finished with result files -> reused, no new job even after the first job is done
    docset_hash = DocsetHash.hash_iri(iri)
    db.finish_job(_jobs(db)[0]["id"])
    db.set_docset_status(docset_hash, "finished")
    _touch_result_files(tmp_path, docset_hash)
    client.get("/start", query_string={"docset_iri": iri})
    assert len(_jobs(db)) == 1

    # failed -> a new request queues a new run
    db.set_docset_status(docset_hash, "error", "boom")
    client.get("/start", query_string={"docset_iri": iri})
    assert [j["status"] for j in _jobs(db)] == ["done", "queued"]
    assert db.get_docset(docset_hash)["status"] == "pending"


def test_result_for_finished_and_failed_tasks(client, db, tmp_path):
    finished_hash, failed_hash = "f" * 32, "e" * 32
    _add_docset(db, finished_hash, "finished")
    _touch_result_files(tmp_path, finished_hash)
    _add_docset(db, failed_hash, "error", error="boom")
    db.save_task("uuid-finished", finished_hash)
    db.save_task("uuid-failed", failed_hash)

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


def test_healthz_checks_the_database(client, db, monkeypatch):
    assert client.get("/healthz").status_code == 200

    monkeypatch.setattr(db.Config, "DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none")
    db.reset_engine()
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "unavailable"


def test_urls_respect_proxy_prefix(client, db, tmp_path):
    # Caddy mounts the app under /thesis and sends the prefix in X-Forwarded-Prefix
    headers = {"X-Forwarded-Prefix": "/thesis", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "example.test"}
    _add_docset(db, "d" * 32, "finished")
    _touch_result_files(tmp_path, "d" * 32)
    db.save_task("uuid-prefixed", "d" * 32)

    body = client.get("/result", query_string={"uuid": "uuid-prefixed"}, headers=headers).get_json()
    assert body["url"] == f"https://example.test/thesis/visualisations/index.html?docset={'d' * 32}"

    spec = client.get("/swagger.yaml", headers=headers).get_data(as_text=True)
    assert 'basePath: "/thesis"' in spec
    assert 'basePath: "/"' in client.get("/swagger.yaml").get_data(as_text=True)
