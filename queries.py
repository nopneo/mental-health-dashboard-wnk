"""ดึงข้อมูลงานสุขภาพจิตจาก HOSxP แล้วประกอบเป็นทะเบียนผู้ป่วย + ตัวเลขสรุป

ข้อตกลง (ดู DATA_MAP.md):
  - ไม่ใช้ทะเบียนคลินิก (ของ รพ.วังน้ำเขียวไม่ได้อัปเดตตั้งแต่ 2019) → นิยามผู้ป่วยในความดูแลจากข้อมูลจริง 12 เดือนล่าสุด
      มี dx F00–F99 หรือ X60–X84 (ทำร้ายตนเอง) · หรือได้ยาฉีด LAI · หรือ 8Q ≥ 17
  - F51 นอนไม่หลับอย่างเดียว แยกเป็นกลุ่มของตัวเอง ไม่นับในยอดหลักและไม่คิดสี
  - นัด = นัดทุกคลินิก (ผู้ป่วยจิตเวชหลายรายได้นัดที่ตรวจโรคทั่วไป/NCD) หรือเฉพาะ MH_CLINIC ถ้าตั้งไว้
  - ขาดนัด = นัดล่าสุดที่ผ่านมาแล้วเกิน 7 วัน และไม่มี visit ตั้งแต่ 3 วันก่อนนัด
  - ยาฉีด: ครบกำหนด = วันนัดที่ออกในวันฉีดครั้งล่าสุด (เลือกนัดที่ใกล้รอบฉีดปกติที่สุด)
      ไม่มีนัด = ฉีดล่าสุด + มัธยฐานของช่วงห่าง (7–90 วัน, ไม่มีประวัติ = 28 วัน) · เลยกำหนดเกิน 7 วัน = ขาดยาฉีด
  - สี: ประเมิน 3 ข้อ (กินยา · ญาติดูแล · ไม่ใช้สารเสพติด) ดีครบ = เขียว · ไม่ดี 1–2 = เหลือง · ไม่ดีครบ 3 = แดง
      กินยา/สารเสพติด มีค่าเริ่มต้นจาก HOSxP · ผลประเมินในแอปชนะ ถ้าประเมินหลังหลักฐานล่าสุดจาก HOSxP
      ญาติดูแล มีจากการประเมินเท่านั้น ยังไม่ประเมิน = คิดสีจาก 2 ข้อ และติดป้าย "ประเมินไม่ครบ"
"""

import csv
import os
import re
import statistics
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import store
from cache import TTLCache
from db import fetch, num

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---- ค่าเฉพาะโรงพยาบาล (ตั้งใน .env) — ค่าเริ่มต้นคือของ รพ.วังน้ำเขียว
# ค่าเหล่านี้ถูกต่อเข้า SQL จึงรับเฉพาะตัวอักษร/ตัวเลข ค่าอื่นถูกทิ้ง


def _codes(name, default):
    return tuple(c for c in (x.strip() for x in os.getenv(name, default).split(","))
                 if re.fullmatch(r"[A-Za-z0-9]{1,12}", c))


def _sql_in(codes):
    return "(" + ",".join(f"'{c}'" for c in codes) + ")" if codes else "('')"


#: จังหวัด/อำเภอของ รพ. ใน patient.chwpart / amppart
CHW = (_codes("MH_CHWPART", "30") or ("30",))[0]
AMP = (_codes("MH_AMPPART", "25") or ("25",))[0]
#: รหัส 5 หลักของ รพ. เอง — ใช้ใน village_pcu.csv แทนหมู่บ้านที่ รพ. ดูแลเอง
HOSPCODE = (_codes("MH_HOSPCODE", "10894") or ("10894",))[0]
#: คลินิกใน oapp.clinic ที่นับเป็นนัดของผู้ป่วย — "all" (ค่าเริ่มต้น) = ทุกคลินิก
#: ผู้ป่วยจิตเวชหลายรายได้นัดที่ตรวจโรคทั่วไป / NCD ไม่ใช่คลินิกจิตเวช จำกัดคลินิกแล้วจะไม่เห็นนัดและนับขาดนัดผิด
CLINICS = _codes("MH_CLINIC", "all")
ALL_CLINICS = not CLINICS or "all" in CLINICS
CLINIC_COND = "" if ALL_CLINICS else f"and a.clinic in {_sql_in(CLINICS)}"

#: ยาฉีดออกฤทธิ์นาน (LAI) "icode=ชื่อ;icode=ชื่อ" — ไม่ใส่ยาฉีดระงับอาการเฉียบพลัน (เช่น Zuclopenthixol acetate)
_LAI_DEFAULT = ("1000310=Fluphenazine decanoate;1000352=Haloperidol decanoate;"
                "1000281=Flupentixol depot 40;1000282=Flupentixol depot 100")
