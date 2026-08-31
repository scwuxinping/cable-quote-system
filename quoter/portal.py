"""客户自助门户：免登录询价 + 报价单在线查看/签收。

安全边界：门户只输出目录价（list price），任何成本/底价/毛利信息
不得出现在门户模板或 JSON 响应中。
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (CableSeries, InquiryLead, InquiryLeadItem, PricingParams,
                     Quotation)
from .pricing import MissingPriceError, jsonable, price_spec, resolve_series_layout

MAX_ROWS = 20


def _portal_code_ok(request):
    params = PricingParams.load()
    if not params.portal_code:
        return True
    return request.session.get('portal_ok') is True


def portal_home(request):
    params = PricingParams.load()
    if not params.portal_enabled:
        return render(request, 'quoter/portal/closed.html', {'params': params},
                      status=503)
    ctx = {'params': params, 'series_list': CableSeries.objects.filter(active=True)}

    # 访问码
    if not _portal_code_ok(request):
        if request.method == 'POST' and request.POST.get('op') == 'code':
            if request.POST.get('code', '').strip() == params.portal_code:
                request.session['portal_ok'] = True
                return redirect('portal_home')
            ctx['code_error'] = '访问码不正确，请联系业务员获取'
        return render(request, 'quoter/portal/code.html', ctx)

    if request.method == 'POST' and request.POST.get('op') == 'inquiry':
        return _submit_inquiry(request, ctx)

    ctx['rows'] = request.session.pop('portal_rows', None)
    return render(request, 'quoter/portal/home.html', ctx)


def _submit_inquiry(request, ctx):
    company = request.POST.get('company', '').strip()
    contact = request.POST.get('contact', '').strip()
    phone = request.POST.get('phone', '').strip()
    texts = request.POST.getlist('row_text')[:MAX_ROWS]
    lens = request.POST.getlist('row_len')[:MAX_ROWS]

    errors = []
    pairs = []
    for i, (text, ln_raw) in enumerate(zip(texts, lens), 1):
        text = text.strip()
        if not text and not ln_raw.strip():
            continue
        try:
            ln = Decimal(ln_raw)
            if ln <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            errors.append('第 %d 行：长度无效' % i)
            continue
        if not text:
            errors.append('第 %d 行：请填写型号规格' % i)
            continue
        try:
            series, layout = resolve_series_layout(text)
            if series is None:
                raise ValueError('型号无法识别')
            spec = series.specs.filter(layout=layout).first()
            if spec is None:
                raise ValueError('规格库中无此规格')
        except ValueError as exc:
            errors.append('第 %d 行「%s」：%s' % (i, text, exc))
            continue
        pairs.append((spec, ln))
    if not company or not contact or not phone:
        errors.append('请填写公司名称、联系人和电话')
    if errors:
        ctx['errors'] = errors
        ctx['form'] = request.POST
        return render(request, 'quoter/portal/home.html', ctx)
    if not pairs:
        ctx['errors'] = ['请至少填写一行询价明细']
        ctx['form'] = request.POST
        return render(request, 'quoter/portal/home.html', ctx)

    # 逐行取目录价快照（价格缺失的行降级为 0 并继续）
    rows = []
    for spec, ln in pairs:
        try:
            bd = price_spec(spec)
            rows.append({'spec': spec, 'length': ln,
                         'price': bd['list_per_m'], 'snap': jsonable(bd)})
        except MissingPriceError:
            rows.append({'spec': spec, 'length': ln, 'price': Decimal('0'),
                         'snap': None})

    lead = InquiryLead.objects.create(
        company=company, contact_name=contact, phone=phone,
        note=request.POST.get('note', '').strip())
    for r in rows:
        InquiryLeadItem.objects.create(
            lead=lead, spec=r['spec'],
            spec_text='%s %s' % (r['spec'].series.code, r['spec'].display),
            length_m=r['length'], quoted_price_per_m=r['price'])
    request.session['portal_rows'] = [
        {'text': it.spec_text, 'length': str(it.length_m),
         'price': str(it.quoted_price_per_m)} for it in lead.items.all()]
    return redirect('portal_success')


def portal_success(request):
    params = PricingParams.load()
    rows = request.session.pop('portal_rows', [])
    return render(request, 'quoter/portal/success.html', {
        'params': params, 'rows': rows})


def portal_price(request):
    """门户即时目录价预览（JSON）。只返回目录价，不含成本。"""
    if not _portal_code_ok(request):
        return JsonResponse({'ok': False, 'error': 'unauthorized'}, status=403)
    q = (request.GET.get('q') or '').strip()
    length_raw = (request.GET.get('length') or '1').strip()
    try:
        length = Decimal(length_raw)
        if length <= 0 or length > Decimal('10000000'):
            raise InvalidOperation
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': '长度无效'})
    try:
        series, layout = resolve_series_layout(q)
        if series is None:
            raise ValueError('型号无法识别')
        spec = series.specs.filter(layout=layout).first()
        if spec is None:
            raise ValueError('规格库中无此规格，请核对写法')
        bd = price_spec(spec)
        return JsonResponse({
            'ok': True,
            'name': '%s %s' % (series.code, spec.display),
            'price_per_m': str(bd['list_per_m'].quantize(Decimal('0.0001'))),
            'total': str((bd['list_per_m'] * length).quantize(Decimal('0.02'))),
        })
    except (ValueError, MissingPriceError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)})


def portal_quote(request, token):
    """客户通过链接查看报价单（仅目录成交价，无成本），可确认签收。"""
    quote = get_object_or_404(
        Quotation.objects.select_related('customer'), share_token=token)
    params = PricingParams.load()
    if quote.status not in (Quotation.STATUS_APPROVED,):
        return render(request, 'quoter/portal/quote_unavailable.html',
                      {'params': params}, status=404)
    return render(request, 'quoter/portal/quote.html', {
        'quote': quote, 'items': quote.items.all(), 'params': params,
        'company': params.company_name})


@require_POST
def portal_sign(request, token):
    quote = get_object_or_404(Quotation, share_token=token)
    signed_by = request.POST.get('signed_by', '').strip()
    if not signed_by:
        messages.error(request, '请填写您的姓名')
        return redirect('portal_quote', token=token)
    if not quote.signed_at:
        quote.signed_at = timezone.now()
        quote.signed_by = signed_by[:50]
        quote.save(update_fields=['signed_at', 'signed_by'])
    return redirect('portal_quote', token=token)
