# MediLink AI — Setup Guide

## โครงสร้างโฟลเดอร์

```
medilink/
├── app.py                  ← Backend หลัก (Flask + SQLAlchemy + SocketIO)
├── requirements.txt
├── medilink.db             ← สร้างอัตโนมัติตอนรันครั้งแรก
├── static/
│   ├── css/  portal.css, styles.css
│   ├── js/   common.js, dashboard.js, login.js, pharmacy.js, register.js
│   └── uploads/            ← รูปภาพอาการที่อัปโหลด
└── templates/
    ├── base.html
    ├── dashboard_base.html ← Sidebar + SocketIO
    ├── dashboard.html      ← ผู้ป่วย
    ├── doctor_dashboard.html
    ├── pharmacy_dashboard.html
    ├── admin_dashboard.html
    ├── login.html, register.html, registered.html
```

---

## การติดตั้งและรัน

### 1. ติดตั้ง dependencies

```bash
cd medilink
pip install -r requirements.txt
```

> Python 3.9+ แนะนำ

### 2. รันเซิร์ฟเวอร์

```bash
python app.py
```

หรือถ้าต้องการ production mode:

```bash
python -m flask run --host=0.0.0.0 --port=5000
```

ครั้งแรก จะสร้าง DB อัตโนมัติและ seed ข้อมูลตัวอย่าง

### 3. เปิดเว็บ

```
http://localhost:5000
```

---

## บัญชี Demo (รหัสผ่านทุกบัญชี: `demo1234`)

| บทบาท | อีเมล |
|---|---|
| Admin | admin@demo.com |
| แพทย์ 1 | doctor@demo.com |
| แพทย์ 2 | doctor2@demo.com |
| ผู้ป่วย 1 | patient@demo.com |
| ผู้ป่วย 2 | patient2@demo.com |
| ร้านยา | pharmacy@demo.com |

---

## Environment Variables (ไม่บังคับ)

สร้างไฟล์ `.env` หรือตั้งค่า environment variables:

```env
SECRET_KEY=your-super-secret-key-here
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=llama-3.1-8b-instant
DATABASE_URL=sqlite:///medilink.db
```

### อัปเกรดเป็น PostgreSQL

```env
DATABASE_URL=postgresql://user:password@localhost/medilink
```

แล้วเพิ่ม `pip install psycopg2-binary` ใน requirements.txt

---

## Deploy บน Cloudflare Tunnel (แชร์ link ออก internet)

### 1. ติดตั้ง cloudflared

```bash
# macOS
brew install cloudflare/cloudflare/cloudflared

# Linux
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
```

### 2. รัน tunnel (ไม่ต้อง login)

```bash
# Terminal 1: รัน Flask
python app.py

# Terminal 2: เปิด Cloudflare Tunnel
cloudflared tunnel --url http://localhost:5000
```

จะได้ URL แบบ: `https://xxxxx.trycloudflare.com`

### ⚠️ หมายเหตุ Cloudflare + SocketIO

เพิ่ม config นี้ใน `app.py` ถ้า SocketIO มีปัญหา:

```python
socketio = SocketIO(app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_timeout=60,
    ping_interval=25
)
```

---

## Flow การทำงาน

```
ผู้ป่วย  → ปรึกษาแพทย์ + รูปอาการ
               ↓ (SocketIO แจ้งแพทย์ทุกคน)
แพทย์   → รับเคส → AI วิเคราะห์ → แชท → สั่งยา
               ↓ (SocketIO แจ้งผู้ป่วย)
ผู้ป่วย  → เห็นใบสั่งยา → เลือกร้าน + วิธีรับ
               ↓ (SocketIO แจ้งร้านยา)
ร้านยา  → รับออเดอร์ → เตรียมยา → จัดส่ง
               ↓ (SocketIO แจ้งผู้ป่วย)
ผู้ป่วย  → Track สถานะ real-time
```

---

## API Endpoints

| Method | Path | ใช้ทำอะไร |
|---|---|---|
| POST | `/api/consultations/request` | ผู้ป่วยส่งอาการ + รูป |
| POST | `/api/consultations/<id>/accept` | แพทย์รับเคส |
| POST | `/api/consultations/<id>/prescribe` | แพทย์สั่งยา |
| POST | `/api/consultations/<id>/confirm` | ผู้ป่วยยืนยัน + เลือกร้าน |
| GET | `/api/messages?consultation_id=...` | ดึงข้อความ chat |
| POST | `/api/messages/send` | ส่งข้อความ (REST fallback) |
| POST | `/api/ai/analyze-symptoms` | Gemini วิเคราะห์อาการ |
| POST | `/api/ai/recommend-medication` | Gemini แนะนำยา (แพทย์) |
| GET | `/api/pharmacies/nearby` | ร้านยาใกล้ที่มียานี้ |
| POST | `/api/pharmacy/orders/<id>/status` | ร้านยาอัปสถานะ |
| POST | `/api/stock/update` | ร้านยาอัปสต็อก |
| POST | `/api/admin/add_doctor` | Admin เพิ่มแพทย์ |
| POST | `/api/admin/add_pharmacy` | Admin เพิ่มร้านยา |
| POST | `/api/upload` | อัปโหลดรูป |

---

## บั๊กที่แก้ไขแล้ว

- ✅ `PHARMACIST` vs `PHARMACY` role ไม่สอดคล้องกัน → ใช้ `PHARMACY` ทั่วทั้งระบบ
- ✅ In-memory data หาย เมื่อ restart → SQLite (SQLAlchemy)
- ✅ Chat เป็น mock polling → Flask-SocketIO เรียลไทม์จริง
- ✅ `async def` ใน Flask ไม่ทำงาน → เปลี่ยนเป็น sync + gemini SDK
- ✅ `pharmacy.get('address', '')` error ใน template → ใช้ object attributes
- ✅ Multi-user ข้าม session ไม่ sync → SocketIO rooms per consultation