LAI_ICODES = {}
for _item in os.getenv("MH_LAI_ICODES", _LAI_DEFAULT).split(";"):
    _code, _, _name = _item.partition("=")
    if re.fullmatch(r"[A-Za-z0-9]{1,12}", _code.strip()):
        LAI_ICODES[_code.strip()] = _name.strip() or _code.strip()
LAI_SQL = _sql_in(tuple(LAI_ICODES))

#: รพ.จิตเวชที่ส่งต่อประจำ (referout.refer_hospcode) — ค่าเริ่มต้น รพ.จิตเวชนครราชสีมาราชนครินทร์
PSY_HOSP = _codes("MH_PSY_HOSPCODES", "12268,EA0012268")
PSY_HOSP_SQL = _sql_in(PSY_HOSP)

SELFHARM = "icd10 regexp '^X(6[0-9]|7[0-9]|8[0-4])'"
REG = f"""(select hn from ovstdiag where vstdate >= curdate() - interval 12 month
             and (icd10 like 'F%' or {SELFHARM})
           union select hn from opitemrece where vstdate >= curdate() - interval 12 month
             and icode in {LAI_SQL}
           union select o.hn from depression_screen s join ovst o on o.vn = s.vn
             where s.screen_datetime >= curdate() - interval 12 month and s.suicide_score >= 17)"""

# กลุ่มโรค เรียงตามลำดับความสำคัญ — กลุ่มหลักของผู้ป่วยคือกลุ่มแรกที่เข้า
GROUPS = [
    ("psy", "โรคจิต (F20–F29)", lambda c: "F20" <= c <= "F29"),
    ("bip", "Bipolar / Mania (F30–F31)", lambda c: c in ("F30", "F31")),
    ("dep", "ซึมเศร้า (F32–F39)", lambda c: "F32" <= c <= "F39"),
    ("harm", "ทำร้ายตนเอง / 8Q สูง", lambda c: c.startswith("X")),
    ("sub", "สุรา / สารเสพติด (F10–F19)", lambda c: "F10" <= c <= "F19"),
    ("org", "สมองเสื่อม / Organic (F00–F09)", lambda c: "F00" <= c <= "F09"),
    ("anx", "วิตกกังวล / เครียด (F40–F48)", lambda c: "F40" <= c <= "F48"),
    ("dev", "พัฒนาการ / เด็ก (F70–F98)", lambda c: "F70" <= c <= "F98"),
    ("oth", "จิตเวชอื่น ๆ (F50–F69, F99)", lambda c: c[:1] == "F" and ("F49" <= c <= "F69" or c == "F99")
                                                    and c != "F51"),
    ("slp", "นอนไม่หลับ (F51)", lambda c: c == "F51"),
]
GROUP_NAME = {k: n for k, n, _ in GROUPS}
MAIN_GROUPS = [k for k, _, _ in GROUPS if k != "slp"]

COLORS = {"green": "เขียว", "yellow": "เหลือง", "red": "แดง"}
CRITERIA = [("med", "การกินยา"), ("care", "ญาติดูแล"), ("sub", "ไม่ใช้สารเสพติด")]

MISSED_GRACE = 7         # วันหลังนัดที่ยังไม่นับว่าขาด
LAI_GRACE = 7            # วันหลังครบรอบฉีดที่ยังไม่นับว่าขาด
SUB_WINDOW = 180         # มี dx สารเสพติดภายในกี่วัน = ยังใช้อยู่
DC_FOLLOW = 30           # D/C ภายในกี่วันที่ต้องติดตาม


# ---------------------------------------------------------------- รพ.สต.

def _load_pcu():
    """village_pcu.csv: tmbpart,tambon,moo,village,hospcode,pcu — ไม่มีไฟล์ = ทุกคนในอำเภอเป็น "ไม่ทราบหมู่" """
    m, names = {}, {}
    path = os.getenv("MH_VILLAGE_CSV") or os.path.join(_HERE, "village_pcu.csv")
    if not os.path.exists(path):
        return m, names
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                m[(r["tmbpart"].strip().zfill(2), int(r["moo"]))] = r["hospcode"].strip()
            except (KeyError, ValueError, AttributeError):
                continue
            names[r["hospcode"].strip()] = r["pcu"].strip()
    return m, names


VILLAGE_PCU, PCU_NAME = _load_pcu()
#: ลำดับแสดงผล: รพ.สต. ตามชื่อ → เขต รพ. → ไม่ทราบหมู่ → นอกเขต
PCU_ORDER = sorted((c for c in PCU_NAME if c != HOSPCODE), key=lambda c: PCU_NAME[c]) + [HOSPCODE, "unk", "out"]
PCU_NAME.setdefault(HOSPCODE, "เขต รพ.")
PCU_NAME.update({"unk": "ในอำเภอ ไม่ทราบหมู่", "out": "นอกเขตอำเภอ"})


def pcu_of(chw, amp, tmb, moo):
    if chw != CHW or amp != AMP:
        return "out"
    try:
        return VILLAGE_PCU.get((tmb, int(moo)), "unk")
    except (TypeError, ValueError):
        return "unk"


