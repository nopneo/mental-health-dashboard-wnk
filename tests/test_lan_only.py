"""หน้ารายชื่อ/ผู้ป่วย/Excel/login เปิดได้เฉพาะเครื่องใน รพ. · IP จริงผ่าน Cloudflare · header จาก proxy ปลอมไม่ได้"""
import pytest

import app as appmod
import queries
import store

CF_EDGE = "172.70.1.1"
HOSP = "203.0.113.10"


def _cf(real):
    return {"headers": {"X-Forwarded-For": f"{real}, {CF_EDGE}", "CF-Connecting-IP": real},
            "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "LAN_NETS", appmod._nets(HOSP))
    monkeypatch.delenv("MH_LIST_LAN_ONLY", raising=False)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


@pytest.mark.parametrize("path", ["/patients", "/patients/export.xlsx", "/patient/123", "/login"])
def test_internet_gets_403(client, path):
    r = client.get(path, **_cf("1.2.3.4"))
    assert r.status_code == 403
    assert "เฉพาะเครื่องในโรงพยาบาล" in r.get_data(as_text=True)


def test_internet_cannot_post_login(client):
    r = client.post("/login", data={"username": "a", "password": "b"}, **_cf("1.2.3.4"))
    assert r.status_code == 403


def test_hospital_ip_via_cloudflare_allowed(client):
    assert client.get("/login", **_cf(HOSP)).status_code == 200
    assert client.get("/patients", **_cf(HOSP)).status_code == 302          # ไป login


def test_lan_domain_allowed(client):
    lan = {"headers": {"X-Forwarded-For": "192.168.11.25"}, "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}
    assert client.get("/login", **lan).status_code == 200


def test_spoofed_headers_from_direct_peer_not_trusted(client):
    # ต่อตรงจากเครื่องนอก (ไม่ใช่ proxy) ส่ง XFF/CF ปลอม → ใช้ IP จริงของ peer
    r = client.get("/login", headers={"X-Forwarded-For": "192.168.1.5", "CF-Connecting-IP": HOSP},
                   environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert r.status_code == 403


def test_cf_header_from_non_cloudflare_hop_ignored(client):
    with appmod.app.test_request_context("/", headers={"X-Forwarded-For": "9.9.9.9", "CF-Connecting-IP": HOSP},
                                         environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert appmod.client_ip() == "9.9.9.9" and not appmod.from_lan()


def test_client_ip_uses_real_ip_for_audit():
    with appmod.app.test_request_context("/", **_cf("1.2.3.4")):
        assert appmod.client_ip() == "1.2.3.4"


def test_public_pages_still_open_and_hide_login(client, monkeypatch):
    monkeypatch.setattr(queries, "registry", lambda fy=None, force=False: (_ for _ in ()).throw(RuntimeError()))
    r = client.get("/healthz", **_cf("1.2.3.4"))
    assert r.status_code == 200


def test_old_session_ignored_outside_hospital(client):
    with appmod.app.test_request_context("/", **_cf("1.2.3.4")):
        from flask import g, session
        session["uid"] = 1
        appmod._load_user()
        assert g.user is None


def test_opt_out(client, monkeypatch):
    monkeypatch.setenv("MH_LIST_LAN_ONLY", "0")
    assert client.get("/login", **_cf("1.2.3.4")).status_code == 200


@pytest.mark.parametrize("ip, lan", [("192.168.1.1", True), ("10.0.0.1", True), ("127.0.0.1", True),
                                     ("198.18.0.1", False), ("203.0.113.7", False), ("8.8.8.8", False),
                                     ("::ffff:192.168.1.1", True)])
def test_private_ranges_explicit(ip, lan):
    with appmod.app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
        assert appmod.from_lan() is lan


def test_prefix_only_from_trusted_proxy(client):
    r = client.get("/login", headers={"X-Forwarded-Prefix": "/evil"}, environ_base={"REMOTE_ADDR": "192.168.1.5"})
    assert "/evil/" not in r.get_data(as_text=True)
    r = client.get("/login", headers={"X-Forwarded-Prefix": "/mh"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert "/mh/static" in r.get_data(as_text=True)
    r = client.get("/login", headers={"X-Forwarded-Prefix": "//evil.example"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert "evil.example" not in r.get_data(as_text=True)
