"""Abandoned sign-ups: accounts whose email was never verified.

Such an account cannot sign in, so after UNVERIFIED_SIGNUP_HOURS it should not
keep someone else from registering its shop name, phone number or email.
"""

import os
from datetime import datetime, timedelta

from flask import current_app

from app.auth.identity import canonical_email, shop_name_key
from app.extensions import db
from app.models import Retailer


def _cutoff():
    return datetime.utcnow() - timedelta(hours=current_app.config.get("UNVERIFIED_SIGNUP_HOURS", 24))


def _abandoned(any_age=False):
    query = Retailer.query.filter(Retailer.email.isnot(None), Retailer.email_verified_at.is_(None))
    return query if any_age else query.filter(Retailer.created_at < _cutoff())


def _holds_shop_data(retailer):
    """Safety net: never delete an account that somehow has real records."""
    return any(
        relation.first() is not None
        for relation in (retailer.customers, retailer.transactions, retailer.payments, retailer.shopkeepers)
    )


def _delete(retailer):
    if retailer.logo_filename:
        try:
            os.remove(os.path.join(current_app.config["SHOP_LOGO_UPLOAD_FOLDER"], retailer.logo_filename))
        except OSError:
            pass
    current_app.logger.info(
        "Released abandoned sign-up %s (unverified since %s).", retailer.retailer_id, retailer.created_at
    )
    db.session.delete(retailer)


def release_abandoned_signups(business_name, phone_number, email):
    """Delete sign-ups that stand in the way of this registration. Returns how many.

    An unverified sign-up for the same email inbox is the same person trying
    again (perhaps the code never arrived), so it is replaced straight away.
    Other unverified sign-ups holding the name or phone are released after
    UNVERIFIED_SIGNUP_HOURS.
    """
    same_details = (
        (db.func.lower(Retailer.business_name) == shop_name_key(business_name))
        | (Retailer.phone_number == phone_number)
        | (Retailer.email == email)
        | (Retailer.email_canonical == canonical_email(email))
    )
    matches = _abandoned().filter(same_details).all()
    matches += [
        retailer
        for retailer in Retailer.query.filter(
            Retailer.email_verified_at.is_(None),
            (Retailer.email == email) | (Retailer.email_canonical == canonical_email(email)),
        ).all()
        if retailer not in matches
    ]
    released = [retailer for retailer in matches if not _holds_shop_data(retailer)]
    for retailer in released:
        _delete(retailer)
    if released:
        db.session.commit()
    return len(released)


def purge_abandoned_signups(dry_run=False, any_age=False):
    """Delete every abandoned sign-up (any_age: every unverified one). Returns those removed."""
    abandoned = [retailer for retailer in _abandoned(any_age).all() if not _holds_shop_data(retailer)]
    if not dry_run:
        for retailer in abandoned:
            _delete(retailer)
        db.session.commit()
    return abandoned
