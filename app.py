"""
MediLink AI — Flask + SQLAlchemy (SQLite) + Flask-SocketIO
เวอร์ชัน 2.0  |  ข้อมูลถาวร · แชทเรียลไทม์ · อัปโหลดรูป
"""
from __future__ import annotations

import json, math, os, time, traceback, uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from flask import (Flask, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_socketio import SocketIO, join_room, emit
from flask_sqlalchemy import SQLAlchemy

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent
UPLOAD_DIR   = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXT  = {"png", "jpg", "jpeg", "gif", "webp"}

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "medilink-secret-2025")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", f"sqlite:///{BASE_DIR}/medilink.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

db       = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Groq AI ───────────────────────────────────────────────────────────────────
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL     = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_API_URL   = "https://api.groq.com/openai/v1/chat/completions"
ai_paused_until = 0.0

def generate_chat_completion(
    messages: list[dict], max_tokens: int = 1024, temperature: float = 0.4
) -> tuple[str | None, str | None]:
    """Call Groq and return (text, error_reason) without exposing credentials."""
    global ai_paused_until
    if not GROQ_API_KEY:
        return None, "GROQ_NOT_CONFIGURED"
    if time.time() < ai_paused_until:
        secs = int(ai_paused_until - time.time())
        return None, f"GROQ_PAUSED:{secs}s"
    try:
        payload = json.dumps(
            {
                "model": GROQ_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib_request.Request(
            GROQ_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
        return text, None
    except urllib_error.HTTPError as exc:
        if exc.code == 429:
            ai_paused_until = time.time() + 10
            app.logger.warning("Groq rate-limited (429), pausing 10s")
            return None, "GROQ_PAUSED:RATE_LIMITED"
        app.logger.warning("Groq HTTP error: %s", exc.code)
        return None, f"GROQ_HTTP_ERROR:{exc.code}"
    except Exception as exc:
        ai_paused_until = time.time() + 10
        app.logger.warning("Groq unavailable: %s", type(exc).__name__)
        return None, f"GROQ_ERROR:{type(exc).__name__}"

def generate_with_gemini(prompt: str) -> tuple[str | None, str | None]:
    """Backward-compatible wrapper used by the existing AI endpoints."""
    return generate_chat_completion([{"role": "user", "content": prompt}])

# ══════════════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════════════

class User(db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20),  nullable=False)   # PATIENT | DOCTOR | PHARMACY | ADMIN
    name          = db.Column(db.String(100), nullable=False)
    specialty     = db.Column(db.String(100))   # DOCTOR
    hospital      = db.Column(db.String(200))   # DOCTOR
    pharmacy_id   = db.Column(db.Integer, db.ForeignKey("pharmacies.id"))  # PHARMACY
    is_active     = db.Column(db.Boolean, default=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    tos_accepted      = db.Column(db.Boolean, default=False)
    tos_accepted_at   = db.Column(db.DateTime)
    ai_doctor_consent = db.Column(db.Boolean, default=False)
    ai_consent_at     = db.Column(db.DateTime)

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)

    def to_session(self) -> dict:
        return {"id": self.id, "email": self.email,
                "role": self.role, "name": self.name}


class PatientProfile(db.Model):
    __tablename__   = "patient_profiles"
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True)
    allergies_json  = db.Column(db.Text, default="[]")
    conditions_json = db.Column(db.Text, default="[]")
    latitude        = db.Column(db.Float, default=13.7563)
    longitude       = db.Column(db.Float, default=100.5018)
    address         = db.Column(db.String(300))
    phone           = db.Column(db.String(20))

    @property
    def allergies(self): return json.loads(self.allergies_json or "[]")
    @allergies.setter
    def allergies(self, v): self.allergies_json = json.dumps(v, ensure_ascii=False)

    @property
    def conditions(self): return json.loads(self.conditions_json or "[]")
    @conditions.setter
    def conditions(self, v): self.conditions_json = json.dumps(v, ensure_ascii=False)


class Pharmacy(db.Model):
    __tablename__      = "pharmacies"
    id                 = db.Column(db.Integer, primary_key=True)
    name               = db.Column(db.String(100), nullable=False)
    address            = db.Column(db.String(300))
    latitude           = db.Column(db.Float, default=13.7380)
    longitude          = db.Column(db.Float, default=100.5600)
    phone              = db.Column(db.String(20))
    rating             = db.Column(db.Float, default=4.5)
    is_official        = db.Column(db.Boolean, default=True)
    delivery_radius_km = db.Column(db.Float, default=5.0)
    delivery_opts_json = db.Column(db.Text, default='["pickup","standard"]')

    @property
    def delivery_options(self): return json.loads(self.delivery_opts_json or '["pickup"]')


class Medicine(db.Model):
    __tablename__      = "medicines"
    id                 = db.Column(db.Integer, primary_key=True)
    name               = db.Column(db.String(100), nullable=False)
    generic            = db.Column(db.String(100))
    form               = db.Column(db.String(50), default="เม็ด")
    dosage             = db.Column(db.String(50))
    instruction        = db.Column(db.Text)
    purpose            = db.Column(db.Text)
    side_effects       = db.Column(db.Text)
    contraindications_j = db.Column(db.Text, default="[]")
    keywords_j         = db.Column(db.Text, default="[]")
    is_otc             = db.Column(db.Boolean, default=True)
    max_daily_dose     = db.Column(db.Integer, default=0)
    dose_unit          = db.Column(db.String(20), default="mg")

    @property
    def contraindications(self): return json.loads(self.contraindications_j or "[]")
    @property
    def keywords(self): return json.loads(self.keywords_j or "[]")


class PharmacyStock(db.Model):
    __tablename__ = "pharmacy_stock"
    id          = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey("pharmacies.id"), nullable=False)
    medicine_id = db.Column(db.Integer, db.ForeignKey("medicines.id"), nullable=False)
    quantity    = db.Column(db.Integer, default=0)
    price       = db.Column(db.Float, default=0)
    pharmacy    = db.relationship("Pharmacy")
    medicine    = db.relationship("Medicine")


class Consultation(db.Model):
    __tablename__  = "consultations"
    id             = db.Column(db.String(20), primary_key=True)
    patient_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    doctor_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    symptoms       = db.Column(db.Text, nullable=False)
    image_url      = db.Column(db.String(300))
    status         = db.Column(db.String(20), default="REQUESTED")
    # REQUESTED → ACCEPTED → PRESCRIBED → CONFIRMED → COMPLETED
    diagnosis      = db.Column(db.Text)
    medicine_id    = db.Column(db.Integer, db.ForeignKey("medicines.id"), nullable=True)
    pharmacy_id    = db.Column(db.Integer, db.ForeignKey("pharmacies.id"), nullable=True)
    delivery_type  = db.Column(db.String(50))
    med_price      = db.Column(db.Float, default=0)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    prescribed_at  = db.Column(db.DateTime)
    confirmed_at   = db.Column(db.DateTime)
    case_closed     = db.Column(db.Boolean, default=False)
    completed_at    = db.Column(db.DateTime)

    patient  = db.relationship("User", foreign_keys=[patient_id])
    doctor   = db.relationship("User", foreign_keys=[doctor_id])
    medicine = db.relationship("Medicine")
    pharmacy = db.relationship("Pharmacy")
    messages = db.relationship("Message", backref="consultation",
                               lazy="dynamic", order_by="Message.created_at")

    def to_dict(self):
        return {
            "id":            self.id,
            "patient_name":  self.patient.name  if self.patient  else "",
            "patient_id":    self.patient_id,
            "doctor_name":   self.doctor.name   if self.doctor   else "",
            "doctor_id":     self.doctor_id,
            "symptoms":      self.symptoms,
            "image_url":     self.image_url,
            "status":        self.status,
            "diagnosis":     self.diagnosis,
            "medicine": {"id": self.medicine.id, "name": self.medicine.name,
                         "instruction": self.medicine.instruction,
                         "dosage": self.medicine.dosage} if self.medicine else None,
            "pharmacy": {"id": self.pharmacy.id, "name": self.pharmacy.name,
                         "address": self.pharmacy.address, "phone": self.pharmacy.phone,
                         "delivery_options": self.pharmacy.delivery_options
                         } if self.pharmacy else None,
            "delivery_type": self.delivery_type,
            "med_price":     self.med_price,
            "created_at":    self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "prescribed_at": self.prescribed_at.strftime("%Y-%m-%d %H:%M") if self.prescribed_at else None,
            "confirmed_at":  self.confirmed_at.strftime("%Y-%m-%d %H:%M") if self.confirmed_at else None,
            "case_closed":   bool(self.case_closed),
            "completed_at":  self.completed_at.strftime("%Y-%m-%d %H:%M") if self.completed_at else None,
        }


class Message(db.Model):
    __tablename__   = "messages"
    id              = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.String(20), db.ForeignKey("consultations.id"), nullable=False)
    sender_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    text            = db.Column(db.Text, nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    sender          = db.relationship("User")

    def to_dict(self):
        return {
            "id":          self.id,
            "sender_id":   self.sender_id,
            "sender_name": self.sender.name if self.sender else "",
            "sender_role": self.sender.role if self.sender else "",
            "text":        self.text,
            "timestamp":   self.created_at.strftime("%H:%M"),
        }


class PharmacyOrder(db.Model):
    __tablename__   = "pharmacy_orders"
    id              = db.Column(db.String(20), primary_key=True)
    consultation_id = db.Column(db.String(20), db.ForeignKey("consultations.id"))
    pharmacy_id     = db.Column(db.Integer, db.ForeignKey("pharmacies.id"), nullable=False)
    patient_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    medicine_id     = db.Column(db.Integer, db.ForeignKey("medicines.id"))
    quantity        = db.Column(db.Integer, default=1)
    unit_price      = db.Column(db.Float, default=0)
    delivery_type   = db.Column(db.String(50), default="pickup")
    address         = db.Column(db.String(300))
    status          = db.Column(db.String(20), default="PENDING")
    # PENDING → ACCEPTED → PREPARING → COMPLETED
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    pharmacy        = db.relationship("Pharmacy")
    patient         = db.relationship("User")
    consultation    = db.relationship("Consultation")
    medicine        = db.relationship("Medicine")

    @property
    def total(self):
        direct_total = (self.unit_price or 0) * (self.quantity or 1)
        if direct_total:
            return direct_total
        return self.consultation.med_price if self.consultation else 0

    def to_dict(self):
        cons = self.consultation
        medicine = self.medicine or (cons.medicine if cons else None)
        return {
            "id":            self.id,
            "patient_name":  self.patient.name if self.patient else "",
            "medicine_name": medicine.name if medicine else "",
            "quantity":      self.quantity or 1,
            "unit_price":    self.unit_price or (cons.med_price if cons else 0) or 0,
            "total":         self.total or (cons.med_price if cons else 0) or 0,
            "delivery_type": self.delivery_type,
            "status":        self.status,
            "created_at":    self.created_at.strftime("%H:%M"),
        }


class Appointment(db.Model):
    __tablename__ = "appointments"
    id           = db.Column(db.String(20), primary_key=True)
    patient_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    doctor_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    appointment_date = db.Column(db.String(10), nullable=False)
    appointment_time = db.Column(db.String(5), nullable=False)
    reason       = db.Column(db.Text)
    status       = db.Column(db.String(20), default="CONFIRMED")
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    patient      = db.relationship("User", foreign_keys=[patient_id])
    doctor       = db.relationship("User", foreign_keys=[doctor_id])


class Review(db.Model):
    __tablename__ = "reviews"
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    target_type = db.Column(db.String(20), nullable=False)  # doctor | product
    target_id   = db.Column(db.Integer, nullable=False)
    rating      = db.Column(db.Integer, nullable=False)
    comment     = db.Column(db.Text, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    author      = db.relationship("User")


class Product(db.Model):
    __tablename__ = "products"
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(150), nullable=False)
    category    = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    usage       = db.Column(db.Text)
    price       = db.Column(db.Float, nullable=False)
    stock       = db.Column(db.Integer, default=0)
    icon        = db.Column(db.String(20), default="health")
    is_active   = db.Column(db.Boolean, default=True)


class ProductStock(db.Model):
    __tablename__ = "product_stock"
    id          = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey("pharmacies.id"), nullable=False)
    product_id  = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity    = db.Column(db.Integer, default=0)
    price       = db.Column(db.Float, nullable=False)
    pharmacy    = db.relationship("Pharmacy")
    product     = db.relationship("Product")
    __table_args__ = (
        db.UniqueConstraint("pharmacy_id", "product_id", name="uq_product_pharmacy"),
    )


class ShopOrder(db.Model):
    __tablename__ = "shop_orders"
    id          = db.Column(db.String(20), primary_key=True)
    patient_id  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total       = db.Column(db.Float, nullable=False)
    status      = db.Column(db.String(20), default="PAID")
    address     = db.Column(db.String(300))
    pharmacy_id = db.Column(db.Integer, db.ForeignKey("pharmacies.id"))
    delivery_type = db.Column(db.String(50), default="pickup")
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    patient     = db.relationship("User")
    pharmacy    = db.relationship("Pharmacy")
    items       = db.relationship("ShopOrderItem", backref="order", lazy=True)


