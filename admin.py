from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from import_export import resources
from .models import Currency, Client, Project, Account, BankAccount, ExpenseManagerAccess, Transaction, LedgerEntry, Employee

class EmployeeResource(resources.ModelResource):
    class Meta:
        model = Employee

class BankAccountResource(resources.ModelResource):
    class Meta:
        model = BankAccount

class ProjectResource(resources.ModelResource):
    class Meta:
        model = Project

class TransactionResource(resources.ModelResource):
    class Meta:
        model = Transaction

@admin.register(BankAccount)
class BankAccountAdmin(ImportExportModelAdmin):
    resource_classes = [BankAccountResource]
    list_display = ('bank_name', 'account_number', 'is_active')
    search_fields = ('bank_name', 'account_number')

    def delete_model(self, request, obj):
        from .models import Transaction, LedgerEntry, Account
        ledger_acc_id = obj.ledger_account_id
        # 1. Purge transactions (Cascades to LedgerEntries)
        txn_ids = list(LedgerEntry.objects.filter(account_id=ledger_acc_id).values_list('transaction_id', flat=True))
        Transaction.objects.filter(id__in=txn_ids).delete()
        
        # 2. Delete Bank (Removes protection from Account)
        super().delete_model(request, obj)
        
        # 3. Delete Account
        Account.objects.filter(id=ledger_acc_id).delete()

    def delete_queryset(self, request, queryset):
        from .models import Transaction, LedgerEntry, Account
        for obj in queryset:
            ledger_acc_id = obj.ledger_account_id
            # 1. Purge transactions
            txn_ids = list(LedgerEntry.objects.filter(account_id=ledger_acc_id).values_list('transaction_id', flat=True))
            Transaction.objects.filter(id__in=txn_ids).delete()
            
            # 2. Capture and delete Account after Bank is gone
            # Note: queryset.delete() happens later, but we can delete individual accounts here 
            # if we delete the protecting bank first.
            obj.delete() # Manual delete to trigger immediate removal of protection
            Account.objects.filter(id=ledger_acc_id).delete()
        
        # The remaining objects in queryset (if any) are deleted by super or handled.
        # But we already manually deleted them in the loop to be safe.

@admin.register(Project)
class ProjectAdmin(ImportExportModelAdmin):
    resource_classes = [ProjectResource]
    list_display = ('name', 'client', 'currency', 'target_budget')
    search_fields = ('name', 'client__name')

@admin.register(Transaction)
class TransactionAdmin(ImportExportModelAdmin):
    resource_classes = [TransactionResource]
    list_display = ('id', 'date', 'description', 'reference', 'project')
    list_filter = ('date', 'project')
    search_fields = ('description', 'reference')

@admin.register(Employee)
class EmployeeAdmin(ImportExportModelAdmin):
    resource_classes = [EmployeeResource]
    list_display = ('name', 'employee_id', 'designation', 'department', 'salary', 'status')
    list_filter = ('status', 'department')
    search_fields = ('name', 'employee_id', 'designation')

admin.site.register(Currency)
admin.site.register(Client)
admin.site.register(Account)
admin.site.register(ExpenseManagerAccess)
admin.site.register(LedgerEntry)
