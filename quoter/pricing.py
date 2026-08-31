"""计价引擎：按当日材料价格对规格计算成本、底价、目录价。

所有金额为 Decimal，单位：
- 重量 kg/km、价格 元/kg（元/t 自动换算）
- 成本/底价/目录价 元/km，单价 元/m
"""
import math
import re
from decimal import Decimal

from django.utils import timezone

from .models import Material, MaterialPrice, PricingParams

D0 = Decimal('0')
D100 = Decimal('100')
D1000 = Decimal('1000')


class MissingPriceError(Exception):
    """有材料还没有录入过价格，无法计价。"""

    def __init__(self, codes):
        self.codes = codes
        super().__init__('缺少材料价格: %s' % ', '.join(codes))


def normalize_layout(text):
    """把用户输入的规格统一为规范代码，如 '3×120+1×70' → '3x120+1x70'。"""
    s = str(text or '').strip().replace('×', 'x').replace('Ｘ', 'x').replace('X', 'x')
    s = s.replace(' ', '')
    if not s:
        raise ValueError('规格为空')
    for part in s.split('+'):
        if 'x' not in part:
            raise ValueError('规格缺少芯数或截面: %s' % text)
        n_str, _, sec_str = part.partition('x')
        if not n_str.isdigit() or int(n_str) < 1:
            raise ValueError('芯数不正确: %s' % text)
        try:
            sec = float(sec_str)
        except ValueError:
            raise ValueError('截面不正确: %s' % text)
        if not (0.5 <= sec <= 1000):
            raise ValueError('截面超出范围: %s' % text)
    return s


def parse_layout(layout):
    """'3x120+1x70' → [(3, Decimal('120')), (1, Decimal('70'))]"""
    parts = []
    for part in normalize_layout(layout).split('+'):
        n, _, sec = part.partition('x')
        parts.append((int(n), Decimal(sec)))
    return parts


def parse_input(text):
    """解析完整输入：'YJV-0.6/1KV 3×120+1×70' → ('YJV', ...)，'wdz-yjy 4x16' → ('WDZYJY', ...)。

    型号取规格前所有字母段拼接（电压等级等数字后缀段自动剔除）。
    """
    s = str(text or '').strip().upper().replace('×', 'x').replace('Ｘ', 'x')
    s = s.replace('X', 'x').replace(' ', '')
    m_layout = re.search(r'(\d+x[\d.]+(?:\+\d+x[\d.]+)?)', s)
    if not m_layout:
        raise ValueError('没有找到芯数×截面，示例：YJV 3x120+1x70')
    layout = normalize_layout(m_layout.group(1))
    prefix = s[:m_layout.start()]
    # 先剥离电压等级段（0.6/1KV、450/750V、1KV 等），剩余字母数字即型号
    prefix = re.sub(r'\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?[KV]+', '', prefix)
    letters = re.sub(r'[^A-Z0-9]', '', prefix)
    if not letters:
        raise ValueError('没有找到型号，示例：YJV 3x120+1x70 或 WDZ-YJY 4x16')
    return letters, layout


def match_series(letters):
    """把 parse_input 的型号串匹配到 CableSeries。找不到返回 None。"""
    from .models import CableSeries

    norm = lambda code: code.replace('-', '').upper()
    by_norm = {norm(s.code): s for s in CableSeries.objects.filter(active=True)}
    series = by_norm.get(letters)
    if series is None:
        series = next((s for k, s in by_norm.items() if k.startswith(letters)), None)
    return series


def resolve_series_layout(text):
    """解析输入并兜底处理 'YJV22 4x16'（型号尾号数字被并进芯数）的情况。

    返回 (series, layout)；series 可能为 None（型号不认识），
    layout 可能查不到规格（上层给出"规格库无此规格"提示）。
    """
    from .models import CableSpec

    letters, layout = parse_input(text)
    series = match_series(letters)
    if series is not None and CableSpec.objects.filter(
            series=series, layout=layout).exists():
        return series, layout
    # 尝试把芯数前导数字并回型号：YJV + 224x16 → YJV22 + 4x16
    for k in range(1, len(layout)):
        head, rest = layout[:k], layout[k:]
        if not head.isdigit():
            break
        try:
            rest_ok = normalize_layout(rest)
        except ValueError:
            continue
        cand = match_series(letters + head)
        if cand is not None and CableSpec.objects.filter(
                series=cand, layout=rest_ok).exists():
            return cand, rest_ok
    return series, layout


