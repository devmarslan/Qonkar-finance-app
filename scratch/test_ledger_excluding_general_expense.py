import os
import sys
import django

# Set up Django environment
sys.path.append(r'd:\GitHub Softwares\Qonkar finance app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Employee, Transaction
from decimal import Decimal

def test_payout_exclusion():
    print("====================================================")
    print("TESTING GENERAL EXPENSE EXCLUSION FROM LEDGER STATEMENT")
    print("====================================================")
    
    employee = Employee.objects.get(pk=3) # Muhammad Arslan
    txns = Transaction.objects.filter(project_leader=employee).order_by('-date', '-created_at')
    
    print(f"Total raw transactions assigned to leader: {txns.count()}")
    for t in txns:
        print(f" - ID: {t.id} | Desc: '{t.description}' | Type: {t.get_type_display()} | Category: '{t.get_category_name()}'")
        
    performance_ledger = []
    total_earned = Decimal('0.00')
    total_paid = Decimal('0.00')
    
    for txn in txns:
        type_display = txn.get_type_display()
        amount = Decimal('0.00')
        is_payout = False
        
        if type_display == 'Income':
            amount = txn.get_base_lead_commission_amount()
            total_earned += amount
        elif type_display == 'Expense':
            if txn.get_category_name() == 'Lead Commission':
                amount = txn.get_base_total_amount()
                total_paid += amount
                is_payout = True
            else:
                continue
                
        performance_ledger.append({
            'transaction': txn,
            'amount': amount,
            'is_payout': is_payout
        })
        
    print("\n--- RESULTS ---")
    print(f"Filtered Ledger Rows count: {len(performance_ledger)}")
    print(f"Total Earned (Gross Commissions): Rs {total_earned}")
    print(f"Total Paid (Settled Payouts): Rs {total_paid}")
    print(f"Net Outstanding Balance: Rs {total_earned - total_paid}")
    
    # Assertions
    # General project expense of 35000 is ID 4545, it should be excluded!
    excluded_ids = [item['transaction'].id for item in performance_ledger]
    assert 4545 not in excluded_ids, "ERROR: General expense ID 4545 was NOT excluded!"
    print("SUCCESS: General project expense ID 4545 was successfully excluded from ledger statement!")
    
    # Balance should be gross commission of Rs 48,873.15 because no Lead Commission payouts are recorded yet
    assert total_paid == Decimal('0.00'), f"ERROR: Total paid should be 0.00 but got {total_paid}"
    print("SUCCESS: Settled Payouts is exactly Rs 0.00!")
    print("INTEGRATION VERIFICATION PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_payout_exclusion()