class ShopOrderItem(db.Model):
    __tablename__ = "shop_order_items"
    id         = db.Column(db.Integer, primary_key=True)
    order_id   = db.Column(db.String(20), db.ForeignKey("shop_orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity   = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    product    = db.relationship("Product")


def seed_marketplace():
    """Seed feature data independently so existing databases receive new demo data."""
    if not Product.query.first():
        db.session.add_all([
            Product(name="Daily Defense Sunscreen SPF50+", category="ดูแลผิว",
                    description="ครีมกันแดดเนื้อบางเบา เหมาะสำหรับใช้ทุกวัน",
                    usage="ทาก่อนออกแดด 15 นาที และทาซ้ำทุก 2-3 ชั่วโมง",
                    price=490, stock=32, icon="sun"),
            Product(name="Gentle Skin Cleanser", category="ดูแลผิว",
                    description="ผลิตภัณฑ์ทำความสะอาดผิวสูตรอ่อนโยน ไม่มีน้ำหอม",
                    usage="ใช้ทำความสะอาดผิวหน้าเช้าและเย็น",
                    price=320, stock=18, icon="drop"),
            Product(name="Digital Thermometer", category="อุปกรณ์การแพทย์",
                    description="เครื่องวัดอุณหภูมิแบบดิจิทัล อ่านค่าได้รวดเร็ว",
                    usage="อ่านคู่มือก่อนใช้และทำความสะอาดหลังใช้งาน",
                    price=259, stock=24, icon="thermometer"),
            Product(name="Vitamin C 500 mg", category="อาหารเสริม",
                    description="ผลิตภัณฑ์เสริมอาหารวิตามินซีสำหรับผู้ใหญ่",
                    usage="รับประทานตามฉลาก ผู้มีโรคประจำตัวควรปรึกษาแพทย์",
                    price=380, stock=40, icon="vitamin"),
            Product(name="Automatic Blood Pressure Monitor", category="อุปกรณ์การแพทย์",
                    description="เครื่องวัดความดันต้นแขนอัตโนมัติ พร้อมบันทึกค่า",
                    usage="นั่งพักอย่างน้อย 5 นาทีก่อนวัดและวางแขนระดับหัวใจ",
                    price=1490, stock=9, icon="heart"),
            Product(name="Saline Nasal Spray", category="ดูแลทางเดินหายใจ",
                    description="สเปรย์น้ำเกลือสำหรับทำความสะอาดโพรงจมูก",
                    usage="ใช้ตามฉลาก หากมีอาการผิดปกติให้หยุดใช้",
                    price=145, stock=36, icon="spray"),
        ])
        db.session.flush()

    if not Review.query.first():
        patients = User.query.filter_by(role="PATIENT").all()
        doctors = User.query.filter_by(role="DOCTOR").all()
        products = Product.query.all()
        if patients and doctors:
            comments = [
                "คุณหมออธิบายละเอียด ใจดี และตอบคำถามเข้าใจง่าย",
                "รอคิวไม่นาน ระบบนัดหมายสะดวกมาก",
                "ให้คำแนะนำชัดเจนและติดตามอาการดี",
                "พูดคุยเป็นกันเอง แต่เวลาให้คำปรึกษาค่อนข้างสั้น",
            ]
            for i, comment in enumerate(comments):
                db.session.add(Review(
                    user_id=patients[i % len(patients)].id,
                    target_type="doctor",
                    target_id=doctors[i % len(doctors)].id,
                    rating=5 if i != 3 else 4,
                    comment=comment))
        if patients and products:
            product_comments = [
                "คุณภาพดี ใช้งานง่าย และจัดส่งรวดเร็ว",
                "สินค้าใช้งานได้ตรงตามรายละเอียด แต่ราคาค่อนข้างสูง",
                "แพ็กสินค้าดี ใช้แล้วพอใจ",
            ]
            for i, comment in enumerate(product_comments):
                db.session.add(Review(
                    user_id=patients[i % len(patients)].id,
                    target_type="product",
                    target_id=products[i % len(products)].id,
                    rating=5 if i != 1 else 4,
                    comment=comment))

    if not ProductStock.query.first():
        pharmacies = Pharmacy.query.all()
        products = Product.query.all()
        for pharmacy in pharmacies:
            for index, product in enumerate(products):
                base_quantity = max(3, product.stock - (pharmacy.id * 3) - index)
                price_adjustment = (pharmacy.id - 1) * 10
                db.session.add(ProductStock(
                    pharmacy_id=pharmacy.id,
                    product_id=product.id,
                    quantity=base_quantity,
                    price=product.price + price_adjustment))
    db.session.commit()


def ensure_schema():
    """Apply additive SQLite migrations for existing demo databases."""
    if db.engine.dialect.name != "sqlite":
        return
    migrations = {
        "consultations": [
            ("case_closed", "BOOLEAN DEFAULT 0"),
            ("completed_at", "DATETIME"),
        ],
        "shop_orders": [
            ("pharmacy_id", "INTEGER REFERENCES pharmacies(id)"),
            ("delivery_type", "VARCHAR(50) DEFAULT 'pickup'"),
        ],
        "pharmacy_orders": [
            ("medicine_id", "INTEGER REFERENCES medicines(id)"),
            ("quantity", "INTEGER DEFAULT 1"),
            ("unit_price", "FLOAT DEFAULT 0"),
            ("address", "VARCHAR(300)"),
        ],
        "patient_profiles": [
            ("phone", "VARCHAR(20)"),
        ],
        "users": [
            ("tos_accepted",      "BOOLEAN DEFAULT 0"),
            ("tos_accepted_at",   "DATETIME"),
            ("ai_doctor_consent", "BOOLEAN DEFAULT 0"),
            ("ai_consent_at",     "DATETIME"),
        ],
        "medicines": [
            ("is_otc",         "BOOLEAN DEFAULT 1"),
            ("max_daily_dose", "INTEGER DEFAULT 0"),
            ("dose_unit",      "VARCHAR(20) DEFAULT 'mg'"),
        ],
    }
    with db.engine.begin() as connection:
        for table, columns in migrations.items():
            existing = {
                row[1] for row in connection.exec_driver_sql(
                    f"PRAGMA table_info({table})").fetchall()
            }
            for column, definition in columns:
                if column not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        connection.exec_driver_sql(
            "UPDATE medicines SET is_otc = 0 WHERE lower(generic) = 'amoxicillin'")

class Notification(db.Model):
    __tablename__ = "notifications"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    type       = db.Column(db.String(40), default="system")
    title      = db.Column(db.String(200), nullable=False)
    body       = db.Column(db.Text, default="")
    link       = db.Column(db.String(300), default="")
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "type": self.type, "title": self.title,
                "body": self.body, "link": self.link, "is_read": self.is_read,
                "created_at": self.created_at.strftime("%d/%m %H:%M")}



# ══════════════════════════════════════════════════════════════════════════════
# SEED DATA  (runs once when DB is empty)
# ══════════════════════════════════════════════════════════════════════════════


def _seed_extra_pharmacies():
    """Add extra pharmacies for medicine finder demo if not already seeded."""
    existing_names = {p.name for p in Pharmacy.query.all()}
    extra = [
        dict(name="เมดิก้า ฟาร์มาซี สีลม",
             address="92 ถ.สีลม แขวงสุริยวงศ์ บางรัก",
             latitude=13.7250, longitude=100.5280,
             phone="02-456-7890", rating=4.6, delivery_radius_km=6,
             delivery_opts_json='["pickup","express","standard"]'),
        dict(name="กรีนครอส ยาและเวชภัณฑ์ รัชดา",
             address="201 ถ.รัชดาภิเษก ห้วยขวาง กรุงเทพฯ",
             latitude=13.7740, longitude=100.5690,
             phone="02-567-8901", rating=4.5, delivery_radius_km=7,
             delivery_opts_json='["pickup","standard"]'),
        dict(name="ฟาร์มาแคร์ อ่อนนุช",
             address="34/5 ถ.อ่อนนุช ซ.60 ประเวศ กรุงเทพฯ",
             latitude=13.7050, longitude=100.6150,
             phone="02-678-9012", rating=4.4, delivery_radius_km=5,
             delivery_opts_json='["pickup","standard"]'),
        dict(name="ยาดี เภสัช ลาดกระบัง",
             address="88 ถ.ลาดกระบัง ลาดกระบัง กรุงเทพฯ",
             latitude=13.7280, longitude=100.7700,
             phone="02-789-0123", rating=4.3, delivery_radius_km=4,
             delivery_opts_json='["pickup"]'),
        dict(name="ซิตี้ ฟาร์มา ปิ่นเกล้า",
             address="41 ถ.บรมราชชนนี ปิ่นเกล้า บางพลัด",
             latitude=13.7780, longitude=100.4750,
             phone="02-890-1234", rating=4.6, delivery_radius_km=6,
             delivery_opts_json='["pickup","express","standard"]'),
    ]
    import random as _rnd
    meds = Medicine.query.all()
    base_prices = [45, 55, 35, 80, 120, 95]
    for d in extra:
        if d["name"] in existing_names:
            continue
        ph = Pharmacy(**d)
        db.session.add(ph)
        db.session.flush()
        for mi, med in enumerate(meds):
            if (ph.id + mi) % 5 == 0:
                continue
            price = base_prices[mi % len(base_prices)] + _rnd.choice([-5, 0, 5, 10])
            db.session.add(PharmacyStock(
                pharmacy_id=ph.id, medicine_id=med.id,
                quantity=_rnd.randint(10, 50), price=price))
    db.session.commit()

def seed_db():
    if User.query.first():
        # Patch: ensure we have enough pharmacies for demo (add if < 5)
        if Pharmacy.query.count() < 5:
            _seed_extra_pharmacies()
        return

    # Pharmacies — 8 locations around Bangkok for demo
    pharmacies_data = [
        dict(name="HealthPlus Pharmacy สาขาสุขุมวิท",
             address="88 ถ.สุขุมวิท ซ.21 กรุงเทพฯ",
             latitude=13.7380, longitude=100.5600,
             phone="02-123-4567", rating=4.9, delivery_radius_km=8,
             delivery_opts_json='["pickup","express","standard"]'),
        dict(name="CarePoint เภสัช เพชรบุรี",
             address="55/3 ถ.เพชรบุรีตัดใหม่ กรุงเทพฯ",
             latitude=13.7500, longitude=100.5400,
             phone="02-234-5678", rating=4.8, delivery_radius_km=5,
             delivery_opts_json='["pickup","standard"]'),
        dict(name="บ้านยาอุ่นใจ ลาดพร้าว",
             address="12 ถ.ลาดพร้าว ซ.15 กรุงเทพฯ",
             latitude=13.7900, longitude=100.5700,
             phone="02-345-6789", rating=4.7, delivery_radius_km=4,
             delivery_opts_json='["pickup"]'),
        dict(name="เมดิก้า ฟาร์มาซี สีลม",
             address="92 ถ.สีลม แขวงสุริยวงศ์ บางรัก",
             latitude=13.7250, longitude=100.5280,
             phone="02-456-7890", rating=4.6, delivery_radius_km=6,
             delivery_opts_json='["pickup","express","standard"]'),
        dict(name="กรีนครอส ยาและเวชภัณฑ์ รัชดา",
             address="201 ถ.รัชดาภิเษก ห้วยขวาง กรุงเทพฯ",
             latitude=13.7740, longitude=100.5690,
             phone="02-567-8901", rating=4.5, delivery_radius_km=7,
             delivery_opts_json='["pickup","standard"]'),
        dict(name="ฟาร์มาแคร์ อ่อนนุช",
             address="34/5 ถ.อ่อนนุช ซ.60 ประเวศ กรุงเทพฯ",
             latitude=13.7050, longitude=100.6150,
             phone="02-678-9012", rating=4.4, delivery_radius_km=5,
             delivery_opts_json='["pickup","standard"]'),
        dict(name="ยาดี เภสัช ลาดกระบัง",
             address="88 ถ.ลาดกระบัง ลาดกระบัง กรุงเทพฯ",
             latitude=13.7280, longitude=100.7700,
             phone="02-789-0123", rating=4.3, delivery_radius_km=4,
             delivery_opts_json='["pickup"]'),
        dict(name="ซิตี้ ฟาร์มา ปิ่นเกล้า",
             address="41 ถ.บรมราชชนนี ปิ่นเกล้า บางพลัด",
             latitude=13.7780, longitude=100.4750,
             phone="02-890-1234", rating=4.6, delivery_radius_km=6,
             delivery_opts_json='["pickup","express","standard"]'),
    ]
    phs = []
    for d in pharmacies_data:
        ph = Pharmacy(**d)
        db.session.add(ph)
        phs.append(ph)
    ph1, ph2, ph3 = phs[0], phs[1], phs[2]
    db.session.flush()

    # Medicines
    meds_data = [
        dict(name="Cetirizine 10 mg",    generic="Cetirizine",  dosage="10 mg",
             instruction="วันละ 1 เม็ด หลังอาหารเย็น",
             purpose="บรรเทาอาการแพ้และผื่นคัน",
             side_effects="ง่วงนอน, ปากแห้ง",
             contraindications_j='["Cetirizine"]',
             keywords_j='["แพ้","ผื่น","คัน","ลมพิษ","น้ำมูก","ภูมิแพ้"]'),
        dict(name="Loratadine 10 mg",    generic="Loratadine",  dosage="10 mg",
             instruction="วันละ 1 เม็ด ตอนเช้า",
             purpose="แก้แพ้ ไม่ทำให้ง่วง",
             side_effects="ปวดหัวเล็กน้อย",
             contraindications_j='["Loratadine"]',
             keywords_j='["แพ้","น้ำมูก","คัน","ภูมิแพ้"]'),
        dict(name="Paracetamol 500 mg",  generic="Paracetamol", dosage="500 mg",
             instruction="1-2 เม็ด ทุก 4-6 ชั่วโมง เมื่อมีอาการ",
             purpose="ลดไข้ แก้ปวด",
             side_effects="หากเกินขนาดอาจเป็นอันตรายต่อตับ",
             keywords_j='["ไข้","ปวดหัว","ปวด","ลดไข้"]'),
        dict(name="Ibuprofen 400 mg",    generic="Ibuprofen",   dosage="400 mg",
             instruction="วันละ 3 ครั้ง หลังอาหาร",
             purpose="แก้ปวด ลดอักเสบ ลดไข้",
             side_effects="ระคายเคืองกระเพาะอาหาร",
             contraindications_j='["NSAIDs","Aspirin","Ibuprofen"]',
             keywords_j='["ปวด","อักเสบ","ไข้","ปวดกล้ามเนื้อ"]'),
        dict(name="Omeprazole 20 mg",    generic="Omeprazole",  dosage="20 mg",
             instruction="วันละ 1 เม็ด ก่อนอาหารเช้า",
             purpose="ลดกรดกระเพาะ แก้แสบร้อนกลางอก",
             side_effects="ปวดหัว ท้องเสีย",
             keywords_j='["กรด","กระเพาะ","แสบ","ท้องอืด","เรอ"]'),
        dict(name="Amoxicillin 500 mg",  generic="Amoxicillin", dosage="500 mg",
             instruction="3 ครั้งต่อวัน ทุก 8 ชั่วโมง ครบ 7 วัน",
             purpose="ยาปฏิชีวนะ รักษาการติดเชื้อแบคทีเรีย",
             side_effects="ท้องเสีย ผื่นแพ้",
             is_otc=False,
             contraindications_j='["Penicillin","Amoxicillin"]',
             keywords_j='["ติดเชื้อ","แบคทีเรีย","ทอนซิล","ไซนัส"]'),
    ]
    meds = []
    for d in meds_data:
        m = Medicine(**d)
        db.session.add(m)
        meds.append(m)
    db.session.flush()

    # Stock — varied per pharmacy for realistic demo
    import random as _rnd
    base_prices = [45, 55, 35, 80, 120, 95]   # one per medicine
    for pi, ph in enumerate(phs):
        for mi, med in enumerate(meds):
            # Some pharmacies intentionally missing some medicines
            if (pi + mi) % 7 == 0:
                continue
            qty = _rnd.randint(8, 60)
            # Price varies slightly between pharmacies
            price = base_prices[mi] + _rnd.choice([-5, 0, 5, 10, 15])
            db.session.add(PharmacyStock(
                pharmacy_id=ph.id, medicine_id=med.id,
                quantity=qty, price=price))
    db.session.flush()

    # Users
    admin = User(email="admin@demo.com", role="ADMIN", name="ผู้ดูแลระบบ")
    admin.set_password("demo1234")

    doc1 = User(email="doctor@demo.com", role="DOCTOR",
                name="พญ. ณิชา วัฒนสุข",
                specialty="อายุรกรรม", hospital="MediLink Medical Center")
    doc1.set_password("demo1234")

    doc2 = User(email="doctor2@demo.com", role="DOCTOR",
                name="นพ. กิตติศักดิ์ เจริญชัย",
                specialty="โรคผิวหนัง", hospital="Bangkok General Hospital")
    doc2.set_password("demo1234")

    pat1 = User(email="patient@demo.com", role="PATIENT", name="พิมพ์ชนก สุขใจ")
    pat1.set_password("demo1234")

    pat2 = User(email="patient2@demo.com", role="PATIENT", name="ธนภัทร แสงดี")
    pat2.set_password("demo1234")

    ph_user = User(email="pharmacy@demo.com", role="PHARMACY",
                   name="ภก. กิตติ เมดิคอล", pharmacy_id=ph1.id)
    ph_user.set_password("demo1234")

    db.session.add_all([admin, doc1, doc2, pat1, pat2, ph_user])
    db.session.flush()

    # Patient profiles
    pp1 = PatientProfile(user_id=pat1.id,
                         latitude=13.7563, longitude=100.5018,
                         address="123 ถ.สุขุมวิท กรุงเทพฯ 10110")
    pp1.allergies  = ["Penicillin", "Sulfonamides"]
    pp1.conditions = ["ภูมิแพ้เรื้อรัง", "หอบหืดเล็กน้อย"]

    pp2 = PatientProfile(user_id=pat2.id,
                         latitude=13.7400, longitude=100.5200,
                         address="456 ถ.พระราม 4 กรุงเทพฯ 10120")
    pp2.allergies  = []
    pp2.conditions = ["ไมเกรน"]

    db.session.add_all([pp1, pp2])
    db.session.commit()
    print("✅ Database seeded with demo data")

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def current_user() -> dict | None:
    return session.get("user")

def require_role(*roles) -> dict | None:
    u = session.get("user")
    if not u or u.get("role") not in roles:
        return None
    return u

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    d  = math.radians
    a  = (math.sin(d(lat2-lat1)/2)**2
          + math.cos(d(lat1)) * math.cos(d(lat2)) * math.sin(d(lng2-lng1)/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def gen_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

def review_stats(target_type: str, target_id: int) -> dict:
    reviews = Review.query.filter_by(target_type=target_type, target_id=target_id).all()
    average = sum(r.rating for r in reviews) / len(reviews) if reviews else 0
    return {"reviews": reviews, "average": round(average, 1), "count": len(reviews)}

def fallback_review_summary(reviews: list[Review]) -> str:
    if not reviews:
        return "ยังไม่มีรีวิวเพียงพอสำหรับสรุป"
    text = " ".join(r.comment for r in reviews)
    positives = []
    for keyword, label in [
        ("อธิบาย", "อธิบายละเอียด"), ("ใจดี", "เป็นมิตรและใจดี"),
        ("รอคิวไม่นาน", "รอคิวไม่นาน"), ("ใช้งานง่าย", "ใช้งานง่าย"),
        ("คุณภาพดี", "คุณภาพดี"), ("จัดส่งรวดเร็ว", "จัดส่งรวดเร็ว"),
    ]:
        if keyword in text:
            positives.append(label)
    caution = " แต่บางรีวิวระบุว่าราคาค่อนข้างสูง" if "ราคาค่อนข้างสูง" in text else ""
    core = " ผู้ใช้ส่วนใหญ่กล่าวว่า" + " ".join(positives[:3]) if positives else " คะแนนรีวิวโดยรวมอยู่ในระดับดี"
    return core.strip() + caution

def summarize_reviews(target_type: str, target_id: int) -> str:
    reviews = Review.query.filter_by(target_type=target_type, target_id=target_id).all()
    if not reviews:
        return "ยังไม่มีรีวิวเพียงพอสำหรับสรุป"
    # Keep detail pages fast; interactive AI endpoints use Gemini with a fallback.
    return fallback_review_summary(reviews)

def recommended_products_for_specialty(specialty: str | None) -> list[Product]:
    specialty = specialty or ""
    category = "ดูแลผิว" if "ผิว" in specialty else (
        "ดูแลทางเดินหายใจ" if any(k in specialty for k in ["หู", "คอ", "จมูก"]) else
        "อุปกรณ์การแพทย์")
    products = Product.query.filter_by(category=category, is_active=True).limit(3).all()
    return products or Product.query.filter_by(is_active=True).limit(3).all()

@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    medicine_cart = session.get("medicine_cart", {})
    return {"current_user": current_user(), "current_year": datetime.now().year,
            "cart_count": (
                sum(int(qty) for qty in cart.values())
                + sum(int(item.get("quantity", 0)) for item in medicine_cart.values())
            )}

# ══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def home():
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("landing"))

@app.route("/login", methods=["GET", "POST"])
@app.route("/login.html", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user     = User.query.filter_by(email=email, is_active=True).first()
        if user and user.check_password(password):
            session["user"] = user.to_session()
            return redirect(url_for("dashboard"))
        error = "อีเมลหรือรหัสผ่านไม่ถูกต้อง กรุณาใช้บัญชีเดโมด้านล่าง"

    # demo_users for the login.html template
    demo_users = {
        u.email: {"password": "demo1234", "name": u.name, "role": u.role}
        for u in User.query.filter_by(is_active=True)
                    .order_by(User.role).all()
    }
    return render_template("login.html", error=error, demo_users=demo_users)

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/register")
@app.get("/register.html")
def register():
    return render_template("register.html")

@app.post("/api/register")
def api_register():
    data      = request.get_json(silent=True) or {}
    email     = str(data.get("email", "")).strip().lower()
    password  = str(data.get("password", ""))
    allergies = data.get("allergies", [])
    conditions = data.get("conditions", [])

    if "@" not in email or len(password) < 8:
        return jsonify({"message": "กรุณาตรวจสอบอีเมลและรหัสผ่าน"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"message": "อีเมลนี้ถูกใช้งานแล้ว"}), 400

    user = User(email=email, role="PATIENT", name=email.split("@")[0])
    user.set_password(password)
    db.session.add(user)
    db.session.flush()

    profile = PatientProfile(user_id=user.id)
    profile.allergies  = allergies if isinstance(allergies, list) else []
    profile.conditions = conditions if isinstance(conditions, list) else []
    loc = data.get("location")
    if loc:
        profile.latitude  = loc.get("latitude",  13.7563)
        profile.longitude = loc.get("longitude", 100.5018)
    db.session.add(profile)
    db.session.commit()

    session["user"]    = user.to_session()
    session["profile"] = {"allergies": allergies, "conditions": conditions,
                          "has_location": bool(loc)}
    return jsonify({"redirect": url_for("registered")}), 201

@app.get("/tos")
def tos_page():
    """TOS page — shown after register if not yet accepted."""
    if not current_user():
        return redirect(url_for("login"))
    redirect_to = request.args.get("next", "/dashboard")
    return render_template("tos.html", redirect_to=redirect_to)

@app.get("/ai-doctor")
def ai_doctor_page():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    db_user = User.query.get(user["id"])
    has_consent = bool(db_user and db_user.ai_doctor_consent)
    return render_template("ai_doctor.html", user=user, has_consent=has_consent)

@app.get("/registered")
@app.get("/registered.html")
def registered():
    if not current_user():
        return redirect(url_for("register"))
    return render_template("registered.html", profile=session.get("profile", {}))

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/dashboard")
@app.get("/dashboard.html")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    role = user["role"]
    if role == "DOCTOR":   return redirect(url_for("doctor_dashboard"))
    if role == "PHARMACY": return redirect(url_for("pharmacy_dashboard"))
    if role == "ADMIN":    return redirect(url_for("admin_dashboard"))

    # PATIENT
    db_user       = User.query.get(user["id"])
    profile       = PatientProfile.query.filter_by(user_id=user["id"]).first()
    consultations = (Consultation.query
                     .filter_by(patient_id=user["id"])
                     .order_by(Consultation.created_at.desc())
                     .all())
    doctors    = User.query.filter_by(role="DOCTOR", is_active=True).all()
    pharmacies = Pharmacy.query.all()
    orders     = (PharmacyOrder.query
                  .filter_by(patient_id=user["id"])
                  .order_by(PharmacyOrder.created_at.desc())
                  .limit(10).all())
    appointments = (Appointment.query
                    .filter_by(patient_id=user["id"])
                    .order_by(Appointment.appointment_date.desc())
                    .limit(5).all())
    recommended_products = Product.query.filter_by(is_active=True).limit(3).all()

    return render_template("dashboard.html",
                           user=db_user, profile=profile,
                           consultations=consultations,
                           doctors=doctors, pharmacies=pharmacies,
                           orders=orders, appointments=appointments,
                           recommended_products=recommended_products)

@app.get("/dashboard/doctor")
def doctor_dashboard():
    user = require_role("DOCTOR")
    if not user:
        return redirect(url_for("login"))

    db_user  = User.query.get(user["id"])
    pending  = (Consultation.query
                .filter_by(status="REQUESTED")
                .order_by(Consultation.created_at.desc())
                .all())
    my_cases = (Consultation.query
                .filter_by(doctor_id=user["id"])
                .order_by(Consultation.created_at.desc())
                .all())
    medicines  = Medicine.query.all()
    pharmacies = Pharmacy.query.all()

    return render_template("doctor_dashboard.html",
                           user=db_user,
                           pending=pending,
                           my_cases=my_cases,
                           medicines=medicines,
                           pharmacies=pharmacies)

@app.get("/dashboard/pharmacy")
def pharmacy_dashboard():
    user = require_role("PHARMACY")
    if not user:
        return redirect(url_for("login"))

    db_user  = User.query.get(user["id"])
    pharmacy = Pharmacy.query.get(db_user.pharmacy_id) if db_user.pharmacy_id else Pharmacy.query.first()

    ph_id  = pharmacy.id if pharmacy else 0
    orders = (PharmacyOrder.query
              .filter_by(pharmacy_id=ph_id)
              .order_by(PharmacyOrder.created_at.desc())
              .all())
    shop_orders = (ShopOrder.query
                   .filter_by(pharmacy_id=ph_id)
                   .order_by(ShopOrder.created_at.desc())
                   .all())
    stock  = PharmacyStock.query.filter_by(pharmacy_id=ph_id).all()
    stock_items = [{"id": s.id, "name": s.medicine.name,
                    "sku": s.medicine.generic or s.medicine.name,
                    "qty": s.quantity, "price": s.price} for s in stock]
    product_stock = ProductStock.query.filter_by(pharmacy_id=ph_id).all()
    product_stock_items = [{
        "id": s.id, "name": s.product.name, "sku": s.product.category,
        "qty": s.quantity, "price": s.price,
    } for s in product_stock]

    return render_template("pharmacy_dashboard.html",
                           user=db_user, pharmacy=pharmacy,
                           orders=orders, shop_orders=shop_orders,
                           stock_items=stock_items,
                           product_stock_items=product_stock_items)

@app.get("/dashboard/admin")
def admin_dashboard():
    user = require_role("ADMIN")
    if not user:
        return redirect(url_for("login"))

    doctors    = User.query.filter_by(role="DOCTOR").all()
    pharmacies = Pharmacy.query.all()

    return render_template("admin_dashboard.html",
                           doctors=[{"email": d.email, "name": d.name} for d in doctors],
                           pharmacies=[{"name": p.name, "address": p.address,
                                        "rating": p.rating, "distance": 0,
                                        "official": p.is_official, "open": "เปิดทำการ"}
                                       for p in pharmacies],
                           total_users=User.query.count(),
                           total_consultations=Consultation.query.count())

# ══════════════════════════════════════════════════════════════════════════════
# DOCTOR DISCOVERY + HEALTH STORE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/doctors")
def doctors_page():
    if not current_user():
        return redirect(url_for("login"))
    specialty = request.args.get("specialty", "").strip()
    date = request.args.get("date", "").strip()
    query = User.query.filter_by(role="DOCTOR", is_active=True)
    if specialty:
        query = query.filter(User.specialty.contains(specialty))
    doctors = query.all()
    cards = []
    for doctor in doctors:
        stats = review_stats("doctor", doctor.id)
        cards.append({"doctor": doctor, **stats,
                      "next_date": date or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")})
    specialties = sorted({d.specialty for d in User.query.filter_by(role="DOCTOR").all() if d.specialty})
    return render_template("doctors.html", doctor_cards=cards, specialties=specialties,
                           selected_specialty=specialty, selected_date=date)

@app.get("/doctors/<int:doctor_id>")
def doctor_detail(doctor_id: int):
    if not current_user():
        return redirect(url_for("login"))
    doctor = User.query.filter_by(id=doctor_id, role="DOCTOR", is_active=True).first_or_404()
    stats = review_stats("doctor", doctor.id)
    slots = []
    for day_offset in range(1, 6):
        day = datetime.now() + timedelta(days=day_offset)
        for time in ("09:00", "11:00", "14:00", "16:00"):
            slots.append({"date": day.strftime("%Y-%m-%d"), "time": time})
    return render_template(
        "doctor_detail.html", doctor=doctor, slots=slots,
        review_summary=summarize_reviews("doctor", doctor.id),
        recommended_products=recommended_products_for_specialty(doctor.specialty),
        **stats)

@app.post("/api/appointments")
def create_appointment():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    doctor = User.query.filter_by(id=data.get("doctor_id"), role="DOCTOR", is_active=True).first()
    date = str(data.get("date", "")).strip()
    time = str(data.get("time", "")).strip()
    if not doctor or not date or not time:
        return jsonify({"message": "กรุณาเลือกแพทย์ วัน และเวลา"}), 400
    duplicate = Appointment.query.filter_by(
        doctor_id=doctor.id, appointment_date=date,
        appointment_time=time, status="CONFIRMED").first()
    if duplicate:
        return jsonify({"message": "ช่วงเวลานี้ถูกจองแล้ว กรุณาเลือกเวลาอื่น"}), 409
    appointment = Appointment(
        id=gen_id("APT"), patient_id=user["id"], doctor_id=doctor.id,
        appointment_date=date, appointment_time=time,
        reason=str(data.get("reason", "")).strip())
    db.session.add(appointment)
    db.session.commit()
    return jsonify({"message": "นัดหมายสำเร็จ", "appointment_id": appointment.id}), 201

@app.get("/shop")
def shop_page():
    if not current_user():
        return redirect(url_for("login"))
    category = request.args.get("category", "").strip()
    search = request.args.get("q", "").strip()
    query = Product.query.filter_by(is_active=True)
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.contains(search))
    products = query.all()
    categories = sorted({p.category for p in Product.query.filter_by(is_active=True).all()})
    return render_template("shop.html", products=products, categories=categories,
                           selected_category=category, search=search)

@app.get("/shop/products/<int:product_id>")
def product_detail(product_id: int):
    if not current_user():
        return redirect(url_for("login"))
    product = Product.query.filter_by(id=product_id, is_active=True).first_or_404()
    stats = review_stats("product", product.id)
    related = Product.query.filter(
        Product.category == product.category, Product.id != product.id,
        Product.is_active.is_(True)).limit(3).all()
    return render_template(
        "product_detail.html", product=product, related=related,
        review_summary=summarize_reviews("product", product.id), **stats)

@app.post("/api/cart/add")
def add_to_cart():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "เฉพาะผู้ป่วยที่เข้าสู่ระบบเท่านั้น"}), 401
    data = request.get_json(silent=True) or {}
    try:
        product_id = int(data.get("product_id"))
        quantity = max(1, int(data.get("quantity", 1)))
    except (TypeError, ValueError):
        return jsonify({"message": "ข้อมูลสินค้าไม่ถูกต้อง"}), 400
    product = Product.query.filter_by(id=product_id, is_active=True).first()
    if not product or product.stock < quantity:
        return jsonify({"message": "สินค้าไม่เพียงพอ"}), 400
    cart = session.get("cart", {})
    key = str(product.id)
    cart[key] = min(product.stock, int(cart.get(key, 0)) + quantity)
    session["cart"] = cart
    session.modified = True
    return jsonify({"message": "เพิ่มสินค้าลงตะกร้าแล้ว",
                    "cart_count": sum(cart.values())})

@app.get("/cart")
def cart_page():
    user = require_role("PATIENT")
    if not user:
        return redirect(url_for("login"))
    cart = session.get("cart", {})
    items, total = [], 0
    for product_id, quantity in cart.items():
        product = db.session.get(Product, int(product_id))
        if product:
            subtotal = product.price * int(quantity)
            items.append({"product": product, "quantity": int(quantity), "subtotal": subtotal})
            total += subtotal

    medicine_items, medicine_total = [], 0
    medicine_pharmacy = None
    for entry in session.get("medicine_cart", {}).values():
        try:
            medicine_id = int(entry["medicine_id"])
            pharmacy_id = int(entry["pharmacy_id"])
            quantity = max(1, int(entry["quantity"]))
        except (KeyError, TypeError, ValueError):
            continue
        stock = PharmacyStock.query.filter_by(
            medicine_id=medicine_id, pharmacy_id=pharmacy_id).first()
        if not stock or not stock.medicine or not stock.pharmacy:
            continue
        quantity = min(quantity, stock.quantity)
        if quantity <= 0:
            continue
        subtotal = stock.price * quantity
        medicine_items.append({
            "medicine": stock.medicine,
            "pharmacy": stock.pharmacy,
            "quantity": quantity,
            "stock_quantity": stock.quantity,
            "unit_price": stock.price,
            "subtotal": subtotal,
        })
        medicine_total += subtotal
        medicine_pharmacy = medicine_pharmacy or stock.pharmacy

    profile = PatientProfile.query.filter_by(user_id=user["id"]).first()
    return render_template(
        "cart.html", items=items, total=total, profile=profile,
        medicine_items=medicine_items, medicine_total=medicine_total,
        medicine_pharmacy=medicine_pharmacy)

@app.post("/api/cart/update")
def update_cart():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    try:
        key = str(int(data.get("product_id")))
        quantity = max(0, int(data.get("quantity", 0)))
    except (TypeError, ValueError):
        return jsonify({"message": "ข้อมูลสินค้าไม่ถูกต้อง"}), 400
    cart = session.get("cart", {})
    if quantity == 0:
        cart.pop(key, None)
    else:
        product = db.session.get(Product, int(key))
        if not product:
            return jsonify({"message": "ไม่พบสินค้า"}), 404
        cart[key] = min(quantity, product.stock)
    session["cart"] = cart
    session.modified = True
    return jsonify({"message": "อัปเดตตะกร้าแล้ว"})

@app.post("/api/medicine-cart/add")
def add_medicine_to_cart():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "เฉพาะผู้ป่วยที่เข้าสู่ระบบเท่านั้น"}), 401
    data = request.get_json(silent=True) or {}
    try:
        medicine_id = int(data.get("medicine_id"))
        pharmacy_id = int(data.get("pharmacy_id"))
        quantity = max(1, int(data.get("quantity", 1)))
    except (TypeError, ValueError):
        return jsonify({"message": "ข้อมูลยาไม่ถูกต้อง"}), 400

    medicine = db.session.get(Medicine, medicine_id)
    stock = PharmacyStock.query.filter_by(
        medicine_id=medicine_id, pharmacy_id=pharmacy_id).first()
    if not medicine or not medicine.is_otc:
        return jsonify({"message": "ยานี้ต้องมีใบสั่งแพทย์และไม่สามารถเพิ่มจากหน้านี้ได้"}), 400
    if not stock or stock.quantity < quantity:
        return jsonify({"message": "สต็อกยาไม่เพียงพอ"}), 400

    medicine_cart = session.get("medicine_cart", {})
    existing_pharmacies = {
        int(item.get("pharmacy_id"))
        for item in medicine_cart.values()
        if item.get("pharmacy_id") is not None
    }
    if existing_pharmacies and pharmacy_id not in existing_pharmacies:
        return jsonify({
            "message": "ตะกร้ายามีสินค้าจากร้านอื่นอยู่ กรุณาชำระเงินหรือล้างตะกร้าก่อน"
        }), 409

    key = f"{pharmacy_id}:{medicine_id}"
    current_quantity = int(medicine_cart.get(key, {}).get("quantity", 0))
    medicine_cart[key] = {
        "medicine_id": medicine_id,
        "pharmacy_id": pharmacy_id,
        "quantity": min(stock.quantity, current_quantity + quantity),
    }
    session["medicine_cart"] = medicine_cart
    session.modified = True
    cart_count = (
        sum(int(qty) for qty in session.get("cart", {}).values())
        + sum(int(item["quantity"]) for item in medicine_cart.values())
    )
    return jsonify({
        "message": "เพิ่มยาลงตะกร้าแล้ว",
        "cart_count": cart_count,
        "cart_url": url_for("cart_page"),
    }), 201

