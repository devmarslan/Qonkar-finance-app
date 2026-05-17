from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from django.core.exceptions import ValidationError
from .utils import compress_image
import calendar


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

    def save(self, *args, **kwargs):
        if self.profile_picture:
            compress_image(self.profile_picture)
        super().save(*args, **kwargs)

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

    def save(self, *args, **kwargs):
        if self.profile_picture:
            compress_image(self.profile_picture)
        super().save(*args, **kwargs)

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

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        if not is_new:
            # Sync existing transactions
            from decimal import Decimal
            from .models import AccountType
            for txn in self.transactions.all():
                changed = False
                
                # 1. Update project leader if different (only for Income transactions)
                if txn.get_type_display() == 'Income' and txn.project_leader != self.project_lead:
                    txn.project_leader = self.project_lead
                    changed = True
                
                # 2. Recalculate transaction tax if tax settings changed
                bank_entry = txn.entries.filter(
                    account__account_type=AccountType.ASSET,
                    account__bank_detail__isnull=False
                ).first()
                if bank_entry:
                    total_amount = bank_entry.amount
                    new_tax = Decimal('0.00')
                    if self.tax_type == 'Percentage':
                        new_tax = (total_amount * self.tax_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
                    elif self.tax_type == 'Fixed':
                        new_tax = self.tax_fixed_amount
                    
                    if txn.tax_amount != new_tax:
                        txn.tax_amount = new_tax
                        changed = True
                
                if changed:
                    txn.save()
            
            # Run project-level synchronization once after all transactions are saved!
            self.sync_project_financials()

    def sync_project_financials(self):
        """
        Recalculates the project's sequential charity splits and lead commissions,
        then updates/synchronizes the ledger entries for Charity across all linked transactions.
        """
        from decimal import Decimal
        from .models import AccountType
        
        # 1. Gather all linked transactions
        project_txns = list(self.transactions.all())
        income_txns = []
        for txn in project_txns:
            if txn.entries.filter(account__account_type=AccountType.REVENUE).exists():
                income_txns.append(txn)
                
        if not income_txns:
            return
            
        total_billed = Decimal('0.00')
        total_tax = Decimal('0.00')
        total_spent = Decimal('0.00')
        project_rate = self.currency.rate_to_pkr or Decimal('1.000000')
        
        # Track project-level split percentages
        charity_pct = Decimal('0.00')
        
        for txn in project_txns:
            if txn.charity_percentage and txn.charity_percentage > charity_pct:
                charity_pct = txn.charity_percentage
                
            first_rev = txn.entries.filter(account__account_type=AccountType.REVENUE).first()
            rate = first_rev.exchange_rate if first_rev else Decimal('1.000000')
            
            # Is it an income transaction?
            if txn in income_txns:
                total_billed += (txn.get_total_amount() * rate) / project_rate
                total_tax += (txn.tax_amount * rate) / project_rate
                
            for entry in txn.entries.all():
                if entry.account.account_type == AccountType.EXPENSE and entry.entry_type == 'DR':
                    total_spent += (entry.amount * entry.exchange_rate) / project_rate
                    
        total_billed = total_billed.quantize(Decimal('0.01'))
        total_spent = total_spent.quantize(Decimal('0.01'))
        total_tax = total_tax.quantize(Decimal('0.01'))
        
        if total_billed <= 0:
            return
            
        # Apply sequential split:
        net_before_deductions = max(Decimal('0.00'), total_billed - total_tax - total_spent)
        total_charity = (net_before_deductions * charity_pct / Decimal('100.00')).quantize(Decimal('0.01'))
        
        # Distribute charity split among income transactions
        remaining_charity = total_charity
        for idx, txn in enumerate(income_txns):
            if idx == len(income_txns) - 1:
                # Last transaction gets any rounding difference
                txn_charity_share = remaining_charity
            else:
                txn_gross = txn.get_total_amount()
                first_rev = txn.entries.filter(account__account_type=AccountType.REVENUE).first()
                rate = first_rev.exchange_rate if first_rev else Decimal('1.000000')
                txn_gross_in_proj = (txn_gross * rate) / project_rate
                txn_charity_share = (txn_gross_in_proj / total_billed * total_charity).quantize(Decimal('0.01'))
                remaining_charity -= txn_charity_share
            
            # Convert txn_charity_share from project currency back to transaction currency
            first_rev = txn.entries.filter(account__account_type=AccountType.REVENUE).first()
            rate = first_rev.exchange_rate if first_rev else Decimal('1.000000')
            txn_charity_share_in_txn_curr = (txn_charity_share * project_rate) / rate
            txn_charity_share_in_txn_curr = txn_charity_share_in_txn_curr.quantize(Decimal('0.01'))
            
            # Sync this single transaction's ledger entries with the overridden charity amount
            txn.sync_ledger_entries(charity_override=txn_charity_share_in_txn_curr)

    @property
    def total_invoiced(self):
        """Calculates total revenue billed for this project in its native currency."""
        if self.project_type == 'Fixed':
            from .models import LedgerEntry, AccountType
            from django.db.models import Sum
            
            # Sum amounts directly — entries are stored in the transaction's native currency.
            # Since project income typically flows through a bank in the same currency as the project,
            # we sum the raw amounts. For cross-currency entries, we convert via exchange rates.
            entries = LedgerEntry.objects.filter(
                transaction__project=self,
                account__account_type=AccountType.REVENUE,
                entry_type='CR'
            ).select_related('transaction__currency')
            
            total = Decimal('0.00')
            project_rate = self.currency.rate_to_pkr or Decimal('1.000000')
            
            for entry in entries:
                entry_currency = entry.transaction.currency
                if entry_currency == self.currency:
                    # Same currency as the project — use raw amount directly
                    total += entry.amount
                else:
                    # Different currency — convert to PKR via exchange_rate, then to project currency
                    pkr_amount = entry.amount * entry.exchange_rate
                    total += pkr_amount / project_rate
            
            return total.quantize(Decimal('0.01'))
            
        if self.project_type == 'Subscription':
            if not self.start_date:
                return Decimal('0.00')
            
            # Determine end point
            if self.status in ['Completed', 'Cancelled']:
                end_point = self.last_billed_date or self.end_date or timezone.now().date()
            else:
                end_point = timezone.now().date()
            
            total = Decimal('0.00')
            curr = self.start_date.replace(day=1)
            end_m = end_point.replace(day=1)
            
            # Prefetch related to avoid N+1 inside property (caller should still prefetch)
            fee_changes = list(self.subscription_changes.all())
            pauses = list(self.pause_periods.all())
            
            while curr <= end_m:
                # 1. Check if this month is paused (if pause covers the 1st of the month)
                is_paused = False
                for p in pauses:
                    p_start = p.pause_start.replace(day=1)
                    p_end = (p.resume_date.replace(day=1) if p.resume_date else end_m)
                    if p_start <= curr <= p_end:
                        is_paused = True
                        break
                
                if not is_paused:
                    # 2. Determine fee for this month
                    active_fee = self.monthly_fee # Default
                    applicable_change = None
                    for change in fee_changes:
                        if change.effective_date.replace(day=1) <= curr:
                            applicable_change = change
                    
                    if applicable_change:
                        active_fee = applicable_change.new_fee
                    elif fee_changes:
                        # If before any recorded change, use the previous_fee of the first change
                        active_fee = fee_changes[0].previous_fee
                            
                    total += active_fee
                
                if curr.month == 12:
                    curr = curr.replace(year=curr.year + 1, month=1)
                else:
                    curr = curr.replace(month=curr.month + 1)
            
            return total.quantize(Decimal('0.01'))
            
        return Decimal('0.00')
            
    @property
    def total_paid(self):
        """Calculates total payments received for this project in its native currency."""
        # Sum up all Bank DR entries, handling cross-currency correctly
        entries = LedgerEntry.objects.filter(
            transaction__project=self,
            account__account_type=AccountType.ASSET,
            account__bank_detail__isnull=False,
            entry_type='DR'
        ).select_related('transaction__currency')
        
        total = Decimal('0.00')
        project_rate = self.currency.rate_to_pkr or Decimal('1.000000')
        
        for entry in entries:
            entry_currency = entry.transaction.currency
            if entry_currency == self.currency:
                # Same currency as the project — use raw amount directly
                total += entry.amount
            else:
                # Different currency — convert to PKR via exchange_rate, then to project currency
                pkr_amount = entry.amount * entry.exchange_rate
                total += pkr_amount / project_rate
        
        return total.quantize(Decimal('0.01'))


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
    def days_until_next_billing(self):
        if self.project_type != 'Subscription':
            return None
        
        today = timezone.now().date()
        current_month_start = today.replace(day=1)
        
        # If not billed this month, it's due (0 days left)
        if not self.last_billed_date or self.last_billed_date < current_month_start:
            return 0
        
        # If billed this month, next billing is 1st of next month
        if today.month == 12:
            next_month_start = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month_start = today.replace(month=today.month + 1, day=1)
            
        return (next_month_start - today).days

    @property
    def subscription_progress(self):
        if self.project_type != 'Subscription':
            return 0
            
        today = timezone.now().date()
        current_month_start = today.replace(day=1)
        
        # If not billed this month, progress is 100% (due)
        if not self.last_billed_date or self.last_billed_date < current_month_start:
            return 100
            
        # If billed this month, calculate progress through the month
        days_in_month = calendar.monthrange(today.year, today.month)[1]

        
        # Percentage of the month that has passed
        elapsed = today.day
        progress = (elapsed / days_in_month) * 100
        return min(100, max(0, progress))

    @property
    def days_remaining(self):
        if self.project_type == 'Subscription':
            return self.days_until_next_billing
            
        if self.end_date:
            today = timezone.now().date()
            return (self.end_date - today).days
        return None

    @property
    def time_percentage(self):
        if self.project_type == 'Subscription':
            return self.subscription_progress
            
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

    def __str__(self):
        return self.name

class ProjectSubscriptionChange(models.Model):
    """
    Tracks changes in monthly fees for subscription projects to prevent retroactive budget inflation.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='subscription_changes')
    previous_fee = models.DecimalField(max_digits=15, decimal_places=2)
    new_fee = models.DecimalField(max_digits=15, decimal_places=2)
    effective_date = models.DateField(default=timezone.now, help_text="The date from which the new fee applies")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['effective_date']

    def __str__(self):
        return f"{self.project.name}: {self.previous_fee} -> {self.new_fee} (Eff: {self.effective_date})"

class ProjectPausePeriod(models.Model):
    """
    Tracks periods when a subscription project was on hold to exclude them from billed months.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='pause_periods')
    pause_start = models.DateField(default=timezone.now)
    resume_date = models.DateField(null=True, blank=True, help_text="Leave blank if currently on hold")
    reason = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.project.name} Paused: {self.pause_start} to {self.resume_date or 'Present'}"

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
    can_manage_charity = models.BooleanField(default=False)
    can_manage_subscriptions = models.BooleanField(default=False)
    can_view_dashboard = models.BooleanField(default=False)
    can_view_all_data = models.BooleanField(default=False)
    profile_picture = models.ImageField(upload_to='users/profiles/', blank=True, null=True)
    
    def save(self, *args, **kwargs):
        if self.profile_picture:
            compress_image(self.profile_picture)
        super().save(*args, **kwargs)

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
    
    # Project-specific metadata
    tax_amount = models.DecimalField(max_digits=19, decimal_places=2, default=0)
    charity_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, blank=True, null=True)
    project_leader = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='led_transactions')
    commission_type = models.CharField(max_length=20, choices=[('Percentage', 'Percentage'), ('Fixed', 'Fixed')], default='Percentage')
    commission_value = models.DecimalField(max_digits=19, decimal_places=2, default=0)



    def save(self, *args, **kwargs):
        if self.receipt:
            compress_image(self.receipt)
        super().save(*args, **kwargs)
        if self.project:
            self.project.sync_project_financials()

    def delete(self, *args, **kwargs):
        project = self.project
        super().delete(*args, **kwargs)
        if project:
            project.sync_project_financials()

    def sync_ledger_entries(self, category_acc=None, bank_ledger_acc=None, amount=None, charity_override=None):
        """
        Reconstructs or updates all LedgerEntry objects for this transaction to ensure
        tax, charity split, and category alignments are correct.
        """
        from .models import LedgerEntry, Account, AccountType
        from decimal import Decimal
        
        # 1. Identify bank, charity, and category accounts from existing entries if not passed
        existing_entries = list(self.entries.select_related('account').all())
        
        charity_revenue_acc = Account.objects.filter(name__icontains='Charity', account_type=AccountType.REVENUE, is_active=True).first()
        charity_expense_acc = Account.objects.filter(name__icontains='Charity', account_type=AccountType.EXPENSE, is_active=True).first()
        charity_account_ids = []
        if charity_revenue_acc: charity_account_ids.append(charity_revenue_acc.id)
        if charity_expense_acc: charity_account_ids.append(charity_expense_acc.id)
        
        detected_bank_acc = None
        detected_charity_acc = None
        detected_category_acc = None
        detected_amount = None
        
        for entry in existing_entries:
            if entry.account.account_type == AccountType.ASSET and hasattr(entry.account, 'bank_detail'):
                detected_bank_acc = entry.account
                if detected_amount is None:
                    detected_amount = entry.amount
            elif entry.account.id in charity_account_ids:
                detected_charity_acc = entry.account
            elif entry.account.account_type in [AccountType.REVENUE, AccountType.EXPENSE, AccountType.EQUITY]:
                detected_category_acc = entry.account
        
        # Use passed parameters or fallbacks
        bank_acc = bank_ledger_acc or detected_bank_acc
        category_acc = category_acc or detected_category_acc
        
        if detected_amount is None:
            detected_amount = Decimal('0.00')
            for entry in existing_entries:
                if entry.amount > detected_amount:
                    detected_amount = entry.amount
                    
        amount = amount or detected_amount
        
        if not bank_acc or not category_acc:
            # Nothing to sync
            return
            
        # Determine rates and currencies
        currency = self.currency or bank_acc.currency
        exchange_rate = currency.rate_to_pkr if currency else Decimal('1.000000')
        
        # Check transaction type: Income (Revenue/Equity credit) vs Expense (Expense debit)
        is_income = category_acc.account_type in [AccountType.REVENUE, AccountType.EQUITY]
        
        # Determine the charity account based on transaction type
        charity_acc = charity_revenue_acc if is_income else charity_expense_acc
        
        # Recalculate tax and charity amounts
        tax_amount = self.tax_amount or Decimal('0.00')
        charity_percentage = self.charity_percentage or Decimal('0.00')
        
        charity_amt = Decimal('0.00')
        if charity_override is not None:
            charity_amt = charity_override
        elif charity_percentage > 0 and charity_acc:
            if is_income:
                base_for_charity = max(Decimal('0.00'), amount - tax_amount)
                charity_amt = (base_for_charity * charity_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
            else:
                charity_amt = (amount * charity_percentage / Decimal('100.00')).quantize(Decimal('0.01'))
                
        remaining_amt = amount - charity_amt
        
        # We need up to 3 entries: bank_entry, category_entry, charity_entry.
        # Clean delete and recreate to be 100% robust and orphan-free
        self.entries.all().delete()
        
        # 1. Create Bank Entry
        LedgerEntry.objects.create(
            transaction=self,
            account=bank_acc,
            entry_type='DR' if is_income else 'CR',
            amount=amount,
            exchange_rate=exchange_rate
        )
        
        # 2. Create Category and Charity Entries
        if charity_amt > 0 and charity_acc:
            if category_acc == charity_acc:
                # If main category is Charity, put the charity portion on Charity
                LedgerEntry.objects.create(
                    transaction=self,
                    account=charity_acc,
                    entry_type='CR' if is_income else 'DR',
                    amount=charity_amt,
                    exchange_rate=exchange_rate
                )
                # Remaining goes to fallback
                fallback_acc = Account.objects.filter(
                    account_type=category_acc.account_type, is_active=True
                ).exclude(id=charity_acc.id).first()
                if fallback_acc and remaining_amt > 0:
                    LedgerEntry.objects.create(
                        transaction=self,
                        account=fallback_acc,
                        entry_type='CR' if is_income else 'DR',
                        amount=remaining_amt,
                        exchange_rate=exchange_rate
                    )
                elif remaining_amt > 0:
                    # Put remainder on charity if no fallback
                    LedgerEntry.objects.create(
                        transaction=self,
                        account=charity_acc,
                        entry_type='CR' if is_income else 'DR',
                        amount=remaining_amt,
                        exchange_rate=exchange_rate
                    )
            else:
                # Normal split
                if remaining_amt > 0:
                    LedgerEntry.objects.create(
                        transaction=self,
                        account=category_acc,
                        entry_type='CR' if is_income else 'DR',
                        amount=remaining_amt,
                        exchange_rate=exchange_rate
                    )
                if charity_amt > 0:
                    LedgerEntry.objects.create(
                        transaction=self,
                        account=charity_acc,
                        entry_type='CR' if is_income else 'DR',
                        amount=charity_amt,
                        exchange_rate=exchange_rate
                    )
        else:
            # Single entry for the whole amount
            LedgerEntry.objects.create(
                transaction=self,
                account=category_acc,
                entry_type='CR' if is_income else 'DR',
                amount=amount,
                exchange_rate=exchange_rate
            )

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

    def get_lead_commission_amount(self):
        """
        Calculates the lead commission following the new sequence:
        1. Gross Income - Tax
        2. Minus Charity (calculated on Gross-Tax)
        3. Calculate Lead % on the remaining balance
        
        If linked to a project, incorporates project-level expenses into the sequence:
        1. Total Income - Total Taxes - Total Expenses
        2. Minus Charity (calculated on the net)
        3. Calculate Lead % on the remaining balance
        """
        if self.commission_value <= 0:
            return Decimal('0.00')
            
        gross = self.get_total_amount()
        base_after_tax = max(Decimal('0.00'), gross - self.tax_amount)
        
        project = self.project
        if project:
            # 1. Total Income (total_billed)
            total_billed = project.total_invoiced
            if total_billed <= 0:
                return Decimal('0.00')
                
            total_tax = Decimal('0.00')
            total_spent = Decimal('0.00')
            project_rate = project.currency.rate_to_pkr or Decimal('1.000000')
            
            from .models import AccountType
            project_txns = list(project.transactions.prefetch_related('entries__account').all())
            income_txns = []
            for txn in project_txns:
                if txn.entries.filter(account__account_type=AccountType.REVENUE).exists():
                    income_txns.append(txn)
                    
            for txn in project_txns:
                first_rev = txn.entries.filter(account__account_type=AccountType.REVENUE).first()
                rate = first_rev.exchange_rate if first_rev else Decimal('1.000000')
                
                if txn in income_txns:
                    total_tax += (txn.tax_amount * rate) / project_rate
                    
                for entry in txn.entries.all():
                    if entry.account.account_type == AccountType.EXPENSE and entry.entry_type == 'DR':
                        total_spent += (entry.amount * entry.exchange_rate) / project_rate
            
            total_spent = total_spent.quantize(Decimal('0.01'))
            total_tax = total_tax.quantize(Decimal('0.01'))
            
            # Apply sequential split on project level:
            net_before_deductions = max(Decimal('0.00'), total_billed - total_tax - total_spent)
            
            # Find the max charity percentage in transactions (project-level logic)
            charity_pct = Decimal('0.00')
            for txn in project_txns:
                if txn.charity_percentage and txn.charity_percentage > charity_pct:
                    charity_pct = txn.charity_percentage
                    
            total_charity = (net_before_deductions * charity_pct / Decimal('100.00')).quantize(Decimal('0.01'))
            
            if self.commission_type == 'Fixed':
                return self.commission_value
                
            base_for_commission = max(Decimal('0.00'), net_before_deductions - total_charity)
            total_commission = (base_for_commission * self.commission_value / Decimal('100.00')).quantize(Decimal('0.01'))
            
            # This transaction's proportional share:
            first_rev = self.entries.filter(account__account_type=AccountType.REVENUE).first()
            rate = first_rev.exchange_rate if first_rev else Decimal('1.000000')
            gross_in_proj = (gross * rate) / project_rate
            
            return (gross_in_proj / total_billed * total_commission).quantize(Decimal('0.01'))
            
        # Fallback to transaction level split if not linked to any project
        charity_pct = self.charity_percentage or Decimal('0.00')
        charity_amt = (base_after_tax * charity_pct / Decimal('100.00')).quantize(Decimal('0.01'))
        
        base_for_lead = max(Decimal('0.00'), base_after_tax - charity_amt)
        
        if self.commission_type == 'Fixed':
            return self.commission_value
            
        return (base_for_lead * self.commission_value / Decimal('100.00')).quantize(Decimal('0.01'))



    def get_base_total_amount(self):
        """Returns the total amount of the transaction converted to the base currency (PKR)"""
        # We use the exchange rate from the first ledger entry
        entry = self.entries.first()
        if entry:
            return self.get_total_amount() * entry.exchange_rate
        return self.get_total_amount()

    def get_base_lead_commission_amount(self):
        """Returns the lead commission amount converted to the base currency (PKR)"""
        entry = self.entries.first()
        if entry:
            return self.get_lead_commission_amount() * entry.exchange_rate
        return self.get_lead_commission_amount()

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

class ActivityLog(models.Model):
    """
    Audit trail for tracking system events and user actions.
    """
    ACTION_CHOICES = [
        ('Create', 'Create'),
        ('Update', 'Update'),
        ('Delete', 'Delete'),
        ('Auth', 'Authentication'),
        ('System', 'System Event'),
        ('Error', 'Error'),
    ]
    
    timestamp = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action_type = models.CharField(max_length=50, choices=ACTION_CHOICES)
    description = models.TextField()
    related_object_id = models.PositiveIntegerField(null=True, blank=True)
    related_object_type = models.CharField(max_length=100, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name_plural = "Activity Logs"

    def __str__(self):
        return f"{self.timestamp} - {self.actor} - {self.action_type}"

class SystemConfiguration(models.Model):
    """
    Global software identity and branding settings.
    """
    software_name = models.CharField(max_length=255, default="Qonkar ERP")
    software_logo = models.ImageField(upload_to='system/branding/', blank=True, null=True)
    favicon = models.ImageField(upload_to='system/branding/', blank=True, null=True)
    footer_copyright = models.CharField(max_length=500, default="© 2024 Qonkar Finance. All rights reserved.")
    brand_color = models.CharField(max_length=7, default="#0F172A", help_text="Primary theme color (Hex)")
    accent_color = models.CharField(max_length=7, default="#10B981", help_text="Accent/Success color (Hex)")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "System Configuration"
        verbose_name_plural = "System Configuration"

    def save(self, *args, **kwargs):
        if self.software_logo:
            compress_image(self.software_logo)
        if self.favicon:
            compress_image(self.favicon)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Config: {self.software_name}"
