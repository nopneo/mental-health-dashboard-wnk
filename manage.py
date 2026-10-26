"""จัดการผู้ใช้ของแอป (รันบนเครื่องที่มีไฟล์ data/mh.sqlite)

  python manage.py adduser <username> --display "ชื่อ" [--hospcode 02840] [--admin]
  python manage.py passwd <username>
  python manage.py disable <username>      # ปิดบัญชี (เจ้าหน้าที่ย้ายหน่วย/ลาออก) · enable = เปิดคืน
  python manage.py users
  python manage.py audit [--hn 0001234] [--user somchai] [--limit 100]   # ใครเปิดดู/บันทึกผู้ป่วยรายไหน
"""

import argparse
import getpass
import sys

import queries
import store


def _ask_password():
    pw = getpass.getpass("รหัสผ่าน: ")
    if len(pw) < 8 or pw != getpass.getpass("ยืนยันรหัสผ่าน: "):
        sys.exit("รหัสผ่านต้องยาวอย่างน้อย 8 ตัวและพิมพ์ตรงกันทั้งสองครั้ง")
    return pw


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("adduser")
    a.add_argument("username")
    a.add_argument("--display", required=True)
    a.add_argument("--hospcode", default="all", help="'all' หรือรหัส รพ.สต. เช่น 02840")
    a.add_argument("--admin", action="store_true")
    p = sub.add_parser("passwd")
    p.add_argument("username")
    for name in ("disable", "enable"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("users")
    au = sub.add_parser("audit")
    au.add_argument("--hn")
    au.add_argument("--user")
    au.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    if args.cmd == "adduser":
        if args.hospcode != "all" and args.hospcode not in queries.PCU_NAME:
            sys.exit("ไม่รู้จักรหัส รพ.สต. นี้: " + args.hospcode)
        store.add_user(args.username, _ask_password(), args.display, args.hospcode, args.admin)
        print("เพิ่มผู้ใช้แล้ว")
    elif args.cmd == "passwd":
        store.set_password(args.username, _ask_password())
        print("เปลี่ยนรหัสผ่านแล้ว")
    elif args.cmd in ("disable", "enable"):
        if not store.set_active(args.username, args.cmd == "enable"):
            sys.exit("ไม่พบผู้ใช้นี้")
        print("ปิดบัญชีแล้ว" if args.cmd == "disable" else "เปิดบัญชีแล้ว")
    elif args.cmd == "audit":
        for r in store.audit(args.hn, args.user, args.limit):
            print(f"{r['at']}  {r['username']:<16} {r['action']:<13} {r['hn'] or '':<10} {r['ip'] or '':<15} {r['detail'] or ''}")
    else:
        for u in store.list_users():
            print(f"{u['username']:<16} {u['hospcode']:<6} {'admin' if u['is_admin'] else '':<6} {'' if u['active'] else '(ปิด)':<5} {u['display']}")


if __name__ == "__main__":
    main()
