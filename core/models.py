from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from django.core.exceptions import ValidationError

class Currency(models.Model):
    """
    Manages supported currencies for the ERP.
    """
    code = models.CharField(max_length=3, unique=True, help_text="e.g. USD, EUR, GBP")
    name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=5)
    is_base = models.BooleanField(default=False, help_text="Is this the default company currency?")
    rate_to_pkr = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1.000000'), help_text="Exchange rate to PKR")
    last_updated = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "Currencies"

    def __str__(self):
        return f"{self.code} - {self.name}"

class Client(models.Model):
    """
    Clients associated with Projects.
    """
    REGION_CHOICES = [('Local', 'Local'), ('Global', 'Global')]
    STATUS_CHOICES = [('Active', 'Active'), ('Archived', 'Archived')]
    
    name = models.CharField(max_length=255)
    company_name = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    contact_number = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    region = models.CharField(max_length=50, choices=REGION_CHOICES, default='Local')
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Active')
    profile_picture = models.ImageField(upload_to='clients/profiles/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    def __str__(self):
        return self.name

class Employee(models.Model):
    """
    Employee records for HR and Payroll management.
    """
    STATUS_CHOICES = [
        ('Active', 'Active'), 
        ('On Leave', 'On Leave'),
        ('Terminated', 'Terminated'), 
        ('Resigned', 'Resigned')
    ]
    
    name = models.CharField(max_length=255)
    employee_id = models.CharField(max_length=50, unique=True, blank=True, null=True, help_text="Company Employee ID")
    designation = models.CharField(max_length=255)
    department = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    contact_number = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    salary = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name='employees', null=True)
    date_joined = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Active')
    profile_picture = models.ImageField(upload_to='employees/profiles/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.designation})"

class Project(models.Model):
    """
    Projects linked to a specific client. Tracks financial milestones.
    """
    STATUS_CHOICES = [
        ('Pipeline / Prospect', 'Pipeline / Prospect'),
        ('Active / In Progress', 'Active / In Progress'),
        ('On Hold', 'On Hold'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled'),
    ]
    TYPE_CHOICES = [('Fixed', 'Fixed'), ('Subscription', 'Subscription')]
    
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='projects')
    project_lead = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name='projects')
    target_budget = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Pipeline / Prospect')
    project_type = models.CharField(max_length=50, choices=TYPE_CHOICES, default='Fixed')
    monthly_fee = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, help_text="Monthly fee for subscription projects")

    timeline = models.CharField(max_length=100, default="Monthly Cycle")
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(blank=True, null=True)
    TAX_TYPE_CHOICES = [('Percentage', 'Percentage'), ('Fixed', 'Fixed')]
    
    tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Estimated tax percentage")
    tax_fixed_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, help_text="Fixed tax amount")
    tax_type = models.CharField(max_length=20, choices=TAX_TYPE_CHOICES, default='Percentage')

    last_billed_date = models.DateField(null=True, blank=True, help_text="The date of the last monthly invoice generated")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    @property
    def total_invoiced(self):
        """Calculates total revenue invoiced for this project in its native currency."""
        from django.db.models import Sum, F
        # Sum up all revenue credits in PKR first
        total_pkr = LedgerEntry.objects.filter(
            transaction__project=self,
            account__account_type=AccountType.REVENUE,
            entry_type='CR'
        ).annotate(
            pkr_amount=F('amount') * F('exchange_rate')
        ).aggregate(total=Sum('pkr_amount'))['total'] or Decimal('0.00')
        
        # Convert PKR total back to project currency
        project_rate = self.currency.rate_to_pkr or Decimal('1.000000')
        return (total_pkr / project_rate).quantize(Decimal('0.01'))


    @property
    def status_color_class(self):
        """Returns Tailwind CSS classes for status badges based on project status."""
        map = {
            'Active / In Progress': 'emerald',
            'Pipeline / Prospect': 'indigo',
            'On Hold': 'amber',
            'Completed': 'slate',
            'Cancelled': 'red',
        }
        color = map.get(self.status, 'gray')
        return f"bg-{color}-50 border-{color}-100 text-{color}-600"

    @property
    def days_total(self):
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days
        return None

    @property
    def days_elapsed(self):
        if self.start_date:
            today = timezone.now().date()
            return (today - self.start_date).days
        return 0

    @property
    def days_remaining(self):
        if self.end_date:
            today = timezone.now().date()
            return (self.end_date - today).days
        return None

    @property
    def time_percentage(self):
        total = self.days_total
        if total and total > 0:
            elapsed = self.days_elapsed
            percent = (elapsed / total) * 100
            return max(0, min(100, percent))
        return 0

    def __str__(self):
        return f"{self.name} ({self.client.name})"

