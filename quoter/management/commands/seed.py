"""初始化种子数据：材料/示例价格、型号系列与规格库、演示用户与客户。

用法：py -3.13 manage.py seed
"""
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from quoter.models import (CableSeries, Customer, CustomerTier, Material,
                           MaterialPrice, PricingParams)
from quoter.specgen import sync_series

MANAGER_GROUP = '经理'

MATERIALS = [
    # code, name, unit, density, loss_rate, sample_price
    ('CU', '铜（电工圆铜杆）', '元/kg', Decimal('8.890'), Decimal('2.0'), Decimal('74.5')),
    ('AL', '铝（电工圆铝杆）', '元/kg', Decimal('2.703'), Decimal('2.5'), Decimal('21.8')),
    ('XLPE', '交联聚乙烯绝缘料', '元/kg', Decimal('0.920'), Decimal('3.0'), Decimal('12.5')),
    ('PVC', '聚氯乙烯护套/绝缘料', '元/kg', Decimal('1.400'), Decimal('3.0'), Decimal('9.2')),
    ('STEEL', '钢带（铠装）', '元/kg', Decimal('7.850'), Decimal('4.0'), Decimal('4.6')),
    ('LSZH', '低烟无卤护套料', '元/kg', Decimal('1.450'), Decimal('3.5'), Decimal('16.0')),
    ('FILLER', '填充/绕包材料', '元/kg', Decimal('0.900'), Decimal('5.0'), Decimal('8.0')),
]

XLPE_T = {  # 0.6/1kV XLPE 绝缘厚度（近似 GB/T 12706）
    '1.5': '0.7', '2.5': '0.7', '4': '0.7', '6': '0.7', '10': '0.7', '16': '0.7',
    '25': '0.9', '35': '0.9', '50': '1.0', '70': '1.1', '95': '1.1',
    '120': '1.2', '150': '1.2', '185': '1.3', '240': '1.4', '300': '1.5', '400': '1.6',
}
PVC_T = {   # 0.6/1kV PVC 绝缘厚度（近似）
    '1.5': '0.8', '2.5': '0.8', '4': '0.8', '6': '0.8', '10': '0.8', '16': '0.9',
    '25': '1.0', '35': '1.0', '50': '1.0', '70': '1.1', '95': '1.1',
    '120': '1.2', '150': '1.3', '185': '1.4', '240': '1.6', '300': '1.8', '400': '2.0',
}
BV_T = {    # 450/750V 布电线 PVC 绝缘厚度（近似 GB/T 5023）
    '1.5': '0.7', '2.5': '0.7', '4': '0.7', '6': '0.7', '10': '0.8', '16': '0.8',
    '25': '0.9', '35': '1.0', '50': '1.1', '70': '1.2', '95': '1.3',
    '120': '1.4', '150': '1.6', '185': '1.8', '240': '2.0', '300': '2.2', '400': '2.4',
}
KVV_T = {   # 450/750V 控制电缆 PVC 绝缘厚度（近似 GB/T 9330）
    '0.75': '0.6', '1': '0.6', '1.5': '0.7', '2.5': '0.8', '4': '0.9',
    '6': '1.0', '10': '1.2',
}
KVV_SECTIONS = [0.75, 1, 1.5, 2.5, 4, 6, 10]
KVV_CORES = ['4', '5', '7', '10', '12', '14', '19', '24', '27', '30', '37']

SECTIONS_1C = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400]
SECTIONS_MC = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300]

SERIES = [
    dict(code='YJV', name='交联聚乙烯绝缘聚氯乙烯护套电力电缆', voltage='0.6/1kV',
         conductor='CU', ins='XLPE', has_sheath=True, armor=False,
         cores=['1', '2', '3', '3+1', '4', '5'], sections=SECTIONS_1C),
    dict(code='YJLV', name='铝芯交联聚乙烯绝缘聚氯乙烯护套电力电缆', voltage='0.6/1kV',
         conductor='AL', ins='XLPE', has_sheath=True, armor=False,
         cores=['1', '2', '3', '3+1', '4', '5'], sections=SECTIONS_1C),
    dict(code='YJV22', name='交联聚乙烯绝缘钢带铠装聚氯乙烯护套电力电缆', voltage='0.6/1kV',
         conductor='CU', ins='XLPE', has_sheath=True, armor=True,
         cores=['1', '2', '3', '3+1', '4', '5'], sections=SECTIONS_1C),
    dict(code='YJLV22', name='铝芯交联聚乙烯绝缘钢带铠装聚氯乙烯护套电力电缆',
         voltage='0.6/1kV', conductor='AL', ins='XLPE', has_sheath=True, armor=True,
         cores=['1', '2', '3', '3+1', '4', '5'], sections=SECTIONS_1C),
    dict(code='VV', name='聚氯乙烯绝缘聚氯乙烯护套电力电缆', voltage='0.6/1kV',
         conductor='CU', ins='PVC', has_sheath=True, armor=False,
         cores=['2', '3', '3+1', '4', '5'], sections=SECTIONS_MC),
    dict(code='VLV', name='铝芯聚氯乙烯绝缘聚氯乙烯护套电力电缆', voltage='0.6/1kV',
         conductor='AL', ins='PVC', has_sheath=True, armor=False,
         cores=['2', '3', '3+1', '4', '5'], sections=SECTIONS_MC),
    dict(code='WDZ-YJY', name='低烟无卤交联聚乙烯绝缘聚烯烃护套电力电缆',
         voltage='0.6/1kV', conductor='CU', ins='XLPE', has_sheath=True, armor=False,
         sheath_mat='LSZH', cores=['1', '2', '3', '3+1', '4', '5'], sections=SECTIONS_1C),
    dict(code='KVV', name='铜芯聚氯乙烯绝缘聚氯乙烯护套控制电缆', voltage='450/750V',
         conductor='CU', ins='PVC', has_sheath=True, armor=False,
         cores=KVV_CORES, sections=KVV_SECTIONS, thickness=KVV_T),
    dict(code='BV', name='铜芯聚氯乙烯绝缘布电线', voltage='450/750V',
         conductor='CU', ins='PVC', has_sheath=False, armor=False,
         cores=['1'], sections=SECTIONS_1C),
]

