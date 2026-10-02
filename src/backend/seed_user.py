"""Kreira administratorski račun iz naredbenog retka, za razvoj ili oporavak pristupa.

Podaci se čitaju iz okoline ili se traže interaktivno (lozinka se ne prikazuje i nikad se ne ispisuje):

    SEED_ADMIN_EMAIL=ja@example.com SEED_ADMIN_PASSWORD='...' python src/backend/seed_user.py
    docker compose exec -it backend python src/backend/seed_user.py

Vrijedi ista pravila kao pri registraciji (lozinka od najmanje 16 znakova). Ova skripta zaobilazi
setup token, pa je koristi samo onaj tko ionako ima pristup bazi.
"""
import getpass
import os
import sys

# Dodaj roditeljski direktorij (src/) u Python path kako bi 'backend' postao dostupan paket
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import bcrypt

# Sada uvozi preko backend paketa (što rješava .config u db.py)
from backend.auth import validate_password
from backend.db import SessionLocal
from backend.models import User


def _ask(name, prompt, secret=False):
    value = os.getenv(name, "").strip()
    if value or not sys.stdin.isatty():
        return value
    return (getpass.getpass if secret else input)(prompt).strip()


def create_initial_user():
    email = _ask("SEED_ADMIN_EMAIL", "Email: ").lower()
    password = _ask("SEED_ADMIN_PASSWORD", "Lozinka (najmanje 16 znakova): ", secret=True)
    name = os.getenv("SEED_ADMIN_NAME", "").strip() or "Admin User"

    if not email or not password:
        print("❌ Postavi SEED_ADMIN_EMAIL i SEED_ADMIN_PASSWORD ili pokreni skriptu interaktivno (docker compose exec -it).")
        return 1
    try:
        validate_password(password)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            print(f"⚠️ Korisnik s emailom '{email}' već postoji u bazi!")
            return 1

        new_user = User(
            email=email,
            name=name,
            password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
            role="admin",
            is_active=True,
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        print(f"✅ Administrator je dodan u bazu (ID {new_user.id}, {new_user.email}).")
        return 0
    except Exception as e:
        db.rollback()
        print(f"❌ Greška: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(create_initial_user())
