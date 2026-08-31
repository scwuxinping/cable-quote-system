from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Material(models.Model):
    """报价涉及的材料（铜、铝、XLPE、PVC、填充等），价格按日维护。"""

    UNIT_KG = '元/kg'
    UNIT_T = '元/t'
    UNIT_CHOICES = [(UNIT_KG, '元/kg'), (UNIT_T, '元/t')]

    code = models.CharField('材料代码', max_length=20, unique=True)
    name = models.CharField('材料名称', max_length=50)
    unit = models.CharField('计价单位', max_length=10, choices=UNIT_CHOICES, default=UNIT_KG)
    density = models.DecimalField(
        '密度 g/cm³', max_digits=6, decimal_places=3, default=0,
        help_text='用于理论重量计算，如铜 8.89、铝 2.703、XLPE 0.92、PVC 1.40')
    loss_rate = models.DecimalField(
        '损耗率 %', max_digits=5, decimal_places=2, default=2,
        help_text='投料损耗，成本按 (1+损耗率) 放大')
    notes = models.CharField('备注', max_length=200, blank=True)

    class Meta:
        verbose_name = '材料'
        verbose_name_plural = '材料'

    def __str__(self):
        return f'{self.name}({self.code})'

    def latest_price(self):
        return self.prices.order_by('-date').first()

    def price_on(self, date):
        """取指定日期（含）之前最近的一次报价。"""
        return self.prices.filter(date__lte=date).order_by('-date').first()


class MaterialPrice(models.Model):
    """材料价格记录。支持自动获取（is_auto=True）与管理员手工录入/修正。

    计价采用“最新一条”记录：手工修正会覆盖当天的自动抓取结果（同一天
    update_or_create），来源与生效时间留痕。
    """

    SOURCE_MANUAL = 'manual'
    SOURCE_CJ = 'changjiang'      # 长江有色现货
    SOURCE_SHFE = 'shfe'          # 上海期货
    SOURCE_LME = 'lme'            # LME（美元，抓取时已按汇率换算为 元/kg）
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, '手工录入/修正'),
        (SOURCE_CJ, '长江现货'),
        (SOURCE_SHFE, '上海期货'),
        (SOURCE_LME, 'LME'),
    ]

    material = models.ForeignKey(
        Material, on_delete=models.CASCADE, related_name='prices', verbose_name='材料')
    date = models.DateField('生效日期', default=timezone.localdate)
    price = models.DecimalField('单价', max_digits=12, decimal_places=4,
                                help_text='统一换算为 元/kg 后存储')
    source = models.CharField('价格来源', max_length=20, choices=SOURCE_CHOICES,
                              default=SOURCE_MANUAL)
    premium = models.DecimalField(
        '升贴水 元/kg', max_digits=10, decimal_places=4, default=Decimal('0'),
        help_text='计价时叠加在基准价上，可为负')
    exchange_rate = models.DecimalField(
        '汇率 USD/CNY', max_digits=10, decimal_places=4, null=True, blank=True,
        help_text='LME 等外币来源抓取时的换算汇率（留痕）')
    is_auto = models.BooleanField('自动获取', default=False)
    created_at = models.DateTimeField('录入时间', default=timezone.now)

    class Meta:
        verbose_name = '材料价格'
        verbose_name_plural = '材料价格'
        ordering = ['-date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['material', 'date'], name='uniq_material_date'),
        ]

    def __str__(self):
        return '%s %s %s (%s)' % (self.material.code, self.date, self.price,
                                  self.get_source_display())

    @property
    def effective_price(self):
        """基准价 + 升贴水（元/kg）。"""
        return self.price + (self.premium or Decimal('0'))


