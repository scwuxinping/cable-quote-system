from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import (CableSeries, CableSpec, Customer, CustomerTier, Material,
                     MaterialPrice, Payment, PricingParams, Quotation,
                     QuotationItem, SalesOrder)
from .pricing import (MissingPriceError, margin_pct, normalize_layout, parse_input,
                      parse_layout, price_spec, resolve_series_layout)
from .specgen import compute_weights


def _mat(code, density, price, loss='2', premium='0'):
    m, _ = Material.objects.update_or_create(
        code=code, defaults={'name': code, 'density': density, 'loss_rate': Decimal(loss)})
    MaterialPrice.objects.update_or_create(
        material=m, date=timezone.localdate(),
        defaults={'price': Decimal(price), 'premium': Decimal(premium)})
    return m


class ParseTests(TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_layout('3×120+1×70'), '3x120+1x70')
        self.assertEqual(normalize_layout(' 4X16 '), '4x16')
        with self.assertRaises(ValueError):
            normalize_layout('4x')

    def test_parse_input(self):
        self.assertEqual(parse_input('YJV-0.6/1KV 3×120+1×70'), ('YJV', '3x120+1x70'))
        self.assertEqual(parse_input('bv 1x2.5'), ('BV', '1x2.5'))
        with self.assertRaises(ValueError):
            parse_input('YJV')

    def test_parse_layout(self):
        self.assertEqual(parse_layout('3x120+1x70'),
                         [(3, Decimal('120')), (1, Decimal('70'))])


class WeightTests(TestCase):
    def setUp(self):
        self.cu = _mat('CU', '8.890', '75')
        _mat('AL', '2.703', '22')
        self.xlpe = _mat('XLPE', '0.920', '12.5', loss='3')
        self.pvc = _mat('PVC', '1.400', '9.2', loss='3')
        self.series = CableSeries.objects.create(
            code='YJV', name='test', insulation=self.xlpe, has_sheath=True,
            sheath_material=self.pvc,
            insulation_thickness={'120': '1.2', '70': '1.1', '16': '0.7'},
            core_options=['3+1'], sections=[120])

    def test_conductor_weight_matches_theory(self):
        w = compute_weights(self.series, '3x120+1x70')
        # 3×120×8.89 + 1×70×8.89 = 3822.7，绞入 1.02 → ≈3899
        self.assertAlmostEqual(float(w['conductor_weight']), 3899.2, delta=10)
        self.assertEqual(w['cores_total'], 4)

    def test_single_core_no_filler(self):
        w = compute_weights(self.series, '1x120')
        self.assertEqual(w['cores_total'], 1)
        self.assertEqual(w['filler_weight'], 0)
        self.assertGreater(float(w['sheath_weight']), 0)


