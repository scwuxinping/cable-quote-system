"""规格理论重量生成器。

按简化几何模型计算各材料理论用量（kg/km），数值可在后台按厂标逐条校准：
- 导体外径 ≈ 导体系数(默认1.18, 绞合紧压) × √截面
- 绝缘/护套用量 = π×(外半径²−内半径²)×密度  （A[mm²]×ρ[g/cm³] = kg/km）
- 多芯成缆外径 = 成缆系数×绝缘芯外径×1.03（3+1 结构再乘 0.95 近似）
- 填充绕包 ≈ 绝缘重量的 20%（≥3 芯）
"""
import math
from decimal import Decimal

from django.db import transaction

from .models import CableSeries, CableSpec, Material
from .pricing import normalize_layout, parse_layout

D2 = Decimal('0.01')

# 3+1 结构主截面 → 中性线截面（GB/T 常用组合）
NEUTRAL_MAP = {
    Decimal('25'): Decimal('16'), Decimal('35'): Decimal('16'),
    Decimal('50'): Decimal('25'), Decimal('70'): Decimal('35'),
    Decimal('95'): Decimal('50'), Decimal('120'): Decimal('70'),
    Decimal('150'): Decimal('70'), Decimal('185'): Decimal('95'),
    Decimal('240'): Decimal('120'), Decimal('300'): Decimal('150'),
}

# 等芯成缆外径 / 单芯绝缘外径（n≤5 查表；n≥6 用外接圆公式）
LAY_FACTOR_TABLE = {1: Decimal('1'), 2: Decimal('2.0'), 3: Decimal('2.16'),
                    4: Decimal('2.42'), 5: Decimal('2.70')}
BEDDING = Decimal('1.03')      # 包带/成缆间隙
FILLER_RATIO = Decimal('0.20')  # 填充绕包占绝缘重量比例（≥3芯）
ARMOR_OVERLAP = Decimal('1.05')  # 钢带搭盖系数


def lay_factor(n):
    """n 根等径圆绞合的外接圆直径系数。"""
    if n in LAY_FACTOR_TABLE:
        return LAY_FACTOR_TABLE[n]
    return Decimal(str(1 + 1 / math.sin(math.pi / n)))


def armor_thickness(d):
    """按成缆外径查钢带铠装等效厚度（两层含搭盖，近似 GB/T）。

    等效厚度 ≈ 单层标称厚度 × 2 × 1.05 搭盖；规格库逐条可校准。
    """
    d = float(d)
    if d < 15:
        return Decimal('0.4')    # 单层 0.2mm ×2
    if d < 25:
        return Decimal('0.5')    # 单层 0.2mm ×2（大外径）
    if d < 40:
        return Decimal('0.8')    # 单层 0.3mm ×2
    return Decimal('1.2')        # 单层 0.5mm ×2


def sheath_thickness(d):
    """按成缆外径查护套厚度（近似 ST2）。"""
    d = float(d)
    if d < 12:
        return Decimal('1.4')
    if d < 20:
        return Decimal('1.6')
    if d < 30:
        return Decimal('1.8')
    if d < 40:
        return Decimal('2.0')
    if d < 50:
        return Decimal('2.2')
    return Decimal('2.4')


def _material_density(code, default):
    mat = Material.objects.filter(code=code).first()
    if mat and mat.density and mat.density > 0:
        return mat.density
    return Decimal(str(default))