class CableSeries(models.Model):
    """电缆型号系列，如 YJV / VV / BV。计价几何参数挂在系列上，可在后台调整。"""

    CU = 'CU'
    AL = 'AL'
    CONDUCTOR_CHOICES = [(CU, '铜'), (AL, '铝')]

    code = models.CharField('型号', max_length=20, unique=True)
    name = models.CharField('名称', max_length=100)
    voltage = models.CharField('电压等级', max_length=20, default='0.6/1kV')
    conductor = models.CharField(
        '导体材料', max_length=2, choices=CONDUCTOR_CHOICES, default=CU)
    insulation = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name='series_insulation',
        verbose_name='绝缘材料', limit_choices_to={'code__in': ['XLPE', 'PVC']})
    has_sheath = models.BooleanField('有护套', default=True)
    sheath_material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name='series_sheath',
        verbose_name='护套材料', null=True, blank=True)
    has_armor = models.BooleanField('有铠装/屏蔽', default=False,
                                    help_text='如 YJV22 钢带铠装、屏蔽型电缆')
    armor_material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name='series_armor',
        verbose_name='铠装/屏蔽材料', null=True, blank=True)
    armor_steel_table = models.JSONField(
        '铠装钢带厚度表', default=dict, blank=True,
        help_text='按成缆外径段配单层钢带厚度(mm)，如 {"15": 0.2, "25": 0.3, "40": 0.5}；'
                  '留空用内置四档')
    # 理论重量计算参数（JSON，可在后台按厂标微调）：
    # 绝缘厚度表 {"截面": 厚度mm}，生成芯数组合 ["1","2","3","3+1","4","5"]，
    # 生成截面列表，导体直径系数，绞入系数
    insulation_thickness = models.JSONField('绝缘厚度表 mm', default=dict)
    core_options = models.JSONField('芯数组合', default=list)
    sections = models.JSONField('生成截面列表', default=list)
    conductor_od_factor = models.DecimalField(
        '导体直径系数', max_digits=5, decimal_places=3, default=1.18,
        help_text='导体外径 ≈ 系数 × √截面（绞合紧压）')
    lay_factor = models.DecimalField('绞合系数', max_digits=5, decimal_places=3,
                                     default=Decimal('1.02'))
    active = models.BooleanField('启用', default=True)

    class Meta:
        verbose_name = '型号系列'
        verbose_name_plural = '型号系列'

    def __str__(self):
        return self.code


class CableSpec(models.Model):
    """具体规格：系列 + 芯数截面组合。重量为理论计算值，可手工校准。"""

    series = models.ForeignKey(
        CableSeries, on_delete=models.CASCADE, related_name='specs', verbose_name='系列')
    layout = models.CharField(
        '规格代码', max_length=50,
        help_text='规范写法，如 3x120+1x70（x 为半角，多芯等截面如 4x16）')
    voltage = models.CharField('电压等级', max_length=20, blank=True)
    conductor_material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name='specs_conductor',
        verbose_name='导体材料')
    cores_total = models.IntegerField('总芯数', default=1)
    section_total = models.DecimalField(
        '截面合计 mm²', max_digits=8, decimal_places=1, default=0,
        help_text='Σ(芯数×截面)，用于加工费分档')
    conductor_weight = models.DecimalField(
        '导体重量 kg/km', max_digits=10, decimal_places=2, default=0)
    insulation_weight = models.DecimalField(
        '绝缘重量 kg/km', max_digits=10, decimal_places=2, default=0)
    sheath_weight = models.DecimalField(
        '护套重量 kg/km', max_digits=10, decimal_places=2, default=0)
    armor_weight = models.DecimalField(
        '屏蔽/铠装重量 kg/km', max_digits=10, decimal_places=2, default=0)
    filler_weight = models.DecimalField(
        '填充绕包重量 kg/km', max_digits=10, decimal_places=2, default=0)
    note = models.CharField('备注', max_length=200, blank=True)

    class Meta:
        verbose_name = '规格'
        verbose_name_plural = '规格库'
        ordering = ['series__code', 'layout']
        constraints = [
            models.UniqueConstraint(fields=['series', 'layout'], name='uniq_series_layout'),
        ]

    def __str__(self):
        return self.full_name

    @property
    def display(self):
        return self.layout.replace('x', '×')

    @property
    def full_name(self):
        return f'{self.series.code} {self.display}'