@app.post("/api/medicine-cart/update")
def update_medicine_cart():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    try:
        medicine_id = int(data.get("medicine_id"))
        pharmacy_id = int(data.get("pharmacy_id"))
        quantity = max(0, int(data.get("quantity", 0)))
    except (TypeError, ValueError):
        return jsonify({"message": "ข้อมูลยาไม่ถูกต้อง"}), 400

    key = f"{pharmacy_id}:{medicine_id}"
    medicine_cart = session.get("medicine_cart", {})
    if quantity == 0:
        medicine_cart.pop(key, None)
    else:
        stock = PharmacyStock.query.filter_by(
            medicine_id=medicine_id, pharmacy_id=pharmacy_id).first()
        if not stock:
            return jsonify({"message": "ไม่พบยาในร้านนี้"}), 404
        if stock.quantity < quantity:
            return jsonify({"message": f"เหลือยาในสต็อก {stock.quantity} รายการ"}), 400
        medicine_cart[key] = {
            "medicine_id": medicine_id,
            "pharmacy_id": pharmacy_id,
            "quantity": quantity,
        }
    session["medicine_cart"] = medicine_cart
    session.modified = True
    return jsonify({"message": "อัปเดตตะกร้ายาแล้ว"})

@app.post("/api/medicine-cart/checkout")
def checkout_medicine_cart():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    medicine_cart = session.get("medicine_cart", {})
    if not medicine_cart:
        return jsonify({"message": "ตะกร้ายาว่าง"}), 400

    data = request.get_json(silent=True) or {}
    delivery_type = str(data.get("delivery_type", "pickup"))
    address = str(data.get("address", "")).strip()
    prepared = []
    pharmacy = None

    for entry in medicine_cart.values():
        try:
            medicine_id = int(entry["medicine_id"])
            pharmacy_id = int(entry["pharmacy_id"])
            quantity = max(1, int(entry["quantity"]))
        except (KeyError, TypeError, ValueError):
            return jsonify({"message": "ข้อมูลในตะกร้ายาไม่ถูกต้อง"}), 400
        stock = PharmacyStock.query.filter_by(
            medicine_id=medicine_id, pharmacy_id=pharmacy_id).first()
        if not stock or not stock.medicine or stock.quantity < quantity:
            name = stock.medicine.name if stock and stock.medicine else str(medicine_id)
            return jsonify({"message": f"ยา {name} มีไม่เพียงพอ"}), 400
        if pharmacy and pharmacy.id != pharmacy_id:
            return jsonify({"message": "กรุณาแยกชำระยาจากคนละร้าน"}), 400
        pharmacy = stock.pharmacy
        prepared.append((stock, quantity))

    if not pharmacy or delivery_type not in pharmacy.delivery_options:
        return jsonify({"message": "ร้านนี้ไม่รองรับวิธีรับยาที่เลือก"}), 400
    if delivery_type != "pickup" and not address:
        return jsonify({"message": "กรุณาระบุที่อยู่จัดส่ง"}), 400

    orders = []
    for stock, quantity in prepared:
        stock.quantity -= quantity
        order = PharmacyOrder(
            id=gen_id("MED"),
            consultation_id=None,
            pharmacy_id=pharmacy.id,
            patient_id=user["id"],
            medicine_id=stock.medicine_id,
            quantity=quantity,
            unit_price=stock.price,
            delivery_type=delivery_type,
            address=address,
            status="PENDING",
        )
        db.session.add(order)
        orders.append(order)
    db.session.commit()

    session["medicine_cart"] = {}
    session.modified = True
    for order in orders:
        socketio.emit("new_order", {
            "id": order.id,
            "patient_name": user["name"],
            "medicine_name": order.medicine.name if order.medicine else "",
            "delivery_type": delivery_type,
            "status": "PENDING",
        }, room=f"pharmacy_{pharmacy.id}")
    return jsonify({
        "message": "สร้างคำสั่งซื้อยาเรียบร้อยแล้ว",
        "order_ids": [order.id for order in orders],
    }), 201