# ---------------------------------------------------------------- SQL

def _q_patients():
    return fetch(f"""
        select p.hn, p.pname, p.fname, p.lname, p.sex, p.birthday,
          timestampdiff(year, p.birthday, curdate()) age,
          p.chwpart, p.amppart, p.tmbpart, p.moopart, p.addrpart,
          coalesce(nullif(trim(p.mobile_phone_number), ''), nullif(trim(p.hometel), '')) phone,
          p.informname, p.informtel, p.death
        from patient p join {REG} r on r.hn = p.hn""")


def _q_dx():
    return fetch(f"""
        select hn, left(icd10, 3) c, max(vstdate) last, min(vstdate) first12
        from ovstdiag where vstdate >= curdate() - interval 12 month
          and (icd10 like 'F%' or {SELFHARM})
        group by hn, c""")


def _q_first_dx():
    return fetch(f"""
        select d.hn, min(d.vstdate) first_dx from ovstdiag d join {REG} r on r.hn = d.hn
        where d.icd10 like 'F%' group by d.hn""")


def _q_lai():
    return fetch(f"""
        select hn, vstdate, max(icode) icode from opitemrece
        where vstdate >= curdate() - interval 400 day and icode in {LAI_SQL}
        group by hn, vstdate""")


def _q_visit():
    return fetch(f"""
        select o.hn, max(o.vstdate) last_visit from ovst o join {REG} r on r.hn = o.hn
        where o.vstdate >= curdate() - interval 400 day group by o.hn""")


def _q_app():
    """นัดรายรายการ — ใช้หานัดล่าสุด/ถัดไป และนัดฉีดยาที่ออกในวันฉีด"""
    return fetch(f"""
        select a.hn, a.vstdate, a.nextdate, c.name clinic from oapp a join {REG} r on r.hn = a.hn
        left join clinic c on c.clinic = a.clinic
        where a.nextdate >= curdate() - interval 365 day {CLINIC_COND}""")


def _q_admit_now():
    return fetch(f"""
        select i.hn, i.an, i.regdate, w.name ward from ipt i join {REG} r on r.hn = i.hn
        left join ward w on w.ward = i.ward
        where i.dchdate is null and i.regdate >= curdate() - interval 180 day""")


def _q_ipd():
    return fetch("""
        select i.hn, i.an, i.regdate, i.dchdate, d.icd10 from ipt i
        join iptdiag d on d.an = i.an and d.diagtype = '1' and d.icd10 like 'F%'
        where i.dchdate >= curdate() - interval 365 day""")


def _q_refer():
    return fetch(f"""
        select r.hn, r.refer_date, r.refer_hospcode, h.name dest from referout r
        join {REG} g on g.hn = r.hn left join hospcode h on h.hospcode = r.refer_hospcode
        where r.refer_date >= curdate() - interval 365 day
          and (r.refer_hospcode in {PSY_HOSP_SQL} or r.pdx like 'F%'
               or exists(select 1 from ovstdiag d where d.vn = r.vn and d.icd10 like 'F%'))""")


def _q_screen():
    return fetch("""
        select o.hn, s.screen_datetime, s.depression_score q9, s.suicide_score q8
        from depression_screen s join ovst o on o.vn = s.vn
        where s.screen_datetime >= curdate() - interval 365 day""")


# ---------------------------------------------------------------- helpers

def _d(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


TH_MONTH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
            "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def thai_date(d, short=False):
    d = _d(d)
    if not d:
        return "-"
    y = d.year + 543
    return f"{d.day} {TH_MONTH[d.month - 1]} {str(y)[2:] if short else y}"


def fiscal_year(d):
    """ปีงบประมาณ พ.ศ. (ต.ค.–ก.ย.)"""
    return d.year + 543 + (1 if d.month >= 10 else 0)


def _lai_status(dates, today, planned=()):
    """(รอบฉีด, ฉีดล่าสุด, ครบกำหนด, ตามนัด?, สถานะ) · สถานะ: ok / due (ครบใน 7 วัน) / late (เลยไม่เกิน 7 วัน) / overdue

    planned = วันนัดที่ออกในวันฉีดครั้งล่าสุด — แพทย์อาจเลื่อนรอบฉีด (เช่น จาก 4 เป็น 8 สัปดาห์) ใช้นัดก่อนรอบเฉลี่ย
    """
    dates = sorted(set(dates))
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if 7 <= (b - a).days <= 90]
    interval = int(statistics.median(gaps[-6:])) if gaps else 28
    last = dates[-1]
    due = last + timedelta(days=interval)
    plan = [d for d in planned if 7 <= (d - last).days <= 120]
    by_plan = bool(plan)
    if plan:
        due = min(plan, key=lambda d: abs((d - last).days - interval))
    if today > due + timedelta(days=LAI_GRACE):
        st = "overdue"
    elif today > due:
        st = "late"
    elif (due - today).days <= 7:
        st = "due"
    else:
        st = "ok"
    return interval, last, due, by_plan, st


