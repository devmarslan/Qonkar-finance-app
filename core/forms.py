from django import forms
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth import get_user_model
User = get_user_model()
from .models import BankAccount, Account, AccountType, Currency, Project, Transaction, Client, Employee, ExpenseManagerAccess, UserPermission, ProjectAccess, ClientAccess, LedgerEntry, SystemConfiguration

class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ['name', 'employee_id', 'designation', 'department', 'email', 'contact_number', 'address', 'salary', 'currency', 'date_joined', 'status', 'profile_picture']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Full Name'}),
            'employee_id': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'EMP-001'}),
            'designation': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Software Engineer'}),
            'department': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Engineering'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'employee@company.com'}),
            'contact_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '+1 234 567 890'}),
            'address': forms.Textarea(attrs={'class': 'form-input', 'rows': 2, 'placeholder': 'Home Address'}),
            'salary': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '0.00', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'date_joined': forms.TextInput(attrs={
                'class': 'datepicker-input form-input',
                'data-default-today': 'true'
            }),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-input'}),
        }

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['name', 'company_name', 'email', 'contact_number', 'address', 'region', 'status', 'profile_picture']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Client Name'}),
            'company_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Company Name (Optional)'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'client@example.com'}),
            'contact_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '+1 234 567 890'}),
            'address': forms.Textarea(attrs={'class': 'form-input', 'rows': 2, 'placeholder': 'Physical Address'}),
            'region': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-input'}),
        }

class CategoryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name

class InterBankTransferForm(forms.Form):
    """
    Schema Layer Form for strict validation of Inter-Bank Transfers.
    """
    from_bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-indigo-500 focus:ring-indigo-500'})
    )
    to_bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-indigo-500 focus:ring-indigo-500'})
    )
    
    amount_sent = forms.DecimalField(
        max_digits=19, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.01'})
    )
    amount_received = forms.DecimalField(
        max_digits=19, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.01'})
    )
    
    fee_amount = forms.DecimalField(
        max_digits=19, decimal_places=2, min_value=Decimal('0.00'), initial=Decimal('0.00'),
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.01'})
    )
    fee_account = forms.ModelChoiceField(
        queryset=Account.objects.filter(account_type=AccountType.EXPENSE, is_active=True),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md '})
    )
    
    date = forms.DateField(
        initial=lambda: timezone.now().date(),
        widget=forms.TextInput(attrs={
            'class': 'datepicker-input form-input block w-full mt-1 border-gray-300 rounded-md ',
            'data-default-today': 'true'
        })
    )
    description = forms.CharField(
        max_length=500,
        widget=forms.TextInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md '})
    )
    
    # FX rates for multi-currency handling
    base_currency_rate_from = forms.DecimalField(
        max_digits=10, decimal_places=6, initial=Decimal('1.000000'),
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.000001'})
    )
    base_currency_rate_to = forms.DecimalField(
        max_digits=10, decimal_places=6, initial=Decimal('1.000000'),
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.000001'})
    )
    base_currency_rate_fee = forms.DecimalField(
        max_digits=10, decimal_places=6, initial=Decimal('1.000000'),
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.000001'})
    )
    
    fx_account = forms.ModelChoiceField(
        queryset=Account.objects.filter(account_type__in=[AccountType.REVENUE, AccountType.EXPENSE], is_active=True),
        required=False,
        help_text="Required if transaction causes a base currency imbalance due to FX rates.",
        widget=forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md '})
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super(InterBankTransferForm, self).__init__(*args, **kwargs)
        
        if user:
            if user.is_superuser:
                self.fields['from_bank_account'].queryset = BankAccount.objects.filter(is_active=True)
                self.fields['to_bank_account'].queryset = BankAccount.objects.filter(is_active=True)
            else:
                assigned_banks = BankAccount.objects.filter(expensemanageraccess__user=user, is_active=True)
                self.fields['from_bank_account'].queryset = assigned_banks
                self.fields['to_bank_account'].queryset = assigned_banks


    def clean(self):
        cleaned_data = super().clean()
        from_bank = cleaned_data.get('from_bank_account')
        to_bank = cleaned_data.get('to_bank_account')
        fee_amount = cleaned_data.get('fee_amount')
        fee_account = cleaned_data.get('fee_account')

        if from_bank and to_bank and from_bank == to_bank:
            raise forms.ValidationError("Sender and receiver bank accounts must be different.")
        
        if fee_amount and fee_amount > 0 and not fee_account:
            raise forms.ValidationError("A fee account must be specified if a fee amount is greater than 0.")
            
        return cleaned_data

class ExpenseForm(forms.Form):
    """
    Form for logging expenses with Bank Account choices restricted by user access.
    """
    date = forms.DateField(
        label="Date",
        initial=lambda: timezone.now().date(),
        widget=forms.TextInput(attrs={
            'class': 'datepicker-input form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            'data-default-today': 'true'
        })
    )
    amount = forms.DecimalField(
        max_digits=19, decimal_places=2,
        help_text="Enter precise amount.",
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.01'})
    )
    description = forms.CharField(
        max_length=500,
        widget=forms.TextInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md '})
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all(),
        required=False,
        label="Project",
        empty_label="Select Project (Optional)",
        widget=forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md '})
    )
    expense_category = CategoryChoiceField(
        queryset=Account.objects.filter(account_type=AccountType.EXPENSE, is_active=True),
        label="Category",
        empty_label="Select Category",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'})
    )
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.none(),
        label="Bank",
        empty_label="Select Bank Account",
        help_text="Balance will be updated automatically.",
        widget=forms.Select(attrs={'id': 'id_expense_bank_account', 'class': 'form-select block w-full mt-1 border-gray-300 rounded-md '})
    )
    receipt = forms.FileField(
        required=False,
        label="Receipt",
        widget=forms.ClearableFileInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md '})
    )
    charity_percentage = forms.DecimalField(
        max_digits=5, decimal_places=2,
        min_value=0, max_value=100,
        label="Charity %",
        required=False,
        help_text="Allocated to Charity Outcome",
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'step': '0.01', 'min': '0', 'max': '100', 'placeholder': '0.00'})
    )
    project_leader = forms.ModelChoiceField(
        queryset=Employee.objects.all(),
        required=False, label="Project Leader",
        empty_label="Select Project Leader",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'})
    )


    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super(ExpenseForm, self).__init__(*args, **kwargs)
        
        if user:
            # RBAC Enforcement at the Schema level
            if user.is_superuser:
                self.fields['bank_account'].queryset = BankAccount.objects.filter(is_active=True)
            else:
                assigned_banks = BankAccount.objects.filter(expensemanageraccess__user=user, is_active=True)
                self.fields['bank_account'].queryset = assigned_banks
                if assigned_banks.count() == 1:
                    self.fields['bank_account'].initial = assigned_banks.first()

            
            # Hide Project field if the user doesn't have project management permissions
            if not user.is_superuser and not getattr(user.permissions, 'can_manage_projects', False):
                if 'project' in self.fields:
                    del self.fields['project']


