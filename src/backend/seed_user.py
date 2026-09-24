import os
import sys

# Dodaj roditeljski direktorij (src/) u Python path kako bi 'backend' postao dostupan paket
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from passlib.context import CryptContext

# Sada uvozi preko backend paketa (što rješava .config u db.py)
from backend.db import SessionLocal
from backend.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_initial_user():
    email = "admin@example.com"
    raw_password = "adminspass123"
    name = "Admin User"
    role = "admin"

    db = SessionLocal()
    try:
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            print(f"⚠️ Korisnik s emailom '{email}' već postoji u bazi!")
            return

        hashed_password = pwd_context.hash(raw_password)

        new_user = User(
            email=email,
            name=name,
            password_hash=hashed_password,
            role=role,
            is_active=True,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        print("--------------------------------------------------")
        print("✅ Korisnik je uspješno dodan u bazu!")
        print(f"   ID:       {new_user.id}")
        print(f"   Email:    {new_user.email}")
        print(f"   Lozinka:  {raw_password}")
        print("--------------------------------------------------")

    except Exception as e:
        db.rollback()
        print(f"❌ Greška: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    create_initial_user()