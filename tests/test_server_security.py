import pytest

import server

LOCAL = f"http://127.0.0.1:{server.PORT}"
EXPORT_JSON = {"format": "nope", "collage": {"canvas": {"aspect": [1, 1]}, "tiles": []}}


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    return server.app.test_client()


@pytest.mark.parametrize("base", [LOCAL, f"http://localhost:{server.PORT}"])
def test_local_get_allowed(client, base):
    assert client.get("/api/capabilities", base_url=base).status_code == 200
    r = client.get("/api/media", base_url=base)
    assert r.status_code == 200 and r.get_json() == []


@pytest.mark.parametrize("host", ["http://evil.example", f"http://evil.example:{server.PORT}",
                                  "http://127.0.0.1:9999"])
def test_foreign_host_blocked(client, host):
    assert client.get("/api/media", base_url=host).status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1:9999"])
def test_cross_origin_writes_blocked(client, origin):
    headers = {"Origin": origin}
    assert client.post("/api/export", json=EXPORT_JSON, headers=headers, base_url=LOCAL).status_code == 403
    assert client.delete("/api/media/abc", headers=headers, base_url=LOCAL).status_code == 403
    assert client.post("/api/media", headers=headers, base_url=LOCAL).status_code == 403


def test_cross_origin_get_still_served(client):
    assert client.get("/api/capabilities", headers={"Origin": "https://evil.example"}, base_url=LOCAL).status_code == 200


def test_text_plain_export_rejected(client):
    r = client.post("/api/export", data='{"format": "jpeg-srgb"}', content_type="text/plain",
                    headers={"Origin": LOCAL}, base_url=LOCAL)
    assert r.status_code == 415


def test_same_origin_json_reaches_handler(client):
    r = client.post("/api/export", json=EXPORT_JSON, headers={"Origin": LOCAL}, base_url=LOCAL)
    assert r.status_code == 400


def test_media_files_only_by_known_name(client):
    assert client.get("/m/unknown/preview.jpg", base_url=LOCAL).status_code == 404
    assert client.get("/m/unknown/source.heic", base_url=LOCAL).status_code == 404


def test_export_path_traversal_rejected(client):
    assert client.get("/exports/..%2fserver.py", base_url=LOCAL).status_code == 404
    assert client.get("/exports/../server.py", base_url=LOCAL).status_code == 404


def test_unknown_export_job(client):
    assert client.get("/api/export/doesnotexist", base_url=LOCAL).status_code == 404