# ---------------------------------------------------------------- ทะเบียน

def _criterion(auto_bad, evidence_date, manual_value, assess_date):
    """(ดี?, ที่มา) · ผลประเมินชนะถ้าประเมินหลังหลักฐานล่าสุดจาก HOSxP"""
    if manual_value is not None and (evidence_date is None or assess_date >= evidence_date):
        return bool(manual_value), "assess"
    if auto_bad is None:
        return None, None
    return (not auto_bad), "hosxp"


def build_registry():
    today = date.today()
    jobs = {"pt": _q_patients, "dx": _q_dx, "first": _q_first_dx, "lai": _q_lai, "visit": _q_visit,
            "app": _q_app, "admit": _q_admit_now, "ipd": _q_ipd, "refer": _q_refer, "screen": _q_screen}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        raw = {k: f.result() for k, f in futs.items()}

    pts = {}
    for r in raw["pt"]:
        if r["death"] == "Y":
            continue
        name = f"{r['pname'] or ''}{r['fname'] or ''} {r['lname'] or ''}".strip()
        pts[r["hn"]] = {
            "hn": r["hn"], "name": name, "sex": {"1": "ช", "2": "ญ"}.get(str(r["sex"]), "-"),
            "age": num(r["age"], None), "addr": r["addrpart"] or "", "moo": r["moopart"] or "",
            "pcu": pcu_of(r["chwpart"], r["amppart"], r["tmbpart"], r["moopart"]),
            "phone": r["phone"] or "", "inform": r["informname"] or "", "inform_tel": r["informtel"] or "",
            "codes": {}, "groups": [], "lai": None, "missed": None, "admit": None,
            "ipd12": 0, "last_dc": None, "refer": None, "screen": None,
            "last_visit": None, "last_app": None, "next_app": None, "next_app_clinic": "", "first_dx": None,
        }

    for r in raw["dx"]:
        p = pts.get(r["hn"])
        if p:
            p["codes"][r["c"]] = _d(r["last"])
    for r in raw["first"]:
        if r["hn"] in pts:
            pts[r["hn"]]["first_dx"] = _d(r["first_dx"])
    for r in raw["visit"]:
        if r["hn"] in pts:
            pts[r["hn"]]["last_visit"] = _d(r["last_visit"])
    made_on = {}                      # (hn, วันที่ออกนัด) → [วันนัด]
    for r in raw["app"]:
        p = pts.get(r["hn"])
        if not p:
            continue
        nd, vd = _d(r["nextdate"]), _d(r["vstdate"])
        if not nd:
            continue
        if vd:
            made_on.setdefault((r["hn"], vd), []).append(nd)
        if nd < today:
            if not p["last_app"] or nd > p["last_app"]:
                p["last_app"] = nd
        elif not p["next_app"] or nd < p["next_app"]:
            p["next_app"], p["next_app_clinic"] = nd, r["clinic"] or ""
    for r in raw["admit"]:
        if r["hn"] in pts:
            pts[r["hn"]]["admit"] = {"an": r["an"], "regdate": _d(r["regdate"]), "ward": r["ward"] or ""}
    for r in raw["ipd"]:
        p = pts.get(r["hn"])
        if p:
            p["ipd12"] += 1
            dc = _d(r["dchdate"])
            if not p["last_dc"] or dc > p["last_dc"]["date"]:
                p["last_dc"] = {"date": dc, "dx": r["icd10"]}
    for r in raw["refer"]:
        p = pts.get(r["hn"])
        rd = _d(r["refer_date"])
        if p and (not p["refer"] or rd > p["refer"]["date"]):
            p["refer"] = {"date": rd, "dest": r["dest"] or r["refer_hospcode"] or "-",
                          "psy": r["refer_hospcode"] in PSY_HOSP}
    for r in raw["screen"]:
        p = pts.get(r["hn"])
        sd = _d(r["screen_datetime"])
        if p and (not p["screen"] or sd >= p["screen"]["date"]):
            p["screen"] = {"date": sd, "q9": num(r["q9"], None), "q8": num(r["q8"], None)}

    lai_dates, lai_drug = {}, {}
    for r in raw["lai"]:
        vd = _d(r["vstdate"])
        lai_dates.setdefault(r["hn"], []).append(vd)
        if vd >= max(lai_dates[r["hn"]]):
            lai_drug[r["hn"]] = r["icode"]
    for hn, ds in lai_dates.items():
        p = pts.get(hn)
        if p and max(ds) >= today - timedelta(days=365):
            interval, last, due, by_plan, st = _lai_status(ds, today, made_on.get((hn, max(ds)), ()))
            p["lai"] = {"interval": interval, "last": last, "due": due, "by_plan": by_plan, "status": st,
                        "late_days": max((today - due).days, 0),
                        "drug": LAI_ICODES.get(lai_drug.get(hn), "อื่น ๆ")}

    assess = store.latest_assessments()

    for p in pts.values():
        codes = p["codes"]
        sc = p["screen"]
        p["q8_high"] = bool(sc and sc["q8"] is not None and sc["q8"] >= 17)
        p["groups"] = [k for k, _, test in GROUPS if any(test(c) for c in codes)]
        if p["lai"] and "psy" not in p["groups"]:
            p["groups"].insert(0, "psy")          # ได้ยาฉีด LAI = ดูแลแบบกลุ่มโรคจิต
        if p["q8_high"] and "harm" not in p["groups"]:
            p["groups"].append("harm")
        p["groups"] = [k for k in GROUP_NAME if k in p["groups"]] or ["oth"]
        p["selfharm"] = max((d for c, d in codes.items() if c.startswith("X")), default=None)
        p["group"] = p["groups"][0]
        p["main"] = p["group"] != "slp"
        sub_last = max((d for c, d in codes.items() if "F10" <= c <= "F19"), default=None)
        p["sub_last"] = sub_last

        # ขาดนัด
        la = p["last_app"]
        if la and la <= today - timedelta(days=MISSED_GRACE) and \
                (p["last_visit"] is None or p["last_visit"] < la - timedelta(days=3)):
            p["missed"] = {"date": la, "days": (today - la).days}

        # D/C ที่ยังไม่มี visit หลังจำหน่าย
        dc = p["last_dc"]
        p["dc_pending"] = bool(dc and dc["date"] >= today - timedelta(days=DC_FOLLOW)
                               and not (p["last_visit"] and p["last_visit"] > dc["date"]))

        # ---- สี
        a = assess.get(p["hn"])
        ad = _d(a["assess_date"]) if a else None
        med_ev = []
        if p["missed"]:
            med_ev.append(p["missed"]["date"])
        if p["lai"] and p["lai"]["status"] == "overdue":
            med_ev.append(p["lai"]["due"] + timedelta(days=LAI_GRACE))
        med_bad = bool(med_ev)
        sub_bad = bool(sub_last and sub_last >= today - timedelta(days=SUB_WINDOW))
        crit = {
            "med": _criterion(med_bad, max(med_ev) if med_ev else None, a and a["med"], ad),
            "care": _criterion(None, None, a and a["care"], ad),
            "sub": _criterion(sub_bad, sub_last if sub_bad else None, a and a["sub"], ad),
        }
        p["crit"] = crit
        p["assess"] = a
        known = [ok for ok, _ in crit.values() if ok is not None]
        bad = sum(1 for ok in known if not ok)
        p["bad"] = bad
        p["incomplete"] = len(known) < 3
        if not p["main"]:
            p["color"] = None
        elif bad == 0:
            p["color"] = "green"
        elif bad == 3:
            p["color"] = "red"
        else:
            p["color"] = "yellow"
        # คะแนนเรียงความเร่งด่วน (มาก = ต้องดูก่อน)
        p["priority"] = (bad * 10 + (5 if p["q8_high"] else 0) + (4 if p["dc_pending"] else 0)
                         + (3 if p["lai"] and p["lai"]["status"] == "overdue" else 0)
                         + (2 if p["missed"] else 0) + (1 if p["incomplete"] else 0))
    return pts


