"""สร้าง dist/mental_health_dashboard-<version>.zip สำหรับแจก รพ. อื่น

ไม่รวมไฟล์เฉพาะ รพ.วังน้ำเขียว: .env, data/, village_pcu.csv, โลโก้/ภาพ รพ., DATA_MAP.md
"""

import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["app.py", "cache.py", "db.py", "queries.py", "store.py", "manage.py", "requirements.txt",
         ".env.example", "village_pcu.example.csv", "README.md", "INSTALL.md", "LICENSE", "NOTICE.md",
         "SECURITY.md", "CHANGELOG.md", os.path.join("docs", "MH_Dashboard_install_manual.pdf")]
DIRS = ["templates", "static", "deploy"]
SKIP = {os.path.join("static", "img", "hospital_logo.png"), os.path.join("static", "img", "hero.jpg")}


def main():
    version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(HERE, "app.py"), encoding="utf-8").read()).group(1)
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    out = os.path.join(HERE, "dist", f"mental_health_dashboard-{version}.zip")
    root = "mental_health_dashboard"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(os.path.join(HERE, f), f"{root}/{f.replace(os.sep, '/')}")
        for d in DIRS:
            for base, _, names in os.walk(os.path.join(HERE, d)):
                for n in names:
                    rel = os.path.relpath(os.path.join(base, n), HERE)
                    if rel in SKIP or "__pycache__" in rel:
                        continue
                    z.write(os.path.join(HERE, rel), f"{root}/{rel.replace(os.sep, '/')}")
    print(out, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main()
