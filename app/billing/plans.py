"""Subscription plans and the free trial.

Prices are whole Kenyan shillings because M-Pesa only takes whole shillings.
Change a price here and every page, prompt and saving label follows.
"""

from dataclasses import dataclass
from datetime import timedelta

TRIAL_DAYS = 7
TRIAL_PERIOD = timedelta(days=TRIAL_DAYS)
# Show the "trial ends / renew soon" banner this many days before access ends.
RENEWAL_NOTICE_DAYS = 3


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price: int  # KES
    days: int
    summary: str

    @property
    def period(self):
        return timedelta(days=self.days)

    @property
    def per_day(self):
        return self.price / self.days

    @property
    def saving(self):
        """Shillings saved against paying monthly for the same number of days (0 if none)."""
        monthly = PLANS["monthly"]
        return max(round(monthly.price * self.days / monthly.days) - self.price, 0)


PLANS = {
    plan.code: plan
    for plan in (
        Plan("weekly", "Weekly", 99, 7, "Pay small, week by week, like a daily float."),
        Plan("monthly", "Monthly", 299, 30, "The usual choice for a busy duka."),
        Plan("quarterly", "3 months", 799, 91, "One payment for the whole season."),
        Plan("yearly", "Yearly", 2999, 365, "Best value: pay once and forget it for a year."),
    )
}
DEFAULT_PLAN = "monthly"