REG_TTL = int(os.getenv("MH_CACHE_TTL", "300"))
_reg_cache = TTLCache(maxsize=1)


def registry(force=False):
    if not force:
        hit = _reg_cache.get("r", REG_TTL)
        if hit:
            return hit
    t = _time.time()
    reg = {"pts": build_registry(), "at": datetime.now(), "secs": round(_time.time() - t, 1)}
    _reg_cache.set("r", reg)
    return reg


def invalidate():
    """หลังบันทึกผลประเมิน สีต้องเปลี่ยนทันที — ดึงใหม่ทั้งทะเบียน (~2,000 ราย ใช้เวลาไม่กี่วินาที)"""
    registry(force=True)


# ---------------------------------------------------------------- สรุป

FLAGS = {
    "missed": ("ขาดนัด", lambda p: p["missed"]),
    "lai": ("ผู้ป่วยยาฉีด", lambda p: p["lai"]),
    "lai_due": ("ยาฉีดครบกำหนด 7 วันนี้", lambda p: p["lai"] and p["lai"]["status"] in ("due", "late")),
    "lai_overdue": ("ยาฉีดเลยกำหนด", lambda p: p["lai"] and p["lai"]["status"] == "overdue"),
    "admit": ("นอน รพ. อยู่ตอนนี้", lambda p: p["admit"]),
    "dc": ("D/C ยังไม่ได้ติดตาม", lambda p: p["dc_pending"]),
    "q8": ("8Q เสี่ยงสูง", lambda p: p["q8_high"]),
    "selfharm": ("ทำร้ายตนเอง 12 เดือน", lambda p: p["selfharm"]),
    "refer": ("Refer 12 เดือน", lambda p: p["refer"]),
    "incomplete": ("ประเมินไม่ครบ", lambda p: p["main"] and p["incomplete"]),
    "new": ("รายใหม่ปีงบนี้", lambda p: p["first_dx"] and fiscal_year(p["first_dx"]) == fiscal_year(date.today())),
}