class TransactionCategory(models.Model):
    """
    Standard categories for a software house.
    Used for organizing Chart of Accounts and providing quick-select options.
    """
    CATEGORY_TYPE_CHOICES = [('Income', 'Income'), ('Expense', 'Expense')]
    
    name = models.CharField(max_length=255, unique=True)
    category_type = models.CharField(max_length=10, choices=CATEGORY_TYPE_CHOICES)
    description = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name_plural = "Transaction Categories"

    def __str__(self):
        return f"{self.name} ({self.category_type})"

class AccountType(models.TextChoices):
    ASSET = 'ASSET', 'Asset'
    LIABILITY = 'LIABILITY', 'Liability'
    EQUITY = 'EQUITY', 'Equity'
    REVENUE = 'REVENUE', 'Revenue'
    EXPENSE = 'EXPENSE', 'Expense'

class Account(models.Model):
    """
    Chart of Accounts node. Required for strict double-entry accounting.
    Examples: "Corporate Checking (Asset)", "Bank Fees (Expense)", "Accounts Receivable (Asset)".
    """
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class BankAccountManager(models.Manager):
    def active_banks(self):
        return self.filter(is_active=True)
        
    def with_balances(self):
        # A custom queryset logic could be added here to sum up LedgerEntries dynamically
        pass

class BankAccount(models.Model):
    """
    Specific bank accounts. Linked 1-to-1 with a Ledger Account of type ASSET.
    """
    ledger_account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name='bank_detail')
    bank_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    
    objects = BankAccountManager()

    def __str__(self):
        account_last_4 = f" - ****{self.account_number[-4:]}" if self.account_number else ""
        return f"{self.bank_name}{account_last_4}"

class ExpenseManagerAccess(models.Model):
    """
    RBAC: Links an Expense Manager (User) to specific Bank Accounts.
    Restricts them to add/edit/delete expenses only for their assigned banks.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE)
    
    class Meta:
        unique_together = ('user', 'bank_account')

    def __str__(self):
        return f"{self.user} -> {self.bank_account}"

class ProjectAccess(models.Model):
    """
    RBAC: Links a User to specific Projects.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    
    class Meta:
        unique_together = ('user', 'project')

    def __str__(self):
        return f"{self.user} -> {self.project.name}"

class ClientAccess(models.Model):
    """
    RBAC: Links a User to specific Clients.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client = models.ForeignKey(Client, on_delete=models.CASCADE)
    
    class Meta:
        unique_together = ('user', 'client')

    def __str__(self):
        return f"{self.user} -> {self.client.name}"

class UserPermission(models.Model):
    """
    Global page-level permissions for users.
    Manageable via the Access Management page.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='permissions')
    can_manage_income = models.BooleanField(default=False)
    can_manage_expense = models.BooleanField(default=False)
    can_manage_banking = models.BooleanField(default=False)
    can_manage_analytics = models.BooleanField(default=False)
    can_manage_projects = models.BooleanField(default=False)
    can_manage_clients = models.BooleanField(default=False)
    can_manage_employees = models.BooleanField(default=False)
    can_view_dashboard = models.BooleanField(default=False)
    can_view_all_data = models.BooleanField(default=False)
    profile_picture = models.ImageField(upload_to='users/profiles/', blank=True, null=True)
    
    def __str__(self):
        return f"Permissions for {self.user.username}"

