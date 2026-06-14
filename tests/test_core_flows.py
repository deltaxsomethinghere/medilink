import os
import tempfile
import unittest
from pathlib import Path


TEST_DIR = tempfile.TemporaryDirectory()
TEST_DB = Path(TEST_DIR.name) / "medilink-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("GROQ_API_KEY", None)

import app as medilink


class CoreFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        medilink.app.config.update(TESTING=True)
        with medilink.app.app_context():
            medilink.db.create_all()
            cls.pharmacy = medilink.Pharmacy(
                name="Test Pharmacy",
                address="Bangkok",
                latitude=13.75,
                longitude=100.50,
                delivery_opts_json='["pickup","standard"]',
            )
            cls.patient = medilink.User(
                email="patient@test.local",
                role="PATIENT",
                name="Test Patient",
                ai_doctor_consent=True,
            )
            cls.patient.set_password("demo1234")
            cls.doctor = medilink.User(
                email="doctor@test.local",
                role="DOCTOR",
                name="Test Doctor",
            )
            cls.doctor.set_password("demo1234")
            cls.admin = medilink.User(
                email="admin@test.local",
                role="ADMIN",
                name="Test Admin",
            )
            cls.admin.set_password("demo1234")
            medilink.db.session.add_all([
                cls.pharmacy, cls.patient, cls.doctor, cls.admin])
            medilink.db.session.flush()

            cls.profile = medilink.PatientProfile(
                user_id=cls.patient.id,
                address="Old address",
            )
            cls.profile.allergies = ["Penicillin"]
            cls.otc = medilink.Medicine(
                name="Paracetamol 500 mg",
                generic="Paracetamol",
                dosage="500 mg",
                instruction="Use as directed",
                purpose="Fever relief",
                keywords_j='["ไข้","ปวดหัว"]',
                is_otc=True,
            )
            cls.rx = medilink.Medicine(
                name="Amoxicillin 500 mg",
                generic="Amoxicillin",
                dosage="500 mg",
                instruction="Prescription only",
                purpose="Antibiotic",
                keywords_j='["ติดเชื้อ"]',
                contraindications_j='["Penicillin","Amoxicillin"]',
                is_otc=False,
            )
            medilink.db.session.add_all([cls.profile, cls.otc, cls.rx])
            medilink.db.session.flush()

            cls.otc_stock = medilink.PharmacyStock(
                pharmacy_id=cls.pharmacy.id,
                medicine_id=cls.otc.id,
                quantity=10,
                price=35,
            )
            cls.rx_stock = medilink.PharmacyStock(
                pharmacy_id=cls.pharmacy.id,
                medicine_id=cls.rx.id,
                quantity=10,
                price=95,
            )
            medilink.db.session.add_all([cls.otc_stock, cls.rx_stock])
            medilink.db.session.commit()
            cls.pharmacy_id = cls.pharmacy.id
            cls.patient_id = cls.patient.id
            cls.doctor_id = cls.doctor.id
            cls.admin_id = cls.admin.id
            cls.otc_id = cls.otc.id
            cls.rx_id = cls.rx.id
            cls.otc_stock_id = cls.otc_stock.id

    @classmethod
    def tearDownClass(cls):
        with medilink.app.app_context():
            medilink.db.session.remove()
            medilink.db.drop_all()
            medilink.db.engine.dispose()
        TEST_DIR.cleanup()

    def setUp(self):
        self.client = medilink.app.test_client()
        with medilink.app.app_context():
            medilink.Appointment.query.delete()
            medilink.PharmacyOrder.query.delete()
            stock = medilink.db.session.get(
                medilink.PharmacyStock, self.otc_stock_id)
            stock.quantity = 10
            medilink.db.session.commit()
            patient = medilink.db.session.get(medilink.User, self.patient_id)
            self.patient_session = patient.to_session()
        with self.client.session_transaction() as session:
            session["user"] = self.patient_session
            session["cart"] = {}
            session["medicine_cart"] = {}

    def test_profile_update_persists_after_reload(self):
        response = self.client.post("/api/profile/update", json={
            "name": "Updated Patient",
            "phone": "0812345678",
            "address": "New persistent address",
        })
        self.assertEqual(response.status_code, 200)

        with medilink.app.app_context():
            medilink.db.session.expire_all()
            user = medilink.db.session.get(medilink.User, self.patient_id)
            profile = medilink.PatientProfile.query.filter_by(
                user_id=self.patient_id).one()
            self.assertEqual(user.name, "Updated Patient")
            self.assertEqual(profile.phone, "0812345678")
            self.assertEqual(profile.address, "New persistent address")

        page = self.client.get("/profile")
        html = page.get_data(as_text=True)
        self.assertIn("Updated Patient", html)
        self.assertIn("0812345678", html)
        self.assertIn("New persistent address", html)

        clear_response = self.client.post("/api/profile/update", json={"phone": ""})
        self.assertEqual(clear_response.status_code, 200)
        with medilink.app.app_context():
            medilink.db.session.expire_all()
            profile = medilink.PatientProfile.query.filter_by(
                user_id=self.patient_id).one()
            self.assertEqual(profile.phone, "")

    def test_patient_can_book_doctor_appointment(self):
        detail_page = self.client.get(f"/doctors/{self.doctor_id}")
        detail_html = detail_page.get_data(as_text=True)
        self.assertIn("bookAppointment()", detail_html)
        self.assertIn('id="appointment-status"', detail_html)

        response = self.client.post("/api/appointments", json={
            "doctor_id": self.doctor_id,
            "date": "2026-06-15",
            "time": "09:00",
            "reason": "Need a checkup",
        })
        self.assertEqual(response.status_code, 201)

        duplicate = self.client.post("/api/appointments", json={
            "doctor_id": self.doctor_id,
            "date": "2026-06-15",
            "time": "09:00",
            "reason": "Duplicate",
        })
        self.assertEqual(duplicate.status_code, 409)

        dashboard = self.client.get("/dashboard")
        dashboard_html = dashboard.get_data(as_text=True)
        self.assertIn("Test Doctor", dashboard_html)
        self.assertIn("2026-06-15", dashboard_html)

    def test_medicine_finder_cart_and_checkout_flow(self):
        add_response = self.client.post("/api/medicine-cart/add", json={
            "medicine_id": self.otc_id,
            "pharmacy_id": self.pharmacy_id,
            "quantity": 2,
        })
        self.assertEqual(add_response.status_code, 201)

        cart_page = self.client.get("/cart")
        self.assertIn("Paracetamol 500 mg", cart_page.get_data(as_text=True))

        rx_response = self.client.post("/api/medicine-cart/add", json={
            "medicine_id": self.rx_id,
            "pharmacy_id": self.pharmacy_id,
            "quantity": 1,
        })
        self.assertEqual(rx_response.status_code, 400)

        checkout = self.client.post("/api/medicine-cart/checkout", json={
            "delivery_type": "pickup",
            "address": "",
        })
        self.assertEqual(checkout.status_code, 201)

        with medilink.app.app_context():
            medilink.db.session.expire_all()
            order = medilink.PharmacyOrder.query.one()
            stock = medilink.db.session.get(
                medilink.PharmacyStock, self.otc_stock_id)
            self.assertEqual(order.medicine_id, self.otc_id)
            self.assertEqual(order.quantity, 2)
            self.assertEqual(order.total, 70)
            self.assertEqual(stock.quantity, 8)

        orders_page = self.client.get("/orders")
        self.assertIn("Paracetamol 500 mg", orders_page.get_data(as_text=True))

    def test_ai_doctor_handler_and_safe_fallback(self):
        medilink.GROQ_API_KEY = ""
        response = self.client.post("/api/ai/doctor-chat", json={
            "message": "มีไข้และปวดหัว",
            "history": [],
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["source"], "rule-based")
        names = [item["name"] for item in data["recommended_medicines"]]
        self.assertIn("Paracetamol 500 mg", names)
        self.assertNotIn("Amoxicillin 500 mg", names)

        page = self.client.get("/ai-doctor")
        html = page.get_data(as_text=True)
        self.assertIn('id="ai-chat-input"', html)
        self.assertIn("sendAiDoctorMessage()", html)

    def test_admin_analytics_and_drug_interaction_endpoints(self):
        with medilink.app.app_context():
            doctor_session = medilink.db.session.get(
                medilink.User, self.doctor_id).to_session()
            admin_session = medilink.db.session.get(
                medilink.User, self.admin_id).to_session()

        doctor_client = medilink.app.test_client()
        with doctor_client.session_transaction() as session:
            session["user"] = doctor_session
        interaction = doctor_client.post("/api/drug-interaction-check", json={
            "new_medicine_id": self.rx_id,
            "patient_id": self.patient_id,
        })
        self.assertEqual(interaction.status_code, 200)
        interaction_data = interaction.get_json()
        self.assertFalse(interaction_data["safe"])
        self.assertEqual(interaction_data["level"], "DANGER")

        admin_client = medilink.app.test_client()
        with admin_client.session_transaction() as session:
            session["user"] = admin_session
        analytics = admin_client.get("/api/admin/analytics")
        self.assertEqual(analytics.status_code, 200)
        analytics_data = analytics.get_json()
        self.assertEqual(len(analytics_data["cons_by_day"]), 14)
        self.assertIn("top_doctors", analytics_data)
        self.assertIn("top_meds", analytics_data)
        self.assertIn("ph_orders", analytics_data)


if __name__ == "__main__":
    unittest.main()
