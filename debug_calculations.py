import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Account, AccountType, Transaction, LedgerEntry
from django.db.models import Sum

print("--- Accounts ---")
for acc in Account.objects.all():
    print(f"ID: {acc.id}, Name: {acc.name}, Type: {acc.account_type}")

print("\n--- Transactions and Ledger Entries for 'Opening Balance' ---")
txns = Transaction.objects.filter(description__icontains="Opening Balance")
for txn in txns:
    print(f"Transaction ID: {txn.id}, Desc: {txn.description}, Date: {txn.date}")
    for entry in txn.entries.all():
        print(f"  Entry: {entry.account.name}, Type: {entry.entry_type}, Amount: {entry.amount}, AccType: {entry.account.account_type}")

print("\n--- Summary ---")
revenue_total = LedgerEntry.objects.filter(account__account_type=AccountType.REVENUE).aggregate(Sum('amount'))['amount__sum']
expense_total = LedgerEntry.objects.filter(account__account_type=AccountType.EXPENSE).aggregate(Sum('amount'))['amount__sum']
print(f"Total Revenue: {revenue_total}")
print(f"Total Expense: {expense_total}")
