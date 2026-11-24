"""Автоматическое прохождение OAuth Flowwow MCP после rate-limit."""
import base64, hashlib, json, re, secrets, socket, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import httpx

HOST = "apiseller-mcp.flowwow.info"
IP = "158.160.254.234"
BASE = f"https://{HOST}"
_orig = socket.getaddrinfo
def patched(host, *a, **kw):
    if host == HOST:
        return _orig(IP, *a, **kw)
    return _orig(host, *a, **kw)
socket.getaddrinfo = patched

print("жду окончания rate-limit (44 мин)...", flush=True)
time.sleep(2640)

CRED = r"C:/Users/наташа/.kimi-code/credentials/mcp"
client = json.load(open(f"{CRED}/flowwow-b9d421363ab06de1ec9f583f-client.json", encoding="utf-8"))
client_id = client["client_id"]
scopes = " ".join(client["scope"]) if isinstance(client.get("scope"), list) else client["scope"]
token = [l.split("=", 1)[1].strip() for l in open(".secrets.local") if l.startswith("FLOWWOW_TOKEN")][0]
port = 41523
redirect = f"http://127.0.0.1:{port}/callback"

captured = {}
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        captured.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
        self.send_response(200); self.end_headers()
        self.wfile.write(b"<h2>OK</h2>")
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

verifier = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
auth_url = (BASE + "/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
    "scope": scopes, "state": secrets.token_hex(16),
    "code_challenge": challenge, "code_challenge_method": "S256",
    "resource": BASE + "/mcp"}))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}

def req(fn, n=6):
    for a in range(n):
        try:
            return fn()
        except Exception as e:
            print("retry:", str(e)[:50], flush=True); time.sleep(8)
    raise SystemExit("network dead")

with httpx.Client(follow_redirects=False, timeout=60, headers=UA) as c:
    r = req(lambda: c.get(auth_url))
    loc = r.headers["location"]
    print("authorize:", r.status_code, flush=True)
    for attempt in range(4):
        r = req(lambda: c.get(BASE + loc if loc.startswith("/") else loc))
        csrf = re.search(r'name="csrf" value="([^"]+)"', r.text)
        rid = re.search(r'name="request_id" value="([^"]+)"', r.text)
        if not csrf:
            print("нет формы:", r.text[:200], flush=True); break
        r = req(lambda: c.post(BASE + "/connect",
                data={"csrf": csrf.group(1), "request_id": rid.group(1), "api_token": token},
                headers={"Origin": BASE, "Referer": str(r.url)}))
        print("connect POST:", r.status_code, flush=True)
        if r.status_code == 429:
            m = re.search(r"через (\d+)", r.text)
            wait = int(m.group(1)) + 20 if m else 300
            print(f"429, жду {wait}с", flush=True)
            time.sleep(wait)
            r2 = req(lambda: c.get(BASE + "/authorize?" + urllib.parse.urlencode({
                "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
                "scope": scopes, "state": secrets.token_hex(16),
                "code_challenge": challenge, "code_challenge_method": "S256",
                "resource": BASE + "/mcp"})))
            loc = r2.headers.get("location", loc)
            continue
        break
    # шаги форм до callback
    step = 0
    while r.status_code in (200, 302, 303) and step < 6:
        loc2 = r.headers.get("location") or ""
        if "127.0.0.1" in loc2:
            req(lambda: c.get(loc2)); print("callback доставлен", flush=True); break
        if r.status_code in (302, 303) and loc2:
            r = req(lambda: c.get(BASE + loc2 if loc2.startswith("/") else loc2))
            continue
        if "127.0.0.1" in r.text:
            m = re.search(r'href="(http://127\.0\.0\.1[^"]+)"', r.text)
            if m: req(lambda: c.get(m.group(1).replace("&amp;", "&"))); print("callback по ссылке", flush=True); break
        forms = re.findall(r'<form[^>]*action="([^"]+)"[^>]*>(.*?)</form>', r.text, re.S)
        if not forms:
            print("форм нет. стоп:", r.text[:300], flush=True); break
        action, body = forms[0]
        fields = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', body))
        for nm, val in re.findall(r'<input[^>]*type="checkbox"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', body)[:1]:
            fields[nm] = val
        print("шаг формы:", action, list(fields), flush=True)
        r = req(lambda: c.post(BASE + action, data=fields, headers={"Origin": BASE, "Referer": str(r.url)}))
        print("POST:", r.status_code, "->", (r.headers.get("location") or "")[:100], flush=True)
        step += 1

deadline = time.time() + 120
while time.time() < deadline and "code" not in captured:
    time.sleep(2)
srv.shutdown()
if "code" not in captured:
    print("КОД НЕ ПОЛУЧЕН", flush=True); raise SystemExit(1)
r = req(lambda: httpx.post(BASE + "/token", data={
    "grant_type": "authorization_code", "code": captured["code"][0],
    "redirect_uri": redirect, "client_id": client_id, "code_verifier": verifier}, timeout=30))
tok = r.json()
open("fw_mcp_tokens.json", "w", encoding="utf-8").write(json.dumps(tok, ensure_ascii=False, indent=1))
print("TOKEN OK, expires_in:", tok.get("expires_in"), flush=True)
