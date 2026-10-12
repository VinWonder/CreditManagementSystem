"""
Seed the database with a demo retailer, customers, transactions, payments
and reminder rules so you can explore the system immediately.

Run with:  python seed.py
"""
from datetime import datetime, timedelta
import random

from app import create_app
from app.extensions import db
from app.models import (
    Retailer, Customer, CreditTransaction, Payment, Ledger, ReminderRule
)

app = create_app()

DEMO_ITEMS = [
    "2kg sugar", "1L cooking oil", "1kg maize flour", "Bar soap", "500g rice",
    "Bread loaf", "Milk 500ml", "Detergent 1kg", "Matchbox & candles", "Salt 500g",
]

CUSTOMERS = [
    ("Mary Wanjiru", "+254711000001"),
    ("John Otieno", "+254711000002"),
    ("Grace Achieng", "+254711000003"),
    ("Peter Kamau", "+254711000004"),
    ("Fatuma Hassan", "+254711000005"),
]


def run():
    with app.app_context():
        db.drop_all()
        db.create_all()

        retailer = Retailer(
            business_name="Mama Njeri General Store",
            owner_name="Njeri Kamande",
            phone_number="+254700111222",
            email="njeri@mamanjeri.example",
            phone_verified_at=datetime.utcnow(),
        )
        retailer.set_password("password123")
        db.session.add(retailer)
        db.session.flush()

        # Default reminder rules mirroring common practice from the literature review
        rules = [
            ReminderRule(
                retailer_id=retailer.retailer_id,
                trigger_days_overdue=7,
                channel="sms",
                message_template=(
                    "Dear {customer_name}, friendly reminder: you have a balance of "
                    "{balance} with {business_name}, overdue by {days_overdue} days. "
                    "Please settle when convenient. Asante!"
                ),
            ),
            ReminderRule(
                retailer_id=retailer.retailer_id,
                trigger_days_overdue=14,
                channel="whatsapp",
                message_template=(
                    "Hello {customer_name}, this is {business_name}. Your outstanding "
                    "balance of {balance} is now {days_overdue} days overdue. Kindly "
                    "clear it soon to keep your credit account active."
                ),
            ),
            ReminderRule(
                retailer_id=retailer.retailer_id,
                trigger_days_overdue=30,
                channel="both",
                message_template=(
                    "URGENT: {customer_name}, your balance of {balance} with "
                    "{business_name} is seriously overdue ({days_overdue} days). "
                    "Please contact us immediately to arrange payment."
                ),
            ),
        ]
        db.session.add_all(rules)

        for name, phone in CUSTOMERS:
            customer = Customer(
                retailer_id=retailer.retailer_id,
                full_name=name,
                phone_number=phone,
                preferred_channel=random.choice(["sms", "whatsapp"]),
            )
            db.session.add(customer)
            db.session.flush()

            ledger = Ledger(retailer_id=retailer.retailer_id, customer_id=customer.customer_id)
            db.session.add(ledger)

            # 2-4 random past credit transactions per customer
            for _ in range(random.randint(2, 4)):
                days_ago = random.randint(1, 40)
                txn = CreditTransaction(
                    retailer_id=retailer.retailer_id,
                    customer_id=customer.customer_id,
                    item_description=random.choice(DEMO_ITEMS),
                    amount=round(random.uniform(50, 800), 2),
                    transaction_date=datetime.utcnow() - timedelta(days=days_ago),
                )
                db.session.add(txn)

            # Some customers have made a partial payment
            if random.random() > 0.4:
                payment = Payment(
                    retailer_id=retailer.retailer_id,
                    customer_id=customer.customer_id,
                    amount_paid=round(random.uniform(50, 300), 2),
                    payment_date=datetime.utcnow() - timedelta(days=random.randint(0, 10)),
                )
                db.session.add(payment)

            db.session.flush()
            ledger.recalculate()

        db.session.commit()

        print("Seed complete.")
        print("Login with phone: +254700111222  |  password: password123")


if __name__ == "__main__":
    run()