@app.post("/api/shop/checkout")
def checkout():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    cart = session.get("cart", {})
    if not cart:
        return jsonify({"message": "ตะกร้าว่าง"}), 400
    data = request.get_json(silent=True) or {}
    pharmacy_id = data.get("pharmacy_id")
    delivery_type = str(data.get("delivery_type", "pickup"))
    pharmacy = db.session.get(Pharmacy, pharmacy_id) if pharmacy_id else None
    if not pharmacy:
        return jsonify({"message": "กรุณาเลือกร้านที่ต้องการรับสินค้า"}), 400
    if delivery_type not in pharmacy.delivery_options:
        return jsonify({"message": "ร้านนี้ไม่รองรับวิธีรับสินค้าที่เลือก"}), 400
    products, total = [], 0
    for product_id, quantity in cart.items():
        product = db.session.get(Product, int(product_id))
        quantity = int(quantity)
        stock = ProductStock.query.filter_by(
            pharmacy_id=pharmacy.id, product_id=int(product_id)).first()
        if not product or not stock or stock.quantity < quantity:
            return jsonify({"message": f"สินค้า {product.name if product else product_id} มีไม่เพียงพอ"}), 400
        products.append((product, stock, quantity))
        total += stock.price * quantity
    order = ShopOrder(id=gen_id("ORD"), patient_id=user["id"], total=total,
                      address=str(data.get("address", "")).strip(),
                      pharmacy_id=pharmacy.id, delivery_type=delivery_type,
                      status="PENDING")
    db.session.add(order)
    for product, stock, quantity in products:
        stock.quantity -= quantity
        product.stock = max(0, product.stock - quantity)
        db.session.add(ShopOrderItem(order_id=order.id, product_id=product.id,
                                     quantity=quantity, unit_price=stock.price))
    db.session.commit()
    session["cart"] = {}
    session.modified = True
    socketio.emit("new_shop_order", {
        "id": order.id, "patient_name": user["name"],
        "delivery_type": delivery_type, "total": total,
    }, room=f"pharmacy_{pharmacy.id}")
    return jsonify({"message": "ชำระเงินและสร้างคำสั่งซื้อสำเร็จ", "order_id": order.id}), 201

@app.get("/api/shop/pharmacies/nearby")
def nearby_product_pharmacies():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    cart = session.get("cart", {})
    if not cart:
        return jsonify([])
    profile = PatientProfile.query.filter_by(user_id=user["id"]).first()
    lat = request.args.get("lat", type=float, default=profile.latitude if profile else 13.7563)
    lng = request.args.get("lng", type=float, default=profile.longitude if profile else 100.5018)
    results = []
    for pharmacy in Pharmacy.query.all():
        items, total = [], 0
        available = True
        for product_id, quantity in cart.items():
            stock = ProductStock.query.filter_by(
                pharmacy_id=pharmacy.id, product_id=int(product_id)).first()
            if not stock or stock.quantity < int(quantity):
                available = False
                break
            items.append({
                "product_id": stock.product_id, "name": stock.product.name,
                "stock": stock.quantity, "price": stock.price,
                "required": int(quantity),
            })
            total += stock.price * int(quantity)
        if available:
            distance = haversine(lat, lng, pharmacy.latitude, pharmacy.longitude)
            delivery_options = list(pharmacy.delivery_options)
            if distance > pharmacy.delivery_radius_km and "express" in delivery_options:
                delivery_options.remove("express")
            results.append({
                "id": pharmacy.id, "name": pharmacy.name,
                "address": pharmacy.address, "phone": pharmacy.phone,
                "rating": pharmacy.rating, "distance": round(distance, 1),
                "delivery_options": delivery_options, "items": items,
                "total": total,
            })
    results.sort(key=lambda item: item["distance"])
    return jsonify(results)

@app.get("/orders")
def order_history():
    user = require_role("PATIENT")
    if not user:
        return redirect(url_for("login"))
    orders = (ShopOrder.query.filter_by(patient_id=user["id"])
              .order_by(ShopOrder.created_at.desc()).all())
    medicine_orders = (PharmacyOrder.query.filter_by(patient_id=user["id"])
                       .order_by(PharmacyOrder.created_at.desc()).all())
    return render_template(
        "orders.html", orders=orders, medicine_orders=medicine_orders)

@app.post("/api/reviews")
def create_review():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    target_type = str(data.get("target_type", ""))
    target_id = int(data.get("target_id", 0))
    rating = int(data.get("rating", 0))
    comment = str(data.get("comment", "")).strip()
    if target_type not in {"doctor", "product"} or not 1 <= rating <= 5 or not comment:
        return jsonify({"message": "ข้อมูลรีวิวไม่ครบ"}), 400
    existing = Review.query.filter_by(
        user_id=user["id"], target_type=target_type, target_id=target_id).first()
    if existing:
        existing.rating, existing.comment = rating, comment
    else:
        db.session.add(Review(user_id=user["id"], target_type=target_type,
                              target_id=target_id, rating=rating, comment=comment))
    db.session.commit()
    return jsonify({"message": "บันทึกรีวิวแล้ว"}), 201

# ══════════════════════════════════════════════════════════════════════════════
# CONSULTATION API
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/consultations/request")
def request_consultation():
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    # Accept both multipart/form-data (with image) and JSON
    if request.content_type and "multipart" in request.content_type:
        symptoms = request.form.get("symptoms", "").strip()
    else:
        symptoms = (request.get_json(silent=True) or {}).get("symptoms", "").strip()

    if not symptoms:
        return jsonify({"message": "กรุณาระบุอาการ"}), 400

    # Handle image upload
    image_url = None
    if "image" in request.files:
        f = request.files["image"]
        if f and f.filename and allowed_file(f.filename):
            fname = secure_filename(f"{uuid.uuid4().hex}_{f.filename}")
            f.save(UPLOAD_DIR / fname)
            image_url = f"/static/uploads/{fname}"

    cons_id = gen_id("C")
    cons    = Consultation(id=cons_id, patient_id=user["id"],
                           symptoms=symptoms, image_url=image_url)
    db.session.add(cons)
    db.session.commit()

    # Notify all doctors in real-time
    socketio.emit("new_consultation", {
        "id": cons_id,
        "patient_name": user["name"],
        "symptoms": symptoms[:80] + ("..." if len(symptoms) > 80 else "")
    }, room="doctors")

    return jsonify({"message": "ส่งอาการให้แพทย์แล้ว", "id": cons_id}), 201

