import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User
from core.models import Currency, Client, Project, AccountType, Account, BankAccount

def setup():
    # Create superuser
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'admin@example.com', 'admin')
        print("Created superuser 'admin' with password 'admin'")

    # Create currencies
    usd, _ = Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'symbol': '$', 'is_base': True})
    eur, _ = Currency.objects.get_or_create(code='EUR', defaults={'name': 'Euro', 'symbol': '€'})
    
    # Create Chart of Accounts
    asset_acct, _ = Account.objects.get_or_create(name='Main Corporate Checking (USD)', account_type=AccountType.ASSET, currency=usd)
    asset_acct_eur, _ = Account.objects.get_or_create(name='European Branch Checking (EUR)', account_type=AccountType.ASSET, currency=eur)
    fee_acct, _ = Account.objects.get_or_create(name='Bank Fees', account_type=AccountType.EXPENSE, currency=usd)
    fx_acct, _ = Account.objects.get_or_create(name='FX Gain/Loss', account_type=AccountType.EXPENSE, currency=usd)

    # Create Bank Accounts
    BankAccount.objects.get_or_create(ledger_account=asset_acct, bank_name='Chase Bank', account_number='123456789')
    BankAccount.objects.get_or_create(ledger_account=asset_acct_eur, bank_name='Deutsche Bank', account_number='987654321')

    print("Created dummy data for currencies, accounts, and bank accounts.")

if __name__ == '__main__':
    setup()
