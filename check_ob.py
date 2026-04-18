import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Account, LedgerEntry
from django.db.models import Sum

out = []
accs = Account.objects.filter(name__icontains="Opening Balance")
for a in accs:
    out.append(f"Account: {a.name} | Type: {a.account_type}")
    total_entries = a.ledger_entries.count()
    total_amt = a.ledger_entries.aggregate(Sum('amount'))['amount__sum']
    out.append(f"  - Total entries: {total_entries} | Total amount: {total_amt}")
    for e in a.ledger_entries.all():
        out.append(f"    * TXN {e.transaction.id} | Amount: {e.amount} | Created by: {e.transaction.created_by}")

with open("opening_balance_debug.txt", "w") as f:
    f.write("\n".join(out))
