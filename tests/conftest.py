"""ตั้งค่าร่วมของ test — ไม่ต่อฐานข้อมูลจริง · ฐานข้อมูลผู้ใช้อยู่ในโฟลเดอร์ชั่วคราว"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MH_SECRET_KEY"] = "test-secret"
os.environ["MH_DATA_DIR"] = tempfile.mkdtemp(prefix="mh-test-")
os.environ.setdefault("MH_HOSPITAL_NAME", "โรงพยาบาลทดสอบ")      # ไม่ต้องอ่าน opdconfig
