import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import (
    CreditTransaction,
    Customer,
    Ledger,
    Payment,
    Retailer,
    Shopkeeper,
)


class TestConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    USSD_CALLBACK_TOKEN = "secret-ussd-token"
    USSD_SERVICE_CODE = "*384*55#"


class Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.shop = self._shop("Mama Njeri Shop", "+254700000001")
        self.other_shop = self._shop("Kiosk Two", "+254700000002")
        self.keeper = Shopkeeper(retailer_id=self.shop.retailer_id, full_name="Peter Keeper",
                                 phone_number="+254700000003", password_hash="x")
        db.session.add(self.keeper)
        self.amina = self._customer(self.shop, "Amina Hassan", "+254711000001")
        self.brian = self._customer(self.shop, "Brian Otieno", "+254711000002")
        # Amina also buys at another shop.
        self.amina_elsewhere = self._customer(self.other_shop, "Amina H", "+254711000001")

        now = datetime.utcnow()
        self._sale(self.amina, "Sugar", "500.00", now - timedelta(days=40))
        self._sale(self.amina, "Soda", "300.00", now - timedelta(hours=2))
        self._sale(self.brian, "Bread", "120.00", now - timedelta(hours=1))
        self._payment(self.amina, "200.00", now - timedelta(hours=1), "approved", "mpesa_send", "QAB123")
        self._payment(self.brian, "50.00", now - timedelta(minutes=30), "pending", "cash", None, self.keeper)
        self._payment(self.brian, "70.00", now - timedelta(days=2), "rejected", "cash", None, self.keeper)
        self._sale(self.amina_elsewhere, "Rice", "999.00", now - timedelta(days=3))
        for customer in (self.amina, self.brian, self.amina_elsewhere):
            ledger = Ledger(retailer_id=customer.retailer_id, customer_id=customer.customer_id)
            db.session.add(ledger)
            db.session.flush()
            ledger.recalculate()
        db.session.commit()

        self.user = self.shop
        test = self

        @self.app.before_request
        def sign_in():
            if test.user is not None:
                login_user(db.session.merge(test.user))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _shop(self, name, phone):
        shop = Retailer(business_name=name, owner_name="Owner " + name, phone_number=phone, password_hash="x")
        shop.set_password("Secret#123")
        db.session.add(shop)
        db.session.flush()
        return shop

    def _customer(self, shop, name, phone):
        customer = Customer(retailer_id=shop.retailer_id, full_name=name, phone_number=phone)
        db.session.add(customer)
        db.session.flush()
        return customer

    def _sale(self, customer, item, amount, when):
        db.session.add(CreditTransaction(retailer_id=customer.retailer_id, customer_id=customer.customer_id,
                                         item_description=item, amount=Decimal(amount), transaction_date=when))

    def _payment(self, customer, amount, when, status, method, reference, keeper=None):
        db.session.add(Payment(
            retailer_id=customer.retailer_id, customer_id=customer.customer_id, amount_paid=Decimal(amount),
            payment_date=when, approval_status=status, payment_method=method, payment_reference=reference,
            submitted_by_shopkeeper_id=keeper.shopkeeper_id if keeper else None,
        ))


