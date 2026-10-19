"""URL fetcher with SSRF guard (public addresses only, re-checked on every redirect) and a size cap."""
import ipaddress
import re
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


_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
_JUNK_RE = re.compile(r"^[\s\"'()<>;,.]*$")


def extract_loader_url(text):
    """The url when `text` is a bare link or a one-line loader like loadstring(game:HttpGet("url"))(), else None."""
    text = text.strip()
    urls = _URL_RE.findall(text)
    if len(urls) != 1 or len(text) > 600:
        return None
    rest = text.replace(urls[0], "", 1)
    if _JUNK_RE.match(rest) or re.search(r"HttpGet|loadstring", rest):
        return urls[0]
    return None
