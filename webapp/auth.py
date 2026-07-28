"""User accounts: email/password signup+login plus "Sign in with Google"
(active once GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set as env vars --
see app.py). Storage is DynamoDB (table degen-predictions-users, partition
key "email", GSI "google_id-index") rather than local SQLite -- unlike the
site's other SQLite modules (analytics.py, feedback.py), user accounts need
to survive independently of any single EC2 instance/EBS volume and be
usable identically from a local dev run and production, which a managed,
serverless table gives for free. Deliberately NOT reviving the old
credits/crypto-deposit betting-game mechanic this module used to carry --
just accounts, consistent with the site's no-gambling stance.
"""
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLE_NAME = os.environ.get("USERS_TABLE_NAME", "degen-predictions-users")

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_table = _dynamodb.Table(TABLE_NAME)


class User(UserMixin):
    def __init__(self, item):
        self.id = item["email"]  # flask-login's user id -- email is the partition key
        self.email = item["email"]
        self.name = item["name"]
        self.password_hash = item.get("password_hash")
        self.google_id = item.get("google_id")
        self.created_at = item.get("created_at")

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)


def _item_to_user(item):
    return User(item) if item else None


def get_user_by_id(user_id):
    """flask-login calls this with the id stored in the session -- here
    that's the email itself, so this is the same lookup as by-email."""
    return get_user_by_email(user_id)


def get_user_by_email(email):
    if not email:
        return None
    resp = _table.get_item(Key={"email": email.lower().strip()})
    return _item_to_user(resp.get("Item"))


def get_user_by_google_id(google_id):
    if not google_id:
        return None
    resp = _table.query(
        IndexName="google_id-index",
        KeyConditionExpression=Key("google_id").eq(google_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    return _item_to_user(items[0]) if items else None


def create_user(email, name, password=None, google_id=None):
    """Raises ValueError if the email is already registered -- the
    conditional expression makes this atomic against a concurrent signup
    for the same email, instead of a check-then-write race."""
    email = email.lower().strip()
    password_hash = generate_password_hash(password) if password else None
    item = {
        "email": email,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if password_hash:
        item["password_hash"] = password_hash
    if google_id:
        item["google_id"] = google_id
    try:
        _table.put_item(Item=item, ConditionExpression="attribute_not_exists(email)")
    except _table.meta.client.exceptions.ConditionalCheckFailedException:
        raise ValueError(f"An account with email {email} already exists")
    return get_user_by_email(email)


def link_google_id(email, google_id):
    _table.update_item(
        Key={"email": email.lower().strip()},
        UpdateExpression="SET google_id = :g",
        ExpressionAttributeValues={":g": google_id},
    )