class ReportTests(Base):
    def test_admin_sees_every_report(self):
        page = self.client.get("/reports/").get_data(as_text=True)
        for title in ("Business summary", "Credit sales", "Collections", "Payment approvals",
                      "Shopkeeper activity", "Balance corrections", "Reminder messages"):
            self.assertIn(title, page)

    def test_shopkeeper_only_gets_reports_for_what_they_can_see(self):
        self.user = self.keeper
        page = self.client.get("/reports/").get_data(as_text=True)
        self.assertIn("Credit sales", page)
        self.assertIn("Customer statement", page)
        self.assertNotIn("/reports/approvals", page)
        self.assertNotIn("/reports/staff", page)
        for key in ("approvals", "staff", "adjustments", "reminders"):
            self.assertEqual(self.client.get(f"/reports/{key}").status_code, 403, key)
        for key in ("summary", "sales", "payments", "receivables", "customers", "statement"):
            self.assertEqual(self.client.get(f"/reports/{key}").status_code, 200, key)
        # Shopkeepers do not see the admin-only "waiting for approval" figure.
        self.assertNotIn("Waiting for approval", self.client.get("/reports/summary").get_data(as_text=True))

    def test_summary_counts_only_this_shop_and_confirmed_payments(self):
        page = self.client.get("/reports/summary?period=30d").get_data(as_text=True)
        self.assertIn("KES 420.00", page)  # credit given in 30 days: 300 + 120
        self.assertIn("KES 200.00", page)  # collected: the pending 50 is not counted
        self.assertNotIn("999", page)  # the other shop's sale
        self.assertIn("Waiting for approval", page)

    def test_sales_and_collections_tables(self):
        sales = self.client.get("/reports/sales?period=year").get_data(as_text=True)
        self.assertIn("Soda", sales)
        self.assertIn("Bread", sales)
        payments = self.client.get("/reports/payments?period=7d").get_data(as_text=True)
        self.assertIn("QAB123", payments)
        self.assertIn("M-Pesa Send Money", payments)

    def test_statement_runs_a_balance_and_stays_inside_the_shop(self):
        page = self.client.get(f"/reports/statement?period=7d&customer_id={self.amina.customer_id}").get_data(as_text=True)
        self.assertIn("Statement · Amina Hassan", page)
        self.assertIn("Opening balance", page)
        self.assertIn("KES 500.00", page)  # the 40-day-old sale is the opening balance
        self.assertIn("KES 600.00", page)  # 500 + 300 - 200
        foreign = self.client.get(f"/reports/statement?customer_id={self.amina_elsewhere.customer_id}")
        self.assertEqual(foreign.status_code, 404)

    def test_approvals_and_staff_reports(self):
        approvals = self.client.get("/reports/approvals?period=30d").get_data(as_text=True)
        self.assertIn("Peter Keeper", approvals)
        self.assertIn("Rejected", approvals)
        staff = self.client.get("/reports/staff?period=30d").get_data(as_text=True)
        self.assertIn("Peter Keeper", staff)

    def test_csv_download(self):
        response = self.client.get("/reports/sales?period=year&format=csv")
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        body = response.get_data(as_text=True)
        self.assertIn("Soda,300.00", body)
        self.assertNotIn("Rice", body)

    def test_custom_period_and_unknown_report(self):
        page = self.client.get("/reports/sales?period=custom&from=2020-01-01&to=2020-01-31")
        self.assertEqual(page.status_code, 200)
        self.assertIn("01 Jan 2020", page.get_data(as_text=True))
        self.assertEqual(self.client.get("/reports/nope").status_code, 404)

    def test_dashboard_widgets_render(self):
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Owed to you", page)
        self.assertIn("Credit given vs collected", page)
        self.assertIn("Who owes the most", page)


