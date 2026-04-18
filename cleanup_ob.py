import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Account, AccountType

ob_accounts = Account.objects.filter(name="Opening Balance")

for acc in ob_accounts:
    print(f"ID {acc.id}: Type {acc.account_type}, Entries {acc.ledger_entries.count()}")
    if acc.ledger_entries.count() == 0:
        print(f"  -> Deleting unused account: ID {acc.id}")
        acc.delete()

print("Done.")
