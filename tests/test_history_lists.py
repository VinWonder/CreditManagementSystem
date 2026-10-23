import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.listing import day_label, nairobi_today
from app.models import CreditTransaction, Customer, Ledger, Payment, Retailer, Shopkeeper


class ListConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class HistoryListTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ListConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.retailer = Retailer(business_name="Kiosk", owner_name="Owner",
                                 phone_number="+254700000091", password_hash="x")
        other_shop = Retailer(business_name="Elsewhere", owner_name="Other",
                              phone_number="+254700000092", password_hash="x")
        db.session.add_all([self.retailer, other_shop])
        db.session.flush()
        self.shop_id = self.retailer.retailer_id
        self.other_shop_id = other_shop.retailer_id
        self.shopkeeper = Shopkeeper(retailer_id=self.shop_id, full_name="Clerk",
                                     phone_number="+254700000093", password_hash="x")
        db.session.add(self.shopkeeper)
        db.session.commit()
        self.user = self.retailer

        @self.app.before_request
        def authenticate():
            g.pop("_login_user", None)
            login_user(self.user)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _customer(self, name, balance="0", days_overdue=0, shop_id=None, phone=None):
        customer = Customer(retailer_id=shop_id or self.shop_id, full_name=name,
                            phone_number=phone or f"+2547{abs(hash(name)) % 10**8:08d}")
        db.session.add(customer)
        db.session.flush()
        db.session.add(Ledger(retailer_id=customer.retailer_id, customer_id=customer.customer_id,
                              outstanding_balance=Decimal(balance), days_overdue=days_overdue))
        db.session.commit()
        return customer

    def _sale(self, customer, item, amount, when):
        db.session.add(CreditTransaction(retailer_id=customer.retailer_id, customer_id=customer.customer_id,
                                         item_description=item, amount=Decimal(amount), transaction_date=when))
        db.session.commit()

    def _payment(self, customer, amount, when, status="approved", reference=None):
        payment = Payment(retailer_id=customer.retailer_id, customer_id=customer.customer_id,
                          amount_paid=Decimal(amount), payment_date=when, approval_status=status,
                          payment_method="cash", payment_reference=reference)
        db.session.add(payment)
        db.session.commit()
        return payment

    def _page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.get_data(as_text=True)

    # Sales

    def test_sales_are_grouped_by_day_with_the_whole_days_totals(self):
        amina = self._customer("Amina Hassan")
        now = utc_now()
        self._sale(amina, "Sugar", "100", now - timedelta(minutes=5))
        self._sale(amina, "Soda", "50", now - timedelta(minutes=1))
        self._sale(amina, "Bread", "70", now - timedelta(days=1))

        page = self._page("/transactions/")

        self.assertIn(">Today<", page)
        self.assertIn("2 sales · KES 150.00", page)
        self.assertIn(">Yesterday<", page)
        self.assertIn("1 sale · KES 70.00", page)
        self.assertLess(page.index("Soda"), page.index("Sugar"))
        self.assertLess(page.index("Sugar"), page.index("Bread"))

    def test_a_day_split_across_batches_keeps_its_full_total(self):
        amina = self._customer("Amina Hassan")
        now = utc_now()
        for minute in range(25):
            self._sale(amina, f"Item {minute:02}", "10", now - timedelta(minutes=minute))

        batch = self._page("/transactions/?page=2&partial=1")

        # The 5 oldest rows arrive in batch 2, under the same day and the day's full total.
        self.assertIn('data-group-key="%s"' % nairobi_today().isoformat(), batch)
        self.assertIn("25 sales · KES 250.00", batch)
        self.assertEqual(batch.count('class="daybook-row"'), 5)

    def test_choosing_a_day_in_the_strip_shows_only_that_day(self):
        amina = self._customer("Amina Hassan")
        now = utc_now()
        self._sale(amina, "Today item", "100", now - timedelta(minutes=1))
        self._sale(amina, "Old item", "70", now - timedelta(days=3))
        old_day = (nairobi_today() - timedelta(days=3)).isoformat()

        page = self._page(f"/transactions/?day={old_day}")

        self.assertIn("Old item", page)
        self.assertNotIn("Today item", page)
        self.assertIn('aria-current="date"', page)
        self.assertIn("All days", page)

    def test_period_chips_limit_how_far_back_sales_go(self):
        amina = self._customer("Amina Hassan")
        now = utc_now()
        self._sale(amina, "Recent item", "100", now - timedelta(days=2))
        self._sale(amina, "Last month item", "70", now - timedelta(days=40))

        week = self._page("/transactions/?period=7d")
        everything = self._page("/transactions/")

        self.assertIn("Recent item", week)
        self.assertNotIn("Last month item", week)
        self.assertIn("Last month item", everything)

    def test_day_strip_covers_14_days_and_only_this_shop(self):
        amina = self._customer("Amina Hassan")
        stranger = self._customer("Stranger", shop_id=self.other_shop_id)
        self._sale(amina, "Sugar", "120", utc_now() - timedelta(minutes=1))
        self._sale(stranger, "Secret", "999", utc_now() - timedelta(minutes=1))

        page = self._page("/transactions/")

        self.assertEqual(page.count('<a class="daystrip-bar'), 14)
        self.assertIn("1 sale, KES 120.00", page)
        self.assertNotIn("KES 999.00", page)

    def test_filter_links_keep_the_search_and_restart_paging(self):
        amina = self._customer("Amina Hassan")
        self._sale(amina, "Sugar", "120", utc_now() - timedelta(minutes=1))

        page = self._page("/transactions/?q=Sugar&page=3")

        self.assertIn('href="/transactions/?q=Sugar&amp;period=7d"', page)
        self.assertNotIn("page=3&amp;period", page)

    # Payments

    def test_pending_payments_wait_on_top_and_stay_out_of_the_history(self):
        amina = self._customer("Amina Hassan")
        self._payment(amina, "40", utc_now() - timedelta(minutes=1), status="pending", reference="PEND123")
        self._payment(amina, "25", utc_now() - timedelta(minutes=2))

        page = self._page("/payments/")
        review, history = page.split("Payment history", 1)

        self.assertIn("Waiting for approval", review)
        self.assertIn("PEND123", review)
        self.assertIn("Approve", review)
        self.assertNotIn("PEND123", history)
        self.assertIn("Received KES 25.00", history)

    def test_shopkeepers_see_pending_payments_without_approve_buttons(self):
        amina = self._customer("Amina Hassan")
        self._payment(amina, "40", utc_now(), status="pending")
        self.user = self.shopkeeper

        page = self._page("/payments/")

        self.assertIn("Waiting for approval", page)
        self.assertNotIn("Reason required to reject", page)

    def test_payments_can_be_found_by_mpesa_code(self):
        amina = self._customer("Amina Hassan")
        brian = self._customer("Brian Otieno")
        self._payment(amina, "25", utc_now(), reference="QFT4ABC123")
        self._payment(brian, "60", utc_now())

        page = self._page("/payments/?q=QFT4")

        self.assertIn("Amina Hassan", page)
        self.assertNotIn("Brian Otieno", page.split("Payment history", 1)[1])

    def test_payment_summary_counts_only_approved_money(self):
        amina = self._customer("Amina Hassan")
        brian = self._customer("Brian Otieno")
        self._payment(amina, "25", utc_now())
        self._payment(brian, "60", utc_now(), status="rejected")

        page = self._page("/payments/")

        self.assertIn("KES 25.00", page)
        self.assertIn("<strong>2</strong>", page)  # payments logged
        self.assertIn("<strong>1</strong>", page)  # customers paid

    # Customers

    def test_customer_chips_count_and_filter_each_kind(self):
        owing = self._customer("Amina Hassan", balance="300")
        late = self._customer("Brian Otieno", balance="500", days_overdue=12)
        cleared = self._customer("Carol Wanjiru")
        self._customer("David New")
        for customer in (owing, late, cleared):
            self._sale(customer, "Sugar", "100", utc_now() - timedelta(days=12))

        page = self._page("/customers/")
        overdue = self._page("/customers/?show=overdue")
        paid_up = self._page("/customers/?show=clear")
        new = self._page("/customers/?show=new")

        self.assertIn('Owing <span class="list-chip-count">2</span>', page)
        self.assertIn('Overdue <span class="list-chip-count">1</span>', page)
        self.assertIn("Brian Otieno", overdue)
        self.assertNotIn("Amina Hassan", overdue)
        self.assertIn("12 days overdue", overdue)
        self.assertIn("Carol Wanjiru", paid_up)
        self.assertNotIn("David New", paid_up)
        self.assertIn("David New", new)
        self.assertNotIn("Carol Wanjiru", new)

    def test_customers_are_grouped_by_letter_and_can_be_narrowed_to_one(self):
        self._customer("amina hassan")
        self._customer("Brian Otieno")
        self._customer("Bella Achieng")

        page = self._page("/customers/")
        only_b = self._page("/customers/?letter=B")

        self.assertLess(page.index("amina hassan"), page.index("Bella Achieng"))
        self.assertLess(page.index("Bella Achieng"), page.index("Brian Otieno"))
        self.assertIn('data-group-key="B"', page)
        self.assertIn("Names starting with B", page)
        self.assertNotIn("amina hassan", only_b)
        self.assertIn('class="is-active" aria-current="true" aria-label="Names starting with B"', only_b)

    def test_customers_can_be_sorted_by_balance(self):
        self._customer("Amina Hassan", balance="300")
        self._customer("Zawadi Hamisi", balance="900")

        page = self._page("/customers/?sort=balance")

        self.assertLess(page.index("Zawadi Hamisi"), page.index("Amina Hassan"))

    def test_customer_list_grows_with_show_more_and_prints_everyone(self):
        for index in range(23):
            self._customer(f"Customer {index:02}")

        page = self._page("/customers/")
        batch = self._page("/customers/?page=2&partial=1")
        printed = self._page("/customers/?print=1")

        self.assertIn("Customer 19", page)
        self.assertNotIn("Customer 20", page)
        self.assertIn("Show 3 more", page)
        self.assertIn("Customer 22", batch)
        self.assertNotIn("Customer 00", batch)
        self.assertIn("Customer 00", printed)
        self.assertIn("Customer 22", printed)
        self.assertIn("window.print()", printed)

    # Balances

    def test_balances_are_banded_by_age_with_an_ageing_bar(self):
        self._customer("Amina Hassan", balance="300", days_overdue=45)
        self._customer("Brian Otieno", balance="100", days_overdue=10)
        self._customer("Carol Wanjiru", balance="100", days_overdue=0)

        page = self._page("/ledger/report")

        self.assertIn('id="age-critical"', page)
        self.assertIn('id="age-watch"', page)
        self.assertIn('id="age-current"', page)
        self.assertNotIn('id="age-recent"', page)
        self.assertIn("Needs attention · over 30 days", page)
        self.assertIn("1 account · KES 300.00", page)
        self.assertIn("(60%)", page)
        self.assertLess(page.index("Amina Hassan"), page.index("Brian Otieno"))

    def test_overdue_only_drops_the_current_band(self):
        self._customer("Amina Hassan", balance="300", days_overdue=45)
        self._customer("Carol Wanjiru", balance="100", days_overdue=0)

        page = self._page("/ledger/report?overdue=1")

        self.assertNotIn('id="age-current"', page)
        self.assertNotIn("Carol Wanjiru", page)


class DayLabelTests(unittest.TestCase):
    def test_recent_days_read_naturally(self):
        today = nairobi_today()
        self.assertEqual(day_label(today, today), "Today")
        self.assertEqual(day_label(today - timedelta(days=1), today), "Yesterday")
        older = today - timedelta(days=3)
        self.assertEqual(day_label(older, today), older.strftime("%a %d %b") if older.year == today.year else older.strftime("%a %d %b %Y"))


if __name__ == "__main__":
    unittest.main()