def filter_patients(pts, pcu=None, color=None, flag=None, group=None, q=None, allowed_pcu="all"):
    out = []
    for p in pts.values():
        if allowed_pcu != "all" and p["pcu"] != allowed_pcu:
            continue
        if pcu and p["pcu"] != pcu:
            continue
        if color and p["color"] != color:
            continue
        if group and group not in p["groups"]:
            continue
        if flag and not FLAGS[flag][1](p):
            continue
        if not group and not flag and not q and not p["main"]:
            continue
        if q and q not in p["hn"] and q not in p["name"]:
            continue
        out.append(p)
    out.sort(key=lambda p: (-p["priority"], p["name"]))
    return out


def summary(pts):
    today = date.today()
    main = [p for p in pts.values() if p["main"]]
    fy = fiscal_year(today)
    kpi = {
        "total": len(main),
        "slp_only": sum(1 for p in pts.values() if not p["main"]),
        "new_fy": sum(1 for p in main if p["first_dx"] and fiscal_year(p["first_dx"]) == fy),
        "missed": sum(1 for p in main if p["missed"]),
        "missed_30": sum(1 for p in main if p["missed"] and p["missed"]["days"] > 30),
        "lai": sum(1 for p in main if p["lai"]),
        "lai_due": sum(1 for p in main if p["lai"] and p["lai"]["status"] in ("due", "late")),
        "lai_overdue": sum(1 for p in main if p["lai"] and p["lai"]["status"] == "overdue"),
        "admit": sum(1 for p in pts.values() if p["admit"]),
        "dc30": sum(1 for p in main if p["last_dc"] and p["last_dc"]["date"] >= today - timedelta(days=DC_FOLLOW)),
        "dc_pending": sum(1 for p in main if p["dc_pending"]),
        "refer_month": sum(1 for p in pts.values() if p["refer"] and p["refer"]["date"] >= today.replace(day=1)),
        "refer_12m": sum(1 for p in pts.values() if p["refer"]),
        "q8": sum(1 for p in pts.values() if p["q8_high"]),
        "selfharm": sum(1 for p in pts.values() if p["selfharm"]),
        "fy": fy,
    }
    colors = {c: sum(1 for p in main if p["color"] == c) for c in COLORS}
    incomplete = sum(1 for p in main if p["incomplete"])
    groups = [{"key": k, "name": GROUP_NAME[k], "n": sum(1 for p in pts.values() if k in p["groups"]),
               "main": sum(1 for p in pts.values() if p["group"] == k)} for k in GROUP_NAME]

    by_pcu = []
    for code in PCU_ORDER:
        ps = [p for p in main if p["pcu"] == code]
        by_pcu.append({
            "code": code, "name": PCU_NAME[code], "n": len(ps),
            "red": sum(1 for p in ps if p["color"] == "red"),
            "yellow": sum(1 for p in ps if p["color"] == "yellow"),
            "green": sum(1 for p in ps if p["color"] == "green"),
            "psy": sum(1 for p in ps if "psy" in p["groups"]),
            "missed": sum(1 for p in ps if p["missed"]),
            "lai": sum(1 for p in ps if p["lai"]),
            "lai_overdue": sum(1 for p in ps if p["lai"] and p["lai"]["status"] == "overdue"),
            "incomplete": sum(1 for p in ps if p["incomplete"]),
        })
    return {"kpi": kpi, "colors": colors, "incomplete": incomplete, "groups": groups, "by_pcu": by_pcu}


# ---------------------------------------------------------------- หน้าสาธารณะ (ยอดรวมเท่านั้น)

AGE_BANDS = [("< 15", 0, 14), ("15–24", 15, 24), ("25–39", 25, 39), ("40–59", 40, 59), ("60+", 60, 200)]
LAI_STATUS = [("ok", "ฉีดตามรอบ", "#16a34a"), ("due", "ครบกำหนดใน 7 วัน", "#f59e0b"),
              ("late", "เลยกำหนด ≤ 7 วัน", "#fb923c"), ("overdue", "เลยกำหนด > 7 วัน", "#dc2626")]
_screen_cache = TTLCache(maxsize=1)


def screening_12m():
    """ผลคัดกรอง 2Q/9Q/8Q 12 เดือน (ทุกคนที่ถูกคัดกรอง ไม่เฉพาะผู้ป่วยในทะเบียน)"""
    hit = _screen_cache.get("s", REG_TTL)
    if hit:
        return hit
    r = fetch("""
        select count(*) screens, count(distinct o.hn) pts,
          sum(s.feel_depression_2_week = 'Y' or s.feel_boring_2_week = 'Y') q2_pos,
          sum(s.depression_score between 7 and 12) q9_mild, sum(s.depression_score between 13 and 18) q9_mod,
          sum(s.depression_score >= 19) q9_sev,
          sum(s.suicide_score between 1 and 8) q8_low, sum(s.suicide_score between 9 and 16) q8_mid,
          sum(s.suicide_score >= 17) q8_high
        from depression_screen s join ovst o on o.vn = s.vn
        where s.screen_datetime >= curdate() - interval 12 month""")
    out = {k: num(v) for k, v in (r[0] if r else {}).items()}
    _screen_cache.set("s", out)
    return out


