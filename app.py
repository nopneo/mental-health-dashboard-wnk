"""Mental Health Dashboard — แดชบอร์ดงานสุขภาพจิต (HOSxP)

หน้าภาพรวม (/) และย้อนหลังรายปี เปิดสาธารณะ — ยอดรวมเท่านั้น
หน้ารายชื่อ/รายละเอียดผู้ป่วยต้อง login · ผู้ใช้ รพ.สต. เห็นรายชื่อเฉพาะในเขตของตัวเอง

รันเอง:      python app.py            → http://127.0.0.1:6300/
production:  gunicorn -b 0.0.0.0:6300 app:app   (ออกผ่าน sub-path เช่น /mh/)
สร้างผู้ใช้:  python manage.py adduser <username> --display "ชื่อ" [--hospcode 02840] [--admin]
"""

import os
import secrets
from datetime import date
from functools import wraps
from urllib.parse import urlsplit

from flask import (Flask, abort, flash, g, redirect, render_template, request, session, url_for)

import db
import queries
import store
from cache import TTLCache

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass


class PrefixMiddleware:
    """อ่าน X-Forwarded-Prefix → SCRIPT_NAME และ X-Forwarded-Proto → url_scheme (sub-path หลัง proxy)"""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "")
        if prefix:
            environ["SCRIPT_NAME"] = prefix.rstrip("/")
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix):] or "/"
        proto = environ.get("HTTP_X_FORWARDED_PROTO")
        if proto:
            environ["wsgi.url_scheme"] = proto
        return self.wsgi_app(environ, start_response)


app = Flask(__name__)
app.wsgi_app = PrefixMiddleware(app.wsgi_app)
app.secret_key = os.getenv("MH_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.getenv("MH_COOKIE_SECURE", "0") == "1",
                  PERMANENT_SESSION_LIFETIME=8 * 3600)

VERSION = "0.2.0"


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"     # มีชื่อผู้ป่วย — ห้ามแคชที่ proxy/เบราว์เซอร์
    return resp


# ---------------------------------------------------------------- auth

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        uid = session.get("uid")
        g.user = store.get_user(uid) if uid else None
        if not g.user:
            # script_root = sub-path หลัง proxy (เช่น /mh) — ไม่ใส่จะเด้งกลับไปเว็บหลักของ รพ.
            return redirect(url_for("login", next=request.script_root + request.full_path
                                    if request.method == "GET" else None))
        return f(*a, **kw)
    return wrap


def _csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def _check_csrf():
    if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "")):
        abort(400)


_HERE = os.path.dirname(os.path.abspath(__file__))
_hosp_cache = TTLCache(maxsize=1)


def hospital_name():
    """ชื่อโรงพยาบาล: env MH_HOSPITAL_NAME > opdconfig.hospitalname ของ HOSxP > "โรงพยาบาล" """
    env = os.getenv("MH_HOSPITAL_NAME")
    if env:
        return env
    hit = _hosp_cache.get("n", 3600)
    if hit:
        return hit
    try:
        name = (db.fetch_one("select hospitalname from opdconfig limit 1").get("hospitalname") or "").strip()
    except Exception:
        name = ""
    name = name or "โรงพยาบาล"
    _hosp_cache.set("n", name)
    return name


@app.context_processor
def _ctx():
    img = lambda n: os.path.exists(os.path.join(_HERE, "static", "img", n))
    return {"version": VERSION, "csrf_token": _csrf_token, "user": g.get("user"),
            "hospital": hospital_name(), "hospital_en": os.getenv("MH_HOSPITAL_NAME_EN", ""),
            "contact": os.getenv("MH_CONTACT", "งานสุขภาพจิต " + hospital_name()),
            "logo_url": url_for("static", filename="img/hospital_logo.png" if img("hospital_logo.png") else "img/logo.png"),
            "hero_url": url_for("static", filename="img/hero.jpg") if img("hero.jpg") else None,
            "own_hospcode": queries.HOSPCODE, "pcu_count": len([c for c in queries.PCU_ORDER if c not in (queries.HOSPCODE, "unk", "out")]),
            "thai_date": queries.thai_date, "COLORS": queries.COLORS, "PCU_NAME": queries.PCU_NAME,
            "GROUP_NAME": queries.GROUP_NAME, "FLAGS": queries.FLAGS, "CRITERIA": queries.CRITERIA,
            "today": date.today()}


