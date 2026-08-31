"""企业微信群机器人通知（可选功能）。

启用方式：设置环境变量 WECOM_WEBHOOK（企业微信群 → 添加机器人 → 复制
Webhook 地址）。未配置时所有通知静默跳过，不影响主流程。

安全模式：仅允许企业微信官方域名（白名单），解析 IP 不得为内网/保留地址，
禁止重定向，短超时，失败静默。
"""
import ipaddress
import json
import logging
import socket
import urllib.request
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

ALLOWED_HOST = 'qyapi.weixin.qq.com'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSError('webhook redirect rejected: %s' % newurl)


def _webhook_safe(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != ALLOWED_HOST:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return False
    return True


def send_wecom(title, lines):
    """发 markdown 消息到企业微信群。fire-and-forget，任何异常仅记日志。"""
    url = getattr(settings, 'WECOM_WEBHOOK', '') or ''
    if not url:
        return False
    if not _webhook_safe(url):
        logger.warning('WECOM_WEBHOOK 非白名单地址，已拒绝发送')
        return False
    content = '**%s**\n%s' % (title, '\n'.join(lines))
    payload = json.dumps({'msgtype': 'markdown',
                          'markdown': {'content': content[:1900]}}).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'cable-quote/1.0'})
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=8) as resp:
            resp.read()
        return True
    except Exception:
        logger.exception('企业微信通知发送失败')
        return False