class UssdTests(Base):
    def _dial(self, phone, text="", token="secret-ussd-token"):
        return self.client.post(f"/ussd/callback/{token}", data={
            "sessionId": "s1", "serviceCode": "*384*55#", "phoneNumber": phone, "text": text,
        })

    def setUp(self):
        super().setUp()
        self.user = None

    def test_wrong_token_is_not_found(self):
        self.assertEqual(self._dial("+254711000001", token="wrong").status_code, 404)

    def test_unknown_number(self):
        reply = self._dial("+254799999999").get_data(as_text=True)
        self.assertTrue(reply.startswith("END "))
        self.assertIn("no account", reply)

    def test_customer_at_two_shops_chooses_then_sees_balance(self):
        menu = self._dial("+254711000001").get_data(as_text=True)
        self.assertTrue(menu.startswith("CON "))
        self.assertIn("1. Kiosk Two", menu)
        self.assertIn("2. Mama Njeri Shop", menu)
        balance = self._dial("+254711000001", "2*1").get_data(as_text=True)
        self.assertIn("You owe KES 600", balance)
        self.assertNotIn("999", balance)

    def test_customer_mini_statement(self):
        reply = self._dial("0711000002").get_data(as_text=True)  # Brian: one shop, local number format
        self.assertIn("2. Mini-statement", reply)
        statement = self._dial("+254711000002", "2").get_data(as_text=True)
        self.assertTrue(statement.startswith("END "))
        self.assertIn("Bread +120", statement)
        self.assertNotIn("-50", statement)  # pending payment not shown
        self.assertIn("Balance: KES 120", statement)
        self.assertLessEqual(len(statement), 182)

    def test_back_and_home(self):
        reply = self._dial("+254711000002", "9*0").get_data(as_text=True)
        self.assertIn("1. My balance", reply)

    def test_owner_needs_a_pin(self):
        reply = self._dial("+254700000001").get_data(as_text=True)
        self.assertIn("Set a USSD PIN", reply)

    def test_owner_menu_with_pin(self):
        self.shop.set_ussd_pin("4826")
        db.session.commit()
        self.assertIn("Enter your USSD PIN", self._dial("+254700000001").get_data(as_text=True))
        menu = self._dial("+254700000001", "4826").get_data(as_text=True)
        self.assertIn("1. Today", menu)
        today = self._dial("+254700000001", "4826*1").get_data(as_text=True)
        self.assertIn("To approve: 1", today)
        owed = self._dial("+254700000001", "4826*3").get_data(as_text=True)
        self.assertIn("Owed: KES 720", owed)
        top = self._dial("+254700000001", "4826*4").get_data(as_text=True)
        self.assertIn("1. Amina H 600", top)
        lookup = self._dial("+254700000001", "4826*6*0711000001").get_data(as_text=True)
        self.assertIn("Amina Hassan", lookup)
        self.assertIn("Owes KES 600", lookup)

    def test_wrong_pins_lock_the_menu(self):
        self.shop.set_ussd_pin("4826")
        db.session.commit()
        for _ in range(4):
            self.assertIn("Wrong PIN", self._dial("+254700000001", "1111").get_data(as_text=True))
        self.assertIn("locked", self._dial("+254700000001", "1111").get_data(as_text=True))
        self.assertIn("Too many wrong PINs", self._dial("+254700000001", "4826").get_data(as_text=True))

    def test_expired_shops_are_hidden_from_customers(self):
        self.other_shop.trial_ends_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()
        reply = self._dial("+254711000001").get_data(as_text=True)
        self.assertIn("Mama Njeri Shop", reply)
        self.assertIn("1. My balance", reply)


class UssdPageTests(Base):
    def test_set_pin(self):
        response = self.client.post("/ussd/pin", data={"current_password": "Secret#123", "pin": "4826", "confirm_pin": "4826"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(db.session.get(Retailer, self.shop.retailer_id).check_ussd_pin("4826"))

    def test_weak_pin_and_wrong_password_refused(self):
        weak = self.client.post("/ussd/pin", data={"current_password": "Secret#123", "pin": "1234", "confirm_pin": "1234"})
        self.assertIn("too easy", weak.get_data(as_text=True))
        wrong = self.client.post("/ussd/pin", data={"current_password": "nope", "pin": "4826", "confirm_pin": "4826"})
        self.assertIn("not your current password", wrong.get_data(as_text=True))

    def test_simulator_is_limited_to_this_shop(self):
        reply = self.client.post("/ussd/simulator/send", json={"as": "customer", "customer_id": self.amina.customer_id, "text": ""}).get_json()
        self.assertIn("Mama Njeri Shop", reply["reply"])
        self.assertNotIn("Kiosk Two", reply["reply"])
        foreign = self.client.post("/ussd/simulator/send", json={"as": "customer", "customer_id": self.amina_elsewhere.customer_id, "text": ""})
        self.assertEqual(foreign.status_code, 400)

    def test_shopkeepers_cannot_use_ussd_pages(self):
        self.user = self.keeper
        self.assertEqual(self.client.get("/ussd/simulator").status_code, 403)
        self.assertEqual(self.client.get("/ussd/pin").status_code, 403)


if __name__ == "__main__":
    unittest.main()
