from django.core.management.base import BaseCommand
from core.models import Account, AccountType, Currency
from decimal import Decimal

class Command(BaseCommand):
    help = 'Seed the database with default categories and PKR currency'

    def handle(self, *args, **options):
        # 1. Create/Get PKR Currency
        pkr, created = Currency.objects.get_or_create(
            code='PKR',
            defaults={
                'name': 'Pakistani Rupee',
                'symbol': 'Rs',
                'is_base': True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS('Created PKR Currency'))

        # 2. Categories mapping
        income_categories = [
            "Consulting Income",
            "Income",
            "Investment Return",
            "Opening Balance"
        ]

        expense_categories = [
            "Advertising & Marketing",
            "Bank Charges",
            "Charity",
            "Charity & Donations",
            "Client Entertainment",
            "Contractors & Freelancers",
            "Employee Benefits",
            "Events & Conferences",
            "Expense",
            "Hardware & Equipment",
            "Insurance",
            "Legal & Professional Fees",
            "Maintenance & Repairs",
            "Office Rent & Lease",
            "Office Supplies"
        ]

        # 3. Create Revenue Accounts
        for name in income_categories:
            Account.objects.get_or_create(
                name=name,
                account_type=AccountType.REVENUE,
                defaults={'currency': pkr}
            )
        
        # 4. Create Expense Accounts
        for name in expense_categories:
            Account.objects.get_or_create(
                name=name,
                account_type=AccountType.EXPENSE,
                defaults={'currency': pkr}
            )

        # 5. Create Demo Clients
        from core.models import Client, Project
        cli1, _ = Client.objects.get_or_create(name="TPWS", defaults={'region': 'Local', 'email': 'contact@tpws.local'})
        cli2, _ = Client.objects.get_or_create(name="Mr. Food", defaults={'region': 'Local'})
        cli3, _ = Client.objects.get_or_create(name="Cafe Kunj", defaults={'region': 'Local'})
        cli4, _ = Client.objects.get_or_create(name="Obtino", defaults={'region': 'Global'})

        # 6. Create Demo Projects
        Project.objects.get_or_create(
            name="TPWS ERP", 
            client=cli1,
            defaults={
                'project_type': 'Subscription',
                'monthly_fee': Decimal('60000.00'),
                'currency': pkr,
                'target_budget': Decimal('500000.00')
            }
        )
        Project.objects.get_or_create(
            name="Food App", 
            client=cli2,
            defaults={'currency': pkr, 'target_budget': Decimal('200000.00')}
        )

        # 7. Cleanup generic accounts that might be confusing
        Account.objects.filter(name__in=["Expense", "Income"]).delete()

        self.stdout.write(self.style.SUCCESS('Successfully seeded all data!'))