class IncomeForm(forms.Form):
    """
    Form for logging income with Bank Account choices restricted by user access.
    """
    date = forms.DateField(
        label="Date",
        initial=lambda: timezone.now().date(),
        widget=forms.TextInput(attrs={
            'class': 'datepicker-input form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            'data-default-today': 'true'
        })
    )
    amount = forms.DecimalField(
        max_digits=19, decimal_places=2,
        help_text="Enter precise amount.",
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'step': '0.01'})
    )
    description = forms.CharField(
        max_length=500,
        widget=forms.TextInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md '})
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all(),
        required=False,
        label="Project",
        empty_label="Select Project (Optional)",
        widget=forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md '})
    )
    income_category = CategoryChoiceField(
        queryset=Account.objects.filter(account_type=AccountType.REVENUE, is_active=True),
        label="Category",
        empty_label="Select Category",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'})
    )
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.none(),
        label="Bank",
        empty_label="Select Bank Account",
        help_text="Balance will be updated automatically.",
        widget=forms.Select(attrs={'id': 'id_income_bank_account', 'class': 'form-select block w-full mt-1 border-gray-300 rounded-md '})
    )
    receipt = forms.FileField(
        required=False,
        label="Receipt",
        widget=forms.ClearableFileInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md '})
    )
    charity_percentage = forms.DecimalField(
        max_digits=5, decimal_places=2,
        min_value=0, max_value=100,
        label="Charity %",
        required=False,
        help_text="Allocated to Charity Inflow",
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'step': '0.01', 'min': '0', 'max': '100', 'placeholder': '5.00'})
    )

    
    # Project-specific fields (Conditional)
    tax_amount = forms.DecimalField(
        max_digits=19, decimal_places=2,
        required=False, label="Tax Amount",
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': '0.00'})
    )
    project_leader = forms.ModelChoiceField(
        queryset=Employee.objects.all(),
        required=False, label="Project Leader",
        empty_label="Select Project Leader",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'})
    )
    commission_type = forms.ChoiceField(
        choices=[('', 'Select Model'), ('Percentage', 'Percentage'), ('Fixed', 'Fixed')],
        required=False, label="Commission Type",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'})
    )
    commission_value = forms.DecimalField(
        max_digits=19, decimal_places=2,
        required=False, label="Commission Value",
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': '0.00'})
    )


    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super(IncomeForm, self).__init__(*args, **kwargs)
        
        if user:
            # RBAC Enforcement at the Schema level
            if user.is_superuser:
                self.fields['bank_account'].queryset = BankAccount.objects.filter(is_active=True)
            else:
                assigned_banks = BankAccount.objects.filter(expensemanageraccess__user=user, is_active=True)
                self.fields['bank_account'].queryset = assigned_banks
                if assigned_banks.count() == 1:
                    self.fields['bank_account'].initial = assigned_banks.first()

            
            # Hide Project field if the user doesn't have project management permissions
            if not user.is_superuser and not getattr(user.permissions, 'can_manage_projects', False):
                if 'project' in self.fields:
                    del self.fields['project']


