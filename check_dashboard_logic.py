import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import LedgerEntry, AccountType
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models import Sum, F, Q
from decimal import Decimal

# get the user
user = User.objects.filter(username__icontains="abdul").first() or User.objects.first()
print(f"User: {user}")

now = timezone.now()
cm_start = now.replace(day=1).date()

# get_total_for_type logic
account_type = AccountType.EXPENSE
start_date = cm_start
end_date = now.date()

qs = LedgerEntry.objects.filter(account__account_type=account_type, transaction__date__range=[start_date, end_date])
qs = qs.filter(Q(transaction__created_by=user) | Q(transaction__entries__account__bank_detail__expensemanageraccess__user=user)).distinct()
total = qs.aggregate(total=Sum(F('amount') * F('exchange_rate')))['total'] or Decimal('0.00')

out = []
out.append(f"User: {user}")
out.append(f"Total Outcome locally: {total}")

# find which entries make up this total
for e in qs:
    out.append(f"  - TXN {e.transaction.id} | Desc: {e.transaction.description} | Account: {e.account.name} | Amount: {e.amount}")

with open("dashboard_logic_output.txt", "w") as f:
    f.write("\n".join(out))
