from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import excel
from .models import (CableSeries, CableSpec, Customer, CustomerTier, InquiryLead,
                     Material, MaterialPrice, Payment, PricingParams, QuoteSendLog,
                     Quotation, QuotationItem, SalesOrder)
from .pricing import (MissingPriceError, jsonable, margin_pct,
                      price_spec, resolve_series_layout)

MANAGER_GROUP = '经理'
BOSS_GROUP = '老板'


def is_manager(user):
    return user.is_superuser or user.groups.filter(name=MANAGER_GROUP).exists()


def is_boss(user):
    return user.is_superuser or user.groups.filter(name=BOSS_GROUP).exists()


def next_number(prefix='BJ'):
    today = timezone.localdate()
    tag = '%s%s' % (prefix, today.strftime('%y%m%d'))
    model = Quotation if prefix == 'BJ' else SalesOrder
    n = model.objects.filter(number__startswith=tag).count() + 1
    while model.objects.filter(number='%s-%03d' % (tag, n)).exists():
        n += 1
    return '%s-%03d' % (tag, n)


def cu_price_now():
    cu = Material.objects.filter(code='CU').first()
    if not cu:
        return None
    mp = cu.latest_price()
    return mp.effective_price if mp else None


def cu_snapshot():
    """铜价快照：价格（含升贴水）、来源、生效时间。"""
    cu = Material.objects.filter(code='CU').first()
    if not cu:
        return None
    mp = cu.latest_price()
    if not mp:
        return None
    return {'price': mp.effective_price, 'source': mp.get_source_display(),
            'time': mp.created_at}


def cu_price_history(days=30):
    """近 N 天铜价（含升贴水），按日期升序。"""
    cu = Material.objects.filter(code='CU').first()
    if not cu:
        return []
    since = timezone.localdate() - timedelta(days=days)
    qs = cu.prices.filter(date__gte=since).order_by('date')
    return [(mp.date, mp.effective_price) for mp in qs]


def sparkline(history, width=280, height=60):
    """把 [(date, price)] 转成 SVG 折线/面积图数据 + 涨跌信息。"""
    points = [float(p) for _, p in history]
    if len(points) < 2:
        return None
    mn, mx = min(points), max(points)
    rng = (mx - mn) or 1.0
    step = width / (len(points) - 1)
    pad = 6
    y = lambda v: height - pad - (v - mn) / rng * (height - 2 * pad)
    coords = ['%.1f,%.1f' % (i * step, y(v)) for i, v in enumerate(points)]
    return {
        'line': ' '.join(coords),
        'area': '0,%d %s %d,%d' % (height, ' '.join(coords), width, height),
        'min': mn, 'max': mx, 'first': points[0], 'last': points[-1],
        'change_pct': (points[-1] / points[0] - 1) * 100 if points[0] else 0,
        'start_date': history[0][0], 'end_date': history[-1][0],
    }


def recent_deals(spec, exclude_quote=None, limit=5):
    """某规格最近成交（已审批报价单）参考。"""
    qs = (QuotationItem.objects
          .filter(spec=spec, quotation__status=Quotation.STATUS_APPROVED)
          .select_related('quotation', 'quotation__customer')
          .order_by('-quotation__created_at'))
    if exclude_quote is not None:
        qs = qs.exclude(quotation=exclude_quote)
    return [{'date': it.quotation.created_at.date(),
             'customer': it.quotation.customer.name,
             'price': it.final_price_per_m,
             'number': it.quotation.number} for it in qs[:limit]]


def _parse_decimal(raw, default, lo, hi):
    try:
        val = Decimal(str(raw).strip())
        assert lo <= val <= hi
        return val
    except (InvalidOperation, AssertionError):
        return None


# ---------------------------------------------------------------- 工作台

@login_required
def dashboard(request):
    today = timezone.localdate()
    recent = Quotation.objects.select_related('customer', 'created_by')[:8]
    pending = Quotation.objects.filter(status=Quotation.STATUS_PENDING)
    if not is_manager(request.user):
        pending = pending.filter(created_by=request.user)
    ctx = {
        'cu_price': cu_price_now(),
        'materials': Material.objects.all(),
        'cu_spark': sparkline(cu_price_history(30)),
        'total_quotes': Quotation.objects.count(),
        'today_quotes': Quotation.objects.filter(created_at__date=today).count(),
        'pending_count': pending.count(),
        'recent': recent,
        'params': PricingParams.load(),
        'is_manager': is_manager(request.user),
        'monthly': monthly_stats(),
        'order_stats': {
            'open': SalesOrder.objects.exclude(status=SalesOrder.STATUS_DONE).exclude(
                status=SalesOrder.STATUS_CANCELLED).count(),
            'receivable': sum((o.balance() for o in
                               SalesOrder.objects.exclude(
                                   status=SalesOrder.STATUS_CANCELLED)
                               .prefetch_related('payments')), Decimal('0')),
        },
    }
    return render(request, 'quoter/dashboard.html', ctx)