@app.post("/api/consultations/<cons_id>/accept")
def accept_consultation(cons_id: str):
    user = require_role("DOCTOR")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    cons = Consultation.query.get(cons_id)
    if not cons:
        return jsonify({"message": "ไม่พบข้อมูล"}), 404
    if cons.status != "REQUESTED":
        return jsonify({"message": "มีแพทย์รับเคสนี้แล้ว"}), 409

    cons.doctor_id = user["id"]
    cons.status    = "ACCEPTED"
    db.session.commit()

    # Notify patient
    socketio.emit("consultation_accepted", {
        "consultation_id": cons_id,
        "doctor_name": user["name"]
    }, room=f"patient_{cons.patient_id}")

    return jsonify({"message": "รับเคสแล้ว", "consultation_id": cons_id})

@app.get("/api/consultations")
def get_consultations():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    if user["role"] == "PATIENT":
        items = (Consultation.query
                 .filter_by(patient_id=user["id"])
                 .order_by(Consultation.created_at.desc()).all())
    elif user["role"] == "DOCTOR":
        items = (Consultation.query
                 .filter((Consultation.status == "REQUESTED") |
                         (Consultation.doctor_id == user["id"]))
                 .order_by(Consultation.created_at.desc()).all())
    else:
        items = Consultation.query.order_by(Consultation.created_at.desc()).all()

    return jsonify([c.to_dict() for c in items])

@app.post("/api/consultations/<cons_id>/prescribe")
def prescribe(cons_id: str):
    user = require_role("DOCTOR")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data      = request.get_json(silent=True) or {}
    cons      = Consultation.query.filter_by(id=cons_id, doctor_id=user["id"]).first()
    if not cons:
        return jsonify({"message": "ไม่พบข้อมูลหรือไม่มีสิทธิ์"}), 404
    if cons.case_closed:
        return jsonify({"message": "เคสนี้จบแล้ว ไม่สามารถจ่ายยาเพิ่มได้"}), 409

    # Cast IDs safely — frontend may send string or int
    try:    med_id = int(data.get("medicine_id"))
    except: return jsonify({"message": "medicine_id ไม่ถูกต้อง"}), 400
    try:    ph_id = int(data.get("pharmacy_id")) if data.get("pharmacy_id") else None
    except: ph_id = None
    diagnosis = str(data.get("diagnosis", "")).strip()

    med = Medicine.query.get(med_id)
    if not med:
        return jsonify({"message": "ไม่พบยาที่เลือก"}), 400

    stock = (PharmacyStock.query
             .filter_by(pharmacy_id=ph_id, medicine_id=med_id)
             .first()) if ph_id else None

    cons.status        = "PRESCRIBED"
    cons.diagnosis     = diagnosis
    cons.medicine_id   = med_id
    cons.pharmacy_id   = ph_id
    cons.med_price     = stock.price if stock else 0
    cons.prescribed_at = datetime.utcnow()
    db.session.commit()

    # Notify patient via personal room + consultation room
    payload = {
        "consultation_id": cons_id,
        "medicine_name":   med.name,
        "diagnosis":       diagnosis,
    }
    socketio.emit("prescription_ready", payload, room=f"cons_{cons_id}")
    socketio.emit("prescription_ready", payload, room=f"patient_{cons.patient_id}")

    return jsonify({"message": "สั่งยาเรียบร้อยแล้ว", "medicine_name": med.name})

@app.post("/api/consultations/<cons_id>/complete")
def complete_consultation(cons_id: str):
    user = require_role("DOCTOR")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    cons = Consultation.query.filter_by(id=cons_id, doctor_id=user["id"]).first()
    if not cons:
        return jsonify({"message": "ไม่พบข้อมูลหรือไม่มีสิทธิ์"}), 404
    if cons.case_closed:
        return jsonify({"message": "เคสนี้จบแล้ว"}), 409
    cons.case_closed = True
    cons.completed_at = datetime.utcnow()
    if cons.status == "ACCEPTED" and not cons.medicine_id:
        cons.status = "COMPLETED"
    db.session.commit()
    socketio.emit("consultation_completed", {
        "consultation_id": cons.id,
        "has_prescription": bool(cons.medicine_id),
    }, room=f"patient_{cons.patient_id}")
    return jsonify({
        "message": "จบเคสเรียบร้อยแล้ว",
        "status": cons.status,
        "case_closed": True,
    })

@app.post("/api/consultations/<cons_id>/confirm")
def confirm_consultation(cons_id: str):
    user = require_role("PATIENT")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data          = request.get_json(silent=True) or {}
    delivery_type = data.get("delivery_type", "pickup")
    pharmacy_id   = data.get("pharmacy_id")  # patient may pick a different pharmacy

    cons = Consultation.query.filter_by(id=cons_id, patient_id=user["id"]).first()
    if not cons or cons.status != "PRESCRIBED":
        return jsonify({"message": "ไม่พบข้อมูลที่ยืนยันได้"}), 404
    if PharmacyOrder.query.filter_by(consultation_id=cons_id).first():
        return jsonify({"message": "ใบสั่งยานี้ถูกสั่งซื้อแล้ว"}), 409

    labels = {"pickup": "รับหน้าร้าน",
              "express": "ส่งด่วน (ภายใน 1 ชม.)",
              "standard": "ส่งมาตรฐาน"}

    ph_id = pharmacy_id or cons.pharmacy_id
    if not ph_id:
        return jsonify({"message": "กรุณาเลือกร้านยา"}), 400
    pharmacy = db.session.get(Pharmacy, int(ph_id))
    if not pharmacy or delivery_type not in pharmacy.delivery_options:
        return jsonify({"message": "ร้านยาไม่รองรับวิธีรับยาที่เลือก"}), 400
    stock = PharmacyStock.query.filter_by(
        pharmacy_id=ph_id, medicine_id=cons.medicine_id).first()
    if not stock or stock.quantity <= 0:
        return jsonify({"message": "ยาหมดสต็อกที่ร้านนี้ กรุณาเลือกร้านอื่น"}), 409

    cons.status        = "CONFIRMED"
    cons.pharmacy_id   = ph_id
    cons.delivery_type = labels.get(delivery_type, "รับหน้าร้าน")
    cons.confirmed_at  = datetime.utcnow()

    order_id = gen_id("RX")
    order    = PharmacyOrder(id=order_id, consultation_id=cons_id,
                             pharmacy_id=ph_id, patient_id=user["id"],
                             medicine_id=cons.medicine_id, quantity=1,
                             unit_price=stock.price,
                             delivery_type=delivery_type)
    db.session.add(order)

    # Reduce stock
    stock.quantity -= 1

    db.session.commit()

    # Notify pharmacy
    socketio.emit("new_order", order.to_dict(), room=f"pharmacy_{ph_id}")

    return jsonify({"message": "ยืนยันการรับยาเรียบร้อย", "order_id": order_id})

# ══════════════════════════════════════════════════════════════════════════════
# MESSAGING API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/messages")
def get_messages():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    cons_id = request.args.get("consultation_id")
    if not cons_id:
        return jsonify({"message": "กรุณาระบุ consultation_id"}), 400

    cons = Consultation.query.get(cons_id)
    if not cons:
        return jsonify([])

    # Access check
    if user["role"] == "PATIENT" and cons.patient_id != user["id"]:
        return jsonify({"message": "Unauthorized"}), 403
    if user["role"] == "DOCTOR" and cons.doctor_id not in (user["id"], None):
        return jsonify({"message": "Unauthorized"}), 403

    msgs = (Message.query
            .filter_by(consultation_id=cons_id)
            .order_by(Message.created_at.asc())
            .all())
    return jsonify([m.to_dict() for m in msgs])

@app.post("/api/messages/send")
def send_message():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data    = request.get_json(silent=True) or {}
    cons_id = data.get("consultation_id")
    text    = data.get("text", "").strip()

    if not cons_id or not text:
        return jsonify({"message": "ข้อมูลไม่ครบ"}), 400

    cons = Consultation.query.get(cons_id)
    if not cons:
        return jsonify({"message": "ไม่พบ consultation"}), 404

    msg = Message(consultation_id=cons_id, sender_id=user["id"], text=text)
    db.session.add(msg)
    db.session.commit()

    msg_dict = msg.to_dict()
    # Broadcast to both patient and doctor in this consultation
    socketio.emit("new_message", {**msg_dict, "consultation_id": cons_id},
                  room=f"cons_{cons_id}")
    return jsonify({"status": "ok", "message": msg_dict})

# ══════════════════════════════════════════════════════════════════════════════
# AI API
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/ai/analyze-symptoms")
def ai_analyze_symptoms():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data       = request.get_json(silent=True) or {}
    symptoms   = data.get("symptoms", "")
    patient_id = data.get("patient_id")
    image_uploaded = bool(data.get("image_url"))
    if not symptoms:
        return jsonify({"message": "กรุณาระบุอาการ"}), 400

    # Pull allergy data so the symptom analysis is allergy-aware
    profile   = PatientProfile.query.filter_by(user_id=patient_id).first() if patient_id else None
    allergies = profile.allergies if profile else []
    conditions = profile.conditions if profile else []
    allergy_str = ", ".join(allergies) if allergies else "ไม่มีประวัติแพ้ยา"
    condition_str = ", ".join(conditions) if conditions else "ไม่มี"

    # Build available medicine list for AI to reference
    meds = Medicine.query.all()
    med_lines = []
    for m in meds:
        contra = m.contraindications
        blocked = any(a in contra for a in allergies) if allergies else False
        flag = " ❌ห้ามใช้(แพ้ยากลุ่มนี้)" if blocked else ""
        med_lines.append(f"  - {m.name} ({m.dosage}): {m.purpose}{flag}")
    med_list_str = "\n".join(med_lines) if med_lines else "  ไม่มีข้อมูลยาในระบบ"

    prompt = f"""คุณเป็นผู้ช่วยแพทย์ AI ทำหน้าที่สรุปข้อมูลและวิเคราะห์เบื้องต้นก่อนที่แพทย์จะตรวจ
ข้อมูลผู้ป่วย:
- อาการที่รายงาน: {symptoms}
- ประวัติแพ้ยา: {allergy_str}
- โรคประจำตัว: {condition_str}
- มีรูปภาพแนบ: {"มี" if image_uploaded else "ไม่มี"}

ยาที่มีในระบบ MediLink:
{med_list_str}

ตอบเป็นภาษาไทย กระชับ แบ่งเป็นหัวข้อชัดเจน ดังนี้:

🔍 อาการหลัก:
[สรุปอาการหลักที่รายงาน และระยะเวลาถ้าระบุมา]

🧬 วิเคราะห์ความน่าจะเป็นของโรค:
[เรียงจากมากไปน้อย ระบุเป็นเปอร์เซ็นต์โดยประมาณ เช่น
• ชื่อโรค — XX% — อธิบายเหตุผลสั้นๆ ว่าทำไมถึงเข้าข่าย
ระบุ 3-5 โรคที่น่าจะเป็นไปได้ อิงจากอาการที่รายงาน]

💊 ยาที่อาจเกี่ยวข้อง (เฉพาะยาที่มีในระบบ):
[แนะนำยาจากรายการในระบบที่เหมาะกับแต่ละโรคที่วิเคราะห์ ระบุชื่อยา+ขนาด และโรคที่ใช้รักษา
ถ้ามียาที่ผู้ป่วยแพ้ให้เตือนด้วย ❌ ว่าห้ามใช้ พร้อมระบุเหตุผล]

⚠️ ข้อควรระวัง:
[ระบุถ้ามีประวัติแพ้ยาหรือโรคประจำตัวที่ต้องระวัง สัญญาณที่น่าเป็นห่วง]

❓ คำถามที่แพทย์ควรถามเพิ่ม:
[3-4 ข้อ กระชับ เพื่อช่วยยืนยันการวินิจฉัย]

📋 คำแนะนำเบื้องต้น:
[ประเมินความเร่งด่วน เช่น ควรตรวจตามนัดปกติ หรือควรรีบตรวจ]

⚕️ หมายเหตุ: ข้อมูลนี้เป็นเพียงการวิเคราะห์เบื้องต้นเพื่อสนับสนุนแพทย์ ไม่ใช่การวินิจฉัย การวินิจฉัยและสั่งยาสุดท้ายต้องทำโดยแพทย์เท่านั้น"""
    gemini_text, gemini_err = generate_with_gemini(prompt)
    if gemini_text:
        return jsonify({"analysis": gemini_text, "source": "gemini"})

    # Fallback rule-based — basic disease probability
    found = [k for k in ["ไข้", "ปวดหัว", "ไอ", "เจ็บคอ", "น้ำมูก",
                         "ผื่น", "คัน", "ท้องเสีย", "ปวดท้อง", "แสบ",
                         "กรด", "ท้องอืด", "ปวด", "อักเสบ"] if k in symptoms]

    # Simple differential
    diseases = []
    if any(k in symptoms for k in ["ไข้", "เจ็บคอ", "น้ำมูก", "ไอ"]):
        diseases.append("• ไข้หวัด/หวัดธรรมดา — สูง")
        diseases.append("• ทอนซิลอักเสบ — ปานกลาง (ถ้าเจ็บคอมาก)")
        diseases.append("• ไข้หวัดใหญ่ — ปานกลาง (ถ้าไข้สูง+ปวดเมื่อย)")
    if any(k in symptoms for k in ["ผื่น", "คัน"]):
        diseases.append("• ผื่นแพ้/ลมพิษ — สูง")
        diseases.append("• ผิวหนังอักเสบ — ปานกลาง")
    if any(k in symptoms for k in ["ท้องเสีย", "ปวดท้อง"]):
        diseases.append("• กระเพาะอาหารอักเสบ — สูง")
        diseases.append("• อาหารเป็นพิษ — ปานกลาง")
    if any(k in symptoms for k in ["แสบ", "กรด", "ท้องอืด", "เรอ"]):
        diseases.append("• กรดไหลย้อน/GERD — สูง")
    if not diseases:
        diseases.append("• ควรประเมินเพิ่มเติมโดยแพทย์")

    allergy_warn = f"\n⚠️ ข้อควรระวัง: ผู้ป่วยแพ้ยา {allergy_str} — ห้ามสั่งยาในกลุ่มนี้" if allergies else ""

    analysis = (
        f"🔍 อาการหลัก: {', '.join(found) if found else symptoms[:100]}\n\n"
        f"🧬 วิเคราะห์ความน่าจะเป็น (rule-based):\n" + "\n".join(diseases) + "\n"
        f"{allergy_warn}\n\n"
        "❓ คำถามติดตาม: เริ่มมีอาการเมื่อใด อุณหภูมิสูงสุดเท่าไร "
        "มีโรคประจำตัว ประวัติแพ้ยา หรือกำลังใช้ยาอะไรอยู่หรือไม่\n\n"
        "⚕️ ข้อมูลนี้เป็นเพียงสรุปเบื้องต้น (AI ไม่พร้อมใช้งาน) "
        "การวินิจฉัยสุดท้ายต้องทำโดยแพทย์เท่านั้น"
    )

    return jsonify({"analysis": analysis, "source": "rule-based", "ai_error": gemini_err})

