import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import LedgerEntry
from django.utils import timezone

start = timezone.now().replace(day=1).date()

out = []
entries = LedgerEntry.objects.filter(amount=5000, transaction__date__gte=start)
for e in entries:
    out.append(f"TXN: {e.transaction.id} | Desc: {e.transaction.description} | Account: {e.account.name} | Type: {e.account.account_type}")

with open("out_5k.txt", "w") as f:
    f.write("\n".join(out))