# ---------------------------------------------------------------- 快速询价

@login_required
def calc(request):
    ctx = {'series_list': CableSeries.objects.filter(active=True), 'result': None,
           'q': '', 'length': '100'}
    q = (request.POST.get('q') or request.GET.get('q') or '').strip()
    length_raw = (request.POST.get('length') or request.GET.get('length') or '100').strip()
    ctx['q'] = q
    ctx['length'] = length_raw
    if q:
        try:
            length = Decimal(length_raw) if length_raw else Decimal('100')
            if length <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            ctx['error'] = '长度请填正数'
            return render(request, 'quoter/calc.html', ctx)
        try:
            series, layout = resolve_series_layout(q)
            if series is None:
                from .pricing import parse_input
                series_code, _ = parse_input(q)
                raise ValueError('型号 %s 不在系统中，可用型号：%s' % (
                    series_code, '、'.join(CableSeries.objects.filter(active=True)
                                           .values_list('code', flat=True))))
            spec = series.specs.filter(layout=layout).first()
            if spec is None:
                raise ValueError('规格库中没有 %s %s。可让管理员在“规格库”中添加，'
                                 '或检查芯数截面写法。' % (series.code, layout))
            bd = price_spec(spec)
            ctx['result'] = {
                'spec': spec,
                'length': length,
                'bd': bd,
                'json': jsonable(bd),
                'margin_list': float(margin_pct(bd['list_per_m'], bd)),
                'total_list': (bd['list_per_m'] * length).quantize(Decimal('0.02')),
                'total_floor': (bd['floor_per_m'] * length).quantize(Decimal('0.02')),
                'deals': recent_deals(spec),
            }
        except (ValueError, MissingPriceError) as exc:
            ctx['error'] = str(exc)
    return render(request, 'quoter/calc.html', ctx)


@login_required
def api_specs(request):
    series_id = request.GET.get('series')
    specs = CableSpec.objects.filter(series_id=series_id).order_by('layout')
    return JsonResponse({'specs': [
        {'id': s.id, 'layout': s.layout, 'display': s.display} for s in specs]})


@login_required
def api_tiers(request):
    customer = Customer.objects.filter(pk=request.GET.get('customer')).first()
    if customer is None:
        return JsonResponse({'tiers': []})
    return JsonResponse({'tiers': [
        {'min_length_m': str(t.min_length_m), 'discount': str(t.discount),
         'note': t.note} for t in customer.tiers.all().order_by('min_length_m')]})


# ---------------------------------------------------------------- 报价单

@login_required
def quote_list(request):
    status = request.GET.get('status', '')
    qs = Quotation.objects.select_related('customer', 'created_by')
    if status:
        qs = qs.filter(status=status)
    # 业务员可见全部报价单（只读），编辑受状态和创建人限制
    return render(request, 'quoter/quote_list.html', {
        'quotes': qs[:200],
        'status': status,
        'STATUS_CHOICES': Quotation.STATUS_CHOICES,
        'is_manager': is_manager(request.user),
    })


def _can_edit(quote, user):
    if quote.status not in (Quotation.STATUS_DRAFT, Quotation.STATUS_REJECTED):
        return False
    return quote.created_by_id == user.id or is_manager(user)


def _build_item(quote, spec, length, sort):
    params = PricingParams.load()
    bd = price_spec(spec, params)
    list_m = bd['list_per_m'].quantize(Decimal('0.0001'))
    final_m = (list_m * quote.discount).quantize(Decimal('0.0001'))
    return QuotationItem(
        quotation=quote, spec=spec,
        spec_text='%s %s' % (spec.series.code, spec.display),
        length_m=length,
        list_price_per_m=list_m,
        final_price_per_m=final_m,
        amount=(final_m * length).quantize(Decimal('0.01')),
        cost_detail=jsonable(bd),
        sort_order=sort)


@login_required
def quote_new(request):
    return _quote_form(request, None)


