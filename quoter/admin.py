from django.contrib import admin

from .models import (CableSeries, CableSpec, Customer, CustomerTier, InquiryLead,
                     InquiryLeadItem, Material, MaterialPrice, Payment,
                     PricingParams, QuoteSendLog, Quotation, QuotationItem,
                     SalesOrder)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'unit', 'density', 'loss_rate', 'latest_price_val')
    search_fields = ('code', 'name')

    @admin.display(description='最新价')
    def latest_price_val(self, obj):
        mp = obj.latest_price()
        return '%s (%s)' % (mp.price, mp.date) if mp else '未录价'


@admin.register(MaterialPrice)
class MaterialPriceAdmin(admin.ModelAdmin):
    list_display = ('material', 'date', 'price', 'premium', 'source', 'is_auto',
                    'created_at')
    list_filter = ('material', 'source', 'date')


class CableSpecInline(admin.TabularInline):
    model = CableSpec
    extra = 0
    fields = ('layout', 'conductor_material', 'cores_total', 'section_total',
              'conductor_weight', 'insulation_weight', 'sheath_weight', 'filler_weight')


@admin.register(CableSeries)
class CableSeriesAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'voltage', 'conductor', 'active')
    inlines = [CableSpecInline]


@admin.register(CableSpec)
class CableSpecAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'voltage', 'cores_total', 'section_total',
                    'conductor_weight', 'insulation_weight', 'sheath_weight')
    list_filter = ('series',)
    search_fields = ('layout',)


class CustomerTierInline(admin.TabularInline):
    model = CustomerTier
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'level', 'discount', 'contact', 'phone')
    list_filter = ('level',)
    search_fields = ('name',)
    inlines = [CustomerTierInline]


@admin.register(PricingParams)
class PricingParamsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'target_margin_pct', 'min_margin_pct',
                    'price_with_tax', 'quote_valid_days')

    def has_add_permission(self, request):
        return not PricingParams.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class QuotationItemInline(admin.TabularInline):
    model = QuotationItem
    extra = 0
    readonly_fields = ('spec_text', 'length_m', 'list_price_per_m',
                       'final_price_per_m', 'amount', 'cost_detail')


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ('number', 'customer', 'created_by', 'created_at', 'amount',
                    'status_label', 'paid_display')
    list_filter = ('status', 'customer')
    search_fields = ('number', 'customer__name', 'quotation__number')
    inlines = [PaymentInline]

    @admin.display(description='已回款')
    def paid_display(self, obj):
        return obj.paid_amount()


@admin.register(QuoteSendLog)
class QuoteSendLogAdmin(admin.ModelAdmin):
    list_display = ('quotation', 'sent_to', 'sent_by', 'sent_at', 'note')
    search_fields = ('quotation__number', 'sent_to')


class InquiryLeadItemInline(admin.TabularInline):
    model = InquiryLeadItem
    extra = 0
    readonly_fields = ('spec_text', 'length_m', 'quoted_price_per_m')


@admin.register(InquiryLead)
class InquiryLeadAdmin(admin.ModelAdmin):
    list_display = ('company', 'contact_name', 'phone', 'created_at', 'status',
                    'quotation_link')
    list_filter = ('status',)
    search_fields = ('company', 'contact_name', 'phone')
    inlines = [InquiryLeadItemInline]

    @admin.display(description='报价单')
    def quotation_link(self, obj):
        return obj.quotation.number if obj.quotation else ''


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ('number', 'customer', 'created_by', 'created_at',
                    'valid_until', 'status_label', 'discount')
    list_filter = ('status', 'customer')
    search_fields = ('number', 'customer__name')
    inlines = [QuotationItemInline]