class PricingTests(TestCase):
    def setUp(self):
        self.cu = _mat('CU', '8.890', '75')
        self.xlpe = _mat('XLPE', '0.920', '12.5', loss='3')
        self.pvc = _mat('PVC', '1.400', '9.2', loss='3')
        self.series = CableSeries.objects.create(
            code='YJV', name='test', insulation=self.xlpe, has_sheath=True,
            sheath_material=self.pvc, insulation_thickness={'16': '0.7'},
            core_options=['4'], sections=[16])
        self.spec = CableSpec.objects.create(
            series=self.series, layout='4x16', conductor_material=self.cu,
            cores_total=4, section_total=Decimal('64'),
            conductor_weight=Decimal('580'), insulation_weight=Decimal('44'),
            sheath_weight=Decimal('118'), filler_weight=Decimal('9'))

    def test_missing_price_raises(self):
        MaterialPrice.objects.all().delete()
        with self.assertRaises(MissingPriceError):
            price_spec(self.spec)

    def test_cost_ordering_and_tax(self):
        p = PricingParams.load()
        bd = price_spec(self.spec)
        self.assertLess(bd['cost_km'], bd['floor_km'])
        self.assertLess(bd['floor_km'], bd['list_km'])
        # 费用拆分：成本 = 材料 + 加工费 + 人工及制造费用
        self.assertAlmostEqual(
            float(bd['material_cost_km'] + bd['process_fee_km'] + bd['overhead_fee_km']),
            float(bd['cost_km']), delta=0.01)
        # 含税 13%：list = cost×1.20×1.13
        expect = bd['cost_km'] * Decimal('1.20') * Decimal('1.13')
        self.assertAlmostEqual(float(bd['list_km']), float(expect), delta=0.01)

    def test_premium_applied(self):
        MaterialPrice.objects.filter(material=self.cu).update(premium=Decimal('2'))
        bd = price_spec(self.spec)
        cu_part = [p for p in bd['parts'] if p['material'] == 'CU'][0]
        # 基准 75 + 升贴水 2 = 77 元/kg
        self.assertEqual(cu_part['price'], Decimal('77'))

    def test_armor_component(self):
        steel = _mat('STEEL', '7.850', '4.6', loss='4')
        self.series.has_armor = True
        self.series.armor_material = steel
        self.series.save()
        self.spec.armor_weight = Decimal('500')
        self.spec.save()
        bd = price_spec(self.spec)
        labels = [p['label'] for p in bd['parts']]
        self.assertIn('铠装屏蔽', labels)
        armor_part = [p for p in bd['parts'] if p['label'] == '铠装屏蔽'][0]
        self.assertAlmostEqual(float(armor_part['cost']),
                               500 * 4.6 * 1.04, delta=0.01)

    def test_margin(self):
        bd = price_spec(self.spec)
        # 目录价毛利率应约等于目标毛利率 20%
        self.assertAlmostEqual(float(margin_pct(bd['list_per_m'], bd)), 20.0, delta=0.5)
        # 打 9 折后毛利应明显下降但仍为正
        self.assertLess(float(margin_pct(bd['list_per_m'] * Decimal('0.9'), bd)), 20.0)

    def test_per_m_conversion(self):
        bd = price_spec(self.spec)
        self.assertAlmostEqual(
            float(bd['list_km'] / Decimal('1000')), float(bd['list_per_m']), delta=1e-9)


