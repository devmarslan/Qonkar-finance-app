import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Account, AccountType, Transaction, LedgerEntry, Currency
from django.db.models import Sum

print("--- Currencies ---")
for c in Currency.objects.all():
    print(f"{c.code}: {c.rate_to_pkr} (Base: {c.is_base})")

print("\n--- Recent Transactions ---")
txns = Transaction.objects.all().order_by('-created_at')[:10]
for txn in txns:
    print(f"TXN ID: {txn.id}, Desc: {txn.description}, Date: {txn.date}, Currency: {txn.currency.code if txn.currency else 'N/A'}")
    for entry in txn.entries.all():
        base_amt = entry.amount * entry.exchange_rate
        print(f"  Entry: {entry.account.name} ({entry.account.account_type}), Type: {entry.entry_type}, "
              f"LocalAmt: {entry.amount}, Rate: {entry.exchange_rate}, BaseAmt: {base_amt}")

print("\n--- Dashboard Totals Verification ---")
from django.utils import timezone
now = timezone.now()
start_date = now.replace(day=1).date()
end_date = now.date()

def get_total(atype):
    total = LedgerEntry.objects.filter(
        account__account_type=atype,
        transaction__date__range=[start_date, end_date]
    ).aggregate(total=Sum(django.db.models.F('amount') * django.db.models.F('exchange_rate')))['total']
    return total or 0

rev = get_total(AccountType.REVENUE)
exp = get_total(AccountType.EXPENSE)
print(f"Revenue (Calculated): {rev}")
print(f"Expense (Calculated): {exp}")
