from django.shortcuts import render, get_object_or_404, redirect, reverse
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F
import csv
import base64
from .forms import InterBankTransferForm, ExpenseForm, IncomeForm, BankAccountForm, TransactionEditForm, EmployeeForm
from .models import Transaction, Project, Account, AccountType, BankAccount, LedgerEntry, Client, Currency, Employee
from .services import perform_inter_bank_transfer
from .filters import TransactionFilter
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.db import transaction as db_transaction
from django.utils import timezone
from datetime import datetime

@login_required
def transaction_export_preview_view(request):
    """
    Renders a modal showing the transactions that will be exported.
    """
    txn_list = Transaction.objects.all().prefetch_related('entries__account', 'project').order_by('-date', '-created_at')
    if not request.user.is_superuser:
        txn_list = txn_list.filter(
            Q(created_by=request.user) | 
            Q(entries__account__bank_detail__expensemanageraccess__user=request.user)
        ).distinct()
    txn_filter = TransactionFilter(request.GET, queryset=txn_list)
    qs = txn_filter.qs
    
    # Passing the full query string as a single parameter to easier confirmation
    query_string = request.GET.urlencode()
    
    return render(request, 'core/partials/transaction_export_preview.html', {
        'transactions': qs,
        'query_string': query_string
    })

@login_required
def inter_bank_transfer_view(request):
    """
    Thin view/router layer to handle inter-bank transfer form rendering and submission via HTMX.
    """
    if request.method == 'POST':
        form = InterBankTransferForm(request.POST, user=request.user)

        if form.is_valid():
            data = form.cleaned_data
            try:
                txn = perform_inter_bank_transfer(
                    from_bank_account_id=data['from_bank_account'].id,
                    to_bank_account_id=data['to_bank_account'].id,
                    amount_sent=data['amount_sent'],
                    amount_received=data['amount_received'],
                    fee_amount=data.get('fee_amount') or Decimal('0.00'),
                    fee_account_id=data['fee_account'].id if data.get('fee_account') else None,
                    date=data['date'],
                    description=data['description'],
                    user_id=request.user.id,
                    base_currency_rate_from=data.get('base_currency_rate_from') or Decimal('1.000000'),
                    base_currency_rate_to=data.get('base_currency_rate_to') or Decimal('1.000000'),
                    base_currency_rate_fee=data.get('base_currency_rate_fee') or Decimal('1.000000'),
                    fx_account_id=data['fx_account'].id if data.get('fx_account') else None
                )
                
                # HTMX Response for Inline UI updates
                if request.headers.get('HX-Request'):
                    return render(request, 'core/partials/transfer_success.html', {'transaction': txn})
                
                # Fallback for standard synchronous requests
                return render(request, 'core/transfer_page.html', {
                    'form': InterBankTransferForm(user=request.user),
                    'success_message': f'Transfer successful. Transaction ID: {txn.id}'
                })

            
            except ValidationError as e:
                # Add validation errors from the Service layer directly to the form
                for message in e.messages:
                    form.add_error(None, message)
        
        # If form is invalid or service raised validation error
        if request.headers.get('HX-Request'):
            return render(request, 'core/partials/transfer_form.html', {'form': form})
            
    else:
        form = InterBankTransferForm(user=request.user)


    return render(request, 'core/transfer_page.html', {'form': form})

@login_required
def project_dashboard_view(request, pk):
    """
    Project Dashboard tracking multi-currency financial milestones.
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Calculate project financials based on transactions linked to this project
    transactions = Transaction.objects.filter(project=project).prefetch_related('entries__account', 'currency')
    
    if not request.user.is_superuser:
        transactions = transactions.filter(
            Q(created_by=request.user) | 
            Q(entries__account__bank_detail__expensemanageraccess__user=request.user)
        ).distinct()

    
    total_spent_pkr = Decimal('0.00')
    total_billed_pkr = Decimal('0.00')
    total_tax_pkr = Decimal('0.00')
    total_charity_pkr = Decimal('0.00')
    total_commission_pkr = Decimal('0.00')
    
    project_invoices = []
    project_expenses = []
    
    for txn in transactions:
        is_income = False
        is_expense = False
        
        # Calculate metadata-based deductions (Tax, Commission)
        # These are typically associated with Income transactions
        first_entry = txn.entries.first()
        txn_rate = first_entry.exchange_rate if first_entry else Decimal('1.000000')
        
        has_revenue = txn.entries.filter(account__account_type=AccountType.REVENUE).exists()
        if has_revenue:
            total_tax_pkr += (txn.tax_amount * txn_rate)
            total_commission_pkr += (txn.get_lead_commission_amount() * txn_rate)

        for entry in txn.entries.all():
            if entry.account.account_type == AccountType.REVENUE:
                if entry.entry_type == 'CR':
                    total_billed_pkr += entry.get_base_amount()
                    is_income = True
                    # If this is a charity-specific revenue account
                    if 'Charity' in entry.account.name:
                        total_charity_pkr += entry.get_base_amount()
            elif entry.account.account_type == AccountType.EXPENSE:
                if entry.entry_type == 'DR':
                    # If this is a charity-specific expense account
                    if 'Charity' in entry.account.name:
                        total_charity_pkr += entry.get_base_amount()
                    else:
                        total_spent_pkr += entry.get_base_amount()
                    is_expense = True
                
        if is_income:
            project_invoices.append(txn)
        elif is_expense:
            project_expenses.append(txn)
    
    # Convert PKR totals to Project's Native Currency
    project_rate = project.currency.rate_to_pkr or Decimal('1.000000')
    total_billed = (total_billed_pkr / project_rate).quantize(Decimal('0.01'))
    total_spent = (total_spent_pkr / project_rate).quantize(Decimal('0.01'))
    total_tax = (total_tax_pkr / project_rate).quantize(Decimal('0.01'))
    total_charity = (total_charity_pkr / project_rate).quantize(Decimal('0.01'))
    total_commission = (total_commission_pkr / project_rate).quantize(Decimal('0.01'))

    
    # Handle Fixed vs Subscription budget
    display_budget = project.target_budget if project.project_type == 'Fixed' else project.monthly_fee
    
    budget_remaining = (project.target_budget - total_billed) if project.project_type == 'Fixed' else Decimal('0.00')
    
    # Financial Waterfall
    # Gross Profit = Total Billed - Direct Project Expenses
    gross_profit = total_billed - total_spent
    # Final Net Profit = Gross Profit - Tax - Charity - Commission
    final_profit = gross_profit - total_tax - total_charity - total_commission

    
    # Progress Bar based on type
    if project.project_type == 'Fixed':
        billed_percentage = (total_billed / project.target_budget * 100) if project.target_budget > 0 else 0
    else:
        billed_percentage = 100 if total_billed >= project.monthly_fee else (total_billed / project.monthly_fee * 100) if project.monthly_fee > 0 else 0
        
    if billed_percentage > 100: billed_percentage = 100

    # Project Timeline Logic
    today = timezone.now().date()
    days_total = project.days_total
    days_elapsed = project.days_elapsed
    days_remaining = project.days_remaining
    time_percentage = project.time_percentage


    context = {
        'project': project,
        'transactions': transactions.order_by('-date')[:10],
        'project_invoices': project_invoices[:5],
        'project_expenses': project_expenses[:5],
        'total_spent': total_spent,
        'total_billed': total_billed,
        'total_tax': total_tax,
        'total_charity': total_charity,
        'total_commission': total_commission,
        'gross_profit': gross_profit,
        'final_profit': final_profit,
        'margin_index': (final_profit / total_billed * 100) if total_billed > 0 else 0,
        'budget_remaining': budget_remaining,


        'billed_percentage': billed_percentage,
        'display_budget': display_budget,
        'days_total': days_total,
        'days_elapsed': days_elapsed,
        'days_remaining': days_remaining,
        'time_percentage': time_percentage,
    }
    return render(request, 'core/project_dashboard.html', context)

@login_required
def get_dashboard_context(request, is_global=False):
    from .models import LedgerEntry, AccountType, Transaction, Account, Project
    from .filters import TransactionFilter
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Sum, Q, F
    from django.core.paginator import Paginator
    from decimal import Decimal

    today = timezone.now().date()
    # current month start
    cm_start = today.replace(day=1)
    # previous month start
    pm_end = cm_start - timedelta(days=1)
    pm_start = pm_end.replace(day=1)

    # Shared filter for accessible transactions to avoid doubling in joins
    def get_accessible_txns(start_date=None, end_date=None):
        txn_qs = Transaction.objects.all()
        if start_date and end_date:
            txn_qs = txn_qs.filter(date__range=[start_date, end_date])
        if not is_global:
            txn_qs = txn_qs.filter(
                Q(created_by=request.user) | 
                Q(entries__account__bank_detail__expensemanageraccess__user=request.user)
            ).distinct()
        return txn_qs

    # Helper for KPI Calculations
    def get_total_for_type(atype, start_date, end_date):
        txns = get_accessible_txns(start_date, end_date)
        return LedgerEntry.objects.filter(
            account__account_type=atype,
            transaction__in=txns
        ).annotate(
            base_amt=F('amount') * F('exchange_rate')
        ).aggregate(total=Sum('base_amt'))['total'] or Decimal('0.00')

    income_curr = get_total_for_type(AccountType.REVENUE, cm_start, today)
    pmtd_end = pm_start + timedelta(days=today.day - 1)
    if pmtd_end > pm_end: pmtd_end = pm_end
    
    income_prev = get_total_for_type(AccountType.REVENUE, pm_start, pmtd_end)
    expense_curr = get_total_for_type(AccountType.EXPENSE, cm_start, today)
    expense_prev = get_total_for_type(AccountType.EXPENSE, pm_start, pmtd_end)

    # Total Assets
    all_accessible_txns = get_accessible_txns()
    
    # Filter asset accounts based on assigned banks
    asset_accounts = Account.objects.filter(account_type=AccountType.ASSET, is_active=True)
    if not is_global:
        asset_accounts = asset_accounts.filter(bank_detail__expensemanageraccess__user=request.user).distinct()
        
    total_assets_pkr = Decimal('0.00')
    for account in asset_accounts:
        qs = LedgerEntry.objects.filter(account=account, transaction__in=all_accessible_txns)
        
        debits = qs.filter(entry_type=LedgerEntry.DR).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        credits = qs.filter(entry_type=LedgerEntry.CR).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        balance = debits - credits
        total_assets_pkr += balance * account.currency.rate_to_pkr

    def calc_growth(curr, prev):
        if prev == 0: return 100 if curr > 0 else 0
        return round(((curr - prev) / prev) * 100, 1)

    income_growth = calc_growth(income_curr, income_prev)
    expense_growth = calc_growth(expense_curr, expense_prev)

    # Categorical Breakdown
    curr_month_txns = get_accessible_txns(cm_start, today)
    
    income_by_cat = LedgerEntry.objects.filter(
        account__account_type=AccountType.REVENUE, 
        transaction__in=curr_month_txns
    ).annotate(base_amt=F('amount') * F('exchange_rate')).values('account__name').annotate(total=Sum('base_amt')).order_by('-total')[:5]

    total_income_sum = sum(i['total'] for i in income_by_cat) or Decimal('1.00')
    for item in income_by_cat:
        item['percent'] = round((item['total'] / total_income_sum) * 100, 1)

    expense_by_cat = LedgerEntry.objects.filter(
        account__account_type=AccountType.EXPENSE, 
        transaction__in=curr_month_txns
    ).annotate(base_amt=F('amount') * F('exchange_rate')).values('account__name').annotate(total=Sum('base_amt')).order_by('-total')[:7]

    total_expense_sum = sum(i['total'] for i in expense_by_cat) or Decimal('1.00')
    for item in expense_by_cat:
        item['percent'] = round((item['total'] / total_expense_sum) * 100, 1)

    # Chart Data
    last_7_days = []
    prev_7_days = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        p_day = today - timedelta(days=i+7)
        last_7_days.append({
            'label': day.strftime('%a'),
            'income': get_total_for_type(AccountType.REVENUE, day, day),
            'expense': get_total_for_type(AccountType.EXPENSE, day, day),
        })
        prev_7_days.append({'expense': get_total_for_type(AccountType.EXPENSE, p_day, p_day)})

    month_daily_data = []
    current_day = cm_start
    while current_day <= today:
        month_daily_data.append({
            'label': current_day.strftime('%d %b'),
            'income': get_total_for_type(AccountType.REVENUE, current_day, current_day),
            'expense': get_total_for_type(AccountType.EXPENSE, current_day, current_day),
        })
        current_day += timedelta(days=1)

    # Transaction Feed
    txn_list = Transaction.objects.all().prefetch_related('entries__account', 'project', 'entries__account__bank_detail').order_by('-date', '-created_at')
    if not is_global:
        txn_list = txn_list.filter(Q(created_by=request.user) | Q(entries__account__bank_detail__expensemanageraccess__user=request.user)).distinct()
    
    txn_filter = TransactionFilter(request.GET, queryset=txn_list)
    paginator = Paginator(txn_filter.qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    # User Performance Analytics (Leaderboard style)
    user_analytics = []
    if is_global:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        # Fetch all users who have ever contributed or are active leads
        contributing_users = User.objects.filter(
            Q(pk__in=Transaction.objects.values_list('created_by', flat=True)) |
            Q(email__in=Employee.objects.values_list('email', flat=True)) |
            Q(username__in=Employee.objects.values_list('name', flat=True))
        ).distinct()
        
        for u in contributing_users:
            # All-time Income
            u_income = LedgerEntry.objects.filter(
                transaction__created_by=u,
                account__account_type=AccountType.REVENUE,
                entry_type=LedgerEntry.CR
            ).annotate(base_amt=F('amount') * F('exchange_rate')).aggregate(total=Sum('base_amt'))['total'] or Decimal('0.00')

            # All-time Outcome
            u_outcome = LedgerEntry.objects.filter(
                transaction__created_by=u,
                account__account_type=AccountType.EXPENSE,
                entry_type=LedgerEntry.DR
            ).annotate(base_amt=F('amount') * F('exchange_rate')).aggregate(total=Sum('base_amt'))['total'] or Decimal('0.00')

            # Project Stats (Linked via Employee Email, Username or Full Name)
            employee = None
            if u.email:
                employee = Employee.objects.filter(email=u.email).first()
            if not employee and u.username:
                employee = Employee.objects.filter(name__icontains=u.username).first()
            if not employee and (u.first_name or u.last_name):
                full_name = f"{u.first_name} {u.last_name}".strip()
                employee = Employee.objects.filter(name__icontains=full_name).first()

            if employee:
                user_projects = Project.objects.filter(Q(project_lead=employee) | Q(created_by=u)).distinct()
            else:
                user_projects = Project.objects.filter(created_by=u).distinct()
            
            if user_projects.exists():


                completed_count = user_projects.filter(status='Completed').count()
                in_progress_count = user_projects.filter(status='Active / In Progress').count()
                completed_value_pkr = user_projects.filter(status__in=['Completed', 'Active / In Progress']).annotate(
                    pkr_val=F('target_budget') * F('currency__rate_to_pkr')
                ).aggregate(total=Sum('pkr_val'))['total'] or Decimal('0.00')

                subscription_count = user_projects.filter(project_type='Subscription').count()
            else:
                completed_count = 0
                in_progress_count = 0
                subscription_count = 0
                completed_value_pkr = Decimal('0.00')

            # Combined score for ranking: Transaction Income + Project Value
            total_performance_score = u_income + completed_value_pkr

            if total_performance_score > 0 or u_outcome > 0:
                user_analytics.append({
                    'user': u,
                    'income': u_income,
                    'outcome': u_outcome,
                    'net': u_income - u_outcome,
                    'completed_projects': completed_count,
                    'in_progress_projects': in_progress_count,
                    'subscription_projects': subscription_count,
                    'completed_value': completed_value_pkr,
                    'performance_score': total_performance_score,
                })

        
        # Sort by total performance score descending and assign ranks
        user_analytics = sorted(user_analytics, key=lambda x: x['performance_score'], reverse=True)
        max_score = max([x['performance_score'] for x in user_analytics]) if user_analytics else Decimal('1.00')
        max_outcome = max([x['outcome'] for x in user_analytics]) if user_analytics else Decimal('1.00')

        for i, item in enumerate(user_analytics):
            item['rank'] = i + 1
            item['income_percent'] = round((item['performance_score'] / max_score) * 100, 1) if max_score > 0 else 0
            item['outcome_percent'] = round((item['outcome'] / max_outcome) * 100, 1) if max_outcome > 0 else 0

    return {
        'total_assets': total_assets_pkr,
        'income_curr': income_curr,
        'income_growth': income_growth,
        'income_prev': income_prev,
        'expense_curr': expense_curr,
        'expense_growth': expense_growth,
        'expense_prev': expense_prev,
        'income_by_cat': income_by_cat,
        'expense_by_cat': expense_by_cat,
        'last_7_days': last_7_days,
        'prev_7_days': prev_7_days,
        'month_daily_data': month_daily_data,
        'active_projects_list': Project.objects.filter(status='Active / In Progress').filter(Q() if is_global else (Q(created_by=request.user) | Q(projectaccess__user=request.user))).distinct().order_by('end_date'),
        'filter': txn_filter,
        'page_obj': page_obj,
        'user_analytics': user_analytics,
        'is_htmx': request.headers.get('HX-Request') is not None,
        'is_global': is_global
    }

@login_required
def dashboard_view(request):
    if request.user.is_superuser:
        process_monthly_billings(request.user)
    
    # By default, show company if permitted, else personal
    if request.user.is_superuser or getattr(request.user.permissions, 'can_view_all_data', False):
        return redirect('core:company_dashboard')
    return redirect('core:personal_dashboard')

@login_required
def company_dashboard_view(request):
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_view_all_data', False):
        return HttpResponseForbidden("You do not have permission to access the Company Dashboard.")
    
    context = get_dashboard_context(request, is_global=True)
    if context['is_htmx']:
        return render(request, 'core/partials/transaction_list.html', context)
    return render(request, 'core/dashboard.html', context)

@login_required
def personal_dashboard_view(request):
    context = get_dashboard_context(request, is_global=False)
    if context['is_htmx']:
        return render(request, 'core/partials/transaction_list.html', context)
    return render(request, 'core/dashboard.html', context)

@login_required
def expense_view(request):
    """
    RBAC enforced Expense logging view.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_expense', False):
        return HttpResponseForbidden("You do not have permission to access the Expense page.")

    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            # Create the expense transaction
            data = form.cleaned_data
            
            from django.db import transaction
            with transaction.atomic():
                bank_acc = data['bank_account']
                currency = bank_acc.ledger_account.currency
                
                txn = Transaction.objects.create(
                    date=data['date'],
                    description=data['description'],
                    reference="Direct Expense",
                    project=data.get('project'),
                    receipt=data.get('receipt'),
                    currency=currency,
                    created_by=request.user,
                    # Attribution
                    project_leader=data.get('project_leader')
                )

                
                # Fetch/Calculate exchange rate to base currency
                exchange_rate = Decimal('1.000000')
                if currency and not currency.is_base:
                    # Use the stored rate to PKR (assuming base is PKR or this is the rate intended)
                    exchange_rate = currency.rate_to_pkr
                
                # Handle Charity Percentage Split
                charity_pct = data.get('charity_percentage', Decimal('0.00'))
                total_amount = data['amount']
                
                charity_expense_acc = Account.objects.filter(name__icontains='Charity', account_type=AccountType.EXPENSE, is_active=True).first()
                
                # Credit Bank Account (Asset decreases) for FULL amount
                LedgerEntry.objects.create(
                    transaction=txn,
                    account=data['bank_account'].ledger_account,
                    entry_type=LedgerEntry.CR,
                    amount=total_amount,
                    exchange_rate=exchange_rate
                )
                
                # Always honour the charity percentage
                if charity_pct > 0 and charity_expense_acc:
                    charity_amt = (total_amount * charity_pct / Decimal('100.00')).quantize(Decimal('0.01'))
                    remaining_amt = total_amount - charity_amt
                    
                    if data['expense_category'] == charity_expense_acc:
                        # User picked "Charity" as the main category
                        # Charity portion stays on Charity account
                        LedgerEntry.objects.create(
                            transaction=txn,
                            account=charity_expense_acc,
                            entry_type=LedgerEntry.DR,
                            amount=charity_amt,
                            exchange_rate=exchange_rate
                        )
                        # Remaining goes to a generic Expense account (first non-Charity expense)
                        if remaining_amt > 0:
                            fallback_acc = Account.objects.filter(
                                account_type=AccountType.EXPENSE, is_active=True
                            ).exclude(id=charity_expense_acc.id).first()
                            if fallback_acc:
                                LedgerEntry.objects.create(
                                    transaction=txn,
                                    account=fallback_acc,
                                    entry_type=LedgerEntry.DR,
                                    amount=remaining_amt,
                                    exchange_rate=exchange_rate
                                )
                            else:
                                LedgerEntry.objects.create(
                                    transaction=txn,
                                    account=charity_expense_acc,
                                    entry_type=LedgerEntry.DR,
                                    amount=remaining_amt,
                                    exchange_rate=exchange_rate
                                )
                    else:
                        # Normal split: selected category + Charity
                        LedgerEntry.objects.create(
                            transaction=txn,
                            account=data['expense_category'],
                            entry_type=LedgerEntry.DR,
                            amount=remaining_amt,
                            exchange_rate=exchange_rate
                        )
                        LedgerEntry.objects.create(
                            transaction=txn,
                            account=charity_expense_acc,
                            entry_type=LedgerEntry.DR,
                            amount=charity_amt,
                            exchange_rate=exchange_rate
                        )
                else:
                    # No charity split — single entry
                    LedgerEntry.objects.create(
                        transaction=txn,
                        account=data['expense_category'],
                        entry_type=LedgerEntry.DR,
                        amount=total_amount,
                        exchange_rate=exchange_rate
                    )
            
            if request.headers.get('HX-Request'):
                recent_expenses = Transaction.objects.filter(
                    entries__account__account_type=AccountType.EXPENSE
                )
                if not request.user.is_superuser:
                    recent_expenses = recent_expenses.filter(
                        Q(created_by=request.user) | 
                        Q(entries__account__bank_detail__expensemanageraccess__user=request.user)
                    ).distinct()
                recent_expenses = recent_expenses.prefetch_related('entries__account', 'project', 'entries__account__bank_detail').order_by('-date', '-created_at')[:5]
                
                # Recalculate monthly expenses for OOB update
                from django.utils import timezone
                now = timezone.now()
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_expenses_qs = LedgerEntry.objects.filter(
                    account__account_type=AccountType.EXPENSE,
                    transaction__date__gte=month_start
                )
                if not request.user.is_superuser:
                    monthly_expenses_qs = monthly_expenses_qs.filter(transaction__created_by=request.user)
                monthly_expenses = monthly_expenses_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

                return render(request, 'core/partials/expense_success.html', {
                    'transaction': txn,
                    'recent_expenses': recent_expenses,
                    'monthly_expenses': monthly_expenses
                })
            
            return redirect('core:expense')
    else:
        initial_data = {}
        if request.GET.get('project'):
            initial_data['project'] = request.GET.get('project')
        if request.GET.get('project_leader'):
            initial_data['project_leader'] = request.GET.get('project_leader')
        form = ExpenseForm(initial=initial_data, user=request.user)


    
    expense_categories = Account.objects.filter(account_type=AccountType.EXPENSE, is_active=True).annotate(usage=Count('ledger_entries')).order_by('-usage').values_list('name', flat=True).distinct()
    
    # Fetch recent expense transactions
    recent_expenses = Transaction.objects.filter(
        entries__account__account_type=AccountType.EXPENSE
    )
    if not request.user.is_superuser:
        recent_expenses = recent_expenses.filter(
            Q(created_by=request.user) | 
            Q(entries__account__bank_detail__expensemanageraccess__user=request.user)
        ).distinct()
    recent_expenses = recent_expenses.prefetch_related('entries__account', 'project', 'entries__account__bank_detail').order_by('-date', '-created_at')[:5]

    # Calculate Month-to-Date Expenses
    from django.utils import timezone
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    monthly_expenses_qs = LedgerEntry.objects.filter(
        account__account_type=AccountType.EXPENSE,
        transaction__date__gte=month_start
    )
    if not request.user.is_superuser:
        monthly_expenses_qs = monthly_expenses_qs.filter(transaction__created_by=request.user)
    monthly_expenses = monthly_expenses_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    context = {
        'form': form, 
        'expense_categories': expense_categories,
        'recent_expenses': recent_expenses,
        'monthly_expenses': monthly_expenses
    }
    
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/expense_form.html', context)
        
    return render(request, 'core/expense_page.html', context)