class Customer(models.Model):
    LEVEL_CHOICES = [('A', 'A类（重点）'), ('B', 'B类（普通）'), ('C', 'C类（零售）')]

    name = models.CharField('客户名称', max_length=100, unique=True)
    level = models.CharField('客户等级', max_length=1, choices=LEVEL_CHOICES, default='B')
    discount = models.DecimalField(
        '等级折扣', max_digits=5, decimal_places=3, default=Decimal('1.000'),
        help_text='报价单默认折扣，1.000 表示不打折')
    contact = models.CharField('联系人', max_length=50, blank=True)
    phone = models.CharField('电话', max_length=30, blank=True)
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '客户'
        verbose_name_plural = '客户'
        ordering = ['name']

    def __str__(self):
        return self.name

    def tier_discount(self, total_length):
        """按整单总长度匹配阶梯价：取满足起定量的最高档折扣，无匹配返回 None。"""
        tier = (self.tiers.filter(min_length_m__lte=total_length)
                .order_by('-min_length_m').first())
        return tier.discount if tier else None


class CustomerTier(models.Model):
    """客户阶梯价：整单总长度达到 min_length_m 即享 discount。"""

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name='tiers', verbose_name='客户')
    min_length_m = models.DecimalField('起订总长度 m', max_digits=12, decimal_places=2)
    discount = models.DecimalField('折扣', max_digits=5, decimal_places=3)
    note = models.CharField('备注', max_length=100, blank=True)

    class Meta:
        verbose_name = '客户阶梯价'
        verbose_name_plural = '客户阶梯价'
        ordering = ['customer', 'min_length_m']
        constraints = [
            models.UniqueConstraint(fields=['customer', 'min_length_m'],
                                    name='uniq_tier_customer_length'),
        ]

    def __str__(self):
        return '%s ≥%sm → %s' % (self.customer.name, self.min_length_m, self.discount)


class PricingParams(models.Model):
    """全局计价参数（单例，id=1）。"""

    company_name = models.CharField('公司名称', max_length=100, default='某某电缆有限公司')
    company_phone = models.CharField('联系电话', max_length=50, blank=True)
    company_address = models.CharField('地址', max_length=200, blank=True)
    quote_note = models.CharField(
        '报价单脚注', max_length=300, blank=True,
        default='以上报价基于当日铜价，有效期后请重新询价；数量与交期以合同为准。')

    target_margin_pct = models.DecimalField('目标毛利率 %', max_digits=5, decimal_places=2,
                                            default=Decimal('20'))
    min_margin_pct = models.DecimalField('最低毛利率 %', max_digits=5, decimal_places=2,
                                         default=Decimal('8'),
                                         help_text='低于该毛利的折扣需要经理审批')
    tax_rate_pct = models.DecimalField('增值税率 %', max_digits=5, decimal_places=2,
                                       default=Decimal('13'))
    price_with_tax = models.BooleanField('报价含税', default=True)
    process_fee_base = models.DecimalField(
        '加工费基础 元/km', max_digits=10, decimal_places=2, default=Decimal('150'))
    process_fee_per_section = models.DecimalField(
        '加工费/截面系数 元/(km·mm²)', max_digits=8, decimal_places=3,
        default=Decimal('1.200'),
        help_text='加工费 = 基础 + 系数×截面合计 + 材料成本×百分比')
    process_fee_pct_material = models.DecimalField(
        '人工及制造费用(材料成本%)', max_digits=5, decimal_places=2, default=Decimal('1'),
        help_text='制造费用分摊：按材料成本的百分比计入')
    quote_valid_days = models.IntegerField('报价有效期（天）', default=3)
    boss_threshold_amount = models.DecimalField(
        '大单审批阈值 元', max_digits=14, decimal_places=2, default=Decimal('500000'),
        help_text='整单金额达到该值时，经理审批后还需老板终审')
    portal_enabled = models.BooleanField('启用客户自助询价门户', default=True)
    portal_code = models.CharField(
        '门户访问码', max_length=30, blank=True, default='',
        help_text='留空则无需访问码；设置后客户需输入访问码进入')
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '计价参数'
        verbose_name_plural = '计价参数'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return '计价参数'


