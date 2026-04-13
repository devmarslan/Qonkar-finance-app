import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Transaction, LedgerEntry, Account

print("Accounts named 'Opening Balance':")
for acc in Account.objects.filter(name__icontains="Opening Balance"):
    print(f"ID: {acc.id}, Name: {acc.name}, Type: {acc.account_type}")

print("\nFixing Transaction 4459:")
txn = Transaction.objects.get(id=4459)
for e in txn.entries.all():
    print(f"Found Entry: {e.account.name} ({e.account.account_type}) | Amount: {e.amount}")
    if e.account.account_type == 'EXPENSE':
        print(f"Deleting malicious Expense entry: {e.id}")
        e.delete()

print("\nDone.")