class HelperTests(TestCase):
    def test_sparkline(self):
        from django.utils import timezone
        from datetime import timedelta
        from quoter.views import cu_price_history, sparkline, recent_deals
        cu = _mat('CU', '8.89', '75')
        today = timezone.localdate()
        for i, p in enumerate([70, 72, 71, 74]):
            MaterialPrice.objects.filter(material=cu, date=today - timedelta(days=3 - i)).delete()
            MaterialPrice.objects.create(material=cu, date=today - timedelta(days=3 - i),
                                         price=Decimal(p))
        hist = cu_price_history(7)
        self.assertEqual(len(hist), 4)
        sp = sparkline(hist)
        self.assertIsNotNone(sp)
        self.assertEqual(sp['first'], 70.0)
        self.assertEqual(sp['last'], 74.0)
        self.assertGreater(sp['change_pct'], 0)
        self.assertIn(',', sp['line'])

    def test_recent_deals(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from datetime import timedelta
        from quoter.views import recent_deals
        cu = _mat('CU', '8.89', '75')
        xlpe = _mat('XLPE', '0.92', '12.5', loss='3')
        pvc = _mat('PVC', '1.40', '9.2', loss='3')
        series, _ = CableSeries.objects.update_or_create(
            code='YJV', defaults={'name': 't', 'insulation': xlpe, 'has_sheath': True,
                                  'sheath_material': pvc,
                                  'insulation_thickness': {'16': '0.7'},
                                  'core_options': ['4'], 'sections': [16]})
        spec, _ = CableSpec.objects.update_or_create(
            series=series, layout='4x16',
            defaults={'conductor_material': cu, 'cores_total': 4,
                      'section_total': Decimal('64'), 'conductor_weight': Decimal('580'),
                      'insulation_weight': Decimal('44'), 'sheath_weight': Decimal('118')})
        user = User.objects.create_user('u')
        cust = Customer.objects.create(name='c')
        for i, (status, price) in enumerate([('approved', '50'), ('approved', '48'),
                                             ('draft', '40')]):
            q = Quotation.objects.create(
                number='RD-%03d' % i, customer=cust, created_by=user,
                valid_until=timezone.localdate() + timedelta(days=3), status=status)
            QuotationItem.objects.create(
                quotation=q, spec=spec, spec_text='YJV 4×16', length_m=Decimal('100'),
                list_price_per_m=Decimal('50'), final_price_per_m=Decimal(price),
                amount=Decimal(price) * 100, cost_detail={'cost_km': '40000'})
        deals = recent_deals(spec)
        # 只含已审批的两单，按时间倒序
        self.assertEqual(len(deals), 2)
        self.assertEqual(Decimal(str(deals[0]['price'])), Decimal('48'))
        # 排除指定报价单
        self.assertEqual(len(recent_deals(spec, limit=5)), 2)


class TierAndApprovalTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.cust = Customer.objects.create(name='阶梯客户', discount=Decimal('1.000'))
        CustomerTier.objects.create(customer=self.cust, min_length_m=Decimal('500'),
                                    discount=Decimal('0.950'))
        CustomerTier.objects.create(customer=self.cust, min_length_m=Decimal('2000'),
                                    discount=Decimal('0.920'))
        self.user = User.objects.create_user('tu')

    def test_tier_match(self):
        self.assertIsNone(self.cust.tier_discount(Decimal('499')))
        self.assertEqual(self.cust.tier_discount(Decimal('500')), Decimal('0.950'))
        self.assertEqual(self.cust.tier_discount(Decimal('3000')), Decimal('0.920'))

    def _make_quote(self, amount):
        """构造一个给定金额、超低折扣（触发审批）的报价单。"""
        from django.utils import timezone
        from datetime import timedelta
        cu = _mat('CU', '8.89', '75')
        xlpe = _mat('XLPE', '0.92', '12.5', loss='3')
        pvc = _mat('PVC', '1.40', '9.2', loss='3')
        series, _ = CableSeries.objects.update_or_create(
            code='YJV', defaults={'name': 't', 'insulation': xlpe, 'has_sheath': True,
                                  'sheath_material': pvc,
                                  'insulation_thickness': {'16': '0.7'},
                                  'core_options': ['4'], 'sections': [16]})
        spec, _ = CableSpec.objects.update_or_create(
            series=series, layout='4x16',
            defaults={'conductor_material': cu, 'cores_total': 4,
                      'section_total': Decimal('64'), 'conductor_weight': Decimal('580'),
                      'insulation_weight': Decimal('44'), 'sheath_weight': Decimal('118')})
        q = Quotation.objects.create(
            number='AP-%s' % amount, customer=self.cust, created_by=self.user,
            valid_until=timezone.localdate() + timedelta(days=3), status=Quotation.STATUS_PENDING)
        QuotationItem.objects.create(
            quotation=q, spec=spec, spec_text='YJV 4×16', length_m=Decimal('1000'),
            list_price_per_m=Decimal('50'), final_price_per_m=Decimal(amount / 1000),
            amount=Decimal(amount), cost_detail={'cost_km': '60000', 'tax_mult': '1.13'})
        return q

    def test_boss_approval_flow(self):
        from django.contrib.auth.models import Group, User
        from django.test import Client
        p = PricingParams.load()
        p.boss_threshold_amount = Decimal('500000')
        p.save()
        mgr_group = Group.objects.get_or_create(name='经理')[0]
        boss_group = Group.objects.get_or_create(name='老板')[0]
        manager = User.objects.create_user('mgr2')
        manager.groups.add(mgr_group)
        boss = User.objects.create_user('boss2')
        boss.groups.add(boss_group)

        client = Client()
        client.force_login(manager)
        big = self._make_quote(600000)
        big.status = Quotation.STATUS_PENDING
        big.save()
        client.post('/quotes/%d/approve/' % big.pk)
        big.refresh_from_db()
        self.assertEqual(big.status, Quotation.STATUS_BOSS_PENDING)
        # 经理无权终审，状态不变
        client.post('/quotes/%d/approve/' % big.pk)
        big.refresh_from_db()
        self.assertEqual(big.status, Quotation.STATUS_BOSS_PENDING)
        # 老板终审通过
        client.force_login(boss)
        client.post('/quotes/%d/approve/' % big.pk)
        big.refresh_from_db()
        self.assertEqual(big.status, Quotation.STATUS_APPROVED)

        # 小单：经理直接批过，无需老板
        small = self._make_quote(100000)
        small.status = Quotation.STATUS_PENDING
        small.save()
        client.force_login(manager)
        client.post('/quotes/%d/approve/' % small.pk)
        small.refresh_from_db()
        self.assertEqual(small.status, Quotation.STATUS_APPROVED)


class QuotationMarginTests(PricingTests):
    def test_quote_margin_pct(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from datetime import timedelta
        p = PricingParams.load()
        user = User.objects.create_user('u1')
        cust = Customer.objects.create(name='c1')
        q = Quotation.objects.create(
            number='BJTEST-001', customer=cust, created_by=user,
            valid_until=timezone.localdate() + timedelta(days=3),
            discount=Decimal('1.000'), base_cu_price=Decimal('75'))
        bd = price_spec(self.spec)
        QuotationItem.objects.create(
            quotation=q, spec=self.spec, spec_text='YJV 4×16',
            length_m=Decimal('1000'),
            list_price_per_m=bd['list_per_m'],
            final_price_per_m=bd['list_per_m'],
            amount=bd['list_per_m'] * 1000,
            cost_detail={'cost_km': str(bd['cost_km']), 'tax_mult': str(bd['tax_mult'])})
        self.assertAlmostEqual(q.margin_pct(), 20.0, delta=0.5)

    def test_total_amount_includes_freight(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from datetime import timedelta
        cust = Customer.objects.create(name='c2')
        q = Quotation.objects.create(
            number='BJTEST-F1', customer=cust, created_by=User.objects.create_user('u2'),
            valid_until=timezone.localdate() + timedelta(days=3), freight=Decimal('500'))
        QuotationItem.objects.create(
            quotation=q, spec=self.spec, spec_text='YJV 4×16',
            length_m=Decimal('100'), list_price_per_m=Decimal('50'),
            final_price_per_m=Decimal('50'), amount=Decimal('5000'),
            cost_detail={'cost_km': '40000', 'tax_mult': '1.13'})
        self.assertEqual(q.total_amount(), Decimal('5500'))

class OrderFlowTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from datetime import timedelta
        cu = _mat('CU', '8.89', '75')
        xlpe = _mat('XLPE', '0.92', '12.5', loss='3')
        pvc = _mat('PVC', '1.40', '9.2', loss='3')
        series, _ = CableSeries.objects.update_or_create(
            code='YJV', defaults={'name': 't', 'insulation': xlpe, 'has_sheath': True,
                                  'sheath_material': pvc,
                                  'insulation_thickness': {'16': '0.7'},
                                  'core_options': ['4'], 'sections': [16]})
        spec, _ = CableSpec.objects.update_or_create(
            series=series, layout='4x16',
            defaults={'conductor_material': cu, 'cores_total': 4,
                      'section_total': Decimal('64'), 'conductor_weight': Decimal('580'),
                      'insulation_weight': Decimal('44'), 'sheath_weight': Decimal('118')})
        self.spec = spec
        self.user = User.objects.create_user('ou')
        self.cust = Customer.objects.create(name='订单客户')
        self.quote = Quotation.objects.create(
            number='OF-001', customer=self.cust, created_by=self.user,
            valid_until=timezone.localdate() + timedelta(days=3),
            status=Quotation.STATUS_APPROVED, freight=Decimal('500'))
        QuotationItem.objects.create(
            quotation=self.quote, spec=spec, spec_text='YJV 4×16',
            length_m=Decimal('100'), list_price_per_m=Decimal('50'),
            final_price_per_m=Decimal('50'), amount=Decimal('5000'),
            cost_detail={'cost_km': '40000', 'tax_mult': '1.13'})

    def test_to_order_and_duplicate_guard(self):
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        resp = client.post('/quotes/%d/to-order/' % self.quote.pk)
        order = SalesOrder.objects.get(quotation=self.quote)
        self.assertEqual(order.amount, Decimal('5500'))
        self.assertEqual(order.status, SalesOrder.STATUS_PRODUCTION)
        # 再次转单被拒，仍只有一张
        client.post('/quotes/%d/to-order/' % self.quote.pk)
        self.assertEqual(SalesOrder.objects.count(), 1)

    def test_payment_and_balance(self):
        order = SalesOrder.objects.create(
            quotation=self.quote, number='SO-T1', customer=self.cust,
            created_by=self.user, amount=Decimal('5500'))
        Payment.objects.create(order=order, amount=Decimal('2000'), method='电汇')
        self.assertEqual(order.paid_amount(), Decimal('2000'))
        self.assertEqual(order.balance(), Decimal('3500'))

    def test_order_status_flow(self):
        from django.test import Client
        order = SalesOrder.objects.create(
            quotation=self.quote, number='SO-T2', customer=self.cust,
            created_by=self.user, amount=Decimal('5500'))
        client = Client()
        client.force_login(self.user)
        client.post('/orders/%d/status/' % order.pk, {'action': 'ship'})
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.STATUS_SHIPPED)
        client.post('/orders/%d/status/' % order.pk, {'action': 'done'})
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.STATUS_DONE)
        # 已完成不允许再取消
        client.post('/orders/%d/status/' % order.pk, {'action': 'cancel'})
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.STATUS_DONE)

