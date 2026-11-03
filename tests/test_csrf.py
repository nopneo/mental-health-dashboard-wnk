import re

import pytest

import app as appmod
import store


@pytest.fixture
def client():
    appmod.app.config["TESTING"] = True
    if not store.check_login("nurse1", "secret-pass"):
        store.add_user("nurse1", "secret-pass", "พยาบาล 1")
    return appmod.app.test_client()


def test_post_without_any_session_token_is_rejected(client):
    # session ใหม่ยังไม่มี csrf → เดิม "" == "" ผ่าน · ต้องปฏิเสธ
    r = client.post("/login", data={"username": "nurse1", "password": "secret-pass"})
    assert r.status_code == 400


def test_login_with_token_from_form_works(client):
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
    r = client.post("/login", data={"csrf": token, "username": "nurse1", "password": "secret-pass"})
    assert r.status_code == 302


def test_wrong_token_is_rejected(client):
    client.get("/login")
    r = client.post("/login", data={"csrf": "0" * 32, "username": "nurse1", "password": "secret-pass"})
    assert r.status_code == 400
