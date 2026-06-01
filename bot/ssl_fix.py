import ssl
import certifi

# Patch Python's SSL to use certifi CA bundle on Windows
ssl.create_default_context = lambda *args, **kwargs: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).__class__.__new__(ssl.SSLContext) or _patched_ctx()

def _patched_ctx(purpose=ssl.Purpose.SERVER_AUTH, **kwargs):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(certifi.where())
    return ctx

ssl.create_default_context = _patched_ctx