class Quotation(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PENDING = 'pending'
    STATUS_BOSS_PENDING = 'boss_pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_DRAFT, '草稿'),
        (STATUS_PENDING, '待审批'),
        (STATUS_BOSS_PENDING, '待老板终审'),
        (STATUS_APPROVED, '已审批'),
        (STATUS_REJECTED, '已驳回'),
    ]

    number = models.CharField('报价单号', max_length=30, unique=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='quotations', verbose_name='客户')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='quotations_created',
        verbose_name='业务员')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    valid_until = models.DateField('有效期至')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT)
    discount = models.DecimalField(
        '整单折扣', max_digits=5, decimal_places=3, default=Decimal('1.000'),
        help_text='在目录价基础上的折扣，0.95 即 95 折')
    freight = models.DecimalField(
        '运费 元', max_digits=12, decimal_places=2, default=Decimal('0'),
        help_text='整单运费，计入报价合计（销售报价 = 货款 + 运费）')
    base_cu_price = models.DecimalField(
        '基准铜价 元/kg', max_digits=10, decimal_places=4, default=0,
        help_text='报价时铜价快照（含升贴水），打印在报价单上')
    cu_price_source = models.CharField(
        '铜价来源', max_length=20, blank=True, default='')
    cu_price_time = models.DateTimeField('铜价生效时间', null=True, blank=True)
    note = models.TextField('备注', blank=True)
    share_token = models.CharField(
        '客户链接令牌', max_length=40, blank=True, default='', db_index=True,
        help_text='生成后客户可通过 /q/<token>/ 免登录查看并签收报价单')
    signed_at = models.DateTimeField('客户签收时间', null=True, blank=True)
    signed_by = models.CharField('签收人', max_length=50, blank=True, default='')

    class Meta:
        verbose_name = '报价单'
        verbose_name_plural = '报价单'
        ordering = ['-created_at']

    def __str__(self):
        return self.number

    @property
    def status_label(self):
        return dict(self.STATUS_CHOICES).get(self.status, self.status)

    @property
    def is_expired(self):
        return self.valid_until < timezone.localdate()

    def total_amount(self):
        """价税合计 = 货款小计 + 运费。"""
        return sum((item.amount for item in self.items.all()),
                   Decimal('0')) + (self.freight or Decimal('0'))

    def margin_pct(self):
        """整单实际毛利率（含税口径先还原为不含税再计算）。"""
        cost = Decimal('0')
        revenue_ex_tax = Decimal('0')
        for item in self.items.all():
            snap = item.cost_detail or {}
            cost += Decimal(str(snap.get('cost_km', 0))) * item.length_m / Decimal('1000')
            tax_mult = Decimal(str(snap.get('tax_mult', '1')))
            revenue_ex_tax += item.final_price_per_m * item.length_m / tax_mult
        if self.items.count() == 0 or revenue_ex_tax == 0:
            return None
        return float((revenue_ex_tax / cost - 1) * 100)

    def min_margin(self):
        return float(PricingParams.load().min_margin_pct)


class QuotationItem(models.Model):
    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name='items', verbose_name='报价单')
    spec = models.ForeignKey(
        CableSpec, on_delete=models.PROTECT, related_name='quote_items', verbose_name='规格')
    spec_text = models.CharField('规格快照', max_length=100, blank=True)
    length_m = models.DecimalField('长度 m', max_digits=12, decimal_places=2)
    list_price_per_m = models.DecimalField(
        '目录单价 元/m', max_digits=10, decimal_places=4, default=0)
    final_price_per_m = models.DecimalField(
        '成交单价 元/m', max_digits=10, decimal_places=4, default=0)
    amount = models.DecimalField('金额 元', max_digits=14, decimal_places=2, default=0)
    cost_detail = models.JSONField('计价明细快照', default=dict, blank=True)
    sort_order = models.IntegerField('排序', default=0)

    class Meta:
        verbose_name = '报价明细'
        verbose_name_plural = '报价明细'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.spec_text or self.spec} × {self.length_m}m'


