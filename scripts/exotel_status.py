"""Check your Exotel account status: credentials, account, rented numbers.

Reads EXOTEL_* credentials from .env:
  python scripts\\exotel_status.py
"""

import base64
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

SID = os.getenv("EXOTEL_ACCOUNT_SID", "")
KEY = os.getenv("EXOTEL_API_KEY", "")
TOKEN = os.getenv("EXOTEL_API_TOKEN", "")
BASE = os.getenv("EXOTEL_API_BASE", "https://api.exotel.com")


def auth():
    return {"Authorization": "Basic " + base64.b64encode(f"{KEY}:{TOKEN}".encode()).decode()}


def main() -> int:
    if not (SID and KEY and TOKEN):
        print("EXOTEL_* credentials missing from .env")
        return 1
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{BASE}/v1/Accounts/{SID}.json", headers=auth())
        if r.status_code != 200:
            print(f"CREDENTIALS: INVALID (HTTP {r.status_code}): {r.text[:200]}")
            return 1
        acc = r.json().get("Account", {})
        print(
            f"CREDENTIALS: OK | account={SID} type={acc.get('Type')} "
            f"status={acc.get('Status')} kyc={acc.get('KycStatus')}"
        )

        r = c.get(f"{BASE}/v1/Accounts/{SID}/IncomingPhoneNumbers.json", headers=auth())
        if r.status_code == 200:
            body = r.json()
            numbers = body.get("IncomingPhoneNumbers", [])
            if not numbers:
                print("NUMBERS: none rented yet - rent one in the Exotel dashboard (My Exotel -> Numbers)")
            for n in numbers:
                print(f"NUMBER: {n.get('PhoneNumber') or n.get('Number')} | {n.get('FriendlyName', '')}")
            return 0
        print(f"NUMBERS: HTTP {r.status_code}: {r.text[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