class SeriesResolveTests(TestCase):
    def setUp(self):
        _mat('CU', '8.89', '75')
        _mat('AL', '2.703', '22')
        self.xlpe = _mat('XLPE', '0.92', '12.5', loss='3')
        self.pvc = _mat('PVC', '1.40', '9.2', loss='3')
        self.steel = _mat('STEEL', '7.85', '4.6', loss='4')
        for code, kwargs in [
                ('YJV', dict(armor=False)),
                ('YJV22', dict(armor=True)),
                ('WDZ-YJY', dict(armor=False))]:
            CableSeries.objects.update_or_create(
                code=code,
                defaults=dict(name=code, insulation=self.xlpe, has_sheath=True,
                              sheath_material=self.pvc,
                              armor_material=self.steel if kwargs['armor'] else None,
                              has_armor=kwargs['armor'],
                              insulation_thickness={'16': '0.7', '120': '1.2', '70': '1.1'},
                              core_options=['4'], sections=[16, 120]))

    def test_parse_variants(self):
        self.assertEqual(parse_input('YJV-0.6/1KV 3x120+1x70'), ('YJV', '3x120+1x70'))
        self.assertEqual(parse_input('YJV22-4x16'), ('YJV22', '4x16'))
        self.assertEqual(parse_input('wdz-yjy 4x16'), ('WDZYJY', '4x16'))

    def test_resolve_with_and_without_dash(self):
        from quoter.specgen import sync_series
        for code in ('YJV', 'YJV22', 'WDZ-YJY'):
            sync_series(CableSeries.objects.get(code=code))
        s1, l1 = resolve_series_layout('YJV22 4x16')
        self.assertEqual((s1.code, l1), ('YJV22', '4x16'))
        s2, l2 = resolve_series_layout('YJV22-4x16')
        self.assertEqual((s2.code, l2), ('YJV22', '4x16'))
        s3, l3 = resolve_series_layout('wdz-yjy 4x16')
        self.assertEqual(s3.code, 'WDZ-YJY')

    def test_armor_weight_generated(self):
        from quoter.specgen import sync_series
        plain = CableSeries.objects.get(code='YJV')
        armored = CableSeries.objects.get(code='YJV22')
        sync_series(plain)
        sync_series(armored)
        w_plain = compute_weights(plain, '4x120')
        w_armor = compute_weights(armored, '4x120')
        self.assertGreater(float(w_armor['armor_weight']), 300)
        self.assertEqual(float(w_plain['armor_weight']), 0)
        # 铠装后护套外径更大，护套料更重
        self.assertGreater(w_armor['sheath_weight'], w_plain['sheath_weight'])

    def test_lay_factor_many_cores(self):
        from quoter.specgen import lay_factor
        self.assertEqual(lay_factor(3), Decimal('2.16'))
        f19 = float(lay_factor(19))
        # 19 = 中心 1 + 外圈 18，外接圆系数 ≈ 1 + 1/sin(π/19) ≈ 7.08
        self.assertGreater(f19, 6.5)
        self.assertLess(f19, 7.5)