@login_required
def income_view(request):
    """
    RBAC enforced Income logging view.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_income', False):
        return HttpResponseForbidden("You do not have permission to access the Income page.")

    # Shared Context: Handle Client pre-fill
    client_id = request.GET.get('client') or request.POST.get('client_id')
    client = get_object_or_404(Client, pk=client_id) if client_id else None

    if request.method == 'POST':
        form = IncomeForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            
            from django.db import transaction
            with transaction.atomic():
                bank_acc = data['bank_account']
                currency = bank_acc.ledger_account.currency
                
                # Handle Charity Percentage Split
                charity_pct = data.get('charity_percentage', Decimal('0.00'))
                total_amount = data['amount']
                
                txn = Transaction.objects.create(
                    date=data['date'],
                    description=data['description'],
                    reference="Direct Income",
                    project=data.get('project'),
                    receipt=data.get('receipt'),
                    currency=currency,
                    created_by=request.user,
                    # Project-specific metadata
                    tax_amount=data.get('tax_amount') or 0,
                    charity_percentage=charity_pct or 0,
                    project_leader=data.get('project_leader'),
                    commission_type=data.get('commission_type') or 'Percentage',
                    commission_value=data.get('commission_value') or 0
                )




                # Fetch/Calculate exchange rate to base currency
                exchange_rate = Decimal('1.000000')
                if currency and not currency.is_base:
                    exchange_rate = currency.rate_to_pkr
                
                tax_amt = data.get('tax_amount') or Decimal('0.00')

                
                charity_revenue_acc = Account.objects.filter(name__icontains='Charity', account_type=AccountType.REVENUE, is_active=True).first()
                
                # Debit Bank Account (Asset increases) for FULL amount
                LedgerEntry.objects.create(
                    transaction=txn,
                    account=data['bank_account'].ledger_account,
                    entry_type=LedgerEntry.DR,
                    amount=total_amount,
                    exchange_rate=exchange_rate 
                )
                
                # Always honour the charity percentage (calculated on Gross - Tax)
                if charity_pct > 0 and charity_revenue_acc:
                    base_for_charity = max(Decimal('0.00'), total_amount - tax_amt)
                    charity_amt = (base_for_charity * charity_pct / Decimal('100.00')).quantize(Decimal('0.01'))
                    remaining_amt = total_amount - charity_amt

                    
                    if data['income_category'] == charity_revenue_acc:
                        # User picked "Charity" as the main category
                        # Charity portion stays on Charity account
                        LedgerEntry.objects.create(
                            transaction=txn,
                            account=charity_revenue_acc,
                            entry_type=LedgerEntry.CR,
                            amount=charity_amt,
                            exchange_rate=exchange_rate
                        )
                        # Remaining goes to a generic Income account (first non-Charity revenue)
                        if remaining_amt > 0:
                            fallback_acc = Account.objects.filter(
                                account_type=AccountType.REVENUE, is_active=True
                            ).exclude(id=charity_revenue_acc.id).first()
                            if fallback_acc:
                                LedgerEntry.objects.create(
                                    transaction=txn,
                                    account=fallback_acc,
                                    entry_type=LedgerEntry.CR,
                                    amount=remaining_amt,
                                    exchange_rate=exchange_rate
                                )
                            else:
                                # No fallback — put the remainder on Charity too
                                LedgerEntry.objects.create(
                                    transaction=txn,
                                    account=charity_revenue_acc,
                                    entry_type=LedgerEntry.CR,
                                    amount=remaining_amt,
                                    exchange_rate=exchange_rate
                                )
                    else:
                        # Normal split: selected category + Charity
                        LedgerEntry.objects.create(
                            transaction=txn,
                            account=data['income_category'],
                            entry_type=LedgerEntry.CR,
                            amount=remaining_amt,
                            exchange_rate=exchange_rate
                        )
                        LedgerEntry.objects.create(
                            transaction=txn,
                            account=charity_revenue_acc,
                            entry_type=LedgerEntry.CR,
                            amount=charity_amt,
                            exchange_rate=exchange_rate
                        )
                else:
                    # No charity split — single entry
                    LedgerEntry.objects.create(
                        transaction=txn,
                        account=data['income_category'],
                        entry_type=LedgerEntry.CR,
                        amount=total_amount,
                        exchange_rate=exchange_rate
                    )
            
            if request.headers.get('HX-Request'):
                # Refresh recent incomes after successful add
                recent_incomes = Transaction.objects.filter(
                    entries__account__account_type=AccountType.REVENUE
                )
                if not request.user.is_superuser:
                    recent_incomes = recent_incomes.filter(
                        Q(created_by=request.user) | 
                        Q(entries__account__bank_detail__expensemanageraccess__user=request.user)
                    ).distinct()
                recent_incomes = recent_incomes.prefetch_related('entries__account', 'project', 'entries__account__bank_detail').order_by('-date', '-created_at')[:5]
                
                # Recalculate monthly revenue for OOB update
                from django.utils import timezone
                now = timezone.now()
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_revenue_qs = LedgerEntry.objects.filter(
                    account__account_type=AccountType.REVENUE,
                    transaction__date__gte=month_start
                )
                if not request.user.is_superuser:
                    monthly_revenue_qs = monthly_revenue_qs.filter(transaction__created_by=request.user)
                monthly_revenue = monthly_revenue_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

                return render(request, 'core/partials/income_success.html', {
                    'transaction': txn,
                    'recent_incomes': recent_incomes,
                    'monthly_revenue': monthly_revenue
                })
            
            income_categories = Account.objects.filter(account_type=AccountType.REVENUE, is_active=True).annotate(usage=Count('ledger_entries')).order_by('-usage').values_list('name', flat=True).distinct()
            return render(request, 'core/income_page.html', {
                'form': IncomeForm(user=request.user),
                'success_message': f'Income logged. Transaction ID: {txn.id}',
                'income_categories': income_categories
            })
    else:
        initial_data = {}
        if request.GET.get('project'):
            initial_data['project'] = request.GET.get('project')
        
        # Pre-fill project if unique to client
        if client:
            client_projects = client.projects.all()
            if client_projects.count() == 1:
                initial_data['project'] = client_projects.first().id
        
        form = IncomeForm(initial=initial_data, user=request.user)
        
        # If client context exists, restrict project choices
        if client:
            form.fields['project'].queryset = client.projects.all()
            if not initial_data.get('project') and client.projects.exists():
                 form.fields['project'].empty_label = f"Select {client.name}'s Project"
        
    income_categories = Account.objects.filter(account_type=AccountType.REVENUE, is_active=True).annotate(usage=Count('ledger_entries')).order_by('-usage').values_list('name', flat=True).distinct()
    
    # Fetch recent income transactions for the UI
    recent_incomes = Transaction.objects.filter(
        entries__account__account_type=AccountType.REVENUE
    )
    if not request.user.is_superuser:
        recent_incomes = recent_incomes.filter(
            Q(created_by=request.user) | 
            Q(entries__account__bank_detail__expensemanageraccess__user=request.user)
        ).distinct()
    recent_incomes = recent_incomes.prefetch_related('entries__account', 'project', 'entries__account__bank_detail').order_by('-date', '-created_at')[:5]

    # Calculate Month-to-Date Revenue
    from django.utils import timezone
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    monthly_revenue_qs = LedgerEntry.objects.filter(
        account__account_type=AccountType.REVENUE,
        transaction__date__gte=month_start
    )
    if not request.user.is_superuser:
        monthly_revenue_qs = monthly_revenue_qs.filter(transaction__created_by=request.user)
    monthly_revenue = monthly_revenue_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    context = {
        'form': form, 
        'income_categories': income_categories,
        'recent_incomes': recent_incomes,
        'monthly_revenue': monthly_revenue,
        'client': client if 'client' in locals() else None
    }
    
    if request.headers.get('HX-Request'):
        if request.GET.get('modal') == 'true':
            return render(request, 'core/partials/income_modal.html', context)
        return render(request, 'core/partials/income_form.html', context)
        
    return render(request, 'core/income_page.html', context)

@login_required
def bank_account_create_view(request):
    """
    HTMX Modal view to create a new bank account inline.
    """
    if request.method == 'POST':
        form = BankAccountForm(request.POST)
        if form.is_valid():
            bank = form.save()
            
            # Prepare the card for OOB swap
            from .models import LedgerEntry
            balance = LedgerEntry.objects.get_account_balance(bank.ledger_account.id)
            
            # Pre-format balance parts for the deluxe card template
            balance_fmt = "{:,.2f}".format(balance)
            balance_parts = balance_fmt.split('.')
            
            entry = {
                'obj': bank, 
                'balance': balance,
                'balance_int': balance_parts[0],
                'balance_decimal': balance_parts[1],
            }
            
            # Return fresh card and closing modal
            # Note: We return an empty targets to close modals, 
            # and the new card with hx-swap-oob to the grid.
            card_html = render(request, "core/partials/bank_account_card.html", {"entry": entry}).content.decode()
            
            # Fetch all active banks for the select inputs
            banks = BankAccount.objects.filter(is_active=True).order_by('bank_name')
            def get_options(selected_id):
                options_list = ['<option value="">Select Bank Account</option>']
                for b in banks:
                    selected = 'selected' if b.id == selected_id else ''
                    options_list.append(f'<option value="{b.id}" {selected}>{b}</option>')
                return "".join(options_list)
            
            options = get_options(bank.id)
            
            # Recalculate stats for OOB update
            from django.contrib.humanize.templatetags.humanize import intcomma
            active_count = banks.count()
            total_balance_pkr = sum([LedgerEntry.objects.get_account_balance(b.ledger_account.id) * b.ledger_account.currency.rate_to_pkr for b in banks])
            formatted_total = intcomma(f"{total_balance_pkr:.2f}")

            response_html = f'''
                <div id="bank-account-modal" hx-swap-oob="delete"></div>
                <div id="category-modal" hx-swap-oob="delete"></div>
                <div id="modal-container" hx-swap-oob="true"></div>
                <div id="modal-container-stacked" hx-swap-oob="true"></div>
                
                <span id="total-liquidity-value" hx-swap-oob="true" class="text-2xl font-bold text-gray-900 tracking-tight">Rs {formatted_total}</span>
                <span id="active-accounts-count" hx-swap-oob="true" class="text-2xl font-bold text-gray-900 tracking-tight">{active_count}</span>

                <select id="id_income_bank_account" name="bank_account" hx-swap-oob="outerHTML" class="form-select block w-full pl-11 border-gray-200/80 rounded-lg bg-white/50 focus:bg-white focus:border-brand-500 focus:ring-4 focus:ring-brand-500/10 text-sm py-3.5 transition-all shadow-sm cursor-pointer">
                    {options}
                </select>

                <select id="id_expense_bank_account" name="bank_account" hx-swap-oob="outerHTML" class="form-select block w-full pl-11 border-gray-200/80 rounded-lg bg-white/50 focus:bg-white focus:border-amber-500 focus:ring-4 focus:ring-amber-500/10 text-sm py-3.5 transition-all shadow-sm cursor-pointer">
                    {options}
                </select>

                <select id="id_bank_account" name="bank_account" hx-swap-oob="outerHTML" class="form-select block w-full border-gray-200 rounded-lg shadow-sm focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4">
                    {options}
                </select>

                <select id="id_from_bank_account" name="from_bank_account" hx-swap-oob="outerHTML" class="form-select block w-full border-gray-300 rounded-l-md shadow-sm focus:border-brand-500 focus:ring-brand-500">
                    {options}
                </select>

                <select id="id_to_bank_account" name="to_bank_account" hx-swap-oob="outerHTML" class="form-select block w-full border-gray-300 rounded-l-md shadow-sm focus:border-brand-500 focus:ring-brand-500">
                    {options}
                </select>

                <div id="bank-accounts-grid" hx-swap-oob="afterbegin">
                    {card_html}
                </div>
                <div id="toast-container" hx-swap-oob="beforeend">
                    <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded relative mb-2 shadow" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">
                        <strong class="font-bold">Success!</strong>
                        <span class="block sm:inline">{bank.bank_name} has been added and selected.</span>
                    </div>
                </div>
            '''
            return HttpResponse(response_html)
    else:
        form = BankAccountForm()

    return render(request, 'core/partials/bank_account_modal.html', {'form': form})

@login_required
def bank_account_confirm_delete_view(request, pk):
    """
    Renders a confirmation modal for deleting a specific bank account.
    """
    bank = get_object_or_404(BankAccount, pk=pk)
    
    # Calculate balance for display
    balance = LedgerEntry.objects.get_account_balance(bank.ledger_account.id)
    
    # Count real transactions beyond initial setup
    real_txns = Transaction.objects.filter(
        entries__account=bank.ledger_account
    ).exclude(reference="Initial setup").count()
    
    return render(request, 'core/partials/bank_confirm_delete_modal.html', {
        'bank': bank,
        'balance': balance,
        'real_txns': real_txns
    })

@login_required
def bank_account_delete_view(request, pk):
    """
    Deletes a bank account and its linked ledger account if it has no REAL history.
    Otherwise, archives it.
    """
    bank = get_object_or_404(BankAccount, pk=pk)
    
    # Check if there are any REAL transactions (beyond opening balance)
    has_real_history = Transaction.objects.filter(
        entries__account=bank.ledger_account
    ).exclude(reference="Initial setup").exists()
    
    if request.method == 'DELETE' or (request.method == 'POST' and request.POST.get('_method') == 'DELETE'):
        name = bank.bank_name
        ledger_acc = bank.ledger_account
        ledger_acc_id = ledger_acc.id
        
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            # 1. Delete all associated transactions (including history)
            # This is a hard purge as requested by the user.
            txn_ids = list(LedgerEntry.objects.filter(account_id=ledger_acc_id).values_list('transaction_id', flat=True))
            Transaction.objects.filter(id__in=txn_ids).delete()
            
            # 2. Delete the bank and ledger account
            bank.delete()
            Account.objects.filter(id=ledger_acc_id).delete()
            
            msg = f"{name} and all associated transactions have been deleted."

        if request.headers.get('HX-Request'):
            # Recalculate stats for OOB update
            from django.contrib.humanize.templatetags.humanize import intcomma
            
            active_banks = BankAccount.objects.filter(is_active=True).select_related('ledger_account__currency')
            active_count = active_banks.count()
            total_balance_pkr = sum([LedgerEntry.objects.get_account_balance(b.ledger_account.id) * b.ledger_account.currency.rate_to_pkr for b in active_banks])
            
            formatted_total = intcomma(f"{total_balance_pkr:.2f}")
            
            return HttpResponse(f'''
                <div id="modal-container" hx-swap-oob="true"></div>
                <div id="bank-account-{pk}" hx-swap-oob="delete"></div>
                
                <span id="total-liquidity-value" hx-swap-oob="true" class="text-2xl font-bold text-gray-900 tracking-tight">Rs {formatted_total}</span>
                <span id="active-accounts-count" hx-swap-oob="true" class="text-2xl font-bold text-gray-900 tracking-tight">{active_count}</span>

                <div id="toast-container" hx-swap-oob="beforeend">
                    <div class="bg-amber-100 border border-amber-400 text-amber-700 px-4 py-3 rounded relative mb-2 shadow font-bold" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">
                        {msg}
                    </div>
                </div>
            ''', status=200)
            
        return redirect('core:banking_overview')
        
    return HttpResponseBadRequest("Invalid request")

@login_required
def transaction_list_view(request):
    """
    Consolidated history of all income and expenses with filters.
    """
    if not request.user.is_superuser:
        perm = request.user.permissions
        if not (perm.can_manage_income or perm.can_manage_expense):
            return HttpResponseForbidden("You do not have permission to access the Transactions page.")

    # Base Queryset with prefetching
    txn_list = Transaction.objects.all().prefetch_related('entries__account', 'project', 'entries__account__bank_detail').order_by('-date', '-created_at')
    
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_view_all_data', False):
        txn_list = txn_list.filter(created_by=request.user)
    
    # Global Filters
    txn_filter = TransactionFilter(request.GET, queryset=txn_list)
    
    # Pagination
    paginator = Paginator(txn_filter.qs, 20)  # 20 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'filter': txn_filter,
        'page_obj': page_obj,
    }
    
    if request.GET.get('load_more'):
        return render(request, 'core/partials/transaction_rows.html', context)

    if request.headers.get('HX-Request') or request.GET.get('is_htmx'):
        return render(request, 'core/partials/transaction_list.html', context)

    return render(request, 'core/transaction_page.html', context)

@login_required
def transaction_delete_view(request, pk):
    """
    Deletes a transaction and returns a success response for HTMX removal.
    """
    txn = get_object_or_404(Transaction, pk=pk)
    
    # Ownership Check: Non-superusers can only delete their own transactions
    if not request.user.is_superuser and txn.created_by != request.user:
        return HttpResponseForbidden("Oops! You are not authorized to delete this record as it was created by an Admin or another user.")

    if request.method in ['DELETE', 'POST']:
        txn_id = txn.pk
        txn.delete()
        if request.headers.get('HX-Request'):
            response = HttpResponse(
                f'<div id="txn-row-{txn_id}" hx-swap-oob="delete"></div>'
                f'<div id="txn-mobile-{txn_id}" hx-swap-oob="delete"></div>'
            )
            response['HX-Trigger'] = 'transactionUpdated'
            return response
        return redirect('core:transaction_list')
    return HttpResponseBadRequest("Invalid request method")

@login_required
def transaction_confirm_delete_view(request):
    """
    Renders a confirmation modal showing which transactions are about to be deleted.
    """
    txn_ids = request.GET.getlist('transaction_ids')
    if not txn_ids:
        # Check if single ID was passed as a query param
        single_id = request.GET.get('id')
        if single_id:
            txn_ids = [single_id]

    transactions = Transaction.objects.filter(pk__in=txn_ids)
    if not request.user.is_superuser:
        transactions = transactions.filter(created_by=request.user)

    
    return render(request, 'core/partials/transaction_confirm_delete.html', {
        'transactions': transactions,
        'txn_ids': ','.join(map(str, txn_ids))
    })

@login_required
def transaction_delete_multiple_view(request):
    """
    Deletes multiple transactions based on a list of IDs.
    """
    if request.method == 'POST':
        txn_ids = request.POST.getlist('transaction_ids')
        if request.POST.get('select_all_matching') == 'true':
            # Re-run filter logic to target ALL matching records across all pages
            txn_qs = Transaction.objects.all()
            if not request.user.is_superuser and not getattr(request.user.permissions, 'can_view_all_data', False):
                txn_qs = txn_qs.filter(created_by=request.user)
            
            # Re-apply the same filters used in the list view
            txn_filter = TransactionFilter(request.GET, queryset=txn_qs)
            qs = txn_filter.qs
            qs.delete()
        elif txn_ids:
            # Delete selected IDs (filtered by ownership)
            qs = Transaction.objects.filter(pk__in=txn_ids)
            if not request.user.is_superuser:
                qs = qs.filter(created_by=request.user)
            qs.delete()

            
        if request.headers.get('HX-Request'):
            # Return updated transaction list partial and clear modal
            list_response = transaction_list_view(request)
            return HttpResponse(f'<div id="modal-container" hx-swap-oob="true"></div>' + list_response.content.decode())
            
    return redirect('core:transaction_list')

@login_required
def transaction_update_view(request, pk):
    """
    Edits non-financial transaction metadata.
    """
    transaction = get_object_or_404(Transaction, pk=pk)
    
    # Ownership Check: Non-superusers can only edit their own transactions
    if not request.user.is_superuser and transaction.created_by != request.user:
        return HttpResponseForbidden("Oops! You are not authorized to modify this record as it was created by an Admin or another user.")

    if request.method == 'POST':
        form = TransactionEditForm(request.POST, request.FILES, instance=transaction, user=request.user)
        if form.is_valid():
            form.save()
            transaction.refresh_from_db()
            if request.headers.get('HX-Request'):
                # Clear modal and trigger a refresh of the transaction list
                response = HttpResponse('<div id="modal-container" hx-swap-oob="true"></div>')
                response['HX-Trigger'] = 'transactionUpdated'
                return response
            return redirect('core:transaction_list')
    else:
        form = TransactionEditForm(instance=transaction, user=request.user)
    
    return render(request, 'core/partials/transaction_edit_form.html', {'form': form, 'transaction': transaction})

@login_required
def transaction_export_view(request):
    """
    Exports filtered transactions to CSV.
    """
    txn_list = Transaction.objects.all().prefetch_related('entries__account', 'project').order_by('-date', '-created_at')
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_view_all_data', False):
        txn_list = txn_list.filter(created_by=request.user)

    txn_filter = TransactionFilter(request.GET, queryset=txn_list)
    qs = txn_filter.qs

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="transactions_export.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Date', 'Description', 'Reference', 'Project', 'Income Amount', 'Expense Amount', 'Bank Account', 'Category'])
    
    for txn in qs:
        t_type = txn.get_type_display()
        amt = txn.get_total_amount()
        inc = amt if t_type == 'Income' else 0
        exc = amt if t_type == 'Expense' else 0
        
        writer.writerow([
            txn.date,
            txn.description,
            txn.reference,
            txn.project.name if txn.project else 'N/A',
            inc,
            exc,
            txn.get_bank_account_name(),
            txn.get_category_name()
        ])
    return response

EXPECTED_FIELDS = [
    ('date', 'Date', True),
    ('description', 'Description', True),
    ('reference', 'Reference', False),
    ('project', 'Project', False),
    ('income_amount', 'Income Amount', True),
    ('expense_amount', 'Expense Amount', True),
    ('bank_account', 'Bank Account', False),
    ('category', 'Category', False),
]

@login_required
def transaction_import_view(request):
    """
    Receives CSV upload and renders column mapping UI.
    """
    if request.method == 'POST' and request.FILES.get('import_file'):
        csv_file = request.FILES['import_file']
        try:
            csv_content = csv_file.read()
            decoded_file = csv_content.decode('utf-8').splitlines()
            if not decoded_file:
                return HttpResponseBadRequest("Empty file")
                
            try:
                dialect = csv.Sniffer().sniff(decoded_file[0][:1024])
                reader = csv.reader(decoded_file, dialect=dialect)
            except csv.Error:
                reader = csv.reader(decoded_file)
                
            headers = next(reader, [])
            b64_csv = base64.b64encode(csv_content).decode('ascii')
            
            # Extract sample row for better visualization
            try:
                sample_row = next(reader)
            except StopIteration:
                sample_row = []
            
            # Combine header with sample row safely
            header_samples = []
            for i, h in enumerate(headers):
                sample = sample_row[i] if i < len(sample_row) else ''
                header_samples.append({'header': h, 'sample': sample})
                
            from .models import Currency
            currencies = Currency.objects.all()
                
            return render(request, 'core/partials/transaction_import_map.html', {
                'header_samples': header_samples,
                'expected_fields': EXPECTED_FIELDS,
                'b64_csv': b64_csv,
                'currencies': currencies
            })
        except Exception as e:
            return HttpResponse(f"<div class='p-4 bg-rose-50 text-rose-700 rounded-lg my-4 text-center font-bold'>Error reading file: {str(e)}</div>")

    return HttpResponseBadRequest("Invalid request")

@login_required
def transaction_import_process_view(request):
    """
    Processes the CSV with mapped columns.
    """
    if request.method == 'POST' and request.POST.get('b64_csv'):
        b64_csv = request.POST['b64_csv']
        
        # Build mapping from expected key to actual CSV header
        mapping = {}
        for key, _, _ in EXPECTED_FIELDS:
            csv_header = request.POST.get(f"map_{key}")
            if csv_header:
                mapping[key] = csv_header

        currency_id = request.POST.get('currency')
        imported_currency = None
        if currency_id:
            from .models import Currency
            imported_currency = Currency.objects.filter(id=currency_id).first()

        imported_count = 0
        skipped_count = 0
        errors = []
        imported_txns = []
        
        try:
            csv_content = base64.b64decode(b64_csv).decode('utf-8').splitlines()
            try:
                dialect = csv.Sniffer().sniff(csv_content[0][:1024])
                reader = csv.DictReader(csv_content, dialect=dialect)
            except csv.Error:
                reader = csv.DictReader(csv_content)
            
            for i, row in enumerate(reader, 1):
                try:
                    date_str = row.get(mapping.get('date', ''), '').strip()
                    description = row.get(mapping.get('description', ''), '').strip()
                    bank_name = row.get(mapping.get('bank_account', ''), '').strip()
                    category_name = row.get(mapping.get('category', ''), '').strip()
                    reference = row.get(mapping.get('reference', ''), '').strip() if mapping.get('reference') else ''
                    project_name = row.get(mapping.get('project', ''), '').strip() if mapping.get('project') else ''

                    if not date_str:
                        errors.append(f"Row {i}: Missing mapped Date.")
                        skipped_count += 1
                        continue

                    # Parse Date
                    try:
                        if '/' in date_str:
                            dt = datetime.strptime(date_str, '%d/%m/%Y').date()
                        else:
                            dt = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        errors.append(f"Row {i}: Invalid date format '{date_str}'. Use DD/MM/YYYY or YYYY-MM-DD.")
                        skipped_count += 1
                        continue

                    income_str = str(row.get(mapping.get('income_amount', ''), '0')).upper()
                    expense_str = str(row.get(mapping.get('expense_amount', ''), '0')).upper()

                    def process_amt(val_str):
                        val_str = val_str.replace(',', '').strip()
                        is_negative = '-' in val_str or 'DR' in val_str or '(' in val_str
                        is_positive = 'CR' in val_str or '+' in val_str
                        
                        num_str = ''.join(c for c in val_str if c.isdigit() or c == '.')
                        if not num_str: return Decimal('0')
                        try:
                            val = Decimal(num_str)
                            if is_negative: return -val
                            if is_positive: return val
                            return val # Default to positive if no sign
                        except:
                            return Decimal('0')

                    inc_val = process_amt(income_str)
                    exc_val = process_amt(expense_str)

                    # Determine txn_type and valid amount
                    # If the user maps "Amount" column to both Income and Expense
                    if inc_val != 0 and exc_val != 0 and inc_val == exc_val:
                        if inc_val > 0:
                            txn_type = 'Income'
                            amount = inc_val
                        else:
                            txn_type = 'Expense'
                            amount = abs(inc_val)
                    else:
                        # Mapped to separate columns or only one is mapped
                        if inc_val > 0:
                            txn_type = 'Income'
                            amount = inc_val
                        elif inc_val < 0:
                            txn_type = 'Expense'
                            amount = abs(inc_val)
                        elif exc_val > 0:
                            # Typically expenses are positive numbers in an "Expense" column
                            txn_type = 'Expense'
                            amount = exc_val
                        elif exc_val < 0:
                            # Negative expense is essentially income (refund)
                            txn_type = 'Income'
                            amount = abs(exc_val)
                        else:
                            txn_type = None
                            amount = Decimal('0')

                    if amount == 0:
                        errors.append(f"Row {i}: Both Income and Expense amounts are empty or zero.")
                        skipped_count += 1
                        continue

                    # Duplicate check
                    existing = Transaction.objects.filter(date=dt, description=description, reference=reference)
                    is_dup = False
                    for txn in existing:
                        if abs(txn.get_total_amount()) == abs(amount):
                            is_dup = True
                            break
                    if is_dup:
                        skipped_count += 1
                        continue

                    # Auto-fallback Bank Account
                    bank_acc = None
                    if bank_name:
                        bank_acc = BankAccount.objects.filter(bank_name__iexact=bank_name).first()
                    
                    if not bank_acc:
                        # Force a single consolidated fallback name to avoid cluttering the grid
                        target_name = "Imported Bank Account"
                        
                        # Use the imported currency if provided, otherwise fallback to PKR/Base
                        creation_currency = imported_currency
                        if not creation_currency:
                            creation_currency = Currency.objects.filter(is_base=True).first() or Currency.objects.first()
                        
                        # Find/Create the Account node and Bank detail
                        ledger_acc, _ = Account.objects.get_or_create(
                            name=target_name,
                            defaults={'account_type': AccountType.ASSET, 'currency': creation_currency, 'is_active': True}
                        )
                        bank_acc, _ = BankAccount.objects.get_or_create(
                            ledger_account=ledger_acc,
                            defaults={'bank_name': target_name, 'is_active': True}
                        )
                        bank_name = bank_acc.bank_name

                    # Auto-fallback Category
                    category_acc = None
                    if category_name:
                        category_acc = Account.objects.filter(name__iexact=category_name).first()

                    if not category_acc:
                        base_currency = Currency.objects.filter(is_base=True).first()
                        if not base_currency:
                            base_currency = Currency.objects.first()
                            
                        cat_type = AccountType.REVENUE if txn_type == 'Income' else AccountType.EXPENSE
                        cat_name = category_name if category_name else ("Income" if txn_type == 'Income' else "Expense")
                        
                        category_acc, _ = Account.objects.get_or_create(
                            name=cat_name,
                            account_type=cat_type,
                            defaults={'currency': base_currency, 'is_active': True}
                        )
                        category_name = category_acc.name

                    project = None
                    if project_name and project_name not in ['N/A', '']:
                        project = Project.objects.filter(name__iexact=project_name).first()

                    with db_transaction.atomic():
                        # Use the imported currency if provided, otherwise fallback to bank's currency
                        txn_currency = imported_currency or bank_acc.ledger_account.currency
                        rate = txn_currency.rate_to_pkr if txn_currency else Decimal('1.000000')

                        txn = Transaction.objects.create(
                            date=dt,
                            description=description,
                            reference=reference,
                            project=project,
                            currency=txn_currency,
                            created_by=request.user
                        )
                        
                        if txn_type == 'Income':
                            LedgerEntry.objects.create(transaction=txn, account=bank_acc.ledger_account, entry_type='DR', amount=amount, exchange_rate=rate)
                            LedgerEntry.objects.create(transaction=txn, account=category_acc, entry_type='CR', amount=amount, exchange_rate=rate)
                        elif txn_type == 'Expense':
                            LedgerEntry.objects.create(transaction=txn, account=category_acc, entry_type='DR', amount=amount, exchange_rate=rate)
                            LedgerEntry.objects.create(transaction=txn, account=bank_acc.ledger_account, entry_type='CR', amount=amount, exchange_rate=rate)
                        else:
                            errors.append(f"Row {i}: Invalid type '{txn_type}'. Expected 'Income' or 'Expense'.")
                            txn.delete()
                            skipped_count += 1
                            continue
                    
                    imported_count += 1
                    imported_txns.append({
                        'date': dt,
                        'description': description,
                        'amount': amount,
                        'type': txn_type,
                        'bank': bank_name
                    })

                except Exception as row_err:
                    errors.append(f"Row {i}: {str(row_err)}")
                    skipped_count += 1

            return render(request, 'core/partials/import_result_modal.html', {
                'imported_count': imported_count,
                'skipped_count': skipped_count,
                'errors': errors,
                'imported_txns': imported_txns
            })

        except Exception as e:
            return HttpResponse(f"<div class='p-4 bg-rose-50 text-rose-700 rounded-lg my-4 text-center font-bold'>Fatal Error: {str(e)}</div>")

    return HttpResponseBadRequest("Invalid request")



@login_required
def employee_list_view(request):
    """
    Premium Employee Management with live analytics.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_employees', False):
        return HttpResponseForbidden("You do not have permission to access the Employee Management page.")

    status = request.GET.get('status', 'Active')
    q = request.GET.get('q', '')
    
    employees_base = Employee.objects.all()
    if not request.user.is_superuser:
        employees_base = employees_base.filter(created_by=request.user)
    
    # Analytics
    active_count = employees_base.filter(status='Active').count()
    on_leave_count = employees_base.filter(status='On Leave').count()
    total_salary = employees_base.filter(status='Active').aggregate(Sum('salary'))['salary__sum'] or Decimal('0.00')
    total_count = employees_base.count()
    
    employees = employees_base.filter(status=status).order_by('name')
    
    if q:
        employees = employees.filter(
            Q(name__icontains=q) | 
            Q(designation__icontains=q) | 
            Q(employee_id__icontains=q) |
            Q(department__icontains=q)
        )
    
    context = {
        'employees': employees,
        'current_status': status,
        'active_count': active_count,
        'on_leave_count': on_leave_count,
        'total_salary': total_salary,
        'total_count': total_count,
        'q': q,
        'status_choices': Employee.STATUS_CHOICES,
        'is_htmx': request.headers.get('HX-Request') is not None
    }

    if context['is_htmx']:
        table_html = render(request, 'core/partials/employee_table_full.html', context).content.decode()
        stats_html = render(request, 'core/partials/employee_oob_stats.html', context).content.decode()
        title_oob = f'<title hx-swap-oob="true">Employees | {status} | Qonkar ERP</title>'
        return HttpResponse(stats_html + title_oob + table_html)
    
    return render(request, 'core/employee_list.html', context)

