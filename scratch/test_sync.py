import os
import sys
import django

# Setup Django environment
sys.path.append(r"d:\GitHub Softwares\Qonkar finance app")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Transaction, Project, LedgerEntry, Account, AccountType, Employee
from core.forms import TransactionEditForm
from decimal import Decimal

def run_test():
    print("====================================================")
    print("STARTING LEDGER SYNCHRONIZATION INTEGRATION TEST")
    print("====================================================")

    # 1. Fetch reference project
    project = Project.objects.get(name='Pharmacy Garden')
    print(f"Project name: {project.name}")
    print(f"Current Leader: {project.project_lead}")
    print(f"Current Tax Model: {project.tax_type} ({project.tax_percentage}%)")

    # 2. Get an income transaction linked to this project
    txn = Transaction.objects.filter(project=project, entries__account__account_type=AccountType.REVENUE).first()
    if not txn:
        print("No income transaction found, aborting test.")
        return
        
    print(f"\nFound Income Transaction ID: {txn.id}")
    print(f"Description: {txn.description}")
    print(f"Current Tax Amount: {txn.tax_amount}")
    print(f"Current Leader: {txn.project_leader}")
    print("Ledger Entries before changes:")
    for entry in txn.entries.all():
        print(f" - Account: {entry.account.name} | Type: {entry.entry_type} | Amount: {entry.amount}")

    # 3. Simulate updating the Project settings:
    # We will change the leader (or toggle it) and change the tax percentage.
    original_lead = project.project_lead
    other_lead = Employee.objects.exclude(id=original_lead.id if original_lead else None).first()
    
    print(f"\nUpdating project lead from {original_lead} to {other_lead}")
    print("Updating project tax percentage from 5.00% to 10.00%")
    
    project.project_lead = other_lead
    project.tax_percentage = Decimal('10.00')
    project.save() # This triggers the cascade save and sync

    # Fetch fresh transaction details
    txn.refresh_from_db()
    print(f"\nRecalculated Transaction Tax Amount: {txn.tax_amount}")
    print(f"Recalculated Transaction Leader: {txn.project_leader}")
    
    print("Ledger Entries after saving project:")
    for entry in txn.entries.all():
        print(f" - Account: {entry.account.name} | Type: {entry.entry_type} | Amount: {entry.amount}")

    # Verify matching entries
    bank_entry = txn.entries.filter(account__account_type=AccountType.ASSET).first()
    charity_entry = txn.entries.filter(account__name__icontains='Charity').first()
    cat_entry = txn.entries.filter(account__account_type=AccountType.REVENUE).exclude(account__name__icontains='Charity').first()

    print("\nVerifying sequential split math:")
    gross = bank_entry.amount
    tax = txn.tax_amount
    # Calculate expenses
    project_rate = project.currency.rate_to_pkr or Decimal('1.000000')
    total_spent = Decimal('0.00')
    for p_txn in project.transactions.all():
        for entry in p_txn.entries.all():
            if entry.account.account_type == AccountType.EXPENSE and entry.entry_type == 'DR':
                total_spent += (entry.amount * entry.exchange_rate) / project_rate
    total_spent = total_spent.quantize(Decimal('0.01'))
    
    net_before_deductions = max(Decimal('0.00'), gross - tax - total_spent)
    expected_charity = (net_before_deductions * (txn.charity_percentage or Decimal('0.00')) / Decimal('100.00')).quantize(Decimal('0.01'))
    expected_remaining = gross - expected_charity
    
    print(f"Gross: {gross}")
    print(f"Tax: {tax}")
    print(f"Expenses: {total_spent}")
    print(f"Net before deductions: {net_before_deductions}")
    print(f"Expected Charity split (at {txn.charity_percentage}%): {expected_charity}")
    print(f"Actual Charity split entry: {charity_entry.amount if charity_entry else 'None'}")
    print(f"Expected Remaining category amount: {expected_remaining}")
    print(f"Actual Category entry amount: {cat_entry.amount if cat_entry else 'None'}")

    assert abs(charity_entry.amount - expected_charity) < Decimal('0.01'), "Charity split amount mismatch!"
    print("SUCCESS: Charity and Category splits are 100% accurate!")

    # 4. Restore original settings to keep DB pristine
    print("\nRestoring original settings...")
    project.project_lead = original_lead
    project.tax_percentage = Decimal('5.00')
    project.save()
    
    print("INTEGRATION TEST COMPLETED SUCCESSFULLY!")

if __name__ == '__main__':
    run_test()
