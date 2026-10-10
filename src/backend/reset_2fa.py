"""Turns off two-factor sign-in for one account and signs it out everywhere.

For an administrator who has lost both their authenticator and their recovery codes. It needs access to
the database and Redis (so run it inside the backend container) and prints no secrets:

    docker compose exec backend python src/backend/reset_2fa.py someone@example.com

The account can sign in with its password afterwards and, if two-factor is required for administrators,
is asked to set it up again.
"""
import os
import sys

# Dodaj roditeljski direktorij (src/) u Python path kako bi 'backend' postao dostupan paket
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import redis

from backend.auth import reset_two_factor
from backend.config import REDIS_URL
from backend.db import SessionLocal
from backend.models import User


def main(argv):
    if len(argv) != 2 or not argv[1].strip():
        print("Usage: python src/backend/reset_2fa.py <email>")
        return 2
    email = argv[1].strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"No account with the email '{email}'.")
            return 1
        reset_two_factor(db, redis.Redis.from_url(REDIS_URL, decode_responses=True), user)
        print(f"Two-factor was turned off for '{email}' and the account was signed out everywhere.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