@login_required
def employee_create_view(request):
    """
    HTMX Modal view to create a new employee.
    """
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.created_by = request.user
            employee.save()
            if request.headers.get('HX-Request'):
                # Refresh list and stats via OOB
                status = 'Active' 
                employees = Employee.objects.filter(status=status).order_by('name')
                
                # Recalculate stats for OOB
                employees_base = Employee.objects.all()
                context = {
                    'employees': employees,
                    'current_status': status,
                    'active_count': employees_base.filter(status='Active').count(),
                    'on_leave_count': employees_base.filter(status='On Leave').count(),
                    'total_salary': employees_base.filter(status='Active').aggregate(Sum('salary'))['salary__sum'] or Decimal('0.00'),
                    'total_count': employees_base.count(),
                    'is_htmx': True
                }
                
                response_html = render(request, 'core/partials/employee_table_full.html', context).content.decode()
                stats_html = render(request, 'core/partials/employee_oob_stats.html', context).content.decode()
                toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-emerald-50 border border-emerald-200 text-emerald-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Employee {employee.name} Created.</div></div>'
                modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
                title_oob = '<title hx-swap-oob="true">Employees | Qonkar ERP</title>'
                
                return HttpResponse(stats_html + toast + modal_clear + title_oob + response_html)
            return redirect('core:employee_list')
    else:
        form = EmployeeForm()
    return render(request, 'core/partials/employee_modal.html', {'form': form})

