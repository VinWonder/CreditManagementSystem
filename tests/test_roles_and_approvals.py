import unittest
from datetime import datetime
from decimal import Decimal

from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import (
    BalanceAdjustment,
    CreditTransaction,
    Customer,
    Ledger,
    Payment,
    ReminderRule,
    Retailer,
    Shopkeeper,
)


class RoleWorkflowConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"


class RoleAndApprovalTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(RoleWorkflowConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
            business_name="Test Shop",
            owner_name="Test Owner",
            phone_number="+254700000041",
            password_hash="test-hash",
        )
        db.session.add(self.retailer)
        db.session.flush()
        self.customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Amina Hassan",
            phone_number="+254700000042",
            preferred_channel="both",
        )
        db.session.add(self.customer)
        db.session.flush()
        self.sale = CreditTransaction(
            retailer_id=self.retailer.retailer_id,
            customer_id=self.customer.customer_id,
            item_description="Groceries",
            amount=Decimal("100.00"),
            transaction_date=datetime(2026, 10, 1),
        )
        self.ledger = Ledger(
            retailer_id=self.retailer.retailer_id,
            customer_id=self.customer.customer_id,
            outstanding_balance=Decimal("100.00"),
        )
        self.shopkeeper = Shopkeeper(
            retailer_id=self.retailer.retailer_id,
            full_name="Shop Clerk",
            phone_number="+254700000043",
            active=True,
        )
        self.shopkeeper.set_password("shopkeeper123")
        db.session.add_all([self.sale, self.ledger, self.shopkeeper])
        db.session.commit()
        self.authenticated_user = self.retailer

        def authenticate_test_user():
            login_user(self.authenticated_user)
        self.authenticate_test_user = authenticate_test_user
        self.app.before_request(authenticate_test_user)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, user):
        self.authenticated_user = user

    def test_shopkeeper_management_page_omits_role_capability_details(self):
        self._login(self.retailer)

        response = self.client.get("/auth/shopkeepers")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Team accounts", response.data)
        self.assertIn(b"Shop Clerk", response.data)
        self.assertNotIn(b"Shopkeepers can record sales", response.data)
        self.assertNotIn(b"Only you can edit or delete customer records", response.data)

    def test_admin_can_reset_shopkeeper_password(self):
        page = self.client.get(
            f"/auth/shopkeepers/{self.shopkeeper.shopkeeper_id}/password"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Set a new password", page.data)

        response = self.client.post(
            f"/auth/shopkeepers/{self.shopkeeper.shopkeeper_id}/password",
            data={
                "password": "NewSecret#123",
                "confirm_password": "NewSecret#123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.shopkeeper.check_password("NewSecret#123"))
        self.assertFalse(self.shopkeeper.check_password("shopkeeper123"))

    def test_admin_password_reset_rejects_invalid_confirmation_and_other_tenants(self):
        response = self.client.post(
            f"/auth/shopkeepers/{self.shopkeeper.shopkeeper_id}/password",
            data={
                "password": "NewSecret#123",
                "confirm_password": "does-not-match",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.shopkeeper.check_password("shopkeeper123"))

        other_retailer = Retailer(
            business_name="Other Shop",
            owner_name="Other Owner",
            phone_number="+254700000046",
            password_hash="test-hash",
        )
        db.session.add(other_retailer)
        db.session.flush()
        other_shopkeeper = Shopkeeper(
            retailer_id=other_retailer.retailer_id,
            full_name="Other Clerk",
            phone_number="+254700000047",
            active=True,
        )
        other_shopkeeper.set_password("original123")
        db.session.add(other_shopkeeper)
        db.session.commit()

        denied = self.client.get(
            f"/auth/shopkeepers/{other_shopkeeper.shopkeeper_id}/password"
        )
        self.assertEqual(denied.status_code, 404)

        self._login(self.shopkeeper)
        forbidden = self.client.get(
            f"/auth/shopkeepers/{self.shopkeeper.shopkeeper_id}/password"
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_shopkeeper_payment_requires_approval_before_balance_changes(self):
        self._login(self.shopkeeper)
        response = self.client.post(
            "/payments/new",
            data={"customer_id": str(self.customer.customer_id), "amount_paid": "40.00", "payment_method": "cash"},
        )

        self.assertEqual(response.status_code, 302)
        payment = Payment.query.one()
        self.assertEqual(payment.approval_status, "pending")
        self.assertEqual(payment.submitted_by_shopkeeper_id, self.shopkeeper.shopkeeper_id)
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("100.00"))

        self._login(self.retailer)
        history = self.client.get("/payments/")
        self.assertIn(b"Approve", history.data)
        self.assertIn(b"Reason required to reject", history.data)
        approval = self.client.post(f"/payments/{payment.payment_id}/approve")
        self.assertEqual(approval.status_code, 302)
        self.assertEqual(payment.approval_status, "approved")
        self.assertEqual(payment.reviewed_by_retailer_id, self.retailer.retailer_id)
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("60.00"))

    def test_dashboard_shows_only_the_two_latest_sales_and_payments(self):
        for day in range(1, 5):
            db.session.add(
                CreditTransaction(
                    retailer_id=self.retailer.retailer_id,
                    customer_id=self.customer.customer_id,
                    item_description=f"Sale item {day}",
                    amount=Decimal("10.00"),
                    transaction_date=datetime(2026, 10, 10 + day),
                )
            )
            db.session.add(
                Payment(
                    retailer_id=self.retailer.retailer_id,
                    customer_id=self.customer.customer_id,
                    amount_paid=Decimal(f"{day}.11"),
                    payment_date=datetime(2026, 10, 10 + day),
                    approval_status="approved",
                )
            )
        db.session.commit()
        self._login(self.retailer)

        page = self.client.get("/dashboard").get_data(as_text=True)
        sales = page.split("Recent credit sales")[1].split("Recent payments")[0]
        payments = page.split("Recent payments")[1]

        self.assertIn("Sale item 4", sales)
        self.assertIn("Sale item 3", sales)
        self.assertNotIn("Sale item 2", sales)
        self.assertNotIn("Groceries", sales)
        self.assertIn("4.11", payments)
        self.assertIn("3.11", payments)
        self.assertNotIn("2.11", payments)
        self.assertIn("/transactions/", sales)
        self.assertIn("/payments/", payments)

    def test_pending_payments_are_flagged_for_the_retailer_only(self):
        self._login(self.shopkeeper)
        self.client.post(
            "/payments/new",
            data={"customer_id": str(self.customer.customer_id), "amount_paid": "40.00", "payment_method": "cash"},
        )
        shopkeeper_dashboard = self.client.get("/dashboard")
        self.assertNotIn(b"Payments awaiting your approval", shopkeeper_dashboard.data)

        self._login(self.retailer)
        dashboard = self.client.get("/dashboard")
        self.assertIn(b"Payments awaiting your approval", dashboard.data)
        self.assertIn(b"nav-approval-badge", dashboard.data)
        # An unverified payment must not appear among money actually received.
        received_table = dashboard.data.split(b"Recent payments")[1]
        self.assertNotIn(b"40.00", received_table)

        payment = Payment.query.one()
        self.client.post(f"/payments/{payment.payment_id}/approve")

        cleared = self.client.get("/dashboard")
        self.assertNotIn(b"Payments awaiting your approval", cleared.data)
        self.assertIn(b"40.00", cleared.data.split(b"Recent payments")[1])

    def test_retailer_can_approve_a_payment_from_the_dashboard(self):
        self._login(self.shopkeeper)
        self.client.post(
            "/payments/new",
            data={"customer_id": str(self.customer.customer_id), "amount_paid": "40.00", "payment_method": "cash"},
        )
        self._login(self.retailer)
        dashboard = self.client.get("/dashboard")
        self.assertIn(b"dashboard-approve-button", dashboard.data)

        payment = Payment.query.one()
        response = self.client.post(f"/payments/{payment.payment_id}/approve")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(payment.approval_status, "approved")
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("60.00"))

    def test_rejection_requires_reason_and_does_not_change_balance(self):
        payment = Payment(
            retailer_id=self.retailer.retailer_id,
            customer_id=self.customer.customer_id,
            amount_paid=Decimal("40.00"),
            approval_status="pending",
            submitted_by_shopkeeper_id=self.shopkeeper.shopkeeper_id,
        )
        db.session.add(payment)
        db.session.commit()
        self._login(self.retailer)

        self.client.post(f"/payments/{payment.payment_id}/reject", data={"review_note": "  "})
        self.assertEqual(payment.approval_status, "pending")

        response = self.client.post(
            f"/payments/{payment.payment_id}/reject",
            data={"review_note": "Cash amount not verified"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(payment.approval_status, "rejected")
        self.assertEqual(payment.review_note, "Cash amount not verified")
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("100.00"))

    def test_shopkeeper_cannot_edit_customers_approve_payments_or_adjust_balances(self):
        payment = Payment(
            retailer_id=self.retailer.retailer_id,
            customer_id=self.customer.customer_id,
            amount_paid=Decimal("20.00"),
            approval_status="pending",
        )
        db.session.add(payment)
        db.session.commit()
        self._login(self.shopkeeper)

        edit = self.client.get(f"/customers/{self.customer.customer_id}/edit")
        delete = self.client.post(f"/customers/{self.customer.customer_id}/delete")
        approve = self.client.post(f"/payments/{payment.payment_id}/approve")
        adjust = self.client.post(
            f"/payments/customers/{self.customer.customer_id}/adjust-balance",
            data={"amount_change": "-10.00", "reason": "Not allowed"},
        )
        manage = self.client.get("/auth/shopkeepers")

        self.assertEqual(edit.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.assertEqual(approve.status_code, 403)
        self.assertEqual(adjust.status_code, 403)
        self.assertEqual(manage.status_code, 403)
        reminder_rules = self.client.get("/reminders/rules")
        # Reminders belong to the retailer; shopkeepers cannot see or reach them.
        self.assertEqual(reminder_rules.status_code, 403)
        self.assertEqual(self.client.get("/reminders/log").status_code, 403)
        dashboard = self.client.get("/dashboard")
        self.assertNotIn(b">Reminders<", dashboard.data)
        create_rule = self.client.get("/reminders/rules/new")
        self.assertEqual(create_rule.status_code, 403)
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("100.00"))
        self.assertEqual(BalanceAdjustment.query.count(), 0)

    def test_shopkeeper_can_add_customers_and_record_credit_sales(self):
        self._login(self.shopkeeper)
        customer_response = self.client.post(
            "/customers/new",
            data={
                "full_name": "New Customer",
                "phone_number": "+254700000045",
                "shop_name": "Main Street Grocery",
                "preferred_channel": "sms",
            },
        )
        self.assertEqual(customer_response.status_code, 302)
        added_customer = Customer.query.filter_by(full_name="New Customer").one()
        self.assertEqual(added_customer.shop_name, "Main Street Grocery")

        sale_response = self.client.post(
            "/transactions/new",
            data={
                "customer_id": str(added_customer.customer_id),
                "items-0-item_description": "Cooking oil",
                "items-0-quantity": "1",
                "items-0-unit_price": "25.00",
            },
        )
        self.assertEqual(sale_response.status_code, 302)
        self.assertEqual(
            Ledger.query.filter_by(customer_id=added_customer.customer_id).one().outstanding_balance,
            Decimal("25.00"),
        )

    def test_admin_account_toggle_is_csrf_protected_and_disables_login(self):
        self._login(self.retailer)
        shopkeeper = self.shopkeeper
        self.app.config["WTF_CSRF_ENABLED"] = True
        response = self.client.post(
            f"/auth/shopkeepers/{shopkeeper.shopkeeper_id}/toggle"
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(shopkeeper.active)

        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client.post(f"/auth/shopkeepers/{shopkeeper.shopkeeper_id}/toggle")
        self.assertFalse(shopkeeper.active)
        self.assertIsNone(self.app.login_manager._user_callback(shopkeeper.get_id()))

    def test_admin_balance_corrections_require_reason_and_are_audited(self):
        self._login(self.retailer)
        response = self.client.post(
            f"/payments/customers/{self.customer.customer_id}/adjust-balance",
            data={"amount_change": "-30.00", "reason": "Verified return"},
        )

        self.assertEqual(response.status_code, 302)
        adjustment = BalanceAdjustment.query.one()
        self.assertEqual(adjustment.amount_change, Decimal("-30.00"))
        self.assertEqual(adjustment.reason, "Verified return")
        self.assertEqual(adjustment.created_by_retailer_id, self.retailer.retailer_id)
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("70.00"))
        history = self.client.get(f"/customers/{self.customer.customer_id}")
        self.assertIn(b"Verified return", history.data)

        response = self.client.post(
            f"/payments/customers/{self.customer.customer_id}/adjust-balance",
            data={"amount_change": "-100.00", "reason": "Too much"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BalanceAdjustment.query.count(), 1)
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("70.00"))

    def test_admin_creates_shopkeeper_and_full_payment_marks_customer_cleared(self):
        self._login(self.retailer)
        response = self.client.post(
            "/auth/shopkeepers",
            data={
                "full_name": "New Clerk",
                "phone_number": "+254700000044",
                "password": "Initial#1234",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Shopkeeper.query.filter_by(phone_number="+254700000044").one()
        self.assertTrue(created.check_password("Initial#1234"))
        self.assertNotEqual(created.password_hash, "Initial#1234")

        self.app.before_request_funcs[None].remove(self.authenticate_test_user)
        g.pop("_login_user", None)
        login_client = self.app.test_client()
        login_response = login_client.post(
            "/auth/login",
            data={"phone_number": "+254700000044", "password": "Initial#1234"},
        )
        self.assertEqual(login_response.status_code, 302)
        with login_client.session_transaction() as login_session:
            self.assertEqual(login_session["_user_id"], created.get_id())

        g.pop("_login_user", None)
        payment_response = self.client.post(
            "/payments/new",
            data={"customer_id": str(self.customer.customer_id), "amount_paid": "100.00", "payment_method": "cash"},
        )
        self.assertEqual(payment_response.status_code, 302)
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("0.00"))
        customer_response = self.client.get(f"/customers/{self.customer.customer_id}")
        self.assertIn(b"All clear", customer_response.data)


    def _add_sale_day_rule(self):
        db.session.add(
            ReminderRule(
                retailer_id=self.retailer.retailer_id,
                trigger_days_overdue=0,
                message_template="Dear {customer_name}, you owe {balance}.",
                channel="both",
                active=True,
            )
        )
        db.session.commit()

    def test_shopkeeper_customer_page_has_no_reminder_send_options(self):
        self._add_sale_day_rule()
        self._login(self.shopkeeper)

        response = self.client.get(f"/customers/{self.customer.customer_id}")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Send a personal reminder", response.data)
        self.assertNotIn(b"https://wa.me/", response.data)
        self.assertNotIn(b"sms:+", response.data)

    def test_retailer_customer_page_keeps_reminder_send_options(self):
        self._add_sale_day_rule()
        self._login(self.retailer)

        response = self.client.get(f"/customers/{self.customer.customer_id}")

        self.assertIn(b"Send a personal reminder", response.data)
        self.assertIn(b"https://wa.me/254700000042", response.data)

    def test_dashboard_reminder_shortcuts_are_for_the_retailer_only(self):
        self._login(self.shopkeeper)
        shopkeeper_dashboard = self.client.get("/dashboard")
        self.assertNotIn(b"/reminders/rules", shopkeeper_dashboard.data)

        self._login(self.retailer)
        dashboard = self.client.get("/dashboard")
        self.assertIn(b"/reminders/rules", dashboard.data)

    def test_customer_list_shows_a_portfolio_button_for_both_roles(self):
        portfolio_url = f'href="/customers/{self.customer.customer_id}"'.encode()
        for user in (self.retailer, self.shopkeeper):
            self._login(user)
            response = self.client.get("/customers/")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"customer-portfolio-button", response.data)
            self.assertIn(b"View portfolio for Amina Hassan", response.data)
            self.assertIn(portfolio_url, response.data)

    def test_portfolio_button_flags_customers_with_a_balance_due(self):
        self._login(self.retailer)
        owing = self.client.get("/customers/")
        self.assertIn(b"customer-portfolio-button has-balance", owing.data)

        self.ledger.outstanding_balance = Decimal("0.00")
        db.session.commit()
        cleared = self.client.get("/customers/")
        self.assertIn(b"customer-portfolio-button", cleared.data)
        self.assertNotIn(b"has-balance", cleared.data)

if __name__ == "__main__":
    unittest.main()