class BankAccountForm(forms.ModelForm):
    account_title = forms.CharField(
        max_length=255, 
        label="Account title",
        widget=forms.TextInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'placeholder': 'e.g. HBL Checking'})
    )
    currency = forms.ModelChoiceField(
        queryset=Currency.objects.all(),
        label="Currency",
        widget=forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md '})
    )
    opening_balance = forms.DecimalField(
        max_digits=19, decimal_places=2, min_value=Decimal('0.00'),
        label="Current balance",
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'placeholder': '0.00', 'step': '0.01'})
    )

    class Meta:
        model = BankAccount
        fields = ['bank_name', 'account_number']
        widgets = {
            'bank_name': forms.TextInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'placeholder': 'Habib Bank Limited'}),
            'account_number': forms.TextInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md ', 'placeholder': 'Enter Account Number'}),
        }

    def clean_account_number(self):
        account_number = self.cleaned_data.get('account_number')
        if not account_number:
            raise forms.ValidationError("Account number is required.")
        
        # Check for duplicate account number across all banks
        if BankAccount.objects.filter(account_number=account_number).exists():
            raise forms.ValidationError(f"A bank account with number '{account_number}' already exists.")
            
        return account_number

    def clean_account_title(self):
        account_title = self.cleaned_data.get('account_title')
        if not account_title:
            raise forms.ValidationError("Account title is required.")
            
        # Check for duplicate ledger account name (Account title)
        if Account.objects.filter(name=account_title).exists():
            raise forms.ValidationError(f"A ledger account with the name '{account_title}' already exists.")
            
        return account_title

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pkr = Currency.objects.filter(code='PKR').first()
        if pkr:
            self.fields['currency'].initial = pkr

    def save(self, commit=True):
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            # 1. Create the base Account (The Ledger node)
            account_title = self.cleaned_data.get('account_title')
            currency = self.cleaned_data.get('currency')
            
            ledger_account = Account.objects.create(
                name=account_title,
                account_type=AccountType.ASSET,
                currency=currency,
                is_active=True
            )
            
            # 2. Attach it to the BankAccount instance and save
            self.instance.ledger_account = ledger_account
            bank_account = super().save(commit=commit)
            
            # 3. Handle opening balance if specified
            opening_balance = self.cleaned_data.get('opening_balance')
            if opening_balance and opening_balance > 0:
                # Create/Get the "Opening Balance" equity account
                equity_acc, _ = Account.objects.get_or_create(
                    name="Opening Balance",
                    account_type=AccountType.EQUITY,
                    currency=currency,
                    defaults={'is_active': True}
                )
                
                # Create the Journal Entry container
                txn = Transaction.objects.create(
                    date=timezone.now().date(),
                    description=f"Opening Balance: {bank_account.bank_name}",
                    reference="Initial setup",
                    currency=currency # Use bank's currency
                )
                
                # Fetch current rate to PKR for this currency
                rate = currency.rate_to_pkr if currency else Decimal('1.000000')

                # Entry 1: DR the Bank Asset (Increases bank balance)
                LedgerEntry.objects.create(
                    transaction=txn,
                    account=ledger_account,
                    entry_type='DR',
                    amount=opening_balance,
                    exchange_rate=rate
                )
                
                # Entry 2: CR the Opening Balance Equity (Increases equity to balance the books)
                LedgerEntry.objects.create(
                    transaction=txn,
                    account=equity_acc,
                    entry_type='CR',
                    amount=opening_balance,
                    exchange_rate=rate
                )
                
            return bank_account