def _price_per_kg(mp):
    """MaterialPrice 记录 → 含升贴水的 元/kg 单价。"""
    if mp.material.unit == Material.UNIT_T:
        base = mp.price / D1000
        premium = mp.premium / D1000
    else:
        base = mp.price
        premium = mp.premium or D0
    return base + premium


def price_spec(spec, params=None, date=None):
    """对一个规格计算完整计价明细（Decimal 字典）。

    公式：
      铜材成本 = 导体重量 × 铜价(含升贴水) × (1+损耗率)   —— 铝材同理
      电缆成本 = 铜材 + 绝缘 + 护套 + 铠装/屏蔽 + 填充 + 加工费 + 人工及制造费用
      销售报价(元/km) = 电缆成本 × (1+利润率) × (1+税率)，报价单层面另加运费

    返回键：
      parts                 —— 材料分项明细
      material_cost_km      —— 材料成本 元/km（含损耗）
      process_fee_km        —— 加工费 元/km（基础 + 截面系数）
      overhead_fee_km       —— 人工及制造费用 元/km（材料成本百分比）
      cost_km / floor_km / list_km / *_per_m / tax_mult
    """
    params = params or PricingParams.load()
    date = date or timezone.localdate()
    series = spec.series

    components = [
        ('导体', spec.conductor_material, spec.conductor_weight),
        ('绝缘', series.insulation, spec.insulation_weight),
    ]
    if series.has_sheath and series.sheath_material_id and spec.sheath_weight > 0:
        components.append(('护套', series.sheath_material, spec.sheath_weight))
    if series.has_armor and series.armor_material_id and spec.armor_weight > 0:
        components.append(('铠装屏蔽', series.armor_material, spec.armor_weight))
    filler = Material.objects.filter(code='FILLER').first()
    if filler is not None and spec.filler_weight > 0:
        components.append(('填充', filler, spec.filler_weight))

    missing = []
    parts = []
    for label, material, weight in components:
        if material is None or weight <= 0:
            continue
        mp = material.price_on(date)
        if mp is None:
            missing.append('%s(%s)' % (label, material.code))
            continue
        price_kg = _price_per_kg(mp)
        loss = Decimal('1') + material.loss_rate / D100
        part = {
            'label': label,
            'material': material.code,
            'weight': weight,
            'price': price_kg,
            'loss_pct': material.loss_rate,
            'cost': weight * price_kg * loss,
        }
        parts.append(part)
    if missing:
        raise MissingPriceError(missing)

    material_cost_km = sum((p['cost'] for p in parts), D0)
    process_fee_km = (params.process_fee_base
                      + params.process_fee_per_section * spec.section_total)
    overhead_fee_km = material_cost_km * params.process_fee_pct_material / D100
    cost_km = material_cost_km + process_fee_km + overhead_fee_km

    tax_mult = Decimal('1')
    if params.price_with_tax:
        tax_mult = Decimal('1') + params.tax_rate_pct / D100

    floor_km = cost_km * (Decimal('1') + params.min_margin_pct / D100) * tax_mult
    list_km = cost_km * (Decimal('1') + params.target_margin_pct / D100) * tax_mult

    return {
        'date': str(date),
        'parts': parts,
        'material_cost_km': material_cost_km,
        'process_fee_km': process_fee_km,
        'overhead_fee_km': overhead_fee_km,
        'cost_km': cost_km,
        'floor_km': floor_km,
        'list_km': list_km,
        'floor_per_m': floor_km / D1000,
        'list_per_m': list_km / D1000,
        'tax_mult': tax_mult,
        'tax_included': params.price_with_tax,
    }


def margin_pct(final_per_m, breakdown):
    """按成交单价反推毛利率 %（含税口径先还原为不含税）。"""
    final_km = final_per_m * D1000
    ex_tax = final_km / breakdown['tax_mult']
    cost_km = breakdown['cost_km']
    if cost_km == 0:
        return None
    return (ex_tax / cost_km - Decimal('1')) * D100


def jsonable(breakdown):
    """把 Decimal 明细序列化为 JSON 可存的字典（快照用）。"""
    return {
        'date': breakdown['date'],
        'parts': [
            {k: str(v) for k, v in p.items()} for p in breakdown['parts']
        ],
        'material_cost_km': str(breakdown['material_cost_km']),
        'process_fee_km': str(breakdown['process_fee_km']),
        'overhead_fee_km': str(breakdown['overhead_fee_km']),
        'cost_km': str(breakdown['cost_km']),
        'floor_km': str(breakdown['floor_km']),
        'list_km': str(breakdown['list_km']),
        'tax_mult': str(breakdown['tax_mult']),
        'tax_included': breakdown['tax_included'],
    }
