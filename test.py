import os
import smtplib

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

print("Checking Namecheap Private Email SMTP settings...")

if not all([SMTP_HOST, SMTP_PORT, SMTP_EMAIL, SMTP_PASSWORD]):
    print("Missing one or more environment variables")
    raise SystemExit(1)

print(f"SMTP_HOST: {SMTP_HOST}")
print(f"SMTP_PORT: {SMTP_PORT}")
print(f"SMTP_EMAIL: {SMTP_EMAIL}")
print("SMTP_PASSWORD: Loaded")

try:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
    print("Namecheap SMTP login successful")
    print("Your .env configuration is correct")
except Exception as exc:
    print("SMTP login failed")
    print(f"Error: {exc}")
    raise SystemExit(1) from exc