@login_required
def quote_edit(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    if not _can_edit(quote, request.user):
        messages.error(request, '只有草稿/驳回状态的报价单可以编辑')
        return redirect('quote_detail', pk=pk)
    return _quote_form(request, quote)


def _quote_form(request, quote=None):
    """新建/编辑报价单共用表单。编辑时按当前价格重算全部快照。"""
    if request.method == 'POST':
        customer_id = request.POST.get('customer')
        discount_raw = request.POST.get('discount', '1').strip() or '1'
        note = request.POST.get('note', '').strip()
        spec_ids = request.POST.getlist('item_spec')
        lengths = request.POST.getlist('item_len')
        customer = Customer.objects.filter(pk=customer_id).first() if customer_id else None
        pairs = []
        errors = []
        for i, (sid, ln) in enumerate(zip(spec_ids, lengths), 1):
            spec = CableSpec.objects.filter(pk=sid).first()
            try:
                ln = Decimal(str(ln).strip())
            except InvalidOperation:
                ln = Decimal('0')
            if spec is None or ln <= 0:
                errors.append('第 %d 行：规格或长度无效' % i)
                continue
            pairs.append((spec, ln))
        if customer is None:
            errors.append('请选择客户')
        auto_tier = request.POST.get('auto_tier') == '1'
        try:
            discount = Decimal(discount_raw)
            if not (Decimal('0.5') <= discount <= Decimal('1.5')):
                raise InvalidOperation
        except InvalidOperation:
            if not auto_tier:
                errors.append('折扣无效（0.5~1.5 之间，0.95 表示 95 折）')
            discount = Decimal('1')
        freight = _parse_decimal(request.POST.get('freight', '0'), Decimal('0'),
                                 Decimal('0'), Decimal('10') ** 8)
        if freight is None:
            errors.append('运费无效')
        tier_applied = None
        if auto_tier and customer is not None:
            total_len = sum(ln for _, ln in pairs)
            tier_discount = customer.tier_discount(total_len)
            if tier_discount is not None:
                discount = tier_discount
                tier_applied = '总长 %s m 达到阶梯，已套用折扣 %s' % (total_len, tier_discount)
            elif pairs:
                tier_applied = '总长 %s m 未达任何阶梯，使用基础折扣 %s' % (total_len, discount)
        if errors:
            for e in errors:
                messages.error(request, e)
        elif not pairs:
            messages.error(request, '至少添加一行明细')
        else:
            try:
                params = PricingParams.load()
                snap = cu_snapshot() or {}
                with transaction.atomic():
                    if quote is None:
                        quote = Quotation.objects.create(
                            number=next_number(), customer=customer,
                            created_by=request.user,
                            valid_until=timezone.localdate() + timedelta(
                                days=params.quote_valid_days),
                            base_cu_price=snap.get('price') or 0,
                            cu_price_source=snap.get('source') or '',
                            cu_price_time=snap.get('time'))
                        action = '创建'
                    else:
                        quote.customer = customer
                        quote.base_cu_price = snap.get('price') or 0
                        quote.cu_price_source = snap.get('source') or ''
                        quote.cu_price_time = snap.get('time')
                        quote.save()
                        quote.items.all().delete()
                        action = '更新'
                    quote.discount = discount
                    quote.freight = freight
                    quote.note = note
                    quote.save()
                    for i, (spec, ln) in enumerate(pairs):
                        _build_item(quote, spec, ln, i).save()
                if tier_applied:
                    messages.info(request, tier_applied)
                messages.success(request, '报价单 %s 已%s（草稿）' % (quote.number, action))
                return redirect('quote_detail', pk=quote.pk)
            except MissingPriceError as exc:
                messages.error(request, '保存失败：%s，请先到“价格维护”录入今日价格' % exc)
    customers = Customer.objects.all()
    series_list = CableSeries.objects.filter(active=True)
    if quote is not None:
        default_discount = str(quote.discount)
        default_customer = quote.customer_id
        default_freight = str(quote.freight)
        default_note = quote.note
        cur_items = [
            {'spec_id': it.spec_id, 'length': str(it.length_m),
             'series_id': it.spec.series_id, 'display': it.spec.display}
            for it in quote.items.all()]
    else:
        default_discount = '1.000'
        default_customer = request.POST.get('customer') or request.GET.get('customer')
        c = Customer.objects.filter(pk=default_customer).first() if default_customer else None
        if c:
            default_discount = str(c.discount)
        default_freight = '0'
        default_note = ''
        cur_items = []
    return render(request, 'quoter/quote_form.html', {
        'customers': customers,
        'series_list': series_list,
        'default_discount': default_discount,
        'default_customer': default_customer,
        'default_freight': default_freight,
        'default_note': default_note,
        'cur_items': cur_items,
        'editing_quote': quote,
    })


@login_required
def quote_detail(request, pk):
    quote = get_object_or_404(Quotation.objects.select_related('customer', 'created_by'), pk=pk)
    items = list(quote.items.select_related('spec', 'spec__series'))
    margin = quote.margin_pct()
    deals = []
    seen = set()
    for item in items:
        if item.spec_id in seen:
            continue
        seen.add(item.spec_id)
        spec_deals = recent_deals(item.spec, exclude_quote=quote, limit=3)
        if spec_deals:
            deals.append({'spec_text': item.spec_text, 'rows': spec_deals})
    can_approve = (
        (quote.status == Quotation.STATUS_PENDING and is_manager(request.user))
        or (quote.status == Quotation.STATUS_BOSS_PENDING and is_boss(request.user)))
    can_reject = can_approve
    return render(request, 'quoter/quote_detail.html', {
        'quote': quote,
        'items': items,
        'margin': margin,
        'deals': deals,
        'can_approve': can_approve,
        'can_reject': can_reject,
        'approve_label': '终审通过' if quote.status == Quotation.STATUS_BOSS_PENDING else '审批通过',
        'goods_total': quote.total_amount() - (quote.freight or Decimal('0')),
        'params': PricingParams.load(),
        'can_edit': _can_edit(quote, request.user),
        'can_submit': _can_edit(quote, request.user),
        'is_manager': is_manager(request.user),
    })


@login_required
@require_POST
def quote_submit(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    if not _can_edit(quote, request.user):
        messages.error(request, '当前状态不允许提交')
        return redirect('quote_detail', pk=pk)
    margin = quote.margin_pct()
    if margin is None:
        messages.error(request, '报价单没有明细，无法提交')
        return redirect('quote_detail', pk=pk)
    params = PricingParams.load()
    big_amount = quote.total_amount() >= params.boss_threshold_amount
    if margin < quote.min_margin():
        quote.status = Quotation.STATUS_PENDING
        quote.save(update_fields=['status'])
        messages.warning(
            request, '实际毛利率 %.2f%% 低于最低 %.2f%%，已提交经理审批%s'
            % (margin, quote.min_margin(),
               '（金额达大单阈值，经理审批后还需老板终审）' if big_amount else ''))
        from .notify import send_wecom
        send_wecom('⏳ 报价单待审批（低毛利）', [
            '> 单号：**%s**（客户 %s，业务员 %s）'
            % (quote.number, quote.customer.name, quote.created_by.username),
            '> 实际毛利率 **%.2f%%** 低于最低 %.2f%%，金额 %s 元'
            % (margin, quote.min_margin(), quote.total_amount()),
            '> [去审批](/quotes/%d/)' % quote.pk,
        ])
    elif big_amount:
        quote.status = Quotation.STATUS_PENDING
        quote.save(update_fields=['status'])
        messages.warning(
            request, '金额 %s 元达到大单阈值（%s 元），需经理审批 + 老板终审'
            % (quote.total_amount(), params.boss_threshold_amount))
        from .notify import send_wecom
        send_wecom('⏳ 大单报价待审批', [
            '> 单号：**%s**（客户 %s，业务员 %s）'
            % (quote.number, quote.customer.name, quote.created_by.username),
            '> 金额 **%s 元** 达到大单阈值（%s 元），需经理审批 + 老板终审'
            % (quote.total_amount(), params.boss_threshold_amount),
            '> [去审批](/quotes/%d/)' % quote.pk,
        ])
    else:
        quote.status = Quotation.STATUS_APPROVED
        quote.save(update_fields=['status'])
        messages.success(request, '毛利率 %.2f%% 达标，报价单已生效' % margin)
    return redirect('quote_detail', pk=pk)


@login_required
@require_POST
def quote_approve(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    params = PricingParams.load()
    if quote.status == Quotation.STATUS_PENDING and is_manager(request.user):
        if quote.total_amount() >= params.boss_threshold_amount:
            quote.status = Quotation.STATUS_BOSS_PENDING
            quote.save(update_fields=['status'])
            messages.warning(
                request, '金额 %s 元达到大单阈值（%s 元），已转老板终审'
                % (quote.total_amount(), params.boss_threshold_amount))
        else:
            quote.status = Quotation.STATUS_APPROVED
            quote.save(update_fields=['status'])
            messages.success(request, '已审批通过')
    elif quote.status == Quotation.STATUS_BOSS_PENDING and is_boss(request.user):
        quote.status = Quotation.STATUS_APPROVED
        quote.save(update_fields=['status'])
        messages.success(request, '老板终审通过，报价单已生效')
    else:
        messages.error(request, '无权限或状态不允许')
    return redirect('quote_detail', pk=pk)


@login_required
@require_POST
def quote_reject(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    can_reject = (
        (quote.status == Quotation.STATUS_PENDING and is_manager(request.user))
        or (quote.status == Quotation.STATUS_BOSS_PENDING and is_boss(request.user)))
    if not can_reject:
        messages.error(request, '无权限或状态不允许')
        return redirect('quote_detail', pk=pk)
    quote.status = Quotation.STATUS_REJECTED
    quote.save(update_fields=['status'])
    messages.warning(request, '已驳回，业务员可修改折扣后重新提交')
    return redirect('quote_detail', pk=pk)


@login_required
@require_POST
def quote_delete(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    if not (quote.status == Quotation.STATUS_DRAFT and
            (quote.created_by_id == request.user.id or is_manager(request.user))):
        messages.error(request, '只有草稿可以删除')
        return redirect('quote_detail', pk=pk)
    number = quote.number
    quote.delete()
    messages.success(request, '报价单 %s 已删除' % number)
    return redirect('quote_list')


@login_required
def quote_export(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    if request.GET.get('erp') == '1':
        return excel.export_erp_csv(excel.quote_erp_rows(quote), excel.ERP_HEADER,
                                    '%s_erp.csv' % quote.number)
    return excel.export_quotation(quote)


@login_required
def order_export(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    return excel.export_erp_csv(excel.order_erp_rows(order), excel.ERP_HEADER,
                                '%s_erp.csv' % order.number)


@login_required
@require_POST
def quote_send(request, pk):
    """把报价单以 HTML 邮件 + Excel 附件发给客户，并留痕。"""
    import re as _re
    from django.conf import settings as dj_settings
    from django.core.mail import EmailMessage

    quote = get_object_or_404(Quotation, pk=pk)
    emails = [e.strip() for e in request.POST.get('emails', '').split(',')
              if e.strip()]
    emails = [e for e in emails if _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', e)]
    if not emails:
        messages.error(request, '请填写有效的收件邮箱')
        return redirect('quote_detail', pk=pk)
    if not dj_settings.EMAIL_HOST:
        messages.error(
            request, '未配置邮件服务：请在启动前设置环境变量 EMAIL_HOST / '
                     'EMAIL_HOST_USER / EMAIL_HOST_PASSWORD（详见 README 部署章节）')
        return redirect('quote_detail', pk=pk)

    params = PricingParams.load()
    body = render(request, 'quoter/mail_quote.html', {
        'quote': quote, 'items': quote.items.all(), 'params': params,
    }).content.decode('utf-8')
    msg = EmailMessage(
        subject='【%s】报价单 %s' % (params.company_name, quote.number),
        body=body,
        from_email=dj_settings.DEFAULT_FROM_EMAIL,
        to=emails,
    )
    msg.content_subtype = 'html'
    msg.attach('%s.xlsx' % quote.number,
               excel.export_quotation_bytes(quote),
               'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    try:
        msg.send()
    except Exception as exc:
        messages.error(request, '发送失败：%s' % exc)
        return redirect('quote_detail', pk=pk)
    QuoteSendLog.objects.create(
        quotation=quote, sent_to=', '.join(emails), sent_by=request.user,
        note=request.POST.get('note', '').strip())
    messages.success(request, '报价单已发送至 %s' % ', '.join(emails))
    return redirect('quote_detail', pk=pk)


# ---------------------------------------------------------------- 价格维护

@login_required
def prices(request):
    today = timezone.localdate()
    materials = list(Material.objects.all())
    if request.method == 'POST':
        updated = 0
        for mat in materials:
            raw = request.POST.get('price_%s' % mat.code, '').strip()
            prem_raw = request.POST.get('premium_%s' % mat.code, '').strip()
            if not raw and not prem_raw:
                continue
            try:
                val = Decimal(raw) if raw else None
                if val is not None and val <= 0:
                    raise InvalidOperation
                premium = Decimal(prem_raw) if prem_raw else Decimal('0')
            except InvalidOperation:
                messages.error(request, '%s 价格无效，已跳过' % mat.name)
                continue
            defaults = {'source': MaterialPrice.SOURCE_MANUAL, 'is_auto': False,
                        'premium': premium}
            if val is not None:
                defaults['price'] = val
            MaterialPrice.objects.update_or_create(
                material=mat, date=today, defaults=defaults)
            updated += 1
        if updated:
            messages.success(request, '已保存 %s 的 %d 项价格（手工录入/修正）' % (today, updated))
        return redirect('prices')
    rows = []
    for mat in materials:
        today_p = mat.prices.filter(date=today).first()
        last_p = mat.latest_price()
        rows.append({
            'mat': mat,
            'today': today_p.price if today_p else '',
            'premium': today_p.premium if today_p else '',
            'source': today_p.get_source_display() if today_p else '',
            'auto': today_p.is_auto if today_p else False,
            'saved_at': today_p.created_at if today_p else None,
            'last': ('%s（%s，%s）' % (last_p.effective_price, last_p.date,
                                     last_p.get_source_display())) if last_p else '从未录价',
        })
    return render(request, 'quoter/prices.html', {'rows': rows, 'today': today})


# ---------------------------------------------------------------- Excel 导入

@login_required
def import_quotes(request):
    """第一步：上传并预览。"""
    if request.method == 'POST' and request.FILES.get('file'):
        try:
            items, errors = excel.parse_inquiry(request.FILES['file'])
        except Exception:
            messages.error(request, '文件无法解析，请使用系统提供的导入模板（.xlsx）')
        else:
            request.session['import_items'] = [
                [spec.id, str(length), '%s %s' % (spec.series.code, spec.display)]
                for spec, length in items]
            request.session['import_errors'] = [
                [str(r), t, str(e)] for r, t, e in errors]
            messages.success(request, '解析完成：%d 条有效，%d 条有误' % (len(items), len(errors)))
        return redirect('import_quotes')
    import_items = request.session.get('import_items', [])
    import_errors = request.session.get('import_errors', [])
    return render(request, 'quoter/import.html', {
        'import_items': import_items,
        'import_errors': import_errors,
        'customers': Customer.objects.all(),
    })


@login_required
@require_POST
def import_create(request):
    import_items = request.session.get('import_items', [])
    customer = Customer.objects.filter(pk=request.POST.get('customer')).first()
    discount_raw = (request.POST.get('discount') or '1').strip()
    if not import_items:
        messages.error(request, '没有待导入的询价数据，请先上传文件')
        return redirect('import_quotes')
    if customer is None:
        messages.error(request, '请选择客户')
        return redirect('import_quotes')
    try:
        discount = Decimal(discount_raw)
        assert Decimal('0.5') <= discount <= Decimal('1.5')
    except (InvalidOperation, AssertionError):
        if request.POST.get('auto_tier') != '1':
            messages.error(request, '折扣无效')
            return redirect('import_quotes')
        discount = Decimal('1')
    if request.POST.get('auto_tier') == '1':
        total_len = sum((Decimal(ln) for _, ln, _ in import_items), Decimal('0'))
        tier_discount = customer.tier_discount(total_len)
        if tier_discount is not None:
            discount = tier_discount
            messages.info(request, '总长 %s m 达到阶梯，已套用折扣 %s'
                          % (total_len, tier_discount))
    try:
        params = PricingParams.load()
        snap = cu_snapshot() or {}
        with transaction.atomic():
            quote = Quotation.objects.create(
                number=next_number(), customer=customer, created_by=request.user,
                valid_until=timezone.localdate() + timedelta(
                    days=params.quote_valid_days),
                discount=discount,
                note=request.POST.get('note', '').strip(),
                freight=_parse_decimal(request.POST.get('freight', '0'), Decimal('0'),
                                       Decimal('0'), Decimal('10') ** 8) or 0,
                base_cu_price=snap.get('price') or 0,
                cu_price_source=snap.get('source') or '',
                cu_price_time=snap.get('time'))
            for i, (sid, ln, _text) in enumerate(import_items):
                spec = CableSpec.objects.filter(pk=sid).first()
                if spec:
                    _build_item(quote, spec, Decimal(ln), i).save()
    except MissingPriceError as exc:
        messages.error(request, '导入失败：%s' % exc)
        return redirect('import_quotes')
    request.session['import_items'] = []
    request.session['import_errors'] = []
    messages.success(request, '已生成报价单草稿 %s，请核对后提交' % quote.number)
    return redirect('quote_detail', pk=quote.pk)


@login_required
def import_template(request):
    return excel.empty_template()


# ---------------------------------------------------------------- 规格库

@login_required
def spec_list(request):
    series_id = request.GET.get('series', '')
    q = request.GET.get('q', '').strip().replace('×', 'x')
    qs = CableSpec.objects.select_related('series', 'conductor_material')
    if series_id:
        qs = qs.filter(series_id=series_id)
    if q:
        qs = qs.filter(layout__icontains=q)
    return render(request, 'quoter/specs.html', {
        'specs': qs[:100],
        'series_list': CableSeries.objects.filter(active=True),
        'series_id': series_id,
        'q': q,
    })


# ---------------------------------------------------------------- 销售订单

@login_required
@require_POST
def quote_to_order(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    if quote.status != Quotation.STATUS_APPROVED:
        messages.error(request, '只有已审批的报价单可以转订单')
        return redirect('quote_detail', pk=pk)
    if hasattr(quote, 'order'):
        messages.error(request, '该报价单已转为订单 %s' % quote.order.number)
        return redirect('order_detail', pk=quote.order.pk)
    order = SalesOrder.objects.create(
        quotation=quote, number=next_number('SO'), customer=quote.customer,
        created_by=request.user, amount=quote.total_amount())
    messages.success(request, '已生成销售订单 %s（生产中）' % order.number)
    return redirect('order_detail', pk=order.pk)


@login_required
def order_list(request):
    status = request.GET.get('status', '')
    qs = SalesOrder.objects.select_related('customer', 'quotation', 'created_by')
    if status:
        qs = qs.filter(status=status)
    return render(request, 'quoter/order_list.html', {
        'orders': qs[:200],
        'status': status,
        'STATUS_CHOICES': SalesOrder.STATUS_CHOICES,
    })


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related('customer', 'quotation', 'created_by'), pk=pk)
    items = list(order.quotation.items.select_related('spec', 'spec__series'))
    can_manage = is_manager(request.user) or order.created_by_id == request.user.id
    next_status = {
        SalesOrder.STATUS_PRODUCTION: (SalesOrder.STATUS_SHIPPED, '标记发货'),
        SalesOrder.STATUS_SHIPPED: (SalesOrder.STATUS_DONE, '标记完成'),
    }.get(order.status)
    return render(request, 'quoter/order_detail.html', {
        'order': order,
        'items': items,
        'payments': order.payments.all(),
        'paid': order.paid_amount(),
        'balance': order.balance(),
        'can_manage': can_manage,
        'next_status': next_status,
    })


@login_required
@require_POST
def order_status(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    if not (is_manager(request.user) or order.created_by_id == request.user.id):
        messages.error(request, '无权限')
        return redirect('order_detail', pk=pk)
    action = request.POST.get('action')
    flow = {
        'ship': (SalesOrder.STATUS_PRODUCTION, SalesOrder.STATUS_SHIPPED),
        'done': (SalesOrder.STATUS_SHIPPED, SalesOrder.STATUS_DONE),
        'cancel': None,
    }
    if action == 'cancel' and order.status != SalesOrder.STATUS_DONE:
        order.status = SalesOrder.STATUS_CANCELLED
        order.save(update_fields=['status'])
        messages.warning(request, '订单已取消')
    elif action in flow and flow[action] and order.status == flow[action][0]:
        order.status = flow[action][1]
        order.save(update_fields=['status'])
        messages.success(request, '状态已更新为“%s”' % order.status_label)
    else:
        messages.error(request, '当前状态不允许该操作')
    return redirect('order_detail', pk=pk)


@login_required
@require_POST
def payment_add(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    amount = _parse_decimal(request.POST.get('amount'), None,
                            Decimal('0.01'), Decimal('10') ** 9)
    if amount is None:
        messages.error(request, '回款金额无效')
        return redirect('order_detail', pk=pk)
    Payment.objects.create(
        order=order, amount=amount,
        method=request.POST.get('method', '').strip(),
        note=request.POST.get('note', '').strip())
    messages.success(request, '已登记回款 %s 元，未回款余额 %s 元'
                     % (amount, order.balance()))
    return redirect('order_detail', pk=pk)


# ---------------------------------------------------------------- 询价线索与客户链接

@login_required
def lead_list(request):
    leads = InquiryLead.objects.prefetch_related('items__spec__series').order_by('-created_at')
    return render(request, 'quoter/lead_list.html', {'leads': leads[:200]})


@login_required
@require_POST
def lead_to_quote(request, pk):
    """把询价线索一键转成报价单草稿（按快照价格）。"""
    import secrets as _secrets
    lead = get_object_or_404(InquiryLead.objects.prefetch_related('items'), pk=pk)
    if lead.quotation_id:
        messages.error(request, '该线索已转过报价单 %s' % lead.quotation.number)
        return redirect('quote_detail', pk=lead.quotation_id)
    customer = Customer.objects.filter(name=lead.company).first()
    if customer is None:
        customer = Customer.objects.create(
            name=lead.company, contact=lead.contact_name, phone=lead.phone,
            notes='门户询价线索 %s' % lead.created_at.strftime('%Y-%m-%d'))
    params = PricingParams.load()
    snap = cu_snapshot() or {}
    with transaction.atomic():
        quote = Quotation.objects.create(
            number=next_number(), customer=customer, created_by=request.user,
            valid_until=timezone.localdate() + timedelta(days=params.quote_valid_days),
            base_cu_price=snap.get('price') or 0,
            cu_price_source=snap.get('source') or '',
            cu_price_time=snap.get('time'),
            note='来源：门户询价（%s %s %s）%s' % (
                lead.company, lead.contact_name, lead.phone, lead.note))
        for i, item in enumerate(lead.items.all()):
            spec = item.spec
            try:
                bd = price_spec(spec)
                list_m = bd['list_per_m'].quantize(Decimal('0.0001'))
                detail = jsonable(bd)
            except MissingPriceError:
                messages.warning(request, '规格 %s 价格缺失，请提交前补录价格' % item.spec_text)
                list_m = Decimal('0')
                detail = {}
            QuotationItem.objects.create(
                quotation=quote, spec=spec, spec_text=item.spec_text,
                length_m=item.length_m,
                list_price_per_m=list_m,
                final_price_per_m=list_m,
                amount=(list_m * item.length_m).quantize(Decimal('0.01')),
                cost_detail=detail, sort_order=i)
        lead.status = InquiryLead.STATUS_HANDLED
        lead.handled_by = request.user
        lead.quotation = quote
        lead.save(update_fields=['status', 'handled_by', 'quotation'])
    messages.success(request, '线索已转为报价单草稿 %s' % quote.number)
    return redirect('quote_detail', pk=quote.pk)


@login_required
@require_POST
def quote_share(request, pk):
    """生成（或复用）报价单的客户查看链接。"""
    import secrets as _secrets
    quote = get_object_or_404(Quotation, pk=pk)
    if quote.status != Quotation.STATUS_APPROVED:
        messages.error(request, '只有已审批的报价单可以生成客户链接')
        return redirect('quote_detail', pk=pk)
    if not quote.share_token:
        quote.share_token = _secrets.token_urlsafe(24)
        quote.save(update_fields=['share_token'])
    link = request.build_absolute_uri('/q/%s/' % quote.share_token)
    messages.info(request, '客户链接（免登录，可查看并签收）：%s' % link)
    return redirect('quote_detail', pk=pk)


# ---------------------------------------------------------------- 客户管理

@login_required
def customer_list(request):
    return render(request, 'quoter/customer_list.html', {
        'customers': Customer.objects.prefetch_related('tiers', 'quotations').all(),
    })


def _customer_form(request, customer=None):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        errors = []
        if not name:
            errors.append('请填写客户名称')
        if (Customer.objects.exclude(pk=customer.pk if customer else None)
                .filter(name=name).exists()):
            errors.append('客户名称已存在')
        discount = _parse_decimal(request.POST.get('discount', '1'), None,
                                  Decimal('0.5'), Decimal('1.5'))
        if discount is None:
            errors.append('等级折扣无效（0.5~1.5）')
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            customer = customer or Customer()
            customer.name = name
            customer.level = request.POST.get('level', 'B')
            customer.discount = discount
            customer.contact = request.POST.get('contact', '').strip()
            customer.phone = request.POST.get('phone', '').strip()
            customer.notes = request.POST.get('notes', '').strip()
            customer.save()
            customer.tiers.all().delete()
            mins = request.POST.getlist('tier_min')
            discs = request.POST.getlist('tier_disc')
            for m, d in zip(mins, discs):
                m, d = m.strip(), d.strip()
                if not m or not d:
                    continue
                tm = _parse_decimal(m, None, Decimal('0.01'), Decimal('10') ** 8)
                td = _parse_decimal(d, None, Decimal('0.5'), Decimal('1.5'))
                if tm is None or td is None:
                    continue
                CustomerTier.objects.create(customer=customer, min_length_m=tm,
                                             discount=td)
            messages.success(request, '客户 %s 已保存' % customer.name)
            return redirect('customer_list')
    return render(request, 'quoter/customer_form.html', {
        'customer': customer,
        'level_choices': Customer.LEVEL_CHOICES,
    })


@login_required
def customer_new(request):
    return _customer_form(request)


@login_required
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    return _customer_form(request, customer)


# ---------------------------------------------------------------- 报表

@login_required
def report(request):
    from django.db.models import Count, Sum

    month_first = timezone.localdate().replace(day=1)
    quotes_m = Quotation.objects.filter(created_at__gte=month_first)
    by_sales = (quotes_m.values('created_by__username')
                .annotate(n=Count('id'), amount=Sum('items__amount'))
                .order_by('-amount'))
    by_customer = (Quotation.objects.filter(
        status=Quotation.STATUS_APPROVED, created_at__gte=month_first)
        .values('customer__name')
        .annotate(n=Count('id'), amount=Sum('items__amount'))
        .order_by('-amount')[:10])
    top_specs = (QuotationItem.objects.filter(
        quotation__status=Quotation.STATUS_APPROVED,
        quotation__created_at__gte=month_first)
        .values('spec_text')
        .annotate(n=Count('id'), length=Sum('length_m'), amount=Sum('amount'))
        .order_by('-amount')[:10])
    return render(request, 'quoter/report.html', {
        'month': month_first.strftime('%Y-%m'),
        'by_sales': by_sales,
        'by_customer': by_customer,
        'top_specs': top_specs,
        'monthly': monthly_stats(),
    })


# ---------------------------------------------------------------- 报表（旧）

def monthly_stats(months=6):
    """近 N 个月：报价数 / 审批数 / 转单数 / 订单额 / 回款额。"""
    from django.db.models import Sum

    rows = []
    today = timezone.localdate()
    for i in range(months - 1, -1, -1):
        month_first = today.replace(day=1) - timedelta(days=i * 31)
        month_first = month_first.replace(day=1)
        month_end = (month_first + timedelta(days=32)).replace(day=1)
        quotes = Quotation.objects.filter(
            created_at__gte=month_first, created_at__lt=month_end)
        approved = quotes.filter(status=Quotation.STATUS_APPROVED).count()
        orders = SalesOrder.objects.filter(
            created_at__gte=month_first, created_at__lt=month_end)
        paid = Payment.objects.filter(
            date__gte=month_first, date__lt=month_end)
        rows.append({
            'month': month_first.strftime('%Y-%m'),
            'quotes': quotes.count(),
            'approved': approved,
            'orders': orders.count(),
            'order_amount': orders.aggregate(s=Sum('amount'))['s'] or Decimal('0'),
            'paid_amount': paid.aggregate(s=Sum('amount'))['s'] or Decimal('0'),
        })
    return rows