class Transaction(models.Model):
    """
    The main container for a double-entry journal movement (Journal Entry).
    """
    date = models.DateField()
    description = models.CharField(max_length=500)
    reference = models.CharField(max_length=100, blank=True, help_text="e.g. Invoice #, Transfer ID")
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    receipt = models.FileField(upload_to='receipts/%Y/%m/%d/', null=True, blank=True)
    custom_fields = models.JSONField(blank=True, default=dict)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name='transactions', null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"TXN {self.id} on {self.date}: {self.description}"

    def get_base_amount(self):
        """Returns the total amount converted to the ERP base currency."""
        # Sum base amounts of the primary entries (Revenue side for income, Expense side for costs)
        entries = self.entries.filter(
            account__account_type__in=[AccountType.REVENUE, AccountType.EXPENSE, AccountType.EQUITY]
        )
        if not entries.exists():
            entries = self.entries.all()
        
        total = sum(e.get_base_amount() for e in entries)
        return total

    def get_type_display(self):
        """Returns 'Income', 'Expense', or 'Transfer' based on ledger entries"""
        # Check if any entry hits a Revenue, Expense, or Equity account
        # Equity (like Opening Balance) is treated as an inward movement (Income)
        has_revenue = self.entries.filter(account__account_type=AccountType.REVENUE).exists()
        has_equity = self.entries.filter(account__account_type=AccountType.EQUITY).exists()
        has_expense = self.entries.filter(account__account_type=AccountType.EXPENSE).exists()
        
        if has_revenue or has_equity: return "Income"
        if has_expense: return "Expense"
        
        # If both or neither, might be a transfer between assets
        return "Transfer"

    def get_category_name(self):
        """Returns the non-asset account name (Revenue/Expense/Equity). Prioritizes non-Charity non-zero amounts."""
        entries = self.entries.filter(
            account__account_type__in=[AccountType.REVENUE, AccountType.EXPENSE, AccountType.EQUITY]
        )
        # If there are multiple entries (split), prefer the non-Charity one with a non-zero amount
        non_charity = entries.exclude(account__name__icontains='Charity').exclude(amount=0)
        if non_charity.exists():
            return non_charity.first().account.name
        
        # Try any non-zero entry
        active_entry = entries.exclude(amount=0).first()
        if active_entry:
             return active_entry.account.name
             
        # Fallback to the first one found
        category_entry = entries.first()
        return category_entry.account.name if category_entry else "General"

    def get_bank_account_name(self):
        """Returns the name of the bank account involved"""
        bank_entry = self.entries.filter(
            account__account_type=AccountType.ASSET,
            account__bank_detail__isnull=False
        ).first()
        return bank_entry.account.bank_detail.bank_name if bank_entry else "N/A"

    def get_total_amount(self):
        """Returns the summed amount for all primary entries (Revenue/Expense/Equity side)"""
        entries = self.entries.filter(
            account__account_type__in=[AccountType.REVENUE, AccountType.EXPENSE, AccountType.EQUITY]
        )
        if not entries.exists():
            return self.entries.first().amount if self.entries.exists() else Decimal('0.00')
        
        from decimal import Decimal
        return sum(e.amount for e in entries)

    def get_charity_amount(self):
        """Specific helper to find amount allocated to Charity account. Sums all matching entries."""
        from django.db.models import Sum
        charity_entries = self.entries.filter(account__name__icontains='Charity')
        if not charity_entries.exists():
            return None
        return charity_entries.aggregate(total=Sum('amount'))['total']

    def get_currency_symbol(self):
        """Returns the currency symbol for the transaction"""
        if self.currency:
            return self.currency.symbol
        # Fallback to base currency or PKR
        return "Rs"

class LedgerEntryManager(models.Manager):
    def get_account_balance(self, account_id):
        """
        Calculates the real-time balance of an account from the ledger entries.
        Positive means normal balance (Debit for Assets/Expenses, Credit for Liability/Equity/Revenue).
        """
        from django.db.models import Sum
        qs = self.filter(account_id=account_id)
        
        debits = qs.filter(entry_type=self.model.DR).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        credits = qs.filter(entry_type=self.model.CR).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        account = Account.objects.get(id=account_id)
        if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            return debits - credits
        else:
            return credits - debits

class LedgerEntry(models.Model):
    """
    A single line item in a Transaction. Must balance (DR == CR) within a Transaction across base currency.
    """
    DR = 'DR'
    CR = 'CR'
    ENTRY_TYPE_CHOICES = [(DR, 'Debit'), (CR, 'Credit')]

    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='entries')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='ledger_entries')
    entry_type = models.CharField(max_length=2, choices=ENTRY_TYPE_CHOICES)
    
    # Amount in the Account's native currency
    amount = models.DecimalField(max_digits=19, decimal_places=4)
    
    # Used for multi-currency balancing. Exchange rate to BASE currency at the time of transaction.
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal('1.000000'), help_text="Rate to base currency")

    objects = LedgerEntryManager()

    def get_base_amount(self):
        """Returns the equivalent amount in the company's base currency"""
        return self.amount * self.exchange_rate

    def __str__(self):
        return f"{self.entry_type} {self.amount} {self.account.currency.code} -> {self.account.name}"

# Signals
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
User = get_user_model()

@receiver(post_save, sender=User)
def create_user_permissions(sender, instance, created, **kwargs):
    if created:
        UserPermission.objects.get_or_create(user=instance)

# No transaction is created automatically when a project is created.
# Fixed project budget values appear in the client ledger as virtual entries,
# read directly from Project.target_budget — so no Transaction object is ever
# written, and nothing clutters the main transaction dashboard.
# Subscription monthly billings are handled by process_monthly_billings() in views.py.
