"""Клиент Flowwow Seller MCP (apiseller-mcp.flowwow.info) с авто-обновлением токена.

Токены: fw_mcp_tokens.json (получены OAuth 25.11, access живёт 900 с, есть refresh_token).
DNS на машине нестабилен — хост резолвится в фиксированный IP.
"""

import json
import socket
import time

import httpx

HOST = "apiseller-mcp.flowwow.info"
IP = "158.160.254.234"
BASE = f"https://{HOST}"
URL = BASE + "/mcp"
CRED = r"C:/Users/наташа/.kimi-code/credentials/mcp"
TOKENS_FILE = "fw_mcp_tokens.json"

_orig = socket.getaddrinfo
socket.getaddrinfo = lambda h, *a, **kw: _orig(IP if h == HOST else h, *a, **kw)

_tok = None
_tok_time = 0.0
_session = None


def _load_tokens():
    global _tok, _tok_time
    _tok = json.load(open(TOKENS_FILE, encoding="utf-8"))
    _tok_time = time.time()


def _refresh():
    global _tok, _tok_time
    client = json.load(open(f"{CRED}/flowwow-b9d421363ab06de1ec9f583f-client.json", encoding="utf-8"))
    r = httpx.post(BASE + "/token", data={
        "grant_type": "refresh_token", "refresh_token": _tok["refresh_token"],
        "client_id": client["client_id"], "resource": BASE + "/mcp"}, timeout=30)
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"refresh failed: {d}")
    if "refresh_token" not in d:
        d["refresh_token"] = _tok["refresh_token"]
    _tok = d
    _tok_time = time.time()
    json.dump(d, open(TOKENS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _access():
    if _tok is None:
        _load_tokens()
    if time.time() - _tok_time > 700:  # запас перед истечением 900 с
        _refresh()
    return _tok["access_token"]


def rpc(method, params=None, rid=1, retries=4):
    global _session
    for a in range(retries):
        try:
            h = {"Authorization": f"Bearer {_access()}", "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"}
            if _session:
                h["Mcp-Session-Id"] = _session
            r = httpx.post(URL, headers=h, timeout=90,
                           json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            if r.status_code == 401 and a < retries - 1:
                _refresh()
                continue
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                _session = sid
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            return r.json()
        except Exception:
            if a == retries - 1:
                raise
            time.sleep(4)


def init():
    global _session
    _session = None
    rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "kimi", "version": "1.0"}})


def call_tool(name, arguments, rid=10):
    d = rpc("tools/call", {"name": name, "arguments": arguments}, rid=rid)
    res = d.get("result") or {}
    content = res.get("content") or []
    for item in content:
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except Exception:
                return {"text": item["text"]}
    return res


SHOP_ID = 222976