@login_required
def employee_update_view(request, pk):
    """
    HTMX Modal view to update an existing employee.
    """
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            employee = form.save()
            if request.headers.get('HX-Request'):
                # Refresh list and stats via OOB
                status = employee.status
                employees = Employee.objects.filter(status=status).order_by('name')
                
                # Recalculate stats for OOB
                employees_base = Employee.objects.all()
                context = {
                    'employees': employees,
                    'current_status': status,
                    'active_count': employees_base.filter(status='Active').count(),
                    'on_leave_count': employees_base.filter(status='On Leave').count(),
                    'total_salary': employees_base.filter(status='Active').aggregate(Sum('salary'))['salary__sum'] or Decimal('0.00'),
                    'total_count': employees_base.count(),
                    'is_htmx': True
                }
                
                response_html = render(request, 'core/partials/employee_table_full.html', context).content.decode()
                stats_html = render(request, 'core/partials/employee_oob_stats.html', context).content.decode()
                toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-brand-50 border border-brand-200 text-brand-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Employee {employee.name} Updated.</div></div>'
                modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
                title_oob = f'<title hx-swap-oob="true">Employees | {employee.name} | Qonkar ERP</title>'
                
                return HttpResponse(stats_html + toast + modal_clear + title_oob + response_html)
            return redirect('core:employee_list')
    else:
        form = EmployeeForm(instance=employee)
    return render(request, 'core/partials/employee_modal.html', {'form': form, 'employee': employee})

@login_required
def employee_confirm_delete_view(request, pk):
    """
    HTMX Modal to confirm employee de-enrollment.
    """
    employee = get_object_or_404(Employee, pk=pk)
    return render(request, 'core/partials/employee_confirm_delete.html', {'employee': employee})

    return redirect('core:employee_list')

@login_required
def employee_delete_view(request, pk):
    """
    HTMX deletion of an employee record.
    """
    employee = get_object_or_404(Employee, pk=pk)
    name = employee.name
    status = employee.status
    employee.delete()
    
    if request.headers.get('HX-Request'):
        employees = Employee.objects.filter(status=status).order_by('name')
        employees_base = Employee.objects.all()
        context = {
            'employees': employees,
            'current_status': status,
            'active_count': employees_base.filter(status='Active').count(),
            'on_leave_count': employees_base.filter(status='On Leave').count(),
            'total_salary': employees_base.filter(status='Active').aggregate(Sum('salary'))['salary__sum'] or Decimal('0.00'),
            'total_count': employees_base.count(),
            'is_htmx': True
        }
        
        response_html = render(request, 'core/partials/employee_table_full.html', context).content.decode()
        stats_html = render(request, 'core/partials/employee_oob_stats.html', context).content.decode()
        toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Employee {name} Deleted.</div></div>'
        return HttpResponse(stats_html + toast + response_html)
        
    return redirect('core:employee_list')

EXPECTED_CLIENT_FIELDS = [
    ('name', 'Name', True),
    ('company_name', 'Company Name', False),
    ('email', 'Email', False),
    ('contact_number', 'Contact Number', False),
    ('address', 'Address', False),
    ('region', 'Region (Local/Global)', False),
    ('status', 'Status (Active/Archived)', False),
]

@login_required
def client_import_view(request):
    """
    Receives CSV upload and renders column mapping UI for clients.
    """
    if request.method == 'POST' and request.FILES.get('import_file'):
        csv_file = request.FILES['import_file']
        try:
            csv_content = csv_file.read()
            decoded_file = csv_content.decode('utf-8').splitlines()
            if not decoded_file:
                return HttpResponseBadRequest("Empty file")
                
            try:
                dialect = csv.Sniffer().sniff(decoded_file[0][:1024])
                reader = csv.reader(decoded_file, dialect=dialect)
            except csv.Error:
                reader = csv.reader(decoded_file)
                
            headers = next(reader, [])
            b64_csv = base64.b64encode(csv_content).decode('ascii')
            
            try:
                sample_row = next(reader)
            except StopIteration:
                sample_row = []
            
            header_samples = []
            for i, h in enumerate(headers):
                sample = sample_row[i] if i < len(sample_row) else ''
                header_samples.append({'header': h, 'sample': sample})
                
            return render(request, 'core/partials/client_import_map.html', {
                'header_samples': header_samples,
                'expected_fields': EXPECTED_CLIENT_FIELDS,
                'b64_csv': b64_csv,
            })
        except Exception as e:
            return HttpResponse(f"<div class='p-4 bg-rose-50 text-rose-700 rounded-lg my-4 text-center font-bold'>Error reading file: {str(e)}</div>")

    return HttpResponseBadRequest("Invalid request")

@login_required
def client_import_process_view(request):
    """
    Processes the Client CSV with mapped columns.
    """
    if request.method == 'POST' and request.POST.get('b64_csv'):
        b64_csv = request.POST['b64_csv']
        
        mapping = {}
        for key, _, _ in EXPECTED_CLIENT_FIELDS:
            csv_header = request.POST.get(f"map_{key}")
            if csv_header:
                mapping[key] = csv_header

        imported_count = 0
        skipped_count = 0
        errors = []
        imported_items = []
        
        try:
            csv_content = base64.b64decode(b64_csv).decode('utf-8').splitlines()
            try:
                dialect = csv.Sniffer().sniff(csv_content[0][:1024])
                reader = csv.DictReader(csv_content, dialect=dialect)
            except csv.Error:
                reader = csv.DictReader(csv_content)
            
            for i, row in enumerate(reader, 1):
                try:
                    name = row.get(mapping.get('name', ''), '').strip()
                    if not name:
                        errors.append(f"Row {i}: Missing mapped Name.")
                        skipped_count += 1
                        continue

                    company_name = row.get(mapping.get('company_name', ''), '').strip()
                    email = row.get(mapping.get('email', ''), '').strip()
                    contact_number = row.get(mapping.get('contact_number', ''), '').strip()
                    address = row.get(mapping.get('address', ''), '').strip()
                    region = row.get(mapping.get('region', ''), '').strip() or 'Local'
                    status = row.get(mapping.get('status', ''), '').strip() or 'Active'

                    # Clean values
                    if region not in ['Local', 'Global']: region = 'Local'
                    if status not in ['Active', 'Archived']: status = 'Active'

                    # Duplicate check by name
                    if Client.objects.filter(name__iexact=name).exists():
                        skipped_count += 1
                        continue

                    Client.objects.create(
                        name=name,
                        company_name=company_name,
                        email=email,
                        contact_number=contact_number,
                        address=address,
                        region=region,
                        status=status,
                        created_by=request.user
                    )
                    
                    imported_count += 1
                    imported_items.append({'name': name, 'email': email, 'status': status})

                except Exception as row_err:
                    errors.append(f"Row {i}: {str(row_err)}")
                    skipped_count += 1

            return render(request, 'core/partials/import_result_modal.html', {
                'imported_count': imported_count,
                'skipped_count': skipped_count,
                'errors': errors,
                'imported_txns': imported_items, # Reuse template variable
                'title': 'Clients Imported'
            })

        except Exception as e:
            return HttpResponse(f"<div class='p-4 bg-rose-50 text-rose-700 rounded-lg my-4 text-center font-bold'>Fatal Error: {str(e)}</div>")

    return HttpResponseBadRequest("Invalid request")

EXPECTED_EMPLOYEE_FIELDS = [
    ('name', 'Name', True),
    ('employee_id', 'Employee ID', False),
    ('designation', 'Designation', True),
    ('department', 'Department', False),
    ('email', 'Email', False),
    ('contact_number', 'Contact Number', False),
    ('address', 'Address', False),
    ('salary', 'Salary', False),
    ('date_joined', 'Date Joined', False),
    ('status', 'Status', False),
]

@login_required
def employee_import_view(request):
    """
    Receives CSV upload and renders column mapping UI for employees.
    """
    if request.method == 'POST' and request.FILES.get('import_file'):
        csv_file = request.FILES['import_file']
        try:
            csv_content = csv_file.read()
            decoded_file = csv_content.decode('utf-8').splitlines()
            if not decoded_file:
                return HttpResponseBadRequest("Empty file")
                
            try:
                dialect = csv.Sniffer().sniff(decoded_file[0][:1024])
                reader = csv.reader(decoded_file, dialect=dialect)
            except csv.Error:
                reader = csv.reader(decoded_file)
                
            headers = next(reader, [])
            b64_csv = base64.b64encode(csv_content).decode('ascii')
            
            try:
                sample_row = next(reader)
            except StopIteration:
                sample_row = []
            
            header_samples = []
            for i, h in enumerate(headers):
                sample = sample_row[i] if i < len(sample_row) else ''
                header_samples.append({'header': h, 'sample': sample})
                
            currencies = Currency.objects.all()
                
            return render(request, 'core/partials/employee_import_map.html', {
                'header_samples': header_samples,
                'expected_fields': EXPECTED_EMPLOYEE_FIELDS,
                'b64_csv': b64_csv,
                'currencies': currencies
            })
        except Exception as e:
            return HttpResponse(f"<div class='p-4 bg-rose-50 text-rose-700 rounded-lg my-4 text-center font-bold'>Error reading file: {str(e)}</div>")

    return HttpResponseBadRequest("Invalid request")

