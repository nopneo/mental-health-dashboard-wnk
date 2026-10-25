"""สร้างคู่มือติดตั้ง PDF จาก INSTALL.md → docs/MH_Dashboard_install_manual.pdf

    python docs/build_manual.py

ต้องมี markdown + playwright (chromium) · ฟอนต์ Sarabun/Kanit จาก static/fonts
"""

import os
import re

import markdown
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FONTS = os.path.join(ROOT, "static", "fonts").replace("\\", "/")

CSS = f"""
@font-face {{ font-family: Sarabun; font-weight: 400; src: url('file:///{FONTS}/Sarabun-Regular.ttf'); }}
@font-face {{ font-family: Sarabun; font-weight: 700; src: url('file:///{FONTS}/Sarabun-Bold.ttf'); }}
@font-face {{ font-family: Kanit; font-weight: 600; src: url('file:///{FONTS}/Kanit-SemiBold.ttf'); }}
body {{ font-family: Sarabun, sans-serif; font-size: 11.5pt; line-height: 1.6; color: #22223a; }}
h1, h2, h3 {{ font-family: Kanit, sans-serif; font-weight: 600; color: #3b1d6e; }}
h1 {{ font-size: 24pt; margin: 0 0 4px; }}
h2 {{ font-size: 16pt; margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 2px solid #e3d8fb; break-after: avoid; }}
h3 {{ font-size: 13pt; margin: 16px 0 6px; break-after: avoid; }}
.cover {{ background: linear-gradient(120deg, #3b1d6e, #7c3aed 60%, #14b8a6); color: #fff; border-radius: 14px;
          padding: 22px 26px; margin-bottom: 18px; }}
.cover h1 {{ color: #fff; }} .cover p {{ margin: 4px 0 0; opacity: .92; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 10pt; break-inside: avoid; }}
th {{ background: #6d28d9; color: #fff; text-align: left; padding: 5px 8px; font-weight: 700; }}
td {{ border-bottom: 1px solid #ece6f8; padding: 5px 8px; vertical-align: top; }}
tr:nth-child(even) td {{ background: #faf7ff; }}
code {{ font-family: Consolas, monospace; font-size: 9.5pt; background: #f1ebff; padding: 1px 4px; border-radius: 4px; }}
pre {{ background: #1f1b2e; color: #f1ecff; padding: 10px 12px; border-radius: 8px; font-size: 9pt;
       white-space: pre-wrap; word-break: break-all; break-inside: avoid; }}
pre code {{ background: none; color: inherit; padding: 0; }}
a {{ color: #6d28d9; text-decoration: none; }}
li {{ margin: 2px 0; }}
img.shot {{ width: 100%; border-radius: 10px; border: 1px solid #e3d8fb; margin: 8px 0; }}
"""


def main():
    version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())[1]
    md = open(os.path.join(ROOT, "INSTALL.md"), encoding="utf-8").read()
    md = md.replace("- [ ] ", "- ☐ ")
    title, rest = md.split("\n", 1)
    body = markdown.markdown(rest, extensions=["tables", "fenced_code"])
    # ภาพหน้าจอหลังหัวข้อ 1 (หน้าสาธารณะเป็นยอดรวม · รายชื่อ/ฟอร์มเป็นภาพจำลอง)
    shots = "".join(f"<img class='shot' src='img/{n}.jpg'>" for n in ("patients", "assess"))
    body = body.replace("<h2>2.", f"{shots}<h2>2.", 1)
    html = (f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>"
            f"<div class='cover'><h1>{title.lstrip('# ')}</h1>"
            f"<p>แดชบอร์ดงานสุขภาพจิตจาก HOSxP · เวอร์ชัน {version}</p>"
            f"<p>github.com/nopneo/mental-health-dashboard-wnk</p></div>{body}")
    src = os.path.join(HERE, "_manual_build.html")          # อยู่ใน docs/ เพื่อให้ path รูปถูก
    open(src, "w", encoding="utf-8").write(html)
    out = os.path.join(HERE, "MH_Dashboard_install_manual.pdf")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            page.goto("file:///" + src.replace("\\", "/"))
            page.wait_for_timeout(600)
            page.pdf(path=out, format="A4", print_background=True, display_header_footer=True,
                     header_template="<span></span>",
                     footer_template="<div style='font-size:8px;width:100%;text-align:center;color:#8b7fa8'>"
                                     "Mental Health Dashboard · <span class='pageNumber'></span>/<span class='totalPages'></span></div>",
                     margin={"top": "14mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
            b.close()
    finally:
        os.remove(src)
    print(out, os.path.getsize(out) // 1024, "KB", "version", version)


if __name__ == "__main__":
    main()