def _pct(a, b):
    return round(a * 100.0 / b, 1) if b else 0.0


def public_summary(pts):
    s = summary(pts)
    today = date.today()
    main = [p for p in pts.values() if p["main"]]
    n = len(main) or 1
    k = s["kpi"]

    s["colors_items"] = [{"key": c, "label": "สี" + COLORS[c], "value": s["colors"][c],
                          "pct": _pct(s["colors"][c], n), "color": col}
                         for c, col in (("red", "#dc2626"), ("yellow", "#f5b014"), ("green", "#16a34a"))]
    s["crit_counts"] = [{"key": key, "label": lb,
                         "good": sum(1 for p in main if p["crit"][key][0] is True),
                         "bad": sum(1 for p in main if p["crit"][key][0] is False),
                         "na": sum(1 for p in main if p["crit"][key][0] is None)} for key, lb in CRITERIA]
    s["age"] = [{"label": lb, "value": sum(1 for p in main if p["age"] is not None and lo <= p["age"] <= hi)}
                for lb, lo, hi in AGE_BANDS]
    for a in s["age"]:
        a["pct"] = _pct(a["value"], n)
    male = sum(1 for p in main if p["sex"] == "ช")
    s["sex"] = [{"label": "ชาย", "value": male, "pct": _pct(male, n), "color": "#6366f1"},
                {"label": "หญิง", "value": n - male, "pct": _pct(n - male, n), "color": "#ec4899"}]

    lai = [p for p in main if p["lai"]]
    s["lai_status"] = [{"key": key, "label": lb, "color": col,
                        "value": sum(1 for p in lai if p["lai"]["status"] == key)} for key, lb, col in LAI_STATUS]
    for it in s["lai_status"]:
        it["pct"] = _pct(it["value"], len(lai))
    drugs = {}
    for p in lai:
        drugs[p["lai"]["drug"]] = drugs.get(p["lai"]["drug"], 0) + 1
    s["lai_drugs"] = sorted(({"name": d, "value": v} for d, v in drugs.items()), key=lambda x: -x["value"])
    s["lai_ontime_pct"] = _pct(sum(1 for p in lai if p["lai"]["status"] in ("ok", "due")), len(lai))

    dests = {}
    for p in pts.values():
        if p["refer"]:
            dests[p["refer"]["dest"]] = dests.get(p["refer"]["dest"], 0) + 1
    s["refer_dests"] = sorted(({"name": d, "value": v} for d, v in dests.items()), key=lambda x: -x["value"])[:5]
    s["ipd"] = {"episodes": sum(p["ipd12"] for p in pts.values()),
                "pts": sum(1 for p in pts.values() if p["ipd12"]),
                "dc30": k["dc30"], "dc_pending": k["dc_pending"], "dc_done": k["dc30"] - k["dc_pending"]}
    s["missed_pct"] = _pct(k["missed"], sum(1 for p in main if p["last_app"] or p["next_app"]))

    al = []
    if k["lai_overdue"]:
        al.append(("high", f"ยาฉีดเลยกำหนดเกิน 7 วัน {k['lai_overdue']} ราย", "ผู้ป่วยยาฉีด", "ติดตามด่วน",
                   "โทรตาม / ประสาน รพ.สต. ให้มาฉีด", "lai_overdue"))
    if k["dc_pending"]:
        al.append(("high", f"D/C ใน 30 วัน ยังไม่มา รพ. {k['dc_pending']} ราย", "หลังจำหน่าย", "ติดตามด่วน",
                   "เยี่ยมบ้าน / โทรติดตามหลังจำหน่าย", "dc"))
    if k["q8"]:
        al.append(("high", f"8Q เสี่ยงฆ่าตัวตายสูง {k['q8']} ราย", "คัดกรอง", "เฝ้าระวัง",
                   "ประเมินซ้ำ / ส่งพบแพทย์", "q8"))
    if k["missed_30"]:
        al.append(("mid", f"ขาดนัดเกิน 30 วัน {k['missed_30']} ราย", "คลินิกจิตเวช", "ติดตาม",
                   "ส่งรายชื่อให้ รพ.สต. ติดตาม", "missed"))
    if k["lai_due"]:
        al.append(("mid", f"ยาฉีดครบกำหนดใน 7 วัน {k['lai_due']} ราย", "ผู้ป่วยยาฉีด", "เตรียมนัด",
                   "แจ้งเตือนผู้ป่วย / ญาติ", "lai_due"))
    if s["incomplete"]:
        al.append(("info", f"ยังประเมินไม่ครบ 3 ข้อ {s['incomplete']:,} ราย", "บันทึก", "ข้อสังเกต",
                   "ประเมินเรื่องญาติดูแลระหว่างเยี่ยมบ้าน", "incomplete"))
    s["alerts"] = [dict(zip(("level", "issue", "where", "status", "action", "flag"), a)) for a in al]
    s["screen"] = screening_12m()
    s["today"] = today
    return s