@login_required
def employee_import_process_view(request):
    """
    Processes the Employee CSV with mapped columns.
    """
    if request.method == 'POST' and request.POST.get('b64_csv'):
        b64_csv = request.POST['b64_csv']
        
        mapping = {}
        for key, _, _ in EXPECTED_EMPLOYEE_FIELDS:
            csv_header = request.POST.get(f"map_{key}")
            if csv_header:
                mapping[key] = csv_header

        currency_id = request.POST.get('currency')
        import_currency = Currency.objects.filter(id=currency_id).first() if currency_id else Currency.objects.filter(is_base=True).first()

        imported_count = 0
        skipped_count = 0
        errors = []
        imported_items = []
        
        try:
            csv_content = base64.b64decode(b64_csv).decode('utf-8').splitlines()
            try:
                dialect = csv.Sniffer().sniff(csv_content[0][:1024])
                reader = csv.DictReader(csv_content, dialect=dialect)
            except csv.Error:
                reader = csv.DictReader(csv_content)
            
            for i, row in enumerate(reader, 1):
                try:
                    name = row.get(mapping.get('name', ''), '').strip()
                    designation = row.get(mapping.get('designation', ''), '').strip()
                    
                    if not name or not designation:
                        errors.append(f"Row {i}: Missing Name or Designation.")
                        skipped_count += 1
                        continue

                    e_id = row.get(mapping.get('employee_id', ''), '').strip()
                    department = row.get(mapping.get('department', ''), '').strip()
                    email = row.get(mapping.get('email', ''), '').strip()
                    contact = row.get(mapping.get('contact_number', ''), '').strip()
                    address = row.get(mapping.get('address', ''), '').strip()
                    salary_str = row.get(mapping.get('salary', ''), '0').replace(',', '').strip()
                    salary = Decimal(salary_str) if salary_str else Decimal('0.00')
                    
                    date_str = row.get(mapping.get('date_joined', ''), '').strip()
                    date_joined = None
                    if date_str:
                        try:
                            date_joined = datetime.strptime(date_str, '%Y-%m-%d').date()
                        except ValueError:
                            pass

                    status = row.get(mapping.get('status', ''), '').strip() or 'Active'
                    if status not in dict(Employee.STATUS_CHOICES): status = 'Active'

                    # Duplicate check by employee_id or name+designation
                    if e_id and Employee.objects.filter(employee_id=e_id).exists():
                        skipped_count += 1
                        continue
                    
                    Employee.objects.create(
                        name=name,
                        employee_id=e_id,
                        designation=designation,
                        department=department,
                        email=email,
                        contact_number=contact,
                        address=address,
                        salary=salary,
                        currency=import_currency,
                        date_joined=date_joined,
                        status=status,
                        created_by=request.user
                    )
                    
                    imported_count += 1
                    imported_items.append({'name': name, 'designation': designation, 'status': status})

                except Exception as row_err:
                    errors.append(f"Row {i}: {str(row_err)}")
                    skipped_count += 1

            return render(request, 'core/partials/import_result_modal.html', {
                'imported_count': imported_count,
                'skipped_count': skipped_count,
                'errors': errors,
                'imported_txns': imported_items,
                'title': 'Employees Imported'
            })

        except Exception as e:
            return HttpResponse(f"<div class='p-4 bg-rose-50 text-rose-700 rounded-lg my-4 text-center font-bold'>Fatal Error: {str(e)}</div>")

    return HttpResponseBadRequest("Invalid request")

EXPECTED_PROJECT_FIELDS = [
    ('name', 'Project Name', True),
    ('client', 'Client Name', True),
    ('status', 'Status', False),
    ('project_type', 'Type (Fixed/Subscription)', False),
    ('project_lead', 'Project Lead (Employee Name)', False),
    ('description', 'Description', False),
    ('target_budget', 'Target Budget', False),
    ('monthly_fee', 'Monthly Fee', False),
    ('start_date', 'Start Date', False),
    ('end_date', 'End Date', False),
]

@login_required
def project_import_view(request):
    """
    Receives CSV upload and renders column mapping UI for projects.
    """
    if request.method == 'POST' and request.FILES.get('import_file'):
        csv_file = request.FILES['import_file']
        try:
            csv_content = csv_file.read()
            decoded_file = csv_content.decode('utf-8').splitlines()
            if not decoded_file:
                return HttpResponseBadRequest("Empty file")
                
            try:
                dialect = csv.Sniffer().sniff(decoded_file[0][:1024])
                reader = csv.reader(decoded_file, dialect=dialect)
            except csv.Error:
                reader = csv.reader(decoded_file)
                
            headers = next(reader, [])
            b64_csv = base64.b64encode(csv_content).decode('ascii')
            
            try:
                sample_row = next(reader)
            except StopIteration:
                sample_row = []
            
            header_samples = []
            for i, h in enumerate(headers):
                sample = sample_row[i] if i < len(sample_row) else ''
                header_samples.append({'header': h, 'sample': sample})
                
            currencies = Currency.objects.all()
                
            return render(request, 'core/partials/project_import_map.html', {
                'header_samples': header_samples,
                'expected_fields': EXPECTED_PROJECT_FIELDS,
                'b64_csv': b64_csv,
                'currencies': currencies
            })
        except Exception as e:
            return HttpResponse(f"<div class='p-4 bg-rose-50 text-rose-700 rounded-lg my-4 text-center font-bold'>Error reading file: {str(e)}</div>")

    return HttpResponseBadRequest("Invalid request")

@login_required
def project_import_process_view(request):
    """
    Processes the Project CSV with mapped columns.
    """
    if request.method == 'POST' and request.POST.get('b64_csv'):
        b64_csv = request.POST['b64_csv']
        
        mapping = {}
        for key, _, _ in EXPECTED_PROJECT_FIELDS:
            csv_header = request.POST.get(f"map_{key}")
            if csv_header:
                mapping[key] = csv_header

        currency_id = request.POST.get('currency')
        import_currency = Currency.objects.filter(id=currency_id).first() if currency_id else Currency.objects.filter(is_base=True).first()

        imported_count = 0
        skipped_count = 0
        errors = []
        imported_items = []
        
        try:
            csv_content = base64.b64decode(b64_csv).decode('utf-8').splitlines()
            try:
                dialect = csv.Sniffer().sniff(csv_content[0][:1024])
                reader = csv.DictReader(csv_content, dialect=dialect)
            except csv.Error:
                reader = csv.DictReader(csv_content)
            
            for i, row in enumerate(reader, 1):
                try:
                    name = row.get(mapping.get('name', ''), '').strip()
                    client_name = row.get(mapping.get('client', ''), '').strip()
                    
                    if not name or not client_name:
                        errors.append(f"Row {i}: Missing Project Name or Client Name.")
                        skipped_count += 1
                        continue

                    client = Client.objects.filter(name__iexact=client_name).first()
                    if not client:
                        # Auto-create client if it doesn't exist
                        client = Client.objects.create(name=client_name, created_by=request.user)

                    status = row.get(mapping.get('status', ''), '').strip() or 'Pipeline / Prospect'
                    if status not in dict(Project.STATUS_CHOICES): status = 'Pipeline / Prospect'

                    p_type = row.get(mapping.get('project_type', ''), '').strip() or 'Fixed'
                    if p_type not in ['Fixed', 'Subscription']: p_type = 'Fixed'

                    budget_str = row.get(mapping.get('target_budget', ''), '0').replace(',', '').strip()
                    target_budget = Decimal(budget_str) if budget_str else Decimal('0.00')

                    fee_str = row.get(mapping.get('monthly_fee', ''), '0').replace(',', '').strip()
                    monthly_fee = Decimal(fee_str) if fee_str else Decimal('0.00')

                    lead_name = row.get(mapping.get('project_lead', ''), '').strip()
                    project_lead = None
                    if lead_name:
                        project_lead = Employee.objects.filter(name__icontains=lead_name).first()
                    
                    start_date_str = row.get(mapping.get('start_date', ''), '').strip()
                    start_date = timezone.now().date()
                    if start_date_str:
                        try:
                            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                        except ValueError:
                            pass

                    end_date_str = row.get(mapping.get('end_date', ''), '').strip()
                    end_date = None
                    if end_date_str:
                        try:
                            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                        except ValueError:
                            pass

                    # Handle Description
                    description = row.get(mapping.get('description', ''), '').strip()

                    # Strict Exact Duplicate check: Only skip if EVERYTHING matches
                    # This allows projects with same name but different leads, budgets, or dates
                    duplicate_exists = Project.objects.filter(
                        name__iexact=name,
                        client=client,
                        project_lead=project_lead,
                        project_type=p_type,
                        target_budget=target_budget,
                        monthly_fee=monthly_fee,
                        start_date=start_date,
                        end_date=end_date
                    ).exists()

                    if duplicate_exists:
                        errors.append(f"Row {i}: Exact duplicate project already exists (Skipping).")
                        skipped_count += 1
                        continue
                    
                    Project.objects.create(
                        name=name,
                        client=client,
                        status=status,
                        project_type=p_type,
                        project_lead=project_lead,
                        description=description,
                        target_budget=target_budget,
                        monthly_fee=monthly_fee,
                        currency=import_currency,
                        start_date=start_date,
                        end_date=end_date,
                        created_by=request.user
                    )
                    
                    imported_count += 1
                    imported_items.append({
                        'name': name, 
                        'client': client.name, 
                        'status': status,
                        'description': description[:50] + '...' if len(description) > 50 else description
                    })

                except Exception as row_err:
                    errors.append(f"Row {i}: {str(row_err)}")
                    skipped_count += 1

            return render(request, 'core/partials/import_result_modal.html', {
                'imported_count': imported_count,
                'skipped_count': skipped_count,
                'errors': errors,
                'imported_txns': imported_items,
                'title': 'Projects Imported'
            })

        except Exception as e:
            return render(request, 'core/partials/import_result_modal.html', {
                'imported_count': 0,
                'skipped_count': 0,
                'errors': [f"System Error: {str(e)}", "Please check your CSV format and column mappings."],
                'title': 'Import Failed'
            })

    return HttpResponseBadRequest("Invalid request")

@login_required
def client_list_view(request):
    """
    Premium Client Management with live analytics and HTMX interaction.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_clients', False):
        return HttpResponseForbidden("You do not have permission to access the Client Management page.")

    status = request.GET.get('status', 'Active')
    q = request.GET.get('q', '')
    
    clients_base = Client.objects.all()
    if not request.user.is_superuser:
        clients_base = clients_base.filter(
            Q(created_by=request.user) | 
            Q(clientaccess__user=request.user)
        ).distinct()
    
    # Analytics
    active_count = clients_base.filter(status='Active').count()
    archived_count = clients_base.filter(status='Archived').count()
    local_reach = clients_base.filter(region='Local').count()
    global_reach = clients_base.filter(region='Global').count()
    total_projects = Project.objects.filter(client__status='Active').count()

    clients = clients_base.filter(status=status).annotate(active_project_count=Count('projects')).order_by('name')
    
    if q:
        clients = clients.filter(
            Q(name__icontains=q) | 
            Q(email__icontains=q) | 
            Q(region__icontains=q)
        )
    
    context = {
        'clients': clients,
        'current_status': status,
        'active_count': active_count,
        'archived_count': archived_count,
        'local_reach': local_reach,
        'global_reach': global_reach,
        'total_projects': total_projects,
        'q': q,
        'is_htmx': request.headers.get('HX-Request') is not None
    }

    if context['is_htmx']:
        table_html = render(request, 'core/partials/client_table_full.html', context).content.decode()
        stats_html = render(request, 'core/partials/client_oob_stats.html', context).content.decode()
        title_oob = f'<title hx-swap-oob="true">Clients | {status} | Qonkar ERP</title>'
        return HttpResponse(stats_html + title_oob + table_html)
    
    return render(request, 'core/client_list.html', context)

@login_required
def client_create_view(request):
    """
    HTMX Modal view to create a new client.
    """
    from .forms import ClientForm
    if request.method == 'POST':
        form = ClientForm(request.POST, request.FILES)
        if form.is_valid():
            client = form.save(commit=False)
            client.created_by = request.user
            client.save()
            
            if request.headers.get('HX-Request'):
                # Refresh list and stats via OOB
                status = 'Active' # Assume active for new clients
                clients = Client.objects.filter(status=status).annotate(active_project_count=Count('projects')).order_by('name')
                
                # Recalculate stats for OOB
                clients_base = Client.objects.all()
                if not request.user.is_superuser:
                    clients_base = clients_base.filter(created_by=request.user)
                
                context = {
                    'clients': clients,
                    'current_status': status,
                    'active_count': clients_base.filter(status='Active').count(),
                    'archived_count': clients_base.filter(status='Archived').count(),
                    'local_reach': clients_base.filter(region='Local').count(),
                    'global_reach': clients_base.filter(region='Global').count(),
                    'total_projects': Project.objects.filter(client__status='Active').count(),
                    'is_htmx': True
                }
                
                response_html = render(request, 'core/partials/client_table_full.html', context).content.decode()
                stats_html = render(request, 'core/partials/client_oob_stats.html', context).content.decode()
                toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-emerald-50 border border-emerald-200 text-emerald-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Client {client.name} Created.</div></div>'
                modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
                title_oob = '<title hx-swap-oob="true">Clients | Qonkar ERP</title>'
                
                return HttpResponse(stats_html + toast + modal_clear + title_oob + response_html)
            
            # Post-creation redirect (non-HTMX)
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect('core:client_list')
    else:
        form = ClientForm()
    return render(request, 'core/partials/client_modal.html', {'form': form})

@login_required
def client_update_view(request, pk):
    """
    HTMX Modal view to update an existing client.
    """
    from .forms import ClientForm
    client = get_object_or_404(Client, pk=pk)
    if request.method == 'POST':
        form = ClientForm(request.POST, request.FILES, instance=client)
        if form.is_valid():
            client = form.save()
            if request.headers.get('HX-Request'):
                # Refresh list and stats via OOB
                status = client.status
                clients = Client.objects.filter(status=status).annotate(active_project_count=Count('projects')).order_by('name')
                
                # Recalculate stats for OOB
                clients_base = Client.objects.all()
                context = {
                    'clients': clients,
                    'current_status': status,
                    'active_count': clients_base.filter(status='Active').count(),
                    'archived_count': clients_base.filter(status='Archived').count(),
                    'local_reach': clients_base.filter(region='Local').count(),
                    'global_reach': clients_base.filter(region='Global').count(),
                    'total_projects': Project.objects.filter(client__status='Active').count(),
                    'is_htmx': True
                }
                
                response_html = render(request, 'core/partials/client_table_full.html', context).content.decode()
                stats_html = render(request, 'core/partials/client_oob_stats.html', context).content.decode()
                toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-brand-50 border border-brand-200 text-brand-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Client {client.name} Updated.</div></div>'
                modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
                title_oob = f'<title hx-swap-oob="true">Clients | {client.name} | Qonkar ERP</title>'
                
                return HttpResponse(stats_html + toast + modal_clear + title_oob + response_html)
            return redirect('core:client_list')
    else:
        form = ClientForm(instance=client)
    return render(request, 'core/partials/client_modal.html', {'form': form, 'client': client})

@login_required
def client_confirm_delete_view(request, pk):
    """
    Renders a confirmation modal for deleting/archiving a client.
    """
    client = get_object_or_404(Client, pk=pk)
    has_projects = client.projects.exists()
    return render(request, 'core/partials/client_confirm_delete.html', {'client': client, 'has_projects': has_projects})

@login_required
def client_delete_view(request, pk):
    """
    Deletes or archives a client with OOB updates.
    """
    client = get_object_or_404(Client, pk=pk)
    if request.method == 'POST' or request.method == 'DELETE':
        name = client.name
        if client.projects.exists():
            client.status = 'Archived'
            client.save()
            msg = f"Client {name} Archived."
        else:
            client.delete()
            msg = f"Client {name} Deleted."
            
        if request.headers.get('HX-Request'):
            status = 'Active' # Usually redirect back to active list
            clients = Client.objects.filter(status=status).annotate(active_project_count=Count('projects')).order_by('name')
            
            # Recalculate stats for OOB
            clients_base = Client.objects.all()
            context = {
                'clients': clients,
                'current_status': status,
                'active_count': clients_base.filter(status='Active').count(),
                'archived_count': clients_base.filter(status='Archived').count(),
                'local_reach': clients_base.filter(region='Local').count(),
                'global_reach': clients_base.filter(region='Global').count(),
                'total_projects': Project.objects.filter(client__status='Active').count(),
                'is_htmx': True
            }
            
            response_html = render(request, 'core/partials/client_table_full.html', context).content.decode()
            stats_html = render(request, 'core/partials/client_oob_stats.html', context).content.decode()
            toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">{msg}</div></div>'
            modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
            title_oob = '<title hx-swap-oob="true">Clients | Qonkar ERP</title>'
            
            return HttpResponse(stats_html + toast + modal_clear + title_oob + response_html)
            
    return redirect('core:client_list')

@login_required
@login_required
def project_list_view(request):
    """
    Project Lifecycle Management.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_projects', False):
        return HttpResponseForbidden("You do not have permission to access the Project Management page.")

    projects = Project.objects.all().select_related('client', 'currency').order_by('-created_at')
    if not request.user.is_superuser:
        projects = projects.filter(
            Q(created_by=request.user) | 
            Q(projectaccess__user=request.user)
        ).distinct()
    
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/project_table.html', {'projects': projects})
        
    return render(request, 'core/project_list.html', {'projects': projects})

