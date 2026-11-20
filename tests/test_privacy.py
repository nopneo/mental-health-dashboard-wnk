"""คนที่ไม่ได้ login ต้องไม่เห็นยอดอ่อนไหว 1–4 ราย (ทำร้ายตนเอง · นอน รพ. · ส่งต่อ · 8Q) ทั้งตัวเลขและความสูงแท่งกราฟ
และไฟล์ Excel ต้องไม่ทำให้ข้อความจาก HOSxP กลายเป็นสูตร"""
import io
import os
import re

import pytest
from openpyxl import load_workbook

import app as appmod
import queries
import store

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROWS = [
    {"fy": 2568, "partial": False, "total": 900, "new_pts": 120, "psy": 200, "bip": 20, "dep": 300, "sub": 150,
     "anx": 90, "lai_pts": 40, "lai_inj": 300, "admits": 12, "refer_psy": 7, "selfharm": 9, "apps": 2000,
     "missed_pct": 10.5, "screens": 5000, "q9_pos": 60, "q8_high": 6},
    {"fy": 2569, "partial": True, "total": 800, "new_pts": 100, "psy": 190, "bip": 18, "dep": 280, "sub": 140,
     "anx": 80, "lai_pts": 38, "lai_inj": 250, "admits": 3, "refer_psy": 2, "selfharm": 1, "apps": 1500,
     "missed_pct": 9.0, "screens": 4000, "q9_pos": 50, "q8_high": 4},
]


@pytest.fixture
def client(monkeypatch):
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(queries, "yearly", lambda: ROWS)
    return appmod.app.test_client()


def _login(client):
    if not store.check_login("nurse1", "secret-pass"):
        store.add_user("nurse1", "secret-pass", "พยาบาล 1")
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/login").get_data(as_text=True)).group(1)
    assert client.post("/login", data={"csrf": token, "username": "nurse1", "password": "secret-pass"}).status_code == 302


def _selfharm_row(html):
    """เซลล์คอลัมน์ทำร้ายตนเอง (ลำดับที่ 13 หลังปีงบ) ของปีงบ 2569"""
    row = re.search(r"<td class=\"b\">2569\*</td>(.*?)</tr>", html, re.S).group(1)
    return re.findall(r"<td>(.*?)</td>", row)


def test_yearly_public_hides_small_counts(client):
    html = client.get("/yearly").get_data(as_text=True)
    cells = _selfharm_row(html)
    # admits 3 · refer_psy 2 · selfharm 1 → "<5"
    assert cells[9] == cells[10] == cells[11] == "&lt;5"
    assert "ปีงบ 2569 (ถึงปัจจุบัน): 1<" not in html            # tooltip แท่งกราฟ
    assert "ปีงบ 2568: 9<" in html                                # ≥ 5 แสดงตามจริง
    assert "login เพื่อดูตัวเลขจริง" in html


def test_yearly_hidden_bar_is_drawn_at_threshold_height(client):
    html = client.get("/yearly").get_data(as_text=True)
    fig = html.split('aria-label="ทำร้ายตนเอง X60–X84')[1].split("</figure>")[0]
    # vmax = 9 → แท่ง "<5" ต้องสูงเท่าค่า 5 (53.3) ไม่ใช่ค่าจริง 1 (10.7)
    assert "V" + str(120 - 5 / 9 * 96 + 3)[:6] in fig.replace(" ", "")
    assert "V" + str(120 - 1 / 9 * 96 + 3)[:6] not in fig.replace(" ", "")


def test_yearly_logged_in_sees_exact(client):
    _login(client)
    cells = _selfharm_row(client.get("/yearly").get_data(as_text=True))
    assert cells[9:12] == ["3", "2", "1"]


def test_cell_filter():
    with appmod.app.test_request_context("/"):
        from flask import g
        g.user = None
        assert appmod._cell(0) == "–" and appmod._cell(3) == "<5" and appmod._cell(1234) == "1,234"
        assert appmod.hidden(4) and not appmod.hidden(5) and not appmod.hidden(0)
        g.user = {"id": 1}
        assert appmod._cell(3) == "3" and not appmod.hidden(3)


def test_public_template_suppresses_sensitive_kpis():
    src = open(os.path.join(HERE, "templates", "public.html"), encoding="utf-8").read()
    for expr in ("k.selfharm|cell", "k.admit|cell", "k.refer_month|cell", "k.q8|cell", "s.ipd.episodes|cell",
                 "s.ipd.dc_pending|cell", "d.value|cell", "it.value|cell"):
        assert expr in src, expr
    assert "{{ k.admit }}" not in src and "{{ k.selfharm }}" not in src


def test_excel_text_never_becomes_formula(monkeypatch):
    monkeypatch.setattr(queries, "flag_text", lambda p: "")
    p = {"color": "red", "name": '=HYPERLINK("http://x","คลิก")', "hn": "000001", "sex": "ช", "age": 40,
         "addr": "+1 ม.2", "moo": "2", "pcu": queries.HOSPCODE, "group": "psy",
         "crit": {"med": (True,), "care": (None,), "sub": (False,)},
         "missed": None, "lai": None, "admit": None, "dc_pending": False, "q8_high": False, "screen": None,
         "selfharm": None, "refer": None, "next_app": None, "next_app_clinic": None, "incomplete": False}
    buf = queries.patients_workbook([p])
    ws = load_workbook(io.BytesIO(buf.getvalue()) if hasattr(buf, "getvalue") else buf).active
    assert ws["B2"].data_type == "s" and ws["B2"].value.startswith("=HYPERLINK")
    assert ws["F2"].data_type == "s"


def test_xl_text_marks_formula_like_strings():
    from openpyxl import Workbook
    ws = Workbook().active
    ws.append(["=1+1", "-2", "ปกติ", 5])
    for c in ws[1]:
        queries._xl_text(c)
    assert ws["A1"].data_type == "s" and ws["A1"].quotePrefix
    assert ws["B1"].data_type == "s"
    assert not ws["C1"].quotePrefix and ws["D1"].value == 5


def test_public_template_compiles():
    appmod.app.jinja_env.get_template("public.html")