def compute_weights(series, layout):
    """返回该系列下某规格的理论重量 dict（kg/km，Decimal）。"""
    layout = normalize_layout(layout)
    parts = parse_layout(layout)
    thickness = {Decimal(str(k)): Decimal(str(v))
                 for k, v in (series.insulation_thickness or {}).items()}

    cond_density = _material_density(
        series.conductor, {'CU': 8.89, 'AL': 2.703}[series.conductor])
    ins_density = _material_density(
        series.insulation.code, 0.92 if series.insulation.code == 'XLPE' else 1.40)

    conductor_w = Decimal('0')
    insulation_w = Decimal('0')
    cores_total = 0
    section_total = Decimal('0')
    main_d_ins = Decimal('0')

    for n, sec in parts:
        t = thickness.get(sec)
        if t is None:
            raise ValueError('绝缘厚度表缺少截面 %s 的数据' % sec)
        d_cond = Decimal(str(series.conductor_od_factor)) * Decimal(str(math.sqrt(float(sec))))
        d_ins = d_cond + Decimal('2') * t
        a_ins = Decimal(str(math.pi)) * (d_ins ** 2 - d_cond ** 2) / Decimal('4')
        conductor_w += n * sec * cond_density * Decimal(str(series.lay_factor))
        insulation_w += n * a_ins * ins_density
        cores_total += n
        section_total += n * sec
        if d_ins > main_d_ins:
            main_d_ins = d_ins

    sheath_w = Decimal('0')
    filler_w = Decimal('0')
    armor_w = Decimal('0')
    if cores_total > 1:
        lay = lay_factor(cores_total)
        bundle = lay * main_d_ins * BEDDING
        if len(parts) > 1:  # 3+1 等不等芯结构近似
            bundle = bundle * Decimal('0.95')
    else:
        bundle = main_d_ins
    if series.has_armor:
        t_a = armor_thickness(bundle)
        a_a = Decimal(str(math.pi)) * ((bundle / Decimal('2') + t_a) ** 2
                                       - (bundle / Decimal('2')) ** 2)
        armor_density = _material_density(
            series.armor_material.code if series.armor_material_id else 'STEEL', 7.85)
        armor_w = a_a * armor_density * ARMOR_OVERLAP
        bundle = bundle + Decimal('2') * t_a  # 铠装后外径，护套包在铠装外
    if series.has_sheath:
        t_s = sheath_thickness(bundle)
        a_s = Decimal(str(math.pi)) * ((bundle / Decimal('2') + t_s) ** 2
                                       - (bundle / Decimal('2')) ** 2)
        sheath_density = 1.40
        if series.sheath_material_id:
            sheath_density = _material_density(series.sheath_material.code, 1.40)
        sheath_w = a_s * Decimal(str(sheath_density))
    if cores_total >= 3:
        filler_w = insulation_w * FILLER_RATIO

    return {
        'cores_total': cores_total,
        'section_total': section_total,
        'conductor_weight': conductor_w.quantize(D2),
        'insulation_weight': insulation_w.quantize(D2),
        'sheath_weight': sheath_w.quantize(D2),
        'armor_weight': armor_w.quantize(D2),
        'filler_weight': filler_w.quantize(D2),
    }


def layouts_for(series):
    """按系列的芯数组合与截面列表展开出规格代码。"""
    layouts = []
    sections = [Decimal(str(s)) for s in series.sections or []]
    for opt in series.core_options or []:
        opt = str(opt)
        for sec in sections:
            if opt == '3+1':
                neutral = NEUTRAL_MAP.get(sec)
                if neutral is None:
                    continue
                layouts.append('3x%s+1x%s' % (int(sec) if sec == sec.to_integral_value() else sec,
                                              int(neutral)))
            else:
                s = int(sec) if sec == sec.to_integral_value() else sec
                layouts.append('%sx%s' % (opt, s))
    return layouts


@transaction.atomic
def sync_series(series):
    """（重新）生成一个系列下全部规格的理论重量。返回 (创建数, 更新数)。"""
    cond_mat = Material.objects.filter(code=series.conductor).first()
    if cond_mat is None:
        raise ValueError('缺少导体材料 %s，请先在后台维护' % series.conductor)
    created = updated = 0
    for layout in layouts_for(series):
        try:
            weights = compute_weights(series, layout)
        except ValueError as exc:
            continue  # 截面不在厚度表等，跳过
        _, created_flag = CableSpec.objects.update_or_create(
            series=series, layout=layout,
            defaults={
                'voltage': series.voltage,
                'conductor_material': cond_mat,
                'cores_total': weights['cores_total'],
                'section_total': weights['section_total'],
                'conductor_weight': weights['conductor_weight'],
                'insulation_weight': weights['insulation_weight'],
                'sheath_weight': weights['sheath_weight'],
                'armor_weight': weights['armor_weight'],
                'filler_weight': weights['filler_weight'],
            })
        if created_flag:
            created += 1
        else:
            updated += 1
    return created, updated
