import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Transaction, LedgerEntry, AccountType

results = []
results.append("All transactions related to user:")
# Assuming the user is Abdul Rehman or similar, let's just get the last few transactions
for txn in Transaction.objects.all().order_by('-created_at')[:10]:
    results.append(f"TXN: {txn.id} | {txn.description} | {txn.date}")
    for e in txn.entries.all():
        results.append(f"  - {e.account.name} ({e.account.account_type}): {e.amount} {e.entry_type}")

results.append("\nAll Expenses in the current month:")
from django.utils import timezone
start = timezone.now().replace(day=1).date()
expenses = LedgerEntry.objects.filter(account__account_type=AccountType.EXPENSE, transaction__date__gte=start)
for exp in expenses:
    results.append(f"TXN {exp.transaction.id} | Desc: {exp.transaction.description} | id: {exp.id} | account: {exp.account.name}: {exp.amount}")

with open("expenses_output_native.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))