# ---------------------------------------------------------------- ย้อนหลังรายปีงบ

FY_FROM = int(os.getenv("MH_FY_FROM", "2561"))
_yearly_cache = TTLCache(maxsize=1)

_FY = "year({c} + interval 3 month) + 543"


YEARLY_TTL = 6 * 3600
_yearly_lock = threading.Lock()


def yearly():
    """ข้อมูลรายปีเปลี่ยนช้าและดึงนาน (~15 วิ) — หมดอายุแล้วส่งของเดิมก่อน แล้วดึงใหม่เบื้องหลัง"""
    hit = _yearly_cache.get("y", YEARLY_TTL)
    if hit:
        return hit
    stale = _yearly_cache.get_stale("y")
    if stale:
        if _yearly_lock.acquire(blocking=False):
            threading.Thread(target=_yearly_refresh, daemon=True).start()
        return stale[1]
    with _yearly_lock:
        return _yearly_cache.get("y", YEARLY_TTL) or _yearly_build()


def _yearly_refresh():
    try:
        _yearly_build()
    finally:
        _yearly_lock.release()


def _yearly_build():
    start = f"{FY_FROM - 544}-10-01"
    sqls = {
        "dx": f"""select {_FY.format(c='vstdate')} fy, count(distinct hn) total,
                   count(distinct case when icd10 between 'F20' and 'F29zz' then hn end) psy,
                   count(distinct case when icd10 between 'F30' and 'F31zz' then hn end) bip,
                   count(distinct case when icd10 between 'F32' and 'F39zz' then hn end) dep,
                   count(distinct case when icd10 between 'F10' and 'F19zz' then hn end) sub,
                   count(distinct case when icd10 between 'F40' and 'F48zz' then hn end) anx
                 from ovstdiag where vstdate >= '{start}' and icd10 like 'F%' and icd10 not like 'F51%'
                 group by fy""",
        "new": f"""select {_FY.format(c='m')} fy, count(*) new_pts from
                   (select hn, min(vstdate) m from ovstdiag where icd10 like 'F%' and icd10 not like 'F51%'
                    group by hn) x where m >= '{start}' group by fy""",
        "lai": f"""select {_FY.format(c='vstdate')} fy, count(distinct hn) lai_pts, count(*) lai_inj
                  from opitemrece where vstdate >= '{start}' and icode in {LAI_SQL} group by fy""",
        "admit": f"""select {_FY.format(c='i.regdate')} fy, count(*) admits, count(distinct i.hn) admit_pts
                    from ipt i join iptdiag d on d.an = i.an and d.diagtype = '1' and d.icd10 like 'F%'
                    where i.regdate >= '{start}' group by fy""",
        "refer": f"""select {_FY.format(c='refer_date')} fy, count(*) refer_psy
                    from referout where refer_date >= '{start}' and refer_hospcode in {PSY_HOSP_SQL} group by fy""",
        "selfharm": f"""select {_FY.format(c='vstdate')} fy, count(distinct hn) selfharm
                       from ovstdiag where vstdate >= '{start}' and {SELFHARM} group by fy""",
        "screen": f"""select {_FY.format(c='screen_datetime')} fy, count(*) screens,
                     sum(depression_score >= 7) q9_pos, sum(suicide_score >= 17) q8_high
                     from depression_screen where screen_datetime >= '{start}' group by fy""",
        "app": f"""select {_FY.format(c='a.nextdate')} fy, count(*) apps,
                  sum(not exists(select 1 from ovst v where v.hn = a.hn
                      and v.vstdate between a.nextdate - interval 3 day and a.nextdate + interval 7 day)) missed
                  from oapp a join (select distinct hn from ovstdiag where icd10 like 'F%' and icd10 not like 'F51%') f
                    on f.hn = a.hn
                  where a.nextdate >= '{start}' {CLINIC_COND}
                    and a.nextdate < curdate() - interval 7 day group by fy""",
    }
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {k: ex.submit(fetch, s) for k, s in sqls.items()}
        raw = {k: f.result() for k, f in futs.items()}
    cur = fiscal_year(date.today())
    rows = {fy: {"fy": fy, "partial": fy == cur} for fy in range(FY_FROM, cur + 1)}
    for part in raw.values():
        for r in part:
            fy = int(num(r["fy"]))
            if fy in rows:
                rows[fy].update({k: num(v) for k, v in r.items() if k != "fy"})
    out = list(rows.values())
    for r in out:
        r["missed_pct"] = round(r.get("missed", 0) * 100.0 / r["apps"], 1) if r.get("apps") else None
    _yearly_cache.set("y", out)
    return out
