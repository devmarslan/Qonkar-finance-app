import django_filters
from django import forms
from .models import Transaction, Project, Client, BankAccount, Account, AccountType

class TransactionFilter(django_filters.FilterSet):
    project = django_filters.ModelChoiceFilter(
        queryset=Project.objects.all(),
        empty_label="All Projects",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg shadow-sm focus:ring-brand-500 text-sm py-2.5 px-4 bg-white'})
    )
    bank = django_filters.ModelChoiceFilter(
        queryset=BankAccount.objects.filter(is_active=True),
        empty_label="All Banks",
        method='filter_bank',
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg shadow-sm focus:ring-brand-500 text-sm py-2.5 px-4 bg-white'})
    )
    category = django_filters.ModelChoiceFilter(
        queryset=Account.objects.filter(account_type__in=[AccountType.REVENUE, AccountType.EXPENSE]),
        empty_label="All Categories",
        method='filter_category',
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg shadow-sm focus:ring-brand-500 text-sm py-2.5 px-4 bg-white'})
    )
    
    # Custom choices for common time frames
    TIME_CHOICES = (
        ('all', 'All Time'),
        ('today', 'Today'),
        ('this_week', 'This Week'),
        ('this_month', 'This Month'),
        ('this_year', 'This Year'),
    )
    duration = django_filters.ChoiceFilter(
        choices=TIME_CHOICES, method='filter_duration',
        label="Time Frame",
        empty_label="Select Duration",
        widget=forms.Select(attrs={'class': 'form-select block w-full border-gray-200 rounded-lg shadow-sm focus:ring-brand-500 text-sm py-2.5 px-4 bg-white'})
    )
    
    description = django_filters.CharFilter(
        lookup_expr='icontains',
        widget=forms.TextInput(attrs={'class': 'form-input block w-full border-gray-200 rounded-lg shadow-sm focus:ring-brand-500 text-sm py-2.5 px-4 bg-white', 'placeholder': 'Search description, category...'})
    )

    date_after = django_filters.DateFilter(
        field_name='date', lookup_expr='gte',
        label="From Date",
        widget=forms.TextInput(attrs={'class': 'datepicker-input form-input block w-full border-gray-200 rounded-lg shadow-sm focus:ring-brand-500 text-sm py-2.5 px-4 bg-white', 'placeholder': 'From Date'})
    )
    date_before = django_filters.DateFilter(
        field_name='date', lookup_expr='lte',
        label="To Date",
        widget=forms.TextInput(attrs={'class': 'datepicker-input form-input block w-full border-gray-200 rounded-lg shadow-sm focus:ring-brand-500 text-sm py-2.5 px-4 bg-white', 'placeholder': 'To Date'})
    )

    def filter_duration(self, queryset, name, value):
        from django.utils import timezone
        import datetime
        now = timezone.now()
        if value == 'today':
            return queryset.filter(date=now.date())
        elif value == 'this_week':
            start_week = now.date() - datetime.timedelta(days=now.weekday())
            return queryset.filter(date__gte=start_week)
        elif value == 'this_month':
            return queryset.filter(date__year=now.year, date__month=now.month)
        elif value == 'this_year':
            return queryset.filter(date__year=now.year)
        return queryset

    def filter_bank(self, queryset, name, value):
        if value:
            return queryset.filter(entries__account__bank_detail=value).distinct()
        return queryset

    def filter_category(self, queryset, name, value):
        if value:
            return queryset.filter(entries__account=value).distinct()
        return queryset

    class Meta:
        model = Transaction
        fields = ['project', 'bank', 'category', 'duration', 'description', 'date_after', 'date_before']
