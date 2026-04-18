import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Transaction, LedgerEntry, Account, AccountType

results = []

txns = Transaction.objects.all().order_by('-created_at')[:20]
for txn in txns:
    results.append(f"TXN ID: {txn.id} | Desc: {txn.description} | Date: {txn.date} | Project: {txn.project}")
    for entry in txn.entries.all():
        results.append(f"  - Account: {entry.account.name} ({entry.account.account_type}) | Type: {entry.entry_type} | Amount: {entry.amount} | Rate: {entry.exchange_rate}")

with open('debug_output.txt', 'w') as f:
    f.write('\n'.join(results))

print("Results written to debug_output.txt")
