from decimal import Decimal
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
import requests
from .models import Transaction, LedgerEntry, BankAccount, Account, AccountType, Currency
import datetime

def perform_inter_bank_transfer(
    from_bank_account_id: int,
    to_bank_account_id: int,
    amount_sent: Decimal, # Amount in sender's currency
    amount_received: Decimal, # Amount in receiver's currency
    fee_amount: Decimal, # Bank fee (charged in sender's currency usually, or specify if different)
    fee_account_id: int,
    date,
    description: str,
    user_id: int,
    base_currency_rate_from: Decimal = Decimal('1.000000'),
    base_currency_rate_to: Decimal = Decimal('1.000000'),
    base_currency_rate_fee: Decimal = Decimal('1.000000'),
    fx_account_id: int = None
) -> Transaction:
    """
    Performs an inter-bank transfer handling double-entry and bank fees.
    Core business logic without the API/View layer.
    """
    
    if amount_sent <= 0 or amount_received <= 0:
        raise ValidationError("Transfer amounts must be greater than zero.")
    if from_bank_account_id == to_bank_account_id:
        raise ValidationError("Cannot transfer to the same bank account.")
    if fee_amount < 0:
        raise ValidationError("Fee amount cannot be negative.")

    with db_transaction.atomic():
        from_bank = BankAccount.objects.select_related('ledger_account__currency').get(id=from_bank_account_id)
        to_bank = BankAccount.objects.select_related('ledger_account__currency').get(id=to_bank_account_id)
        
        # Validate Fee Account
        if fee_amount > 0:
            fee_account = Account.objects.get(id=fee_account_id)
            if fee_account.account_type != AccountType.EXPENSE:
                raise ValidationError("Fee account must be an Expense account.")
        else:
            fee_account = None

        # Create Journal Entry (Transaction)
        txn = Transaction.objects.create(
            date=date,
            description=description,
            created_by_id=user_id,
            reference=f"Transfer {from_bank.bank_name} -> {to_bank.bank_name}"
        )

        total_deducted = amount_sent + fee_amount
        
        # Calculate Base Currency Equivalents for Balancing
        base_dr_bank = amount_received * base_currency_rate_to
        base_dr_fee = fee_amount * base_currency_rate_fee
        base_cr_bank = total_deducted * base_currency_rate_from
        
        total_dr_base = round(base_dr_bank + base_dr_fee, 4)
        total_cr_base = round(base_cr_bank, 4)
        
        # Strict Double-Entry Base Currency Balancing check
        imbalance = total_dr_base - total_cr_base
        
        if abs(imbalance) > Decimal('0.0000'):
            if not fx_account_id:
                raise ValidationError(
                    f"Ledger unbalanced by {imbalance} base currency units. "
                    "An FX Gain/Loss account ID must be provided to balance the transaction."
                )
            
            fx_account = Account.objects.get(id=fx_account_id)
            if fx_account.account_type not in [AccountType.REVENUE, AccountType.EXPENSE]:
                raise ValidationError("FX Gain/Loss account must be Revenue or Expense.")
                
            # If Debits > Credits, we need a Credit to balance (FX Gain)
            # If Credits > Debits, we need a Debit to balance (FX Loss)
            if imbalance > 0:
                LedgerEntry.objects.create(
                    transaction=txn,
                    account=fx_account,
                    entry_type=LedgerEntry.CR,
                    amount=abs(imbalance),
                    exchange_rate=Decimal('1.000000') # Base currency equivalent
                )
            else:
                LedgerEntry.objects.create(
                    transaction=txn,
                    account=fx_account,
                    entry_type=LedgerEntry.DR,
                    amount=abs(imbalance),
                    exchange_rate=Decimal('1.000000')
                )

        # 1. Credit the source bank account for the total deducted (Send Amount + Fee)
        LedgerEntry.objects.create(
            transaction=txn,
            account=from_bank.ledger_account,
            entry_type=LedgerEntry.CR,
            amount=total_deducted,
            exchange_rate=base_currency_rate_from
        )

        # 2. Debit the destination bank account for the received amount
        LedgerEntry.objects.create(
            transaction=txn,
            account=to_bank.ledger_account,
            entry_type=LedgerEntry.DR,
            amount=amount_received,
            exchange_rate=base_currency_rate_to
        )

        # 3. Debit the Fee Account (Expense)
        if fee_amount > 0 and fee_account:
            LedgerEntry.objects.create(
                transaction=txn,
                account=fee_account,
                entry_type=LedgerEntry.DR,
                amount=fee_amount,
                exchange_rate=base_currency_rate_fee
            )

        return txn

def update_exchange_rates():
    """
    Fetches real-time exchange rates from a public API and updates Currency models.
    Base currency for rates is PKR.
    """
    # Using open.er-api.com (No API key required for basic usage)
    API_URL = "https://open.er-api.com/v6/latest/PKR"
    
    try:
        response = requests.get(API_URL, timeout=10)
        data = response.json()
        
        if data['result'] == 'success':
            rates = data['rates']
            # rates will be in format: "USD": 0.00359, "EUR": 0.0033...
            # We want rate_to_pkr = 1 / PKR_rate
            
            currencies = Currency.objects.all()
            for currency in currencies:
                code = currency.code.upper()
                if code == 'PKR':
                    currency.rate_to_pkr = Decimal('1.000000')
                    currency.last_updated = timezone.now()
                    currency.save()
                    continue
                
                if code in rates:
                    # 1 PKR = rate[code] units
                    # So 1 unit = 1 / rate[code] PKR
                    rate_to_pkr = 1 / rates[code]
                    currency.rate_to_pkr = Decimal(str(rate_to_pkr))
                    currency.last_updated = timezone.now()
                    currency.save()
                    
            return True
    except Exception as e:
        print(f"Error fetching exchange rates: {e}")
        return False