class SalesOrder(models.Model):
    """销售订单：由已审批报价单一键转化，跟踪发货与回款。"""

    STATUS_PRODUCTION = 'production'
    STATUS_SHIPPED = 'shipped'
    STATUS_DONE = 'done'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PRODUCTION, '生产中'),
        (STATUS_SHIPPED, '已发货'),
        (STATUS_DONE, '已完成'),
        (STATUS_CANCELLED, '已取消'),
    ]

    quotation = models.OneToOneField(
        Quotation, on_delete=models.PROTECT, related_name='order', verbose_name='来源报价单')
    number = models.CharField('订单号', max_length=30, unique=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='orders', verbose_name='客户')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='orders_created', verbose_name='创建人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    amount = models.DecimalField('订单金额 元', max_digits=14, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES,
                              default=STATUS_PRODUCTION)
    note = models.TextField('备注', blank=True)

    class Meta:
        verbose_name = '销售订单'
        verbose_name_plural = '销售订单'
        ordering = ['-created_at']

    def __str__(self):
        return self.number

    @property
    def status_label(self):
        return dict(self.STATUS_CHOICES).get(self.status, self.status)

    def paid_amount(self):
        return sum((p.amount for p in self.payments.all()), Decimal('0'))

    def balance(self):
        return self.amount - self.paid_amount()


class Payment(models.Model):
    """订单回款记录。"""

    order = models.ForeignKey(
        SalesOrder, on_delete=models.CASCADE, related_name='payments', verbose_name='订单')
    date = models.DateField('回款日期', default=timezone.localdate)
    amount = models.DecimalField('回款金额 元', max_digits=14, decimal_places=2)
    method = models.CharField('方式', max_length=20, blank=True,
                              help_text='电汇/承兑/现金等')
    note = models.CharField('备注', max_length=200, blank=True)

    class Meta:
        verbose_name = '回款记录'
        verbose_name_plural = '回款记录'
        ordering = ['-date', '-id']

    def __str__(self):
        return '%s %s %s' % (self.order.number, self.date, self.amount)


class QuoteSendLog(models.Model):
    """报价单邮件发送留痕。"""

    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name='send_logs',
        verbose_name='报价单')
    sent_to = models.CharField('收件人', max_length=200,
                               help_text='多个邮箱用逗号分隔')
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='quotes_sent', verbose_name='发送人')
    sent_at = models.DateTimeField('发送时间', auto_now_add=True)
    note = models.CharField('备注', max_length=200, blank=True)

    class Meta:
        verbose_name = '报价发送记录'
        verbose_name_plural = '报价发送记录'
        ordering = ['-sent_at']

    def __str__(self):
        return '%s → %s (%s)' % (self.quotation.number, self.sent_to,
                                 self.sent_at.strftime('%m-%d %H:%M'))


class InquiryLead(models.Model):
    """客户自助询价门户提交的线索。"""

    STATUS_NEW = 'new'
    STATUS_HANDLED = 'handled'
    STATUS_CHOICES = [(STATUS_NEW, '新线索'), (STATUS_HANDLED, '已处理')]

    company = models.CharField('公司名称', max_length=100)
    contact_name = models.CharField('联系人', max_length=50)
    phone = models.CharField('联系电话', max_length=30)
    note = models.TextField('留言', blank=True)
    created_at = models.DateTimeField('提交时间', auto_now_add=True)
    status = models.CharField('状态', max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_NEW)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leads_handled', verbose_name='处理人')
    quotation = models.ForeignKey(
        Quotation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='from_leads', verbose_name='转出的报价单')

    class Meta:
        verbose_name = '询价线索'
        verbose_name_plural = '询价线索'
        ordering = ['-created_at']

    def __str__(self):
        return '%s %s %s' % (self.company, self.contact_name,
                             self.created_at.strftime('%m-%d %H:%M'))


class InquiryLeadItem(models.Model):
    lead = models.ForeignKey(
        InquiryLead, on_delete=models.CASCADE, related_name='items', verbose_name='线索')
    spec = models.ForeignKey(
        CableSpec, on_delete=models.PROTECT, related_name='lead_items',
        verbose_name='规格')
    spec_text = models.CharField('规格快照', max_length=100, blank=True)
    length_m = models.DecimalField('长度 m', max_digits=12, decimal_places=2)
    quoted_price_per_m = models.DecimalField(
        '门户目录价快照 元/m', max_digits=10, decimal_places=4, default=0,
        help_text='客户在门户看到的价格（目录价，不含成本信息）')

    class Meta:
        verbose_name = '询价明细'
        verbose_name_plural = '询价明细'

    def __str__(self):
        return '%s × %sm' % (self.spec_text or self.spec, self.length_m)