def _safe_next(nxt):
    """next= หลัง login: รับเฉพาะ path ในแอป · เบราว์เซอร์ตัด tab/ขึ้นบรรทัด และอ่าน \\ เป็น /
    ("/\\t/evil" → "//evil") จึงปฏิเสธอักขระควบคุมและ \\ ทั้งหมด"""
    if "\\" in nxt or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in nxt):
        return None
    parts = urlsplit(nxt)
    if parts.scheme or parts.netloc or nxt.startswith("//"):
        return None
    return nxt if nxt.startswith(request.script_root + "/") else None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        _check_csrf()
        u = store.check_login(request.form.get("username", "").strip(), request.form.get("password", ""))
        if u:
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            return redirect(_safe_next(request.args.get("next") or "") or url_for("patients"))
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    _check_csrf()
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- pages

def _scope():
    """รหัส รพ.สต. ที่ผู้ใช้เห็นรายชื่อได้ ('all' = ทุกแห่ง)"""
    return g.user["hospcode"] or "all"


def _load_user():
    """หน้าสาธารณะ: ไม่บังคับ login แต่ถ้า login อยู่ให้รู้ว่าเป็นใคร (แสดงปุ่มรายชื่อ)"""
    uid = session.get("uid")
    g.user = store.get_user(uid) if uid else None


@app.route("/")
def dashboard():
    """หน้าสาธารณะแบบ ER/IPD Dashboard — ยอดรวมเท่านั้น ไม่มีชื่อ/HN ผู้ป่วย ใครเข้าดูก็ได้"""
    _load_user()
    reg = queries.registry()
    years = queries.yearly()
    return render_template("public.html", s=queries.public_summary(reg["pts"]), reg=reg,
                           years=years[-10:])


@app.route("/patients")
@login_required
def patients():
    reg = queries.registry()
    a = request.args
    pcu = a.get("pcu") if a.get("pcu") in queries.PCU_NAME else None
    color = a.get("color") if a.get("color") in queries.COLORS else None
    flag = a.get("flag") if a.get("flag") in queries.FLAGS else None
    group = a.get("group") if a.get("group") in queries.GROUP_NAME else None
    q = (a.get("q") or "").strip()[:40] or None
    scope = _scope()
    if scope != "all":
        pcu = scope
    rows = queries.filter_patients(reg["pts"], pcu=pcu, color=color, flag=flag, group=group, q=q,
                                   allowed_pcu=scope)
    return render_template("patients.html", rows=rows, reg=reg, scope=scope,
                           f={"pcu": pcu, "color": color, "flag": flag, "group": group, "q": q})


@app.route("/patient/<hn>", methods=["GET", "POST"])
@login_required
def patient(hn):
    reg = queries.registry()
    p = reg["pts"].get(hn)
    if not p or (_scope() != "all" and p["pcu"] != _scope()):
        abort(404)
    if request.method == "POST":
        _check_csrf()
        f = request.form
        val = lambda k: {"1": 1, "0": 0}.get(f.get(k))
        try:
            ad = date.fromisoformat(f.get("assess_date", ""))
        except ValueError:
            ad = date.today()
        ad = min(ad, date.today())
        store.add_assessment(hn, ad.isoformat(), val("med"), val("care"), val("sub"),
                             f.get("note"), g.user["username"])
        queries.invalidate()
        flash("บันทึกผลประเมินแล้ว")
        return redirect(url_for("patient", hn=hn))
    return render_template("patient.html", p=p, history=store.history(hn))


@app.route("/yearly")
def yearly():
    _load_user()
    return render_template("yearly.html", rows=queries.yearly())


@app.route("/refresh", methods=["POST"])
@login_required
def refresh():
    _check_csrf()
    queries.registry(force=True)
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url)
                    else url_for("dashboard"))


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1" if debug else os.getenv("MH_HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "6300")), debug=debug)
