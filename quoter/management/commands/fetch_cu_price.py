"""铜价获取：自动抓取 + 管理员手工录入/修正，写入 MaterialPrice 留痕。

用法：
  py -3.13 manage.py fetch_cu_price                    # 自动：上海期货 沪铜连续（元/吨→元/kg）
  py -3.13 manage.py fetch_cu_price --premium 1.5      # 自动 + 升贴水 1.5 元/kg
  py -3.13 manage.py fetch_cu_price --price 75.2       # 手工录入/修正（最高优先级）
  py -3.13 manage.py fetch_cu_price --price 75.2 --premium 0.8

来源说明：
  shfe       上海期货 沪铜连续（经新浪行情接口，结算参考）
  lme        LME：需外币价与汇率，按 --price 手工换算后录入并带 --rate 留痕
  changjiang 长江现货：无公开稳定接口，建议盘中手工录入或自建采集后走 --price

可在 Windows 任务计划中每天早上执行一次实现自动化；当天如已存在记录会被
本次结果覆盖（update_or_create），手工修正同样覆盖自动值。
"""
import ipaddress
import re
import socket
import urllib.request
from decimal import Decimal
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from quoter.models import Material, MaterialPrice

ALLOWED_SCHEME = 'https'
ALLOWED_HOST = 'hq.sinajs.cn'
SINA_FUT_URL = 'https://hq.sinajs.cn/list=nf_CU0'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CommandError('行情接口发生重定向，已按安全策略拒绝（%s）' % newurl)


def _assert_public_https(url):
    """固定 URL 边界校验：仅允许 https + 白名单域名，且解析 IP 不得为内网/环回地址。"""
    parsed = urlparse(url)
    if parsed.scheme != ALLOWED_SCHEME or parsed.hostname != ALLOWED_HOST:
        raise CommandError('非白名单行情地址：%s' % url)
    try:
        infos = socket.getaddrinfo(parsed.hostname, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise CommandError('行情域名解析失败：%s' % exc)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast):
            raise CommandError('行情域名解析到内网/保留地址 %s，已拒绝' % ip)


def _fetch_shfe_cu():
    """新浪期货行情取沪铜连续最新价（元/吨），返回 (元/kg, 合约名, 行情日期)。"""
    _assert_public_https(SINA_FUT_URL)
    req = urllib.request.Request(SINA_FUT_URL, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0 (compatible; CableQuote/1.0)',
    })
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=15) as resp:
        text = resp.read().decode('gbk', errors='ignore')
    m = re.search(r'"([^"]+)"', text)
    if not m or not m.group(1):
        raise CommandError('行情接口返回为空')
    fields = m.group(1).split(',')
    try:
        latest_t = Decimal(fields[8])       # 最新价 元/吨
    except Exception:
        raise CommandError('行情接口字段解析失败：%s' % text[:80])
    if latest_t <= 0:
        raise CommandError('行情接口返回价格异常：%s' % latest_t)
    return latest_t / Decimal('1000'), fields[0].strip(), fields[16]


class Command(BaseCommand):
    help = '获取当日铜价（自动抓取或手工录入），写入材料价格表'

    def add_arguments(self, parser):
        parser.add_argument('--price', type=str, default=None,
                            help='手工录入/修正价（元/kg），优先于自动抓取')
        parser.add_argument('--lme', type=str, default=None,
                            help='LME 结算价（USD/吨），配合 --rate 自动换算为 元/kg 录入')
        parser.add_argument('--premium', type=str, default='0',
                            help='铜价升贴水 元/kg，可为负')
        parser.add_argument('--rate', type=str, default=None,
                            help='汇率 USD/CNY（--lme 必填；--price 时仅留痕）')
        parser.add_argument('--source', default='shfe',
                            choices=['shfe', 'changjiang', 'lme'],
                            help='自动抓取来源（默认 shfe 沪铜连续）')

    def handle(self, *args, **options):
        cu = Material.objects.filter(code='CU').first()
        if cu is None:
            raise CommandError('材料 CU 不存在，请先执行 seed')

        premium = Decimal(options['premium'])
        rate = Decimal(options['rate']) if options['rate'] else None

        if options['lme'] is not None:
            if rate is None:
                raise CommandError('使用 --lme 时必须同时指定 --rate 汇率，'
                                   '示例：--lme 10500 --rate 7.15')
            price = (Decimal(options['lme']) * rate / Decimal('1000')).quantize(
                Decimal('0.0001'))
            source = MaterialPrice.SOURCE_LME
            is_auto = False
            self.stdout.write('LME %s USD/t × 汇率 %s ÷ 1000 = %s 元/kg'
                              % (options['lme'], rate, price))
        elif options['price'] is not None:
            price = Decimal(options['price'])
            source = MaterialPrice.SOURCE_MANUAL
            is_auto = False
            self.stdout.write('手工录入模式')
        elif options['source'] == 'shfe':
            price, name, qdate = _fetch_shfe_cu()
            source = MaterialPrice.SOURCE_SHFE
            is_auto = True
            self.stdout.write('已获取 %s（%s）：%s 元/吨' % (name, qdate, price * 1000))
        elif options['source'] == 'lme':
            raise CommandError(
                'LME 暂无免费稳定接口：请查 LME 结算价后用 '
                '--lme <USD/吨> --rate <汇率> 自动换算录入（来源留痕为 LME）')
        else:
            raise CommandError(
                '长江现货暂无公开稳定接口：请盘中查询后用 --price 手工录入，'
                '或自建采集程序调用本命令')

        today = timezone.localdate()
        mp, created = MaterialPrice.objects.update_or_create(
            material=cu, date=today,
            defaults={'price': price, 'source': source, 'premium': premium,
                      'exchange_rate': rate, 'is_auto': is_auto})
        self.stdout.write(self.style.SUCCESS(
            '%s %s：%s 元/kg（升贴水 %s，来源 %s，生效 %s %s）' % (
                '新建' if created else '更新', cu.code, price, premium,
                mp.get_source_display(), mp.date,
                timezone.localtime(mp.created_at).strftime('%H:%M'))))
        self.stdout.write('当前计价铜价（含升贴水）：%s 元/kg' % mp.effective_price)