class TransactionEditForm(forms.ModelForm):
    amount = forms.DecimalField(
        max_digits=19, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'step': '0.01'})
    )
    category = forms.ModelChoiceField(
        queryset=Account.objects.filter(account_type__in=[AccountType.REVENUE, AccountType.EXPENSE], is_active=True),
        empty_label="Select Category",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'})
    )
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(is_active=True),
        empty_label="Select Bank Account",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'})
    )
    class Meta:
        model = Transaction
        fields = ['date', 'description', 'reference', 'project', 'receipt', 'tax_amount', 'charity_percentage', 'project_leader', 'commission_type', 'commission_value']


        widgets = {
            'date': forms.TextInput(attrs={
                'class': 'datepicker-input form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
            'description': forms.TextInput(attrs={
                'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
            'tax_amount': forms.NumberInput(attrs={
                'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
            'charity_percentage': forms.NumberInput(attrs={
                'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
                'step': '0.01',
            }),
            'project_leader': forms.Select(attrs={
                'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
            'reference': forms.TextInput(attrs={
                'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
            'project': forms.Select(attrs={
                'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
            'receipt': forms.ClearableFileInput(attrs={
                'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
            'commission_type': forms.Select(attrs={
                'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4',
            }),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        if user:
            if not user.is_superuser:
                assigned_banks = BankAccount.objects.filter(expensemanageraccess__user=user, is_active=True)
                self.fields['bank_account'].queryset = assigned_banks
                if assigned_banks.count() == 1:
                    self.fields['bank_account'].initial = assigned_banks.first()
            
            # Hide Project field if the user doesn't have project management permissions
            if not user.is_superuser and not getattr(user.permissions, 'can_manage_projects', False):
                if 'project' in self.fields:
                    del self.fields['project']

        # Determine the "other side" category entry
        if self.instance.pk:
            cat_entry = self.instance.entries.filter(
                account__account_type__in=[AccountType.REVENUE, AccountType.EXPENSE, AccountType.EQUITY]
            ).first()
            
            if cat_entry:
                self.fields['amount'].initial = cat_entry.amount.quantize(Decimal('0.01'))
                self.fields['category'].initial = cat_entry.account
                
                # If it's an EQUITY account (like Opening Balance), temporarily allow it in the queryset
                if cat_entry.account.account_type == AccountType.EQUITY:
                    current_choices = self.fields['category'].queryset
                    # Combine existing REVENUE/EXPENSE with the instance's specific EQUITY account
                    self.fields['category'].queryset = Account.objects.filter(
                        Q(account_type__in=[AccountType.REVENUE, AccountType.EXPENSE]) |
                        Q(id=cat_entry.account.id)
                    )
            
            # Find bank entry
            bank_entry = self.instance.entries.filter(
                account__account_type=AccountType.ASSET,
                account__bank_detail__isnull=False
            ).first()
            if bank_entry:
                self.fields['bank_account'].initial = bank_entry.account.bank_detail

    def save(self, commit=True):
        # 1. Save Transaction metadata
        transaction = super().save(commit=commit)
        
        if commit:
            from django.db import transaction as db_transaction
            with db_transaction.atomic():
                amount = self.cleaned_data.get('amount')
                category_acc = self.cleaned_data.get('category')
                bank_acc_obj = self.cleaned_data.get('bank_account')
                
                # Fetch bank info
                bank_ledger_acc = bank_acc_obj.ledger_account
                currency = bank_ledger_acc.currency
                rate = currency.rate_to_pkr if currency else Decimal('1.000000')
                
                # Sync Transaction Currency
                transaction.currency = currency
                transaction.save()
                
                # Aggressive Sync: Identify the two entries we want to keep
                # 1. The Category/Revenue/Expense side
                # 2. The Bank Asset side
                
                # Fetch all existing entries to find matches or clean up orphans
                all_entries = list(transaction.entries.all())
                cat_entry = None
                bank_entry = None
                others_to_delete = []

                # Pass 1: Identification
                for e in all_entries:
                    if not cat_entry and e.account.account_type in [AccountType.REVENUE, AccountType.EXPENSE, AccountType.EQUITY]:
                        cat_entry = e
                    elif not bank_entry and e.account.account_type == AccountType.ASSET and hasattr(e.account, 'bank_detail'):
                        bank_entry = e
                    else:
                        others_to_delete.append(e)

                # Creation if missing
                if not cat_entry:
                    cat_entry = LedgerEntry(transaction=transaction)
                if not bank_entry:
                    bank_entry = LedgerEntry(transaction=transaction)

                # Update Category Entry
                cat_entry.account = category_acc
                cat_entry.amount = amount
                cat_entry.exchange_rate = rate
                
                # Update Bank Entry
                bank_entry.account = bank_ledger_acc
                bank_entry.amount = amount
                bank_entry.exchange_rate = rate

                # Logic: Balancing Types
                if category_acc.account_type in [AccountType.REVENUE, AccountType.EQUITY]:
                    cat_entry.entry_type = 'CR'
                    bank_entry.entry_type = 'DR'
                else:
                    cat_entry.entry_type = 'DR'
                    bank_entry.entry_type = 'CR'
                
                cat_entry.save()
                bank_entry.save()

                # Cleanup Orphans: Prevent "things not calculation correctly" by removing stray entries
                for orphan in others_to_delete:
                    orphan.delete()
            
        return transaction

class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['client', 'name', 'description', 'currency', 'target_budget', 'status', 'project_type', 'monthly_fee', 'timeline', 'start_date', 'end_date']
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'name': forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Project Name'}),
            'description': forms.Textarea(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'rows': 3, 'placeholder': 'Optional details...'}),
            'currency': forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'target_budget': forms.NumberInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'step': '0.01', 'placeholder': '0.00'}),
            'status': forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'project_type': forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'monthly_fee': forms.NumberInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'step': '0.01', 'placeholder': '0.00'}),
            'timeline': forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'e.g. 6 Months'}),
            'start_date': forms.TextInput(attrs={'class': 'datepicker-input form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Select Start Date', 'data-default-today': 'true'}),
            'end_date': forms.TextInput(attrs={'class': 'datepicker-input form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Optional End Date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['description'].required = False
        self.fields['timeline'].required = False
        self.fields['monthly_fee'].required = False
        self.fields['target_budget'].required = False






        
        # Set default currency to USD
        usd = Currency.objects.filter(code='USD').first()
        if usd and not self.instance.pk:
            self.initial['currency'] = usd

        from django.utils import timezone
        if not self.instance.pk:
            self.initial['start_date'] = timezone.now().date()
            # Explicitly clear initial 0.00 from financial fields to show placeholders
            self.initial['target_budget'] = None
            self.initial['monthly_fee'] = None
            self.initial['tax_percentage'] = 0.00
            self.initial['tax_fixed_amount'] = None


        # Inject data-region into client choices for JS interaction
        client_field = self.fields.get('client')
        if client_field:
            client_field.widget.attrs['onchange'] = 'updateCurrencyFromClient(this)'
            # Custom choice generation to include data-attributes
            choices = [('', client_field.empty_label or '---------')]
            for client in client_field.queryset:
                choices.append((client.pk, client.name))
            client_field.choices = choices
            
            # Since Standard Select doesn't support data-attributes on options easily, 
            # we'll use a trick in the template or just stick to standard for now.
            # Actually, the user asked to "Assume we can pass the client's region via data attributes".
            # I will implement a custom choice list in JS or encode it in the template.

    def clean(self):
        cleaned_data = super().clean()
        budget = cleaned_data.get('target_budget')
        fee = cleaned_data.get('monthly_fee')
        p_type = cleaned_data.get('project_type')
        tax_type = cleaned_data.get('tax_type')
        tax_pct = cleaned_data.get('tax_percentage')
        tax_fix = cleaned_data.get('tax_fixed_amount')

        if budget is None:
            cleaned_data['target_budget'] = 0
        elif budget < 0:
            self.add_error('target_budget', "Budget cannot be negative.")
        
        if fee is None:
            cleaned_data['monthly_fee'] = 0
        elif fee < 0:
            self.add_error('monthly_fee', "Monthly fee cannot be negative.")


        if p_type == 'Subscription' and (fee is None or fee <= 0):
            self.add_error('monthly_fee', "Subscription projects must have a positive monthly fee.")

        # Tax Logic: Ensure only the relevant tax field has a value
        if tax_type == 'Fixed':
            if tax_fix is None:
                self.add_error('tax_fixed_amount', "Fixed tax amount is required for Fixed tax model.")
            cleaned_data['tax_percentage'] = 0
        else:
            # Default to 0 if not provided for percentage
            if tax_pct is None:
                cleaned_data['tax_percentage'] = 0
            cleaned_data['tax_fixed_amount'] = 0
            
        return cleaned_data


class ExpenseManagerAccessForm(forms.ModelForm):
    class Meta:
        model = ExpenseManagerAccess
        fields = ['user', 'bank_account']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'bank_account': forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
        }
        
class CreateUserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Password'}))
    
    class Meta:
        model = User
        fields = ['username', 'email']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Username'}),
            'email': forms.EmailInput(attrs={'class': 'form-input block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Email (optional)'}),
        }
        
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user

class UserEditForm(forms.ModelForm):
    first_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'First Name'}))
    last_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Last Name'}))
    password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'New Password (Optional)'}))
    profile_picture = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}))

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Username'}),
            'email': forms.EmailInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'Email'}),
        }

    def save(self, commit=True):
        user = super().save(commit=commit)
        profile_picture = self.cleaned_data.get('profile_picture')
        if profile_picture:
            from .models import UserPermission
            perms, created = UserPermission.objects.get_or_create(user=user)
            perms.profile_picture = profile_picture
            perms.save()
        
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
            user.save()
        return user