CUSTOMERS = [
    ('某某电力安装公司', 'A', Decimal('0.970'), '张工', '13800000001'),
    ('某某建设工程有限公司', 'B', Decimal('1.000'), '李经理', '13800000002'),
    ('某某机电经销部', 'C', Decimal('1.000'), '王老板', '13800000003'),
]


class Command(BaseCommand):
    help = '初始化种子数据（幂等，可重复执行）'

    def handle(self, *args, **options):
        from django.utils import timezone
        today = timezone.localdate()

        # 材料 + 今日示例价格
        for code, name, unit, density, loss, price in MATERIALS:
            mat, _ = Material.objects.update_or_create(
                code=code,
                defaults={'name': name, 'unit': unit, 'density': density, 'loss_rate': loss})
            if not MaterialPrice.objects.filter(material=mat, date=today).exists():
                MaterialPrice.objects.create(material=mat, date=today, price=price)
        self.stdout.write('材料与示例价格就绪（示例价格请在“价格维护”中改为实际值）')

        # 铜价近 14 天演示历史（仅当历史不足时生成，供走势图演示）
        cu = Material.objects.get(code='CU')
        if cu.prices.count() < 3:
            import random
            random.seed(42)
            base = Decimal('73.5')
            for i in range(14, 0, -1):
                d = today - timezone.timedelta(days=i)
                base = base + Decimal(str(round(random.uniform(-0.8, 0.9), 2)))
                MaterialPrice.objects.create(material=cu, date=d, price=base)
            self.stdout.write('已生成近 14 天铜价演示历史（真实使用请每日 fetch_cu_price 或手工录入）')

        pvc = Material.objects.get(code='PVC')
        xlpe = Material.objects.get(code='XLPE')
        steel = Material.objects.get(code='STEEL')
        lszh = Material.objects.get(code='LSZH')

        # 型号系列 + 规格库
        total_created = total_updated = 0
        for cfg in SERIES:
            ins = xlpe if cfg['ins'] == 'XLPE' else pvc
            if 'thickness' in cfg:
                thickness = cfg['thickness']
            elif cfg['ins'] == 'XLPE':
                thickness = XLPE_T
            elif cfg['code'] == 'BV':
                thickness = BV_T
            else:
                thickness = PVC_T
            sheath_mat = pvc
            if cfg.get('sheath_mat') == 'LSZH':
                sheath_mat = lszh
            series, _ = CableSeries.objects.update_or_create(
                code=cfg['code'],
                defaults={
                    'name': cfg['name'], 'voltage': cfg['voltage'],
                    'conductor': cfg['conductor'], 'insulation': ins,
                    'has_sheath': cfg['has_sheath'],
                    'sheath_material': sheath_mat if cfg['has_sheath'] else None,
                    'has_armor': cfg['armor'],
                    'armor_material': steel if cfg['armor'] else None,
                    'insulation_thickness': thickness,
                    'core_options': cfg['cores'], 'sections': cfg['sections'],
                })
            created, updated = sync_series(series)
            total_created += created
            total_updated += updated
            self.stdout.write('  %s: 新增 %d 规格，更新 %d 规格' % (cfg['code'], created, updated))
        self.stdout.write('规格库合计：新增 %d，更新 %d' % (total_created, total_updated))

        # 客户
        for name, level, disc, contact, phone in CUSTOMERS:
            Customer.objects.update_or_create(name=name, defaults={
                'level': level, 'discount': disc, 'contact': contact, 'phone': phone})

        # 计价参数单例
        PricingParams.load()

        # 用户与角色
        group, _ = Group.objects.get_or_create(name=MANAGER_GROUP)
        boss_group, _ = Group.objects.get_or_create(name='老板')
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', '', 'admin1234')
            self.stdout.write(self.style.WARNING('创建超级用户 admin / admin1234（请尽快改密码！）'))
        if not User.objects.filter(username='manager').exists():
            u = User.objects.create_user('manager', '', 'manager123')
            u.is_staff = True
            u.groups.add(group)
            u.save()
            self.stdout.write(self.style.WARNING('创建经理账号 manager / manager123（请改密码）'))
        if not User.objects.filter(username='boss').exists():
            u = User.objects.create_user('boss', '', 'boss123')
            u.is_staff = True
            u.groups.add(boss_group)
            u.save()
            self.stdout.write(self.style.WARNING('创建老板账号 boss / boss123（大单终审，请改密码）'))
        if not User.objects.filter(username='sales').exists():
            u = User.objects.create_user('sales', '', 'sales123')
            u.is_staff = True
            u.save()
            self.stdout.write(self.style.WARNING('创建业务员账号 sales / sales123（请改密码）'))

        # 演示阶梯价（A 类客户：量大优惠）
        cust_a = Customer.objects.filter(name=CUSTOMERS[0][0]).first()
        if cust_a and not cust_a.tiers.exists():
            CustomerTier.objects.create(customer=cust_a, min_length_m=Decimal('500'),
                                        discount=Decimal('0.950'), note='满500米')
            CustomerTier.objects.create(customer=cust_a, min_length_m=Decimal('2000'),
                                        discount=Decimal('0.920'), note='满2000米')

        self.stdout.write(self.style.SUCCESS('种子数据初始化完成。'))