@app.post("/api/ai/case-summary")
def ai_case_summary():
    user = require_role("DOCTOR")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data       = request.get_json(silent=True) or {}
    symptoms   = data.get("symptoms", "")
    patient_id = data.get("patient_id")
    image_uploaded = bool(data.get("image_url"))
    profile = PatientProfile.query.filter_by(user_id=patient_id).first() if patient_id else None
    allergies = profile.allergies if profile else []
    conditions = profile.conditions if profile else []
    allergy_str = ", ".join(allergies) if allergies else "ไม่มี"
    condition_str = ", ".join(conditions) if conditions else "ไม่มี"

    # Build medicine reference for the summary
    meds = Medicine.query.all()
    med_lines = []
    for m in meds:
        contra = m.contraindications
        blocked = any(a in contra for a in allergies) if allergies else False
        flag = " ❌ห้ามใช้(ผู้ป่วยแพ้)" if blocked else ""
        med_lines.append(f"  - {m.name} ({m.dosage}): {m.purpose} | ข้อห้าม: {', '.join(contra) if contra else 'ไม่มี'}{flag}")
    med_list_str = "\n".join(med_lines) if med_lines else "  ไม่มีข้อมูล"

    prompt = f"""คุณเป็นผู้ช่วยแพทย์ AI สรุปเคสก่อนตรวจและวิเคราะห์เบื้องต้นสำหรับแพทย์
ข้อมูลผู้ป่วย:
- อาการที่รายงาน: {symptoms}
- ประวัติแพ้ยา: {allergy_str}
- โรคประจำตัว: {condition_str}
- มีรูปภาพแนบ: {"มี — แพทย์ควรดูรูปประกอบด้วย" if image_uploaded else "ไม่มี"}

ยาที่มีในระบบ MediLink:
{med_list_str}

สรุปเป็นภาษาไทย กระชับ แบ่งหัวข้อชัดเจน:

📌 สรุปเคส:
[2-3 ประโยค สรุปภาพรวมอาการที่ผู้ป่วยรายงาน]

🧬 Differential Diagnosis (วินิจฉัยแยกโรค):
[เรียงจากน่าจะเป็นมากไปน้อย ระบุ % โดยประมาณ
• ชื่อโรค — XX% — เหตุผลสั้นๆ
ระบุ 3-5 โรค]

💊 แนะนำยาจากระบบ (สำหรับแพทย์พิจารณา):
[จับคู่ยาจากรายการในระบบกับแต่ละโรคที่วิเคราะห์
- ชื่อยา+ขนาด → ใช้สำหรับโรคอะไร
- ถ้ามียาที่ผู้ป่วยแพ้ ให้เตือน ❌ พร้อมแนะนำยาทดแทนที่ปลอดภัย]

⚠️ แจ้งเตือนแพทย์:
[ระบุถ้ามีประวัติแพ้ยา โรคประจำตัว หรือข้อมูลที่แพทย์ควรระวังก่อนสั่งยา]

❓ แนะนำคำถามเพิ่มเติม:
[3-4 ข้อที่แพทย์ควรถามผู้ป่วยเพื่อยืนยันการวินิจฉัย]

⚕️ ข้อมูลนี้เป็นการวิเคราะห์เบื้องต้นของ AI เพื่อสนับสนุนแพทย์ การวินิจฉัยและสั่งยาสุดท้ายต้องทำโดยแพทย์เท่านั้น"""
    summary, gemini_err = generate_with_gemini(prompt)
    if summary:
        return jsonify({"summary": summary, "patient_allergies": allergies,
                        "source": "gemini"})
    summary = (
        f"📌 สรุปเคส: ผู้ป่วยรายงานอาการ: {symptoms}\n"
        f"{'มีรูปภาพแนบ — แพทย์ควรดูประกอบ' if image_uploaded else ''}\n"
        f"ประวัติแพ้ยา: {allergy_str} | โรคประจำตัว: {condition_str}\n\n"
        "❓ คำถามที่ควรถาม: ระยะเวลาอาการ, อุณหภูมิสูงสุด, ยาที่ใช้อยู่, ความรุนแรง\n\n"
        "⚕️ ข้อมูลนี้เป็นสรุปเบื้องต้น (AI ไม่พร้อมใช้งาน) การวินิจฉัยต้องทำโดยแพทย์"
    )
    return jsonify({"summary": summary, "patient_allergies": allergies,
                    "source": "rule-based", "ai_error": gemini_err})

@app.post("/api/ai/chat")
def ai_chat():
    if not current_user():
        return jsonify({"message": "Unauthorized"}), 401
    question = str((request.get_json(silent=True) or {}).get("message", "")).strip()
    if not question:
        return jsonify({"message": "กรุณาพิมพ์คำถาม"}), 400
    disclaimer = "ข้อมูลนี้ไม่ใช่การวินิจฉัย หากอาการรุนแรงหรือฉุกเฉินควรพบแพทย์ทันที"
    prompt = f"""คุณเป็นผู้ช่วยทั่วไปของระบบ telemedicine ตอบภาษาไทยอย่างกระชับ
คำถามผู้ใช้: {question}
ช่วยแนะนำประเภทแพทย์หรือวิธีใช้ระบบได้ แต่ห้ามวินิจฉัย ห้ามสั่งยา
ถ้ามีสัญญาณฉุกเฉินให้แนะนำไปโรงพยาบาล และลงท้ายด้วยคำเตือนว่าไม่ใช่การวินิจฉัย"""
    answer, _ = generate_with_gemini(prompt)
    if answer:
        return jsonify({"answer": answer, "source": "gemini"})
    if any(k in question for k in ["เจ็บคอ", "ไอ", "คอ"]):
        answer = "คุณอาจเริ่มจากแพทย์ทั่วไปหรือแพทย์หู คอ จมูก เพื่อประเมินอาการ"
    elif any(k in question for k in ["ผิว", "ผื่น", "สิว", "คัน"]):
        answer = "แพทย์โรคผิวหนังเหมาะสำหรับอาการผื่น คัน สิว หรือความผิดปกติของผิวหนัง"
    else:
        answer = "แนะนำเริ่มจากแพทย์ทั่วไป ซึ่งจะช่วยประเมินและส่งต่อแพทย์เฉพาะทางได้"
    return jsonify({"answer": f"{answer}\n\n{disclaimer}", "source": "rule-based"})

# ══════════════════════════════════════════════════════════════════════════════
# PHARMACY API
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# TOS / CONSENT API
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/consent/tos")
def accept_tos():
    """Record general TOS + PDPA acceptance."""
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    db_user = User.query.get(user["id"])
    if not db_user:
        return jsonify({"message": "User not found"}), 404
    db_user.tos_accepted    = True
    db_user.tos_accepted_at = datetime.utcnow()
    db.session.commit()
    session["user"] = db_user.to_session()
    return jsonify({"ok": True})

@app.post("/api/consent/ai-doctor")
def accept_ai_doctor_consent():
    """Record AI Doctor specific consent (shown before first AI Doctor session)."""
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    db_user = User.query.get(user["id"])
    if not db_user:
        return jsonify({"message": "User not found"}), 404
    db_user.ai_doctor_consent = True
    db_user.ai_consent_at     = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})

@app.get("/api/consent/status")
def consent_status():
    """Return current consent flags for the logged-in user."""
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    db_user = User.query.get(user["id"])
    return jsonify({
        "tos_accepted":      bool(db_user.tos_accepted) if db_user else False,
        "ai_doctor_consent": bool(db_user.ai_doctor_consent) if db_user else False,
    })

# ══════════════════════════════════════════════════════════════════════════════
# AI DOCTOR API  (OTC guidance only — requires ai_doctor_consent)
# ══════════════════════════════════════════════════════════════════════════════

def _medicine_conflicts_with_allergies(medicine: Medicine, allergies: list[str]) -> bool:
    allergy_terms = [str(value).strip().lower() for value in allergies if str(value).strip()]
    contraindications = [
        str(value).strip().lower()
        for value in medicine.contraindications
        if str(value).strip()
    ]
    return any(
        allergy in contraindication or contraindication in allergy
        for allergy in allergy_terms
        for contraindication in contraindications
    )

def _medicine_recommendation(medicine: Medicine) -> dict | None:
    stock = (PharmacyStock.query.filter_by(medicine_id=medicine.id)
             .filter(PharmacyStock.quantity > 0)
             .order_by(PharmacyStock.price.asc()).first())
    if not stock:
        return None
    return {
        "id": medicine.id,
        "name": medicine.name,
        "dosage": medicine.dosage,
        "instruction": medicine.instruction,
        "pharmacy_name": stock.pharmacy.name,
        "pharmacy_id": stock.pharmacy_id,
        "price": stock.price,
    }

def _ai_doctor_fallback(
    message: str, allergies: list[str], conditions: list[str],
    otc_medicines: list[Medicine],
) -> tuple[str, list[dict]]:
    normalized = message.lower()
    emergency_terms = [
        "หายใจไม่ออก", "เจ็บหน้าอก", "หมดสติ", "ชัก", "หน้าเบี้ยว",
        "แขนขาอ่อนแรง", "เลือดออกมาก", "แพ้รุนแรง",
    ]
    if any(term in normalized for term in emergency_terms):
        return (
            "อาการที่เล่ามาอาจเป็นภาวะฉุกเฉิน กรุณาโทร 1669 หรือไปห้องฉุกเฉินทันที "
            "ไม่ควรรอดูอาการหรือซื้อยารับประทานเอง\n\n"
            "⚕️ AI ให้คำแนะนำเบื้องต้นเท่านั้น ไม่ใช่การวินิจฉัยทางการแพทย์",
            [],
        )

    matched = []
    for medicine in otc_medicines:
        terms = [medicine.name.lower(), (medicine.generic or "").lower()]
        terms.extend(str(keyword).lower() for keyword in medicine.keywords)
        if any(term and term in normalized for term in terms):
            if (
                (medicine.generic or "").lower() == "ibuprofen"
                and any("หอบหืด" in str(condition) for condition in conditions)
            ):
                continue
            if not _medicine_conflicts_with_allergies(medicine, allergies):
                matched.append(medicine)

    self_care = []
    if any(term in normalized for term in ["ไข้", "ปวดหัว", "ปวดเมื่อย"]):
        self_care.append("พักผ่อน ดื่มน้ำให้เพียงพอ และวัดอุณหภูมิเป็นระยะ")
    if any(term in normalized for term in ["ไอ", "เจ็บคอ", "น้ำมูก"]):
        self_care.append("จิบน้ำอุ่น หลีกเลี่ยงควัน และสังเกตอาการหายใจลำบาก")
    if any(term in normalized for term in ["ท้องเสีย", "อาเจียน"]):
        self_care.append("จิบน้ำหรือสารละลายเกลือแร่บ่อย ๆ เพื่อป้องกันภาวะขาดน้ำ")
    if not self_care:
        self_care.append("พักผ่อนและติดตามความรุนแรง ระยะเวลา และอาการร่วมอย่างใกล้ชิด")

    recommendations = []
    medicine_lines = []
    for medicine in matched[:1]:
        payload = _medicine_recommendation(medicine)
        if payload:
            recommendations.append(payload)
            medicine_lines.append(
                f"- {medicine.name}: {medicine.instruction or 'ใช้ตามฉลากยา'}")

    profile_note = ""
    if allergies:
        profile_note += f"\nประวัติแพ้ยาที่บันทึกไว้: {', '.join(allergies)}"
    if conditions:
        profile_note += f"\nโรคประจำตัวที่บันทึกไว้: {', '.join(conditions)}"
    medicine_note = (
        "\n\nยาที่อาจใช้บรรเทาอาการได้:\n" + "\n".join(medicine_lines)
        + "\nควรอ่านฉลากและปรึกษาเภสัชกรก่อนใช้"
        if medicine_lines else
        "\n\nยังไม่ควรเลือกยาจากข้อมูลนี้เพียงอย่างเดียว กรุณาบอกอายุ อาการร่วม "
        "ระยะเวลาที่เป็น และยาที่ใช้อยู่เพิ่มเติม"
    )
    reply = (
        "คำแนะนำเบื้องต้น:\n- " + "\n- ".join(self_care)
        + medicine_note + profile_note
        + "\n\nหากไข้สูงต่อเนื่อง อาการแย่ลง ซึม รับประทานไม่ได้ หรือหายใจลำบาก ให้พบแพทย์ทันที"
        + "\n\n⚕️ AI ให้คำแนะนำเบื้องต้นเท่านั้น ไม่ใช่การวินิจฉัยทางการแพทย์"
    )
    return reply, recommendations

@app.post("/api/ai/doctor-chat")
def ai_doctor_chat():
    """AI Doctor: symptom Q&A + OTC medicine suggestions. Requires consent."""
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    db_user = User.query.get(user["id"])
    if not db_user or not db_user.ai_doctor_consent:
        return jsonify({"message": "CONSENT_REQUIRED"}), 403

    data     = request.get_json(silent=True) or {}
    message  = str(data.get("message", "")).strip()
    history  = data.get("history", [])   # [{role, content}, ...]
    if not message:
        return jsonify({"message": "กรุณาพิมพ์อาการหรือคำถาม"}), 400

    # Pull patient profile for allergy-aware responses
    profile   = PatientProfile.query.filter_by(user_id=user["id"]).first()
    allergies = profile.allergies if profile else []
    conditions = profile.conditions if profile else []

    # Available OTC medicines in system
    otc_meds = Medicine.query.filter_by(is_otc=True).all()
    otc_list = ", ".join(f"{m.name} ({m.dosage})" for m in otc_meds[:20])

    system_prompt = f"""คุณเป็น AI Doctor ผู้ช่วยทางการแพทย์เบื้องต้นของระบบ MediLink
ข้อมูลผู้ใช้:
- ประวัติแพ้ยา: {", ".join(allergies) if allergies else "ไม่มี"}
- โรคประจำตัว: {", ".join(conditions) if conditions else "ไม่มี"}
- ยาที่มีในระบบ: {otc_list}

กฎสำคัญ:
1. ตอบเป็นภาษาไทย กระชับ เข้าใจง่าย
2. แนะนำยา OTC ได้ แต่ต้องตรวจสอบกับประวัติแพ้ยาก่อนทุกครั้ง
3. ถ้าอาการรุนแรง ฉุกเฉิน หรือต้องการยาที่มีใบสั่งแพทย์ → แนะนำพบหมอจริงหรือโทร 1669
4. ห้ามวินิจฉัยโรคอย่างเป็นทางการ ใช้คำว่า "อาจเป็น" หรือ "น่าจะเกี่ยวกับ"
5. ลงท้ายทุกคำตอบด้วย: "⚕️ AI ให้คำแนะนำเบื้องต้นเท่านั้น ไม่ใช่การวินิจฉัยทางการแพทย์"
6. ถ้าแนะนำยาให้ระบุชื่อยาที่มีในระบบเท่านั้น"""

    # Build messages with history
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-6:]:   # keep last 6 turns for context
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    reply, ai_error = generate_chat_completion(
        messages, max_tokens=800, temperature=0.3)
    if reply:
        recommended = []
        for medicine in otc_meds:
            if medicine.name in reply and not _medicine_conflicts_with_allergies(
                    medicine, allergies):
                payload = _medicine_recommendation(medicine)
                if payload:
                    recommended.append(payload)
        return jsonify({
            "reply": reply,
            "recommended_medicines": recommended,
            "source": "groq",
        })

    fallback_reply, recommended = _ai_doctor_fallback(
        message, allergies, conditions, otc_meds)
    return jsonify({
        "reply": fallback_reply,
        "recommended_medicines": recommended,
        "source": "rule-based",
        "ai_error": ai_error,
    })