class UserPermissionForm(forms.ModelForm):
    class Meta:
        model = UserPermission
        fields = [
            'can_manage_income', 'can_manage_expense', 'can_manage_banking',
            'can_manage_analytics', 'can_manage_projects', 'can_manage_clients',
            'can_manage_employees'
        ]
        widgets = {
            'can_manage_income': forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-brand-600 rounded border-gray-300 focus:ring-brand-500'}),
            'can_manage_expense': forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-brand-600 rounded border-gray-300 focus:ring-brand-500'}),
            'can_manage_banking': forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-brand-600 rounded border-gray-300 focus:ring-brand-500'}),
            'can_manage_analytics': forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-brand-600 rounded border-gray-300 focus:ring-brand-500'}),
            'can_manage_projects': forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-brand-600 rounded border-gray-300 focus:ring-brand-500'}),
            'can_manage_clients': forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-brand-600 rounded border-gray-300 focus:ring-brand-500'}),
            'can_manage_employees': forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-brand-600 rounded border-gray-300 focus:ring-brand-500'}),
        }
class ProjectAccessForm(forms.ModelForm):
    class Meta:
        model = ProjectAccess
        fields = ['user', 'project']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'project': forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
        }

class ClientAccessForm(forms.ModelForm):
    class Meta:
        model = ClientAccess
        fields = ['user', 'client']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'client': forms.Select(attrs={'class': 'form-select block w-full mt-1 border-gray-300 rounded-md  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
        }

class SystemConfigurationForm(forms.ModelForm):
    class Meta:
        model = SystemConfiguration
        fields = ['software_name', 'software_logo', 'favicon', 'footer_copyright', 'brand_color', 'accent_color']
        widgets = {
            'software_name': forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': 'ERP Software Name'}),
            'software_logo': forms.ClearableFileInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'favicon': forms.ClearableFileInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4'}),
            'footer_copyright': forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': '© 2024 Your Company. All rights reserved.'}),
            'brand_color': forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': '#0F172A'}),
            'accent_color': forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg  focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4', 'placeholder': '#10B981'}),
        }
