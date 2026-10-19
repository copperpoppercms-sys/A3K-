"""URL fetcher with SSRF guard (public addresses only, re-checked on every redirect) and a size cap."""
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 2 * 1024 * 1024


class FetchError(Exception):
    pass


def _check(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("only http(s) urls are allowed")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FetchError("dns failed: %s" % e)
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise FetchError("blocked non-public address")


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=15):
    _check(url)
    opener = urllib.request.build_opener(_Redirects)
    req = urllib.request.Request(url, headers={"User-Agent": "Roblox/WinInet"})
    try:
        with opener.open(req, timeout=timeout) as r:
            data = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise FetchError(str(e))
    if len(data) > MAX_BYTES:
        raise FetchError("response larger than %d bytes" % MAX_BYTES)
    return data.decode("latin-1")
