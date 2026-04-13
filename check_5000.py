import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import LedgerEntry, AccountType, Transaction

print("Looking for ANY expense of 5000...")
entries = LedgerEntry.objects.filter(amount=5000, account__account_type=AccountType.EXPENSE)
for e in entries:
    print(f"Entry ID: {e.id} | TXN ID: {e.transaction.id} | Account: {e.account.name}")

print("Done.")