@app.post("/api/drug-interaction-check")
def drug_interaction_check():
    user = require_role("DOCTOR")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    try:
        medicine_id = int(data.get("new_medicine_id"))
        patient_id = int(data.get("patient_id"))
    except (TypeError, ValueError):
        return jsonify({"message": "ข้อมูลยาและผู้ป่วยไม่ถูกต้อง"}), 400

    medicine = db.session.get(Medicine, medicine_id)
    patient = User.query.filter_by(id=patient_id, role="PATIENT").first()
    if not medicine or not patient:
        return jsonify({"message": "ไม่พบข้อมูลยา หรือผู้ป่วย"}), 404

    profile = PatientProfile.query.filter_by(user_id=patient_id).first()
    allergies = profile.allergies if profile else []
    conditions = profile.conditions if profile else []
    recent_consultations = (Consultation.query
                            .filter(
                                Consultation.patient_id == patient_id,
                                Consultation.medicine_id.isnot(None),
                                Consultation.created_at >= datetime.utcnow() - timedelta(days=180),
                            )
                            .order_by(Consultation.created_at.desc()).limit(10).all())
    current_medicines = []
    seen_ids = set()
    for consultation in recent_consultations:
        if consultation.medicine and consultation.medicine_id not in seen_ids:
            seen_ids.add(consultation.medicine_id)
            current_medicines.append(consultation.medicine)

    current_names = [item.name for item in current_medicines]
    generic = (medicine.generic or medicine.name).lower()
    warnings = []
    level = "WARNING"

    if _medicine_conflicts_with_allergies(medicine, allergies):
        level = "DANGER"
        warnings.append(
            f"ผู้ป่วยมีประวัติแพ้ยาที่อาจเกี่ยวข้องกับ {medicine.name}")

    if medicine.id in seen_ids:
        warnings.append(f"พบ {medicine.name} ในประวัติยาล่าสุด อาจเป็นการสั่งยาซ้ำ")

    antihistamines = {"cetirizine", "loratadine"}
    if generic in antihistamines:
        duplicate = next(
            (item for item in current_medicines
             if (item.generic or "").lower() in antihistamines
             and item.id != medicine.id),
            None,
        )
        if duplicate:
            warnings.append(
                f"{medicine.name} และ {duplicate.name} เป็นยาแก้แพ้กลุ่มใกล้เคียงกัน "
                "ควรทบทวนความจำเป็นก่อนใช้ร่วมกัน")

    if generic == "ibuprofen" and any(
            "หอบหืด" in str(condition) for condition in conditions):
        level = "DANGER"
        warnings.append(
            "ผู้ป่วยมีประวัติหอบหืด ควรประเมินความเสี่ยงจากยา NSAIDs ก่อนสั่ง Ibuprofen")

    if warnings:
        return jsonify({
            "safe": False,
            "level": level,
            "warning": " · ".join(warnings)
                       + " · การตรวจนี้เป็นเพียงตัวช่วยเบื้องต้น แพทย์ต้องตรวจสอบซ้ำ",
            "current_meds": current_names,
        })
    return jsonify({
        "safe": True,
        "level": "OK",
        "warning": "",
        "current_meds": current_names,
    })

# ══════════════════════════════════════════════════════════════════════════════
# MEDICINE FINDER API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/medicine-finder")
def medicine_finder():
    user = current_user()
    medicines = Medicine.query.order_by(Medicine.name).all()
    return render_template("medicine_finder.html", medicines=medicines, user=user)

@app.get("/api/medicine-finder/search")
def medicine_finder_search():
    """Search medicines by name and return nearby pharmacies with stock."""
    q          = request.args.get("q", "").strip()
    user_lat   = request.args.get("lat", type=float, default=13.7563)
    user_lng   = request.args.get("lng", type=float, default=100.5018)
    max_km     = request.args.get("km",  type=float, default=20.0)

    meds_q = Medicine.query
    if q:
        meds_q = meds_q.filter(
            db.or_(Medicine.name.ilike(f"%{q}%"),
                   Medicine.generic.ilike(f"%{q}%")))
    medicines = meds_q.limit(20).all()

    results = []
    for med in medicines:
        stocks = PharmacyStock.query.filter_by(medicine_id=med.id)                    .filter(PharmacyStock.quantity > 0).all()
        nearby = []
        for s in stocks:
            ph  = s.pharmacy
            km  = haversine(user_lat, user_lng, ph.latitude, ph.longitude)
            if km <= max_km:
                nearby.append({
                    "pharmacy_id":   ph.id,
                    "pharmacy_name": ph.name,
                    "address":       ph.address,
                    "phone":         ph.phone,
                    "latitude":      ph.latitude,
                    "longitude":     ph.longitude,
                    "distance_km":   round(km, 1),
                    "quantity":      s.quantity,
                    "price":         s.price,
                    "delivery_options": ph.delivery_options,
                    "rating":        ph.rating,
                })
        nearby.sort(key=lambda x: x["distance_km"])
        results.append({
            "id": med.id, "name": med.name, "generic": med.generic,
            "form": med.form, "dosage": med.dosage,
            "is_otc": bool(med.is_otc),
            "purpose": med.purpose, "instruction": med.instruction,
            "nearby_pharmacies": nearby[:5],
        })
    return jsonify(results)


@app.get("/api/pharmacy/<int:pharmacy_id>/stock")
def pharmacy_stock_detail(pharmacy_id):
    """Return full stock list for a single pharmacy — used by medicine finder detail panel."""
    ph = Pharmacy.query.get_or_404(pharmacy_id)
    stocks = (PharmacyStock.query
              .filter_by(pharmacy_id=pharmacy_id)
              .filter(PharmacyStock.quantity > 0)
              .all())
    return jsonify({
        "id":       ph.id,
        "name":     ph.name,
        "address":  ph.address,
        "phone":    ph.phone,
        "rating":   ph.rating,
        "latitude": ph.latitude,
        "longitude":ph.longitude,
        "delivery_options": ph.delivery_options,
        "medicines": [{
            "medicine_id":   s.medicine_id,
            "name":          s.medicine.name,
            "generic":       s.medicine.generic,
            "dosage":        s.medicine.dosage,
            "form":          s.medicine.form,
            "purpose":       s.medicine.purpose,
            "instruction":   s.medicine.instruction,
            "side_effects":  s.medicine.side_effects,
            "is_otc":        bool(s.medicine.is_otc),
            "quantity":      s.quantity,
            "price":         s.price,
        } for s in stocks]
    })

@app.post("/api/medicine-finder/order")
def medicine_finder_order():
    """Compatibility endpoint: adding from medicine finder now uses the cart."""
    return add_medicine_to_cart()

@app.get("/api/pharmacies/nearby")
def nearby_pharmacies():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    med_id = request.args.get("medicine_id", type=int)
    patient_id = request.args.get("patient_id", type=int)
    profile = None
    if user["role"] == "DOCTOR" and patient_id:
        profile = PatientProfile.query.filter_by(user_id=patient_id).first()
    elif user["role"] == "PATIENT":
        profile = PatientProfile.query.filter_by(user_id=user["id"]).first()
    lat = request.args.get("lat", type=float, default=profile.latitude if profile else 13.7563)
    lng = request.args.get("lng", type=float, default=profile.longitude if profile else 100.5018)

    q = PharmacyStock.query
    if med_id:
        q = q.filter_by(medicine_id=med_id).filter(PharmacyStock.quantity > 0)

    results = []
    for s in q.all():
        ph   = s.pharmacy
        dist = haversine(lat, lng, ph.latitude, ph.longitude)
        delivery_options = list(ph.delivery_options)
        if dist > ph.delivery_radius_km and "express" in delivery_options:
            delivery_options.remove("express")
        results.append({
            "id": ph.id, "name": ph.name, "address": ph.address,
            "phone": ph.phone, "rating": ph.rating,
            "distance": round(dist, 1),
            "stock": s.quantity, "price": s.price,
            "delivery_options": delivery_options,
            "medicine_id": s.medicine_id,
        })
    results.sort(key=lambda x: x["distance"])
    return jsonify(results[:6])

@app.get("/api/pharmacy-stock/<int:pharmacy_id>")
def pharmacy_stock_api(pharmacy_id):
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    stocks = PharmacyStock.query.filter_by(pharmacy_id=pharmacy_id).all()
    return jsonify([{"medicine_id": s.medicine_id, "name": s.medicine.name,
                     "quantity": s.quantity, "price": s.price} for s in stocks])

@app.post("/api/pharmacy/orders/<order_id>/status")
def update_order_status(order_id: str):
    user = require_role("PHARMACY")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data   = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in {"ACCEPTED", "PREPARING", "COMPLETED"}:
        return jsonify({"message": "สถานะไม่ถูกต้อง"}), 400

    db_user = db.session.get(User, user["id"])
    order = PharmacyOrder.query.filter_by(
        id=order_id, pharmacy_id=db_user.pharmacy_id).first()
    if not order:
        return jsonify({"message": "ไม่พบออเดอร์"}), 404

    order.status = status
    db.session.commit()

    # Notify patient
    socketio.emit("order_status_update", {"order_id": order_id, "status": status},
                  room=f"patient_{order.patient_id}")

    return jsonify({"message": "อัปเดตสถานะแล้ว", "status": status})

@app.post("/api/shop/orders/<order_id>/status")
def update_shop_order_status(order_id: str):
    user = require_role("PHARMACY")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    db_user = db.session.get(User, user["id"])
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in {"ACCEPTED", "PREPARING", "COMPLETED"}:
        return jsonify({"message": "สถานะไม่ถูกต้อง"}), 400
    order = ShopOrder.query.filter_by(
        id=order_id, pharmacy_id=db_user.pharmacy_id).first()
    if not order:
        return jsonify({"message": "ไม่พบออเดอร์"}), 404
    order.status = status
    db.session.commit()
    socketio.emit("shop_order_status_update", {
        "order_id": order.id, "status": status,
    }, room=f"patient_{order.patient_id}")
    return jsonify({"message": "อัปเดตสถานะแล้ว", "status": status})

@app.post("/api/stock/update")
def update_stock():
    user = require_role("PHARMACY")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    db_user = db.session.get(User, user["id"])
    data = request.get_json(silent=True) or {}
    s = PharmacyStock.query.filter_by(
        id=data.get("stock_id"),
        pharmacy_id=db_user.pharmacy_id if db_user else None,
    ).first()
    if not s:
        return jsonify({"message": "ไม่พบรายการ"}), 404

    try:
        if data.get("quantity") is not None:
            s.quantity = max(0, int(data["quantity"]))
        if data.get("price") is not None:
            s.price = max(0, float(data["price"]))
    except (TypeError, ValueError):
        return jsonify({"message": "จำนวนหรือราคาไม่ถูกต้อง"}), 400
    db.session.commit()
    return jsonify({"message": "อัปเดตสต็อกแล้ว"})

@app.post("/api/product-stock/update")
def update_product_stock():
    user = require_role("PHARMACY")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    db_user = db.session.get(User, user["id"])
    data = request.get_json(silent=True) or {}
    stock = ProductStock.query.filter_by(
        id=data.get("stock_id"), pharmacy_id=db_user.pharmacy_id).first()
    if not stock:
        return jsonify({"message": "ไม่พบรายการ"}), 404
    try:
        if data.get("quantity") is not None:
            stock.quantity = max(0, int(data["quantity"]))
        if data.get("price") is not None:
            stock.price = max(0, float(data["price"]))
    except (TypeError, ValueError):
        return jsonify({"message": "จำนวนหรือราคาไม่ถูกต้อง"}), 400
    db.session.commit()
    return jsonify({"message": "อัปเดตสต็อกสินค้าแล้ว"})

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/admin/analytics")
def admin_analytics():
    user = require_role("ADMIN")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    today = datetime.utcnow().date()
    start_date = today - timedelta(days=13)
    start_datetime = datetime.combine(start_date, datetime.min.time())
    daily_counts = {}
    for consultation in Consultation.query.filter(
            Consultation.created_at >= start_datetime).all():
        day = consultation.created_at.date()
        daily_counts[day] = daily_counts.get(day, 0) + 1
    cons_by_day = []
    for offset in range(14):
        day = start_date + timedelta(days=offset)
        cons_by_day.append({
            "date": day.strftime("%d/%m"),
            "count": daily_counts.get(day, 0),
        })

    top_doctors = []
    for doctor in User.query.filter_by(role="DOCTOR").all():
        cases = Consultation.query.filter_by(doctor_id=doctor.id).count()
        if cases:
            top_doctors.append({"name": doctor.name, "cases": cases})
    top_doctors.sort(key=lambda item: item["cases"], reverse=True)

    medicine_counts = {}
    for medicine_id, in db.session.query(Consultation.medicine_id).filter(
            Consultation.medicine_id.isnot(None)).all():
        medicine_counts[medicine_id] = medicine_counts.get(medicine_id, 0) + 1
    for medicine_id, in db.session.query(PharmacyOrder.medicine_id).filter(
            PharmacyOrder.medicine_id.isnot(None)).all():
        medicine_counts[medicine_id] = medicine_counts.get(medicine_id, 0) + 1
    medicines_by_id = ({
        medicine.id: medicine.name
        for medicine in Medicine.query.filter(
            Medicine.id.in_(medicine_counts.keys())).all()
    } if medicine_counts else {})
    top_meds = [
        {"name": medicines_by_id.get(medicine_id, str(medicine_id)), "count": count}
        for medicine_id, count in medicine_counts.items()
    ]
    top_meds.sort(key=lambda item: item["count"], reverse=True)

    ph_orders = []
    for pharmacy in Pharmacy.query.all():
        order_count = (
            PharmacyOrder.query.filter_by(pharmacy_id=pharmacy.id).count()
            + ShopOrder.query.filter_by(pharmacy_id=pharmacy.id).count()
        )
        if order_count:
            ph_orders.append({"name": pharmacy.name, "orders": order_count})
    ph_orders.sort(key=lambda item: item["orders"], reverse=True)

    return jsonify({
        "cons_by_day": cons_by_day,
        "top_doctors": top_doctors[:5],
        "top_meds": top_meds[:5],
        "ph_orders": ph_orders[:8],
    })

@app.post("/api/admin/add_doctor")
def add_doctor():
    user = require_role("ADMIN")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data      = request.get_json(silent=True) or {}
    email     = str(data.get("email", "")).strip().lower()
    name      = str(data.get("name", "")).strip()
    specialty = str(data.get("specialty", "ทั่วไป"))
    password  = str(data.get("password", "doctor1234"))

    if not email or not name:
        return jsonify({"message": "กรุณากรอกข้อมูลให้ครบ"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"message": "อีเมลนี้ถูกใช้แล้ว"}), 400

    doc = User(email=email, role="DOCTOR", name=name, specialty=specialty)
    doc.set_password(password)
    db.session.add(doc)
    db.session.commit()
    return jsonify({"message": f"เพิ่มแพทย์ {name} สำเร็จ | รหัส: {password}"}), 201