@login_required
def client_export_view(request):
    """
    Exports filtered clients to CSV.
    """
    clients_base = Client.objects.all()
    if not request.user.is_superuser:
        clients_base = clients_base.filter(
            Q(created_by=request.user) | 
            Q(clientaccess__user=request.user)
        ).distinct()

    q = request.GET.get('q', '')
    status = request.GET.get('status', 'Active')
    
    clients = clients_base.filter(status=status).order_by('name')
    if q:
        clients = clients.filter(
            Q(name__icontains=q) | 
            Q(email__icontains=q) | 
            Q(region__icontains=q)
        )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="clients_export_{status}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Name', 'Company Name', 'Email', 'Contact Number', 'Address', 'Region', 'Status'])
    
    for client in clients:
        writer.writerow([
            client.name,
            client.company_name or '',
            client.email or '',
            client.contact_number or '',
            client.address or '',
            client.region,
            client.status
        ])
    return response

@login_required
def employee_export_view(request):
    """
    Exports filtered employees to CSV.
    """
    employees_base = Employee.objects.all()
    if not request.user.is_superuser:
        employees_base = employees_base.filter(created_by=request.user)

    q = request.GET.get('q', '')
    status = request.GET.get('status', 'Active')
    
    employees = employees_base.filter(status=status).order_by('name')
    if q:
        employees = employees.filter(
            Q(name__icontains=q) | 
            Q(designation__icontains=q) | 
            Q(employee_id__icontains=q) |
            Q(department__icontains=q)
        )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="employees_export_{status}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Name', 'Employee ID', 'Designation', 'Department', 'Email', 'Contact Number', 'Address', 'Salary', 'Date Joined', 'Status'])
    
    for emp in employees:
        writer.writerow([
            emp.name,
            emp.employee_id or '',
            emp.designation,
            emp.department or '',
            emp.email or '',
            emp.contact_number or '',
            emp.address or '',
            emp.salary,
            emp.date_joined.strftime('%Y-%m-%d') if emp.date_joined else '',
            emp.status
        ])
    return response

@login_required
def project_export_view(request):
    """
    Exports filtered projects to CSV.
    """
    projects_base = Project.objects.all().select_related('client', 'currency').order_by('-created_at')
    if not request.user.is_superuser:
        projects_base = projects_base.filter(
            Q(created_by=request.user) | 
            Q(projectaccess__user=request.user)
        ).distinct()

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="projects_export.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Name', 'Client', 'Status', 'Type', 'Project Lead', 'Target Budget', 'Monthly Fee', 'Currency', 'Start Date', 'End Date'])
    
    for proj in projects_base:
        writer.writerow([
            proj.name,
            proj.client.name,
            proj.status,
            proj.project_type,
            proj.project_lead.name if proj.project_lead else '',
            proj.target_budget,
            proj.monthly_fee,
            proj.currency.code,
            proj.start_date.strftime('%Y-%m-%d') if proj.start_date else '',
            proj.end_date.strftime('%Y-%m-%d') if proj.end_date else ''
        ])
    return response

@login_required
def project_create_view(request):
    """
    HTMX Modal view to create a new project.
    """
    from .forms import ProjectForm
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            if request.headers.get('HX-Request'):
                # Refresh list and close modal
                projects = Project.objects.all().select_related('client', 'currency').order_by('-created_at')
                # Wrap table in OOB container
                table_html = f'<div id="project-table-container" hx-swap-oob="true">{render(request, "core/partials/project_table.html", {"projects": projects}).content.decode()}</div>'
                toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-emerald-50 border border-emerald-200 text-emerald-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Project {project.name} Created.</div></div>'
                modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
                return HttpResponse(toast + modal_clear + table_html)

            return redirect('core:project_list')
    else:
        form = ProjectForm()
    
    # Context for JS interactions (Smart Defaults)
    client_regions = {str(c.id): c.region for c in Client.objects.all()}
    
    # Find Base Currency vs Global Currency IDs
    base_currency = Currency.objects.filter(is_base=True).first()
    global_currency = Currency.objects.filter(code='USD').first()
    
    currency_defaults = {
        'Local': str(base_currency.id) if base_currency else '',
        'Global': str(global_currency.id) if global_currency else str(base_currency.id) if base_currency else ''
    }

    import json
    return render(request, 'core/partials/project_modal.html', {
        'form': form, 
        'client_regions': json.dumps(client_regions),
        'currency_defaults': json.dumps(currency_defaults)
    })

@login_required
def project_update_view(request, pk):
    """
    HTMX Modal view to update an existing project.
    """
    from .forms import ProjectForm
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            project = form.save()
            if request.headers.get('HX-Request'):
                projects = Project.objects.all().select_related('client', 'currency').order_by('-created_at')
                if not request.user.is_superuser:
                    projects = projects.filter(
                        Q(created_by=request.user) |
                        Q(projectaccess__user=request.user)
                    ).distinct()
                # Wrap table in OOB container
                table_html = f'<div id="project-table-container" hx-swap-oob="true">{render(request, "core/partials/project_table.html", {"projects": projects}).content.decode()}</div>'
                toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-brand-50 border border-brand-200 text-brand-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Project {project.name} Updated.</div></div>'
                modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
                return HttpResponse(toast + modal_clear + table_html)

            return redirect('core:project_list')
    else:
        form = ProjectForm(instance=project)
    
    client_regions = {str(c.id): c.region for c in Client.objects.all()}
    base_currency = Currency.objects.filter(is_base=True).first()
    global_currency = Currency.objects.filter(code='USD').first()
    currency_defaults = {
        'Local': str(base_currency.id) if base_currency else '',
        'Global': str(global_currency.id) if global_currency else str(base_currency.id) if base_currency else ''
    }

    import json
    return render(request, 'core/partials/project_modal.html', {
        'form': form, 
        'project': project,
        'client_regions': json.dumps(client_regions),
        'currency_defaults': json.dumps(currency_defaults)
    })

@login_required
def project_confirm_delete_view(request, pk):
    """
    Renders a confirmation modal for deleting a project.
    """
    project = get_object_or_404(Project, pk=pk)
    has_transactions = project.transactions.exists()
    return render(request, 'core/partials/project_confirm_delete.html', {'project': project, 'has_transactions': has_transactions})

@login_required
def project_delete_view(request, pk):
    """
    Deletes or archives a project with OOB updates.
    """
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST' or request.method == 'DELETE':
        name = project.name
        if project.transactions.exists():
            project.status = 'Completed'
            project.save()
            msg = f"Project {name} Completed (has transactions)."
        else:
            project.delete()
            msg = f"Project {name} Deleted."
            
        if request.headers.get('HX-Request'):
            projects = Project.objects.all().select_related('client', 'currency').order_by('-created_at')
            if not request.user.is_superuser:
                projects = projects.filter(
                    Q(created_by=request.user) |
                    Q(projectaccess__user=request.user)
                ).distinct()
            table_html = render(request, 'core/partials/project_table.html', {'projects': projects}).content.decode()
            
            toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">{msg}</div></div>'
            modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
            
            return HttpResponse(toast + modal_clear + table_html)
            
    return redirect('core:project_list')

@login_required
def project_bulk_delete_confirm_view(request):
    """
    HTMX Modal to confirm bulk deletion of projects.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_projects', False):
        return HttpResponseForbidden("Not authorized")
    
    project_ids = request.POST.getlist('selected_projects')
    if not project_ids:
        return HttpResponse("") 
        
    projects = Project.objects.filter(id__in=project_ids)
    return render(request, 'core/partials/project_bulk_delete_confirm.html', {
        'projects': projects,
        'project_ids': ','.join(project_ids)
    })

@login_required
def project_bulk_delete_view(request):
    """
    Execute bulk deletion and return updated table.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_projects', False):
        return HttpResponseForbidden("Not authorized")
        
    project_ids = request.POST.get('project_ids', '').split(',')
    if project_ids and project_ids[0]:
        deleted_count = Project.objects.filter(id__in=project_ids).delete()[0]
    
    projects = Project.objects.all().select_related('client', 'currency').order_by('-created_at')
    if not request.user.is_superuser:
        projects = projects.filter(
            Q(created_by=request.user) | Q(projectaccess__user=request.user)
        ).distinct()
        
    table_html = render(request, 'core/partials/project_table.html', {'projects': projects}).content.decode()
    toast = f'<div id="toast-container" hx-swap-oob="beforeend"><div class="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">Bulk Project Deletion Successful.</div></div>'
    modal_clear = '<div id="modal-container" hx-swap-oob="true"></div>'
    
    return HttpResponse(toast + modal_clear + table_html)

