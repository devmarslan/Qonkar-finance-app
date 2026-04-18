import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import AccountType, LedgerEntry
from django.db.models import Sum
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

with open('debug_totals.txt', 'w') as f:
    f.write(f"Revenue: {rev}\nExpense: {exp}\n")