@app.post("/api/admin/add_pharmacy")
def add_pharmacy():
    user = require_role("ADMIN")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data     = request.get_json(silent=True) or {}
    name     = str(data.get("name", "")).strip()
    address  = str(data.get("address", ""))
    email    = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "pharmacy1234"))

    if not name:
        return jsonify({"message": "กรุณาระบุชื่อร้าน"}), 400

    ph = Pharmacy(name=name, address=address)
    db.session.add(ph)
    db.session.flush()

    if email:
        if User.query.filter_by(email=email).first():
            return jsonify({"message": "อีเมลนี้ถูกใช้แล้ว"}), 400
        ph_user = User(email=email, role="PHARMACY", name=name, pharmacy_id=ph.id)
        ph_user.set_password(password)
        db.session.add(ph_user)

    db.session.commit()
    return jsonify({"message": f"เพิ่มร้านยา {name} สำเร็จ"}), 201

@app.post("/api/admin/reset_password")
def reset_password():
    user = require_role("ADMIN")
    if not user:
        return jsonify({"message": "Unauthorized"}), 401

    data     = request.get_json(silent=True) or {}
    email    = str(data.get("email", "")).strip().lower()
    new_pass = str(data.get("new_password", ""))

    if not email or len(new_pass) < 6:
        return jsonify({"message": "ข้อมูลไม่ครบ"}), 400
    target = User.query.filter_by(email=email).first()
    if not target:
        return jsonify({"message": "ไม่พบผู้ใช้"}), 404

    target.set_password(new_pass)
    db.session.commit()
    return jsonify({"message": "รีเซ็ตรหัสผ่านแล้ว"})

# ══════════════════════════════════════════════════════════════════════════════
# FILE UPLOAD + HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/upload")
def upload_file():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    if "file" not in request.files:
        return jsonify({"message": "ไม่พบไฟล์"}), 400

    f = request.files["file"]
    if not f or not allowed_file(f.filename):
        return jsonify({"message": "ประเภทไฟล์ไม่รองรับ (png, jpg, jpeg, gif, webp)"}), 400

    fname = secure_filename(f"{uuid.uuid4().hex}_{f.filename}")
    f.save(UPLOAD_DIR / fname)
    return jsonify({"url": f"/static/uploads/{fname}"}), 201

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "version": "2.0", "db": "sqlite"})

@app.get("/api/ai/status")
def ai_status():
    """Debug endpoint — shows whether Groq AI is ready."""
    user = current_user()
    if not user or user.get("role") not in ("DOCTOR", "ADMIN"):
        return jsonify({"message": "Unauthorized"}), 401

    paused = time.time() < ai_paused_until
    status = {
        "provider": "Groq",
        "model": GROQ_MODEL,
        "api_key_set": bool(GROQ_API_KEY),
        "paused": paused,
        "paused_seconds_remaining": max(0, int(ai_paused_until - time.time())) if paused else 0,
    }
    if GROQ_API_KEY and not paused:
        text, err = generate_with_gemini("ping — reply with OK only")
        status["live_test"] = "OK" if text else f"FAIL: {err}"
    return jsonify(status)

@app.post("/api/ai/reset-pause")
def ai_reset_pause():
    """Clear the AI pause so the next request retries immediately."""
    global ai_paused_until
    user = current_user()
    if not user or user.get("role") not in ("DOCTOR", "ADMIN"):
        return jsonify({"message": "Unauthorized"}), 401
    ai_paused_until = 0.0
    return jsonify({"message": "AI pause cleared — will retry on next request"})


# ══════════════════════════════════════════════════════════════════════════════
# LANDING PAGE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/landing")
@app.get("/home")
def landing():
    """Public landing page — accessible without login."""
    total_doctors       = User.query.filter_by(role="DOCTOR", is_active=True).count()
    total_patients      = User.query.filter_by(role="PATIENT").count()
    total_consultations = Consultation.query.count()
    total_pharmacies    = Pharmacy.query.count()
    # Pull 3 recent 5-star reviews for testimonials
    testimonials = (Review.query
                    .filter(Review.rating >= 4)
                    .order_by(Review.created_at.desc())
                    .limit(6).all())
    return render_template("landing.html",
                           stats={"doctors": total_doctors,
                                  "patients": total_patients,
                                  "consultations": total_consultations,
                                  "pharmacies": total_pharmacies},
                           testimonials=testimonials,
                           user=current_user())

# ══════════════════════════════════════════════════════════════════════════════
# DIGITAL PRESCRIPTION
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/prescription/<cons_id>")
def prescription_view(cons_id):
    """Printable digital prescription page."""
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    cons = Consultation.query.get_or_404(cons_id)
    # Allow patient, assigned doctor, or pharmacy to view
    allowed = (
        user["role"] == "ADMIN" or
        user["id"] == cons.patient_id or
        user["id"] == cons.doctor_id or
        (user["role"] == "PHARMACY" and cons.pharmacy_id)
    )
    if not allowed:
        return redirect(url_for("dashboard"))
    if not cons.medicine:
        return "ยังไม่มีใบสั่งยาสำหรับเคสนี้", 404
    profile = PatientProfile.query.filter_by(user_id=cons.patient_id).first()
    return render_template("prescription.html", cons=cons, profile=profile,
                           user=User.query.get(user["id"]))

# ══════════════════════════════════════════════════════════════════════════════
# VIDEO CALL (WebRTC signalling via SocketIO)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/video/<cons_id>")
def video_call(cons_id):
    """WebRTC video call room for a consultation."""
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    cons = Consultation.query.get_or_404(cons_id)
    allowed = (user["id"] == cons.patient_id or user["id"] == cons.doctor_id)
    if not allowed:
        return redirect(url_for("dashboard"))
    return render_template("video_call.html", cons=cons,
                           user=User.query.get(user["id"]),
                           role=user["role"])


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/notifications")
def get_notifications():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    try:
        notifs = (Notification.query.filter_by(user_id=user["id"])
                  .order_by(Notification.created_at.desc()).limit(50).all())
        unread = Notification.query.filter_by(user_id=user["id"], is_read=False).count()
        return jsonify({"notifications": [n.to_dict() for n in notifs], "unread": unread})
    except Exception:
        return jsonify({"notifications": [], "unread": 0})

@app.post("/api/notifications/read-all")
def notifications_read_all():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    try:
        Notification.query.filter_by(user_id=user["id"], is_read=False).update({"is_read": True})
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({"ok": True})

@app.post("/api/notifications/<int:nid>/read")
def notification_read(nid):
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    n = Notification.query.filter_by(id=nid, user_id=user["id"]).first()
    if n:
        n.is_read = True
        db.session.commit()
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
# CONSULTATION HISTORY
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/history")
def consultation_history():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    role = user["role"]
    if role == "PATIENT":
        consultations = (Consultation.query.filter_by(patient_id=user["id"])
                         .order_by(Consultation.created_at.desc()).all())
    elif role == "DOCTOR":
        consultations = (Consultation.query.filter_by(doctor_id=user["id"])
                         .order_by(Consultation.created_at.desc()).all())
    else:
        return redirect(url_for("dashboard"))
    return render_template("consultation_history.html",
                           consultations=consultations,
                           user=User.query.get(user["id"]),
                           active_page="history")

# ══════════════════════════════════════════════════════════════════════════════
# PATIENT PROFILE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/profile")
def profile_page():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    db_user = User.query.get(user["id"])
    profile = PatientProfile.query.filter_by(user_id=user["id"]).first()
    consultations = (Consultation.query.filter_by(patient_id=user["id"])
                     .order_by(Consultation.created_at.desc()).all())
    orders = (PharmacyOrder.query.filter_by(patient_id=user["id"])
              .order_by(PharmacyOrder.created_at.desc()).limit(10).all())
    return render_template("profile.html", user=db_user, profile=profile,
                           consultations=consultations, orders=orders,
                           active_page="profile")

@app.post("/api/profile/update")
def profile_update():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data    = request.get_json(silent=True) or {}
    db_user = User.query.get(user["id"])
    profile = PatientProfile.query.filter_by(user_id=user["id"]).first()
    if not db_user:
        return jsonify({"message": "ไม่พบข้อมูลผู้ใช้"}), 404
    if not profile:
        profile = PatientProfile(user_id=user["id"])
        db.session.add(profile)

    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return jsonify({"message": "กรุณาระบุชื่อ-นามสกุล"}), 400
        db_user.name = name[:100]
    if "address" in data:
        profile.address = str(data["address"]).strip()[:300]
    if "phone" in data:
        phone = str(data["phone"]).strip()
        if len(phone) > 20:
            return jsonify({"message": "เบอร์โทรศัพท์ยาวเกินไป"}), 400
        profile.phone = phone
    for field in ("allergies", "conditions"):
        if field not in data:
            continue
        if not isinstance(data[field], list):
            return jsonify({"message": f"{field} ต้องเป็นรายการ"}), 400
        cleaned = []
        for value in data[field]:
            value = str(value).strip()
            if value and value not in cleaned:
                cleaned.append(value[:100])
        setattr(profile, field, cleaned[:50])

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Profile update failed for user %s", user["id"])
        return jsonify({"message": "ไม่สามารถบันทึกข้อมูลได้ กรุณาลองใหม่"}), 500
    session["user"] = db_user.to_session()
    session.modified = True
    return jsonify({"ok": True, "message": "บันทึกข้อมูลสำเร็จ"})

@app.post("/api/profile/change-password")
def profile_change_password():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data    = request.get_json(silent=True) or {}
    db_user = User.query.get(user["id"])
    if not db_user.check_password(data.get("current_password", "")):
        return jsonify({"message": "รหัสผ่านปัจจุบันไม่ถูกต้อง"}), 400
    new_pw = data.get("new_password", "")
    if len(new_pw) < 8:
        return jsonify({"message": "รหัสผ่านใหม่ต้องมีอย่างน้อย 8 ตัวอักษร"}), 400
    db_user.set_password(new_pw)
    db.session.commit()
    return jsonify({"ok": True, "message": "เปลี่ยนรหัสผ่านสำเร็จ"})

# ══════════════════════════════════════════════════════════════════════════════
# PAYMENT MOCK
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/payment/<order_id>")
def payment_page(order_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    order = PharmacyOrder.query.get(order_id)
    shop  = ShopOrder.query.get(order_id) if not order else None
    target = order or shop
    if not target:
        return redirect(url_for("dashboard"))
    amount = getattr(target, "med_price", None) or getattr(target, "total", 0) or 0
    return render_template("payment.html", order=target, amount=amount,
                           order_id=order_id, user=User.query.get(user["id"]),
                           active_page="")

@app.post("/api/payment/confirm")
def payment_confirm():
    user = current_user()
    if not user:
        return jsonify({"message": "Unauthorized"}), 401
    data     = request.get_json(silent=True) or {}
    order_id = data.get("order_id", "")
    order = PharmacyOrder.query.get(order_id)
    if order:
        order.status = "CONFIRMED"
    else:
        shop = ShopOrder.query.get(order_id)
        if shop:
            shop.status = "CONFIRMED"
    db.session.commit()
    return jsonify({"ok": True, "message": "ชำระเงินสำเร็จ"})

# ══════════════════════════════════════════════════════════════════════════════
# SOCKETIO EVENTS
# ══════════════════════════════════════════════════════════════════════════════

# ── WebRTC signalling events ──────────────────────────────────────────────────
@socketio.on("webrtc_offer")
def on_webrtc_offer(data):
    cons_id = data.get("cons_id")
    emit("webrtc_offer", data, room=f"cons_{cons_id}", include_self=False)

@socketio.on("webrtc_answer")
def on_webrtc_answer(data):
    cons_id = data.get("cons_id")
    emit("webrtc_answer", data, room=f"cons_{cons_id}", include_self=False)

@socketio.on("webrtc_ice")
def on_webrtc_ice(data):
    cons_id = data.get("cons_id")
    emit("webrtc_ice", data, room=f"cons_{cons_id}", include_self=False)

@socketio.on("video_join")
def on_video_join(data):
    cons_id = data.get("cons_id")
    if cons_id:
        join_room(f"cons_{cons_id}")
        emit("video_peer_joined", {"user": data.get("user")}, room=f"cons_{cons_id}", include_self=False)

@socketio.on("connect")
def on_connect():
    u = current_user()
    if not u:
        return
    # Personal room for notifications
    if u["role"] == "PATIENT":
        join_room(f"patient_{u['id']}")
    elif u["role"] == "DOCTOR":
        join_room("doctors")                     # receives new_consultation events
        join_room(f"doctor_{u['id']}")
    elif u["role"] == "PHARMACY":
        db_user = User.query.get(u["id"])
        if db_user and db_user.pharmacy_id:
            join_room(f"pharmacy_{db_user.pharmacy_id}")

@socketio.on("join_consultation")
def on_join_consultation(data):
    u = current_user()
    if not u:
        return
    cons_id = data.get("consultation_id")
    if not cons_id:
        return
    cons = Consultation.query.get(cons_id)
    if not cons:
        return
    # Allow patient, assigned doctor, and assigned pharmacy to join
    uid  = u["id"]
    role = u["role"]
    allowed = (
        role == "ADMIN" or
        (role == "PATIENT"  and cons.patient_id == uid) or
        (role == "DOCTOR"   and (cons.doctor_id == uid or cons.doctor_id is None)) or
        (role == "PHARMACY" and cons.pharmacy_id is not None and
         User.query.filter_by(id=uid, pharmacy_id=cons.pharmacy_id).first())
    )
    if allowed:
        join_room(f"cons_{cons_id}")
        # Notify others that this party joined
        emit("party_joined", {
            "user_id":   uid,
            "user_name": u["name"],
            "role":      role,
            "consultation_id": cons_id,
        }, room=f"cons_{cons_id}")

@socketio.on("send_message")
def on_send_message(data):
    u = current_user()
    if not u:
        return

    cons_id = data.get("consultation_id")
    text    = (data.get("text") or "").strip()
    if not cons_id or not text:
        return

    cons = Consultation.query.get(cons_id)
    if not cons:
        return

    msg = Message(consultation_id=cons_id, sender_id=u["id"], text=text)
    db.session.add(msg)
    db.session.commit()

    msg_dict = msg.to_dict()
    msg_dict["consultation_id"] = cons_id
    emit("new_message", msg_dict, room=f"cons_{cons_id}")

    # Auto-invite pharmacy to room when pharmacy is assigned and sends first message
    if u["role"] == "PHARMACY" and cons.pharmacy_id:
        socketio.emit("pharmacy_joined_case", {
            "consultation_id": cons_id,
            "pharmacy_name": cons.pharmacy.name if cons.pharmacy else "",
        }, room=f"cons_{cons_id}")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_schema()
        seed_db()
        seed_marketplace()
    debug_mode = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    socketio.run(
    app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
        debug=debug_mode, use_reloader=debug_mode,
        allow_unsafe_werkzeug=True,
    )
