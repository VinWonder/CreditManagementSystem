"""How registrations are compared, so one shop, inbox or phone can only sign up once."""

import re

# Gmail ignores dots and treats googlemail.com as gmail.com.
GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def clean_shop_name(name):
    """The shop name as stored: trimmed, with runs of spaces collapsed."""
    return re.sub(r"\s+", " ", name or "").strip()


def shop_name_key(name):
    """Comparison key for shop names: 'Mama  Njeri SHOP' matches 'mama njeri shop'."""
    return clean_shop_name(name).lower()


def canonical_email(email):
    """The mailbox an address delivers to, used only to spot duplicate sign-ups.

    owner+shop2@example.com and Owner@Example.com both reach owner@example.com;
    for Gmail, o.w.n.e.r@gmail.com does too. Codes are still sent to the address
    the retailer typed.
    """
    email = (email or "").strip().lower()
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        return email
    local = local.split("+", 1)[0]
    if domain in GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"