@login_required
def banking_view(request):
    """
    Banking & Accounts overview with live balances and status filtering.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_banking', False):
        return HttpResponseForbidden("You do not have permission to access the Banking page.")

    status = request.GET.get('status', 'active')
    
    if status == 'archived':
        bank_accounts = BankAccount.objects.filter(is_active=False)
    elif status == 'all':
        bank_accounts = BankAccount.objects.all()
    else: # Default to active
        bank_accounts = BankAccount.objects.filter(is_active=True)
    
    bank_accounts = bank_accounts.select_related('ledger_account__currency').order_by('bank_name')
    if not request.user.is_superuser:
        bank_accounts = bank_accounts.filter(expensemanageraccess__user=request.user)
    
    # Enrich accounts with real-time balance
    enriched_accounts = []
    total_balance_pkr = Decimal('0.00')
    active_count = 0
    
    # 24h Fetch Logic
    from .services import update_exchange_rates
    from django.utils import timezone
    last_update = Currency.objects.filter(last_updated__isnull=False).order_by('last_updated').first()
    if not last_update or (timezone.now() - last_update.last_updated).total_seconds() > 86400:
        update_exchange_rates()

    for bank in bank_accounts:
        # Calculate balance for this user's view (personal ledger)
        qs = LedgerEntry.objects.filter(account=bank.ledger_account)
        if not request.user.is_superuser:
            qs = qs.filter(
                Q(transaction__created_by=request.user) | 
                Q(transaction__entries__account__bank_detail__expensemanageraccess__user=request.user)
            ).distinct()
            
        debits = qs.filter(entry_type=LedgerEntry.DR).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        credits = qs.filter(entry_type=LedgerEntry.CR).aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        balance = debits - credits
        
        # Pre-format balance parts to avoid rounding issues in template
        balance_fmt = "{:,.2f}".format(balance)
        balance_parts = balance_fmt.split('.')
        
        enriched_accounts.append({
            'obj': bank,
            'balance': balance,
            'balance_int': balance_parts[0],
            'balance_decimal': balance_parts[1],
        })
        if bank.is_active:
            # Convert to PKR for the total display
            currency_rate = bank.ledger_account.currency.rate_to_pkr
            total_balance_pkr += balance * currency_rate
            active_count += 1
        
    context = {
        'accounts': enriched_accounts,
        'current_status': status,
        'total_balance': total_balance_pkr,
        'active_count': active_count
    }
    
    if request.headers.get('HX-Request') and not request.headers.get('HX-Control'):
        # If it's a tab switch via HTMX, we can just return the grid partial? 
        # For simplicity, let's just return the whole page or update the card grid. 
        # Actually returning the whole page is fine for now as it's fast.
        pass

    return render(request, 'core/banking_overview.html', context)

@login_required
def analytics_view(request):
    """
    Analytics & Performance Reports with real trend data.
    """
    if not request.user.is_superuser and not getattr(request.user.permissions, 'can_manage_analytics', False):
        return HttpResponseForbidden("You do not have permission to access the Analytics page.")

    from django.utils import timezone
    from django.db.models.functions import TruncDay
    import datetime
    
    # Calculate key metrics
    income_qs = LedgerEntry.objects.filter(account__account_type=AccountType.REVENUE)
    expense_qs = LedgerEntry.objects.filter(account__account_type=AccountType.EXPENSE)
    
    if not request.user.is_superuser:
        income_qs = income_qs.filter(
            Q(transaction__created_by=request.user) | 
            Q(transaction__entries__account__bank_detail__expensemanageraccess__user=request.user)
        ).distinct()
        expense_qs = expense_qs.filter(
            Q(transaction__created_by=request.user) | 
            Q(transaction__entries__account__bank_detail__expensemanageraccess__user=request.user)
        ).distinct()

    total_income = income_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    total_expense = expense_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    net_result = total_income - total_expense
    
    # Handle period filtering
    days = int(request.GET.get('days', 30))
    now = timezone.now().date()
    period_start = now - datetime.timedelta(days=days)
    prev_period_start = period_start - datetime.timedelta(days=days)
    
    # Aggregating Income for Current and Previous Period for growth calculation
    current_income_total = income_qs.filter(transaction__date__gte=period_start).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    prev_income_total = income_qs.filter(transaction__date__gte=prev_period_start, transaction__date__lt=period_start).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    income_growth = 0
    if prev_income_total > 0:
        income_growth = ((current_income_total - prev_income_total) / prev_income_total) * 100

    # Daily Trends for Current Period
    daily_income = income_qs.filter(
        entry_type='CR',
        transaction__date__gte=period_start
    ).annotate(day=TruncDay('transaction__date')).values('day').annotate(total=Sum('amount')).order_by('day')
    
    daily_expense = expense_qs.filter(
        entry_type='DR',
        transaction__date__gte=period_start
    ).annotate(day=TruncDay('transaction__date')).values('day').annotate(total=Sum('amount')).order_by('day')

    # Prepare chart labels and data
    labels = []
    income_data = []
    expense_data = []
    
    income_map = {d['day'].strftime('%d/%m'): float(d['total']) for d in daily_income}
    expense_map = {d['day'].strftime('%d/%m'): float(d['total']) for d in daily_expense}
    
    # Create the time series based on selected days
    # For large ranges, we might want to truncate or aggregate by week, but for now we follow the 30-day pattern
    step = 1 if days <= 30 else (7 if days <= 90 else 30)
    for i in range(0, days, step):
        day_date = period_start + datetime.timedelta(days=i)
        day_str = day_date.strftime('%d/%m')
        labels.append(day_str)
        income_data.append(income_map.get(day_str, 0))
        expense_data.append(expense_map.get(day_str, 0))
    
    # Avg Transaction
    txn_count = income_qs.filter(transaction__date__gte=period_start).count()
    avg_transaction = current_income_total / txn_count if txn_count > 0 else Decimal('0.00')
    
    # Total Assets (same logic as dashboard)
    asset_accounts = Account.objects.filter(account_type=AccountType.ASSET, is_active=True)
    total_assets = Decimal('0.00')
    for acc in asset_accounts:
        qs_a = LedgerEntry.objects.filter(account=acc)
        if not request.user.is_superuser:
            qs_a = qs_a.filter(
                Q(transaction__created_by=request.user) |
                Q(transaction__entries__account__bank_detail__expensemanageraccess__user=request.user)
            ).distinct()
        dr = qs_a.filter(entry_type='DR').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        cr = qs_a.filter(entry_type='CR').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        total_assets += (dr - cr) * acc.currency.rate_to_pkr

    # Spending Breakdown (Top 4 categories + Others)
    spending_qs = expense_qs.filter(transaction__date__gte=period_start).values('account__name').annotate(total=Sum('amount')).order_by('-total')
    spending_breakdown = []
    other_total = Decimal('0.00')
    for i, item in enumerate(spending_qs):
        if i < 4:
            spending_breakdown.append({
                'name': item['account__name'],
                'total': float(item['total']),
                'percent': round(float(item['total'] / total_expense * 100), 1) if total_expense > 0 else 0
            })
        else:
            other_total += item['total']
            
    if other_total > 0:
        spending_breakdown.append({
            'name': 'Others',
            'total': float(other_total),
            'percent': round(float(other_total / total_expense * 100), 1) if total_expense > 0 else 0
        })

    context = {
        'total_income': total_income,
        'total_expense': total_expense,
        'net_result': net_result,
        'current_income': current_income_total,
        'income_growth': round(income_growth, 1),
        'avg_transaction': avg_transaction,
        'total_assets': total_assets,
        'chart_labels': labels,
        'chart_income': income_data,
        'chart_expense': expense_data,
        'spending_breakdown': spending_breakdown,
        'recent_transactions': Transaction.objects.order_by('-date', '-id')[:6],
        'days': days,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'core/analytics_report.html', context)

    return render(request, 'core/analytics_report.html', context)
@login_required
def category_create_view(request):
    """
    HTMX Modal view to create a new category (Account) inline.
    """
    if request.method == 'POST':
        name = request.POST.get('name')
        account_type = request.POST.get('account_type')
        from .models import Currency
        currency = Currency.objects.filter(is_base=True).first()
        
        if name and account_type and currency:
            account, created = Account.objects.get_or_create(
                name=name,
                account_type=account_type,
                currency=currency,
                defaults={'is_active': True}
            )
            if not created and not account.is_active:
                account.is_active = True
                account.save()
            
            # Identify which select to update based on origin, but we'll try to update both via OOB
            # Note: We keep the account_type for creation to maintain semi-logical accounting,
            # but the UI will show all categories for both.
            
            # Fetch fresh unified list
            categories = Account.objects.filter(account_type__in=[AccountType.REVENUE, AccountType.EXPENSE], is_active=True).exclude(name__in=["Expense", "Income"]).order_by('name')
            
            def get_options(selected_id):
                return "".join([f'<option value="{a.id}" {"selected" if a.id == selected_id else ""}>{a.name}</option>' for a in categories])

            # We return the primary select as main response and the other as OOB swap
            # This ensures that if the user switches pages or is on a dashboard with both forms, both stay in sync.
            expense_options = get_options(account.id if account_type == AccountType.EXPENSE else None)
            income_options = get_options(account.id if account_type == AccountType.REVENUE else None)
            general_options = get_options(account.id)

            return HttpResponse(f'''
                <div id="category-modal" hx-swap-oob="delete"></div>
                <div id="modal-container" hx-swap-oob="true"></div>
                <div id="modal-container-stacked" hx-swap-oob="true"></div>
                
                <select id="id_expense_category" name="expense_category" hx-swap-oob="outerHTML" class="form-select block w-full border-gray-200 rounded-lg shadow-sm focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4">
                    <option value="">Select Category</option>
                    {expense_options}
                </select>

                <select id="id_income_category" name="income_category" hx-swap-oob="outerHTML" class="form-select block w-full border-gray-200 rounded-lg shadow-sm focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4">
                    <option value="">Select Category</option>
                    {income_options}
                </select>

                <select id="id_category" name="category" hx-swap-oob="outerHTML" class="form-select block w-full border-gray-200 rounded-lg shadow-sm focus:border-brand-500 focus:ring-brand-500 text-sm py-3 px-4">
                    <option value="">Select Category</option>
                    {general_options}
                </select>

                <div id="toast-container" hx-swap-oob="beforeend">
                    <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded relative mb-2 shadow" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">
                        <strong class="font-bold">Success!</strong>
                        <span class="block sm:inline">{account.name} has been added and selected.</span>
                    </div>
                </div>
            ''')
    
    account_type = request.GET.get('type', 'EXPENSE')
    return render(request, 'core/partials/category_modal.html', {'account_type': account_type})

@login_required
def access_management_view(request):
    from .models import ExpenseManagerAccess, UserPermission, ProjectAccess, ClientAccess
    from .forms import ExpenseManagerAccessForm, UserPermissionForm, ProjectAccessForm, ClientAccessForm
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    if not request.user.is_superuser:
        return HttpResponseBadRequest("Not authorized")
    
    # Get all users to manage their global permissions
    all_users = User.objects.all().prefetch_related('permissions').order_by('-is_superuser', 'username')
    
    # Initialize permissions for users who don't have them
    for user in all_users:
        if not hasattr(user, 'permissions'):
            UserPermission.objects.get_or_create(user=user)
            user.permissions = user.permissions # refresh

    # Data for the lists
    bank_accesses = ExpenseManagerAccess.objects.select_related('user', 'bank_account').all().order_by('user__username')
    project_accesses = ProjectAccess.objects.select_related('user', 'project').all().order_by('user__username')
    client_accesses = ClientAccess.objects.select_related('user', 'client').all().order_by('user__username')
    
    # Forms
    bank_form = ExpenseManagerAccessForm(prefix='bank')
    project_form = ProjectAccessForm(prefix='project')
    client_form = ClientAccessForm(prefix='client')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'assign_bank':
            bank_form = ExpenseManagerAccessForm(request.POST, prefix='bank')
            if bank_form.is_valid():
                bank_form.save()
                return redirect('core:access_management')
        elif action == 'assign_project':
            project_form = ProjectAccessForm(request.POST, prefix='project')
            if project_form.is_valid():
                project_form.save()
                return redirect('core:access_management')
        elif action == 'assign_client':
            client_form = ClientAccessForm(request.POST, prefix='client')
            if client_form.is_valid():
                client_form.save()
                return redirect('core:access_management')
        
    return render(request, 'core/access_management.html', {
        'bank_accesses': bank_accesses,
        'project_accesses': project_accesses,
        'client_accesses': client_accesses,
        'all_users': all_users,
        'bank_form': bank_form,
        'project_form': project_form,
        'client_form': client_form,
    })

@login_required
def permission_toggle_view(request, user_id, permission_name):
    from .models import UserPermission
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    if not request.user.is_superuser:
        return HttpResponseBadRequest("Not authorized")
    
    user_to_mod = get_object_or_404(User, id=user_id)
    
    # Check if it's a field on User model (like is_active, is_superuser)
    if hasattr(user_to_mod, permission_name):
        current_val = getattr(user_to_mod, permission_name)
        # Prevent self-demotion or self-deactivation
        if user_to_mod == request.user and permission_name in ['is_active', 'is_superuser']:
             return HttpResponseBadRequest("Cannot deactivate or demote yourself")
             
        setattr(user_to_mod, permission_name, not current_val)
        user_to_mod.save()
        new_val = getattr(user_to_mod, permission_name)
        color = "text-emerald-500" if new_val else "text-gray-300"
        svg_check = f'<svg class="w-6 h-6 {color} transition-colors" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg>'
        return HttpResponse(svg_check)

    # Otherwise check UserPermission model
    permission = get_object_or_404(UserPermission, user_id=user_id)
    if hasattr(permission, permission_name):
        current_val = getattr(permission, permission_name)
        setattr(permission, permission_name, not current_val)
        permission.save()
        
        # Return the new state icon for HTMX update
        new_val = getattr(permission, permission_name)
        color = "text-emerald-500" if new_val else "text-gray-300"
        svg_check = f'<svg class="w-6 h-6 {color} transition-colors" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg>'
        return HttpResponse(svg_check)
    
    return HttpResponseBadRequest("Invalid permission")

@login_required
def access_delete_view(request, pk):
    from .models import ExpenseManagerAccess
    if not request.user.is_superuser:
        return HttpResponseForbidden("Not authorized")
        
    access = get_object_or_404(ExpenseManagerAccess, pk=pk)
    user_name = access.user.username
    bank_name = access.bank_account.bank_name

    if request.method in ['POST', 'DELETE']:
        access.delete()
        if request.headers.get('HX-Request'):
            # Recalculate count for OOB
            from .models import ExpenseManagerAccess
            count = ExpenseManagerAccess.objects.count()
            
            return HttpResponse(f'''
                <span id="bank-access-count" hx-swap-oob="true" class="bg-brand-100 text-brand-700 text-[10px] font-bold px-2 py-0.5 rounded-full">{count}</span>
                <div id="toast-container" hx-swap-oob="beforeend">
                    <div class="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">
                        Access removed for {user_name} on {bank_name}.
                    </div>
                </div>
            ''', status=200)
        return redirect('core:access_management')
    return HttpResponseBadRequest("Invalid request")

@login_required
def project_access_delete_view(request, pk):
    from .models import ProjectAccess
    if not request.user.is_superuser:
        return HttpResponseForbidden("Not authorized")
    access = get_object_or_404(ProjectAccess, pk=pk)
    user_name = access.user.username
    project_name = access.project.name

    if request.method in ['POST', 'DELETE']:
        access.delete()
        if request.headers.get('HX-Request'):
            count = ProjectAccess.objects.count()
            return HttpResponse(f'''
                <span id="project-access-count" hx-swap-oob="true" class="bg-blue-100 text-blue-700 text-[10px] font-bold px-2 py-0.5 rounded-full">{count}</span>
                <div id="toast-container" hx-swap-oob="beforeend">
                    <div class="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">
                        Project restriction removed for {user_name}.
                    </div>
                </div>
            ''', status=200)
        return redirect('core:access_management')
    return HttpResponseBadRequest("Invalid request")

@login_required
def client_access_delete_view(request, pk):
    from .models import ClientAccess
    if not request.user.is_superuser:
        return HttpResponseForbidden("Not authorized")
    access = get_object_or_404(ClientAccess, pk=pk)
    user_name = access.user.username
    client_name = access.client.name

    if request.method in ['POST', 'DELETE']:
        access.delete()
        if request.headers.get('HX-Request'):
            count = ClientAccess.objects.count()
            return HttpResponse(f'''
                <span id="client-access-count" hx-swap-oob="true" class="bg-amber-100 text-amber-700 text-[10px] font-bold px-2 py-0.5 rounded-full">{count}</span>
                <div id="toast-container" hx-swap-oob="beforeend">
                    <div class="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-lg relative mb-2 shadow-lg font-bold animate-in slide-in-from-right-4 duration-300" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">
                        Client restriction removed for {user_name}.
                    </div>
                </div>
            ''', status=200)
        return redirect('core:access_management')
    return HttpResponseBadRequest("Invalid request")

@login_required
def user_delete_view(request, user_id):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if not request.user.is_superuser:
        return HttpResponseBadRequest("Not authorized")
    
    user_to_del = get_object_or_404(User, id=user_id)
    if user_to_del == request.user:
        return HttpResponseBadRequest("Cannot delete yourself")
        
    user_to_del.delete()
    return HttpResponse("", status=200)

@login_required
def user_create_view(request):
    from .forms import CreateUserForm
    if not request.user.is_superuser:
        return HttpResponseBadRequest("Not authorized")
        
    if request.method == 'POST':
        form = CreateUserForm(request.POST)
        if form.is_valid():
            user = form.save()
            return HttpResponse(f'''
                <div id="modal-container" hx-swap-oob="true"></div>
                <div id="toast-container" hx-swap-oob="beforeend">
                    <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded relative mb-2 shadow" role="alert" x-data="{{ show: true }}" x-show="show" x-init="setTimeout(() => show = false, 3000)">
                        <strong class="font-bold">Success!</strong>
                        <span class="block sm:inline">User {user.username} created. Please refresh the page to assign access.</span>
                    </div>
                </div>
            ''')
    else:
        form = CreateUserForm()
        
    return render(request, 'core/partials/create_user_modal.html', {'form': form})

@login_required
def user_edit_view(request, user_id):
    from .forms import UserEditForm
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    if not request.user.is_superuser:
        return HttpResponseForbidden("Not authorized")
    
    user_to_edit = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        form = UserEditForm(request.POST, request.FILES, instance=user_to_edit)
        if form.is_valid():
            user = form.save()
            return HttpResponse(f'''
                <div id="edit-user-modal" hx-swap-oob="true" style="display:none"></div>
                <script>window.location.reload();</script>
            ''')
    else:
        form = UserEditForm(instance=user_to_edit)
    
    return render(request, 'core/partials/edit_user_modal.html', {'form': form, 'user_obj': user_to_edit})

@login_required
def profile_edit_view(request):
    from .forms import UserEditForm
    user = request.user
    
    if request.method == 'POST':
        form = UserEditForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            form.save()
            return HttpResponse(f'''
                <div id="edit-user-modal" hx-swap-oob="true" style="display:none"></div>
                <script>window.location.reload();</script>
            ''')
    else:
        form = UserEditForm(instance=user)
    
    return render(request, 'core/partials/edit_user_modal.html', {'form': form, 'user_obj': user, 'is_profile': True})

@login_required
def get_client_ledger_context(request, pk):
    """
    Builds client ledger data from two sources:
      1. Virtual invoice entries — Fixed project budgets read directly from the Project
         model. No Transaction is ever created, so they never appear in the dashboard.
      2. Real payment transactions — Direct Income entries (DR Bank) when client pays,
         plus Subscription billing entries from process_monthly_billings.

    Ledger columns:
      DEBIT (SALE)  = amount billed / owed by client
      CREDIT (RECV) = payment received from client
    """
    client = get_object_or_404(Client, pk=pk)

    if request.user.is_superuser:
        process_monthly_billings(request.user)

    projects = client.projects.exclude(status__in=['Completed', 'Cancelled'])
    today = timezone.now().date()

    # --- Multi-currency logic ---
    selected_currency_code = request.GET.get('currency')
    base_currency = Currency.objects.filter(is_base=True).order_by('-code').first() # Prefer PKR over USD if both are base
    
    # Available currencies for this client's projects
    available_currencies = Currency.objects.filter(
        projects__client=client
    ).distinct()

    # Determine default display currency:
    # If client only has one currency, use that. Otherwise use global base.
    default_currency = base_currency
    if not selected_currency_code and available_currencies.count() == 1:
        default_currency = available_currencies.first()

    target_currency = None
    if selected_currency_code:
        target_currency = Currency.objects.filter(code=selected_currency_code).first()
    
    # If no currency selected, use our smart default
    display_currency = target_currency or default_currency
    
    if target_currency:
        # Filter to only show business in this currency
        projects = projects.filter(currency=target_currency)
    
    from_date_str = request.GET.get('from')
    to_date_str   = request.GET.get('to')
    from_start    = request.GET.get('from_start') == 'on'

    # Parse filter dates safely
    from_date = None
    to_date   = None
    if from_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if to_date_str:
        try:
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    if from_start:
        from_date = None  # Show everything; opening balance = 0

    # ------------------------------------------------------------------
    # Build a unified event list
    # ------------------------------------------------------------------
    all_events = []

    # 1. Fixed project invoices — virtual entries from Project.target_budget
    #    No Transaction is created; the budget is read directly from the model.
    for project in projects.filter(project_type='Fixed'):
        budget = project.target_budget
        if not budget or budget <= 0:
            continue

        # Calculate conversion to display currency
        # amount_in_display = amount * (rate_to_pkr / display_currency.rate_to_pkr)
        exchange_rate_pkr = project.currency.rate_to_pkr
        display_rate = display_currency.rate_to_pkr or Decimal('1.000000')
        conversion_factor = exchange_rate_pkr / display_rate

        event_date = project.start_date or today
        all_events.append({
            'date':         event_date,
            'sort_key':     (event_date, project.created_at),
            'reference':    f"INV-{event_date.strftime('%y%m')}-{project.id}",
            'description':  f"Project Invoice - {project.name}",
            'debit':        budget * conversion_factor,
            'credit':       Decimal('0.00'),
            'project_name': project.name,
        })

    # 2. Transaction-based events (payments + subscription billings)
    all_transactions = Transaction.objects.filter(
        project__in=projects
    ).prefetch_related('entries__account', 'project', 'currency').order_by('date', 'created_at')

    for txn in all_transactions:
        entries = list(txn.entries.all())

        has_rev  = any(e.account.account_type == 'REVENUE' and e.entry_type == 'CR' for e in entries)
        has_bank = any(
            e.account.account_type == 'ASSET' and e.entry_type == 'DR' and hasattr(e.account, 'bank_detail')
            for e in entries
        )
        is_direct_income = has_rev and has_bank

        debit  = Decimal('0.00')
        credit = Decimal('0.00')

        for entry in entries:
            # Conversion factor: from entry currency to display currency
            display_rate = display_currency.rate_to_pkr or Decimal('1.000000')
            conversion_factor = entry.exchange_rate / display_rate

            if entry.account.account_type == 'REVENUE' and entry.entry_type == 'CR':
                # Subscription billing (no bank DR) → billed amount column
                if not is_direct_income:
                    debit += entry.amount * conversion_factor
            elif entry.account.account_type == 'ASSET' and entry.entry_type == 'DR':
                # Bank debit = client paid us
                if hasattr(entry.account, 'bank_detail'):
                    credit += entry.amount * conversion_factor


        if debit > 0 or credit > 0:
            all_events.append({
                'date':         txn.date,
                'sort_key':     (txn.date, txn.created_at),
                'reference':    txn.reference or f"REF-{txn.id:04d}",
                'description':  txn.description,
                'debit':        debit,
                'credit':       credit,
                'project_name': txn.project.name if txn.project else 'N/A',
            })

    # Sort chronologically
    all_events.sort(key=lambda e: e['sort_key'])

    # ------------------------------------------------------------------
    # Calculate Balance B/F from events before from_date
    # ------------------------------------------------------------------
    balance_bf      = Decimal('0.00')
    running_balance = Decimal('0.00')

    if from_date and not from_start:
        for event in all_events:
            if event['date'] < from_date:
                balance_bf += event['debit'] - event['credit']
        running_balance = balance_bf

    # ------------------------------------------------------------------
    # Filter events for the display window and build ledger rows
    # ------------------------------------------------------------------
    total_billed = Decimal('0.00')
    total_paid   = Decimal('0.00')
    ledger_data  = []

    for event in all_events:
        if from_date and event['date'] < from_date:
            continue
        if to_date and event['date'] > to_date:
            continue

        running_balance += event['debit'] - event['credit']
        ledger_data.append({
            'date':            event['date'],
            'reference':       event['reference'],
            'description':     event['description'],
            'debit':           event['debit'],
            'credit':          event['credit'],
            'running_balance': running_balance,
            'project_name':    event['project_name'],
        })
        total_billed += event['debit']
        total_paid   += event['credit']

    base_currency   = Currency.objects.filter(is_base=True).first()
    currency_symbol = base_currency.symbol if base_currency else 'Rs'

    return {
        'client':              client,
        'ledger_data':         ledger_data[::-1],   # Newest first for display
        'balance_bf':          balance_bf,
        'total_billed':        total_billed,
        'total_paid':          total_paid,
        'outstanding_balance': running_balance,
        'currency_symbol':     display_currency.symbol if display_currency else 'Rs',
        'display_currency':    display_currency,
        'available_currencies': available_currencies,
        'selected_currency':   selected_currency_code,
        'from_date':           from_date_str,
        'to_date':             to_date_str,
        'base_currency':       base_currency,
    }

@login_required
def client_ledger_view(request, pk):
    context = get_client_ledger_context(request, pk)
    context['today'] = timezone.now().date()
    
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/client_ledger_partial.html', context)
    return render(request, 'core/client_ledger.html', context)

@login_required
def client_ledger_pdf_view(request, pk):
    """
    Generates a professional PDF ledger using reportlab.
    """
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from django.http import HttpResponse
    from django.contrib.humanize.templatetags.humanize import intcomma

    context = get_client_ledger_context(request, pk)
    client = context['client']
    ledger_data = context['ledger_data']
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Ledger_{client.name}_{timezone.now().strftime("%Y%m%d")}.pdf"'
    
    from io import BytesIO
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#111827'),
        spaceAfter=10
    )
    
    subtitle_style = ParagraphStyle(
        'SubtitleStyle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.grey,
        spaceAfter=20
    )

    # Helper for consistent amount formatting
    def fmt_amt(val):
        if not val: return "--"
        try:
            return f"{float(val):,.2f}"
        except (ValueError, TypeError):
            return str(val)

    # 1. Header with styling
    from datetime import datetime
    elements.append(Paragraph(f"Client Ledger: {client.name}", title_style))
    
    # Format dates for header
    period_from = datetime.strptime(context['from_date'], '%Y-%m-%d').strftime('%d %b %Y') if context['from_date'] else "All Time"
    period_to = datetime.strptime(context['to_date'], '%Y-%m-%d').strftime('%d %b %Y') if context['to_date'] else "Present"
    date_range_str = f"Statement Period: {period_from} — {period_to}"
    
    elements.append(Paragraph(date_range_str, subtitle_style))
    
    # Generated on info
    gen_style = ParagraphStyle('GenStyle', parent=styles['Normal'], fontSize=8, textColor=colors.grey, alignment=2)
    elements.append(Paragraph(f"Generated on {timezone.now().strftime('%d %b %Y, %H:%M')}", gen_style))
    elements.append(Spacer(1, 15))
    
    # 2. Summary Table (Modern Look)
    summary_data = [
        ['Total Billed', 'Total Paid', 'Outstanding Balance'],
        [
            f"{context['currency_symbol']} {fmt_amt(context['total_billed'])}", 
            f"{context['currency_symbol']} {fmt_amt(context['total_paid'])}", 
            f"{context['currency_symbol']} {fmt_amt(context['outstanding_balance'])}"
        ]
    ]
    summary_table = Table(summary_data, colWidths=[175, 175, 175])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f8fafc')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#475569')),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('TOPPADDING', (0,0), (-1,0), 12),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('TEXTCOLOR', (0,1), (-1,1), colors.HexColor('#0f172a')),
        ('FONTNAME', (0,1), (-1,1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,1), (-1,1), 15),
        ('BOTTOMPADDING', (0,1), (-1,1), 15),
        ('LINEBELOW', (2,1), (2,1), 2, colors.HexColor('#10b981')), # Success color underline for balance
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 30))
    
    # 3. Main Ledger Table
    data = [['Date', 'Ref', 'Description', 'Debit (Owed)', 'Credit (Paid)', 'Balance']]
    
    # Add Balance B/F row
    if context.get('balance_bf', 0) != 0:
        data.append([
            "--",
            "--",
            "Balance Brought Forward (B/F)",
            "--",
            "--",
            fmt_amt(context['balance_bf'])
        ])

    # Re-reverse to chronological for report
    report_list = ledger_data[::-1]
    
    for item in report_list:
        data.append([
            item['date'].strftime("%d-%m-%Y"),
            item['reference'],
            Paragraph(item['description'], styles['Normal']),
            fmt_amt(item['debit']) if item['debit'] > 0 else "--",
            fmt_amt(item['credit']) if item['credit'] > 0 else "--",
            fmt_amt(item['running_balance'])
        ])
    
    # Column widths adjusted for A4
    table = Table(data, colWidths=[65, 60, 185, 75, 75, 75])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,0), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('BOTTOMPADDING', (0,0), (-1,0), 10),
        ('TOPPADDING', (0,0), (-1,0), 10),
        
        ('TEXTCOLOR', (0,1), (-1,-1), colors.HexColor('#334155')),
        ('ALIGN', (3,1), (-1,-1), 'RIGHT'), # Amounts to the right
        ('FONTNAME', (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.1, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]), # Zebra striping
    ]))
    
    elements.append(table)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("--- End of Statement ---", ParagraphStyle('EndStyle', parent=styles['Normal'], alignment=1, textColor=colors.grey, fontSize=7)))
    
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    
    response.write(pdf)
    return response

def process_monthly_billings(user=None):
    """
    Standard billing logic: Finds projects due for billing and generates transactions.
    Usable by both manual and automated processes.
    """
    today = timezone.now().date()
    current_month_start = today.replace(day=1)
    
    # Identify Subscription projects that haven't been billed yet this month.
    # We include 'Pipeline / Prospect' as requested by the user, and 'Active / In Progress'.
    projects_to_bill = Project.objects.filter(
        project_type='Subscription',
        status__in=['Pipeline / Prospect', 'Active / In Progress']
    ).filter(
        Q(last_billed_date__lt=current_month_start) | Q(last_billed_date__isnull=True)
    )
    
    if not projects_to_bill.exists():
        return 0, "No pending monthly billings found for this period."

    revenue_account = Account.objects.filter(account_type=AccountType.REVENUE, is_active=True).first()
    if not revenue_account:
        return 0, "System requires at least one active Revenue account."
        
    billed_count = 0
    with db_transaction.atomic():
        for project in projects_to_bill:
            description = f"Monthly Retainer - {today.strftime('%B %Y')}"
            
            # Create the parent transaction
            txn = Transaction.objects.create(
                date=today,
                description=description,
                reference=f"AUTO-{today.strftime('%y%m')}-{project.id}",
                project=project,
                currency=project.currency,
                created_by=user
            )
            
            exchange_rate = Decimal('1.000000')
            if project.currency and not project.currency.is_base:
                exchange_rate = project.currency.rate_to_pkr
                
            LedgerEntry.objects.create(
                transaction=txn,
                account=revenue_account,
                entry_type=LedgerEntry.CR, 
                amount=project.monthly_fee,
                exchange_rate=exchange_rate
            )
            
            # Update the project last_billed_date
            Project.objects.filter(id=project.id).update(last_billed_date=today)
            billed_count += 1
            
    return billed_count, f"{billed_count} monthly retainers have been logged."

@login_required
def run_monthly_billing_view(request):
    if not request.user.is_superuser:
         return HttpResponseForbidden("Only administrators can perform bulk billing.")
    
    billed_count, msg = process_monthly_billings(request.user)
    
    if request.headers.get('HX-Request'):
         if billed_count == 0:
             return HttpResponse(f'<div class="p-4 bg-gray-50 text-gray-500 text-[10px] font-bold uppercase rounded-lg border border-gray-100 mb-4">{msg}</div>')
         
         return HttpResponse(f'<div class="p-4 bg-emerald-50 text-emerald-700 text-[10px] font-bold uppercase rounded-lg border border-emerald-500/20 mb-4 flex items-center"><svg class="w-4 h-4 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg> Success: {msg}</div>')
         
    return redirect('core:dashboard')
            
    # Success response
    if request.headers.get('HX-Request'):
         msg = f"Success: {billed_count} monthly retainers have been logged to ledgers."
         return HttpResponse(f'<div class="p-4 bg-emerald-50 text-emerald-700 text-[10px] font-bold uppercase rounded-lg border border-emerald-500/20 mb-4 flex items-center"><svg class="w-4 h-4 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg> {msg}</div>')
         
    return redirect('core:dashboard')

@login_required
@login_required
def charity_view(request):
    """
    Charity Fund Dashboard.
    Calculates charity based on:
      - Inflows: Transactions logged to the "Charity" Revenue account
      - Outflows: Transactions logged to the "Charity" Expense account
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only administrators can access the Charity dashboard.")

    from datetime import timedelta
    from django.db.models import F, Sum, Q

    today = timezone.now().date()
    
    # Identify the two Charity accounts
    charity_revenue_acc = Account.objects.filter(name__icontains='Charity', account_type=AccountType.REVENUE, is_active=True).first()
    charity_expense_acc = Account.objects.filter(name__icontains='Charity', account_type=AccountType.EXPENSE, is_active=True).first()

    # 1. Calculate Total Inflows (Funded)
    total_inflow = Decimal('0.00')
    if charity_revenue_acc:
        inflow_qs = LedgerEntry.objects.filter(
            account=charity_revenue_acc,
            entry_type='CR' # Revenue increased by Credit
        ).annotate(
            base_amt=F('amount') * F('exchange_rate')
        ).aggregate(total=Sum('base_amt'))['total']
        total_inflow = inflow_qs or Decimal('0.00')

    # 2. Calculate Total Outflows (Spent)
    total_outflow = Decimal('0.00')
    if charity_expense_acc:
        outflow_qs = LedgerEntry.objects.filter(
            account=charity_expense_acc,
            entry_type='DR' # Expense increased by Debit
        ).annotate(
            base_amt=F('amount') * F('exchange_rate')
        ).aggregate(total=Sum('base_amt'))['total']
        total_outflow = outflow_qs or Decimal('0.00')

    # 3. Current Fund Balance
    fund_balance = total_inflow - total_outflow

    # 4. Transaction Breakdown (List of all charity-related movements)
    charity_account_ids = []
    if charity_revenue_acc: charity_account_ids.append(charity_revenue_acc.id)
    if charity_expense_acc: charity_account_ids.append(charity_expense_acc.id)

    all_charity_txns = Transaction.objects.filter(
        entries__account_id__in=charity_account_ids
    ).prefetch_related(
        'entries__account', 'project'
    ).distinct().order_by('-date', '-created_at')

    # Prepare data for the breakdown table
    transaction_data = []
    for txn in all_charity_txns:
        # Find the specific entry for one of our charity accounts
        # Sum all charity-related entries for this transaction to handle splits correctly
        charity_entries = txn.entries.filter(account_id__in=charity_account_ids)
        if not charity_entries.exists(): continue

        # Use the first one to determine type, but sum amounts
        first_entry = charity_entries.first()
        is_inflow = (first_entry.account.account_type == AccountType.REVENUE)
        total_charity_amt = sum(e.get_base_amount() for e in charity_entries)
        
        transaction_data.append({
            'date': txn.date,
            'description': txn.description,
            'project': txn.project,
            'bank': txn.get_bank_account_name(),
            'type': 'Inflow' if is_inflow else 'Outflow',
            'amount': total_charity_amt,
            'currency': txn.currency.symbol if txn.currency else 'Rs',
            'status': 'Success'
        })

    # 5. Monthly Trend (last 6 months of fund activity)
    monthly_trend = []
    for i in range(5, -1, -1):
        m_start = (today.replace(day=1) - timedelta(days=i * 30)).replace(day=1)
        if i > 0:
            m_end = (m_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        else:
            m_end = today

        m_inflow = Decimal('0.00')
        if charity_revenue_acc:
            m_inflow = LedgerEntry.objects.filter(
                account=charity_revenue_acc,
                entry_type='CR',
                transaction__date__range=[m_start, m_end]
            ).annotate(base_amt=F('amount') * F('exchange_rate')).aggregate(total=Sum('base_amt'))['total'] or Decimal('0.00')

        m_outflow = Decimal('0.00')
        if charity_expense_acc:
            m_outflow = LedgerEntry.objects.filter(
                account=charity_expense_acc,
                entry_type='DR',
                transaction__date__range=[m_start, m_end]
            ).annotate(base_amt=F('amount') * F('exchange_rate')).aggregate(total=Sum('base_amt'))['total'] or Decimal('0.00')

        monthly_trend.append({
            'label': m_start.strftime('%b %y'),
            'inflow': m_inflow,
            'outflow': m_outflow,
            'net': m_inflow - m_outflow,
            'amount': m_outflow # Compat for chart which uses .amount
        })

    # Metrics for the template
    context = {
        'total_inflow': total_inflow,
        'total_outflow': total_outflow,
        'fund_balance': fund_balance,
        'transaction_data': transaction_data, # Replaces project_data
        'monthly_trend': monthly_trend,
        'fulfillment_pct': round((total_outflow / total_inflow * 100), 1) if total_inflow > 0 else 0,
        'grand_total_owed': total_inflow, # Mapping for UI compatibility
        'grand_total_paid': total_outflow, # Mapping for UI compatibility
        'grand_balance': fund_balance, # Mapping for UI compatibility
    }
    return render(request, 'core/charity_dashboard.html', context)

@login_required
def employee_performance_list_view(request):
    """
    Overview of all employees and their commission earnings/payouts.
    """
    q = request.GET.get('q', '')
    employees_base = Employee.objects.all()
    if not request.user.is_superuser:
        employees_base = employees_base.filter(created_by=request.user)
    
    if q:
        employees_base = employees_base.filter(name__icontains=q) | employees_base.filter(designation__icontains=q)
    
    analytics = []
    for emp in employees_base:
        txns = Transaction.objects.filter(project_leader=emp)
        
        total_earned = Decimal('0.00')
        total_paid = Decimal('0.00')
        
        for txn in txns:
            if txn.get_type_display() == 'Income':
                total_earned += txn.get_base_lead_commission_amount()
            elif txn.get_type_display() == 'Expense':
                total_paid += txn.get_base_total_amount()
        
        balance = total_earned - total_paid

        
        analytics.append({
            'employee': emp,
            'earned': total_earned,
            'paid': total_paid,
            'balance': balance,
            'status': 'Outstanding' if balance > 0 else 'Settled'
        })
    
    balance_total = sum(item['balance'] for item in analytics)
    earned_total = sum(item['earned'] for item in analytics)
    paid_total = sum(item['paid'] for item in analytics)
    base_currency = Currency.objects.filter(is_base=True).first()
    
    context = {
        'analytics': analytics,
        'balance_total': balance_total,
        'earned_total': earned_total,
        'paid_total': paid_total,
        'base_currency': base_currency,
        'title': 'Project Leader Registry'
    }

    return render(request, 'core/employee_performance_list.html', context)


@login_required
def employee_performance_view(request, pk):
    """
    Detailed ledger for a specific project leader.
    Shows earned commissions vs payouts.
    """
    employee = get_object_or_404(Employee, pk=pk)
    
    # RBAC
    if not request.user.is_superuser and employee.created_by != request.user:
        from django.http import Http404
        raise Http404

    
    txns = Transaction.objects.filter(project_leader=employee).order_by('-date', '-created_at')
    
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
            amount = txn.get_base_total_amount()
            total_paid += amount
            is_payout = True

            
        performance_ledger.append({
            'transaction': txn,
            'amount': amount,
            'is_payout': is_payout,
            'running_balance': total_earned - total_paid # This will be wrong in loop order, will fix below
        })
    
    # Recalculate running balance correctly (bottom-up)
    current_bal = Decimal('0.00')
    for item in reversed(performance_ledger):
        if item['is_payout']:
            current_bal -= item['amount']
        else:
            current_bal += item['amount']
        item['running_balance'] = current_bal

    base_currency = Currency.objects.filter(is_base=True).first()
    context = {
        'employee': employee,
        'ledger': performance_ledger,
        'total_earned': total_earned,
        'total_paid': total_paid,
        'balance': total_earned - total_paid,
        'base_currency': base_currency
    }

    return render(request, 'core/employee_performance_detail.html', context)
