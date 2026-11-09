"""Fixed redirects for the native Today browser; no profile or query data forwarded."""
from urllib.parse import urlsplit

HOST = 'https://riseofincarnates.artificerreviver.com'


def respond(method, path):
    route = urlsplit(path).path
    if route not in ('/today/news','/today/support'):
        return None
    headers = {'Cache-Control':'no-store','Content-Length':'0'}
    if method not in ('GET','HEAD'):
        return 405, dict(headers, Allow='GET, HEAD'), b''
    return 302, dict(headers, Location=HOST+'/'+route.rsplit('/',1)[1]), b''
