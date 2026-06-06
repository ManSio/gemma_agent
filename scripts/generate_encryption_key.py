#!/usr/bin/env python3
"""Generate Fernet ENCRYPTION_KEY for .env (memory at-rest encryption)."""
from cryptography.fernet import Fernet

if __name__ == "__main__":
    print(Fernet.generate_key().decode())
