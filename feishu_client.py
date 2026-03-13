"""Feishu (Lark) API client for MCP Feedback Assistant.

Supports two token modes:
  1. **tenant_access_token** (default) — obtained from app_id + app_secret,
     no user login required. Supports chat & department search.
  2. **user_access_token** (OAuth) — browser-based login, supports all
     three search types including user search.
"""

import os
import sys
import json
import time
import threading
import webbrowser
import urllib.request
import urllib.parse
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, List, Any

FEISHU_BASE_URL = "https://open.feishu.cn"
FEISHU_AUTH_URL = "https://accounts.feishu.cn"
DEFAULT_OAUTH_PORT = 3000

# QSettings keys (shared with settings_dialog.py)
KEY_FEISHU_APP_ID = "feishu_app_id"
KEY_FEISHU_APP_SECRET = "feishu_app_secret"
KEY_FEISHU_MCP_JSON_PATH = "feishu_mcp_json_path"
KEY_FEISHU_OAUTH_PORT = "feishu_oauth_port"


def _token_dir() -> str:
    """Platform-aware config directory for token storage.

    Uses a fixed path independent of QStandardPaths to avoid
    inconsistencies caused by different applicationName values.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME",
                              os.path.join(os.path.expanduser("~"), ".config"))
    d = os.path.join(base, "mcp-feedback")
    os.makedirs(d, exist_ok=True)
    return d


class FeishuClient:
    """Client for Feishu Open API.

    Works in two modes:
      * **app mode** (default): uses ``tenant_access_token`` for chat and
        department search.  No user login required.
      * **user mode**: after OAuth login, uses ``user_access_token`` for
        user / chat / department search.
    """

    def __init__(self):
        self._app_id: str = ""
        self._app_secret: str = ""
        # tenant (app-level) token
        self._tenant_token: str = ""
        self._tenant_token_expires: float = 0.0
        # user (OAuth) token
        self._user_token: str = ""
        self._user_refresh: str = ""
        self._user_token_expires: float = 0.0
        self._user_name: str = ""
        self._user_open_id: str = ""

        self._token_path: str = os.path.join(_token_dir(), "feishu_token.json")
        self._lock = threading.Lock()
        self._load_credentials()
        self._load_tokens()

    # ── Credential management ────────────────────────────────────────────

    def _load_credentials(self):
        """Load app_id / app_secret.  Priority: mcp.json → QSettings."""
        app_id, app_secret = "", ""

        mcp_path = self._mcp_json_path()
        if mcp_path and os.path.isfile(mcp_path):
            try:
                with open(mcp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for _name, cfg in data.get("mcpServers", {}).items():
                    if "lark" in _name.lower():
                        args = cfg.get("args", [])
                        for i, a in enumerate(args):
                            if a == "-a" and i + 1 < len(args):
                                app_id = args[i + 1]
                            elif a == "-s" and i + 1 < len(args):
                                app_secret = args[i + 1]
                        if app_id:
                            break
            except Exception:
                pass

        if not app_id:
            try:
                from PySide6.QtCore import QSettings
                s = QSettings("InteractiveFeedbackMCP", "Settings")
                app_id = s.value(KEY_FEISHU_APP_ID, "", type=str)
                app_secret = s.value(KEY_FEISHU_APP_SECRET, "", type=str)
            except Exception:
                pass

        self._app_id = app_id
        self._app_secret = app_secret

    @staticmethod
    def _mcp_json_path() -> str:
        try:
            from PySide6.QtCore import QSettings
            s = QSettings("InteractiveFeedbackMCP", "Settings")
            p = s.value(KEY_FEISHU_MCP_JSON_PATH, "", type=str)
            if p:
                return p
        except Exception:
            pass
        return os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json")

    def reload_credentials(self):
        self._load_credentials()

    # ── Token persistence ────────────────────────────────────────────────

    def _load_tokens(self):
        if not os.path.isfile(self._token_path):
            return
        try:
            with open(self._token_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self._user_token = d.get("access_token", "")
            self._user_refresh = d.get("refresh_token", "")
            self._user_token_expires = d.get("expires_at", 0.0)
            self._user_name = d.get("user_name", "")
            self._user_open_id = d.get("user_open_id", "")
        except Exception:
            pass

    def _save_tokens(self):
        try:
            os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
            with open(self._token_path, "w", encoding="utf-8") as f:
                json.dump({
                    "access_token": self._user_token,
                    "refresh_token": self._user_refresh,
                    "expires_at": self._user_token_expires,
                    "user_name": self._user_name,
                    "user_open_id": self._user_open_id,
                }, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    @property
    def has_user_token(self) -> bool:
        return bool(self._user_token and time.time() < self._user_token_expires)

    @property
    def user_name(self) -> str:
        return self._user_name

    @property
    def status_text(self) -> str:
        if not self.is_configured:
            return "未配置飞书应用"
        if self.has_user_token:
            return f"已连接: {self._user_name}" if self._user_name else "已连接（用户模式）"
        if self._user_refresh:
            return "Token 已过期（可刷新）"
        return "应用模式（群/部门搜索可用）"

    # ── Low-level HTTP helper ────────────────────────────────────────────

    @staticmethod
    def _http(method: str, url: str, body: dict = None,
              token: str = None, timeout: int = 10) -> dict:
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json; charset=utf-8")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:
                return {"code": e.code, "msg": str(e)}
        except Exception as e:
            return {"code": -1, "msg": str(e)}

    def _api(self, method: str, path: str, body: dict = None,
             token: str = None) -> dict:
        return self._http(method, f"{FEISHU_BASE_URL}{path}", body, token)

    # ── Tenant access token (app-level, no OAuth) ────────────────────────

    def _ensure_tenant_token(self) -> str:
        """Get a valid tenant_access_token, refreshing if expired."""
        if self._tenant_token and time.time() < self._tenant_token_expires:
            return self._tenant_token
        if not self.is_configured:
            return ""
        r = self._api("POST", "/open-apis/auth/v3/tenant_access_token/internal", {
            "app_id": self._app_id,
            "app_secret": self._app_secret,
        })
        token = r.get("tenant_access_token", "")
        if token:
            self._tenant_token = token
            self._tenant_token_expires = time.time() + r.get("expire", 7200) - 60
        return token

    # ── User access token (OAuth) ────────────────────────────────────────

    def _ensure_user_token(self) -> str:
        """Get a valid user_access_token, refreshing if expired."""
        if self._user_token and time.time() < self._user_token_expires:
            return self._user_token
        if self._user_refresh:
            if self._refresh_user_token():
                return self._user_token
        return ""

    def _refresh_user_token(self) -> bool:
        app_token = self._get_app_access_token()
        if not app_token:
            return False
        r = self._api(
            "POST",
            "/open-apis/authen/v1/oidc/refresh_access_token",
            body={
                "grant_type": "refresh_token",
                "refresh_token": self._user_refresh,
            },
            token=app_token,
        )
        d = r.get("data", r)
        if d.get("access_token"):
            self._user_token = d["access_token"]
            self._user_refresh = d.get("refresh_token", self._user_refresh)
            self._user_token_expires = time.time() + d.get("expires_in", 7200) - 60
            self._save_tokens()
            return True
        return False

    def _best_token(self) -> str:
        """Return user_access_token if available, else tenant_access_token."""
        t = self._ensure_user_token()
        if t:
            return t
        return self._ensure_tenant_token()

    # ── OAuth flow ───────────────────────────────────────────────────────

    def _get_app_access_token(self) -> str:
        """Get app_access_token for OIDC token exchange."""
        r = self._api("POST", "/open-apis/auth/v3/app_access_token/internal", {
            "app_id": self._app_id,
            "app_secret": self._app_secret,
        })
        return r.get("app_access_token", "")

    def start_oauth(self, port: int = DEFAULT_OAUTH_PORT, callback=None):
        """Start browser-based OIDC login (same flow as lark-mcp).

        *callback(success: bool, message: str)* is invoked when done.
        """
        if not self.is_configured:
            if callback:
                callback(False, "飞书应用未配置 (app_id / app_secret)")
            return

        def _worker():
            try:
                code_box: Dict[str, Optional[str]] = {"code": None}

                class _Handler(BaseHTTPRequestHandler):
                    def do_GET(self_h):
                        qs = urllib.parse.parse_qs(
                            urllib.parse.urlparse(self_h.path).query
                        )
                        c = qs.get("code", [None])[0]
                        if c:
                            code_box["code"] = c
                            self_h.send_response(200)
                            self_h.send_header("Content-Type",
                                               "text/html; charset=utf-8")
                            self_h.end_headers()
                            self_h.wfile.write(
                                "<html><body style='font-family:sans-serif;"
                                "text-align:center;padding:60px'>"
                                "<h2>✅ 授权成功！请返回 MCP 反馈助手。</h2>"
                                "<script>setTimeout(()=>window.close(),2000)"
                                "</script></body></html>".encode("utf-8")
                            )
                        else:
                            self_h.send_response(400)
                            self_h.end_headers()

                    def log_message(self_h, fmt, *args):
                        pass

                srv = HTTPServer(("127.0.0.1", port), _Handler)
                srv.timeout = 5

                base_callback = f"http://localhost:{port}/callback"
                # Match lark-mcp's redirect_uri format exactly
                redirect = f"{base_callback}?redirect_uri={base_callback}"
                url = (
                    f"{FEISHU_BASE_URL}/open-apis/authen/v1/index"
                    f"?app_id={self._app_id}"
                    f"&redirect_uri={urllib.parse.quote(redirect)}"
                    f"&state=mcp_feedback"
                )
                webbrowser.open(url)

                deadline = time.time() + 120
                while code_box["code"] is None and time.time() < deadline:
                    srv.handle_request()
                srv.server_close()

                if code_box["code"] is None:
                    if callback:
                        callback(False, "OAuth 超时（2 分钟），请重试")
                    return

                # OIDC token exchange: uses app_access_token in header
                app_token = self._get_app_access_token()
                if not app_token:
                    if callback:
                        callback(False, "获取 app_access_token 失败")
                    return

                r = self._api(
                    "POST",
                    "/open-apis/authen/v1/oidc/access_token",
                    body={
                        "grant_type": "authorization_code",
                        "code": code_box["code"],
                    },
                    token=app_token,
                )
                d = r.get("data", r)
                if not d.get("access_token"):
                    msg = r.get("msg", d.get("msg", "未知错误"))
                    if callback:
                        callback(False, f"获取 Token 失败: {msg}")
                    return

                self._user_token = d["access_token"]
                self._user_refresh = d.get("refresh_token", "")
                self._user_token_expires = (
                    time.time() + d.get("expires_in", 7200) - 60
                )

                ui = self._api("GET", "/open-apis/authen/v1/user_info",
                               token=self._user_token)
                ud = ui.get("data", {})
                self._user_name = ud.get("name", "")
                self._user_open_id = ud.get("open_id", "")
                self._save_tokens()

                if callback:
                    name = self._user_name or "用户"
                    callback(True, f"已连接: {name}")
            except Exception as e:
                if callback:
                    callback(False, f"OAuth 异常: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def disconnect(self):
        """Clear user tokens (tenant token stays)."""
        self._user_token = ""
        self._user_refresh = ""
        self._user_token_expires = 0.0
        self._user_name = ""
        self._user_open_id = ""
        try:
            if os.path.exists(self._token_path):
                os.unlink(self._token_path)
        except Exception:
            pass

    # ── Search APIs ──────────────────────────────────────────────────────

    def search_users(self, query: str, page_size: int = 10,
                     page_token: str = "") -> Dict[str, Any]:
        """Search users (requires user_access_token).

        Returns ``{"items": [...], "page_token": str, "has_more": bool}``.
        """
        token = self._ensure_user_token()
        if not token:
            return {"items": [], "page_token": "", "has_more": False}
        url = (
            f"/open-apis/search/v1/user"
            f"?query={urllib.parse.quote(query)}&page_size={page_size}"
        )
        if page_token:
            url += f"&page_token={urllib.parse.quote(page_token)}"
        r = self._api("GET", url, token=token)
        d = r.get("data", {})
        items = [
            {
                "type": "user",
                "name": u.get("name", ""),
                "id": u.get("open_id", ""),
                "subtitle": "",
                "user_id": u.get("user_id", ""),
                "avatar_url": u.get("avatar", {}).get("avatar_72", ""),
            }
            for u in d.get("users", [])
        ]
        return {
            "items": items,
            "page_token": d.get("page_token", ""),
            "has_more": d.get("has_more", False),
        }

    def search_chats(self, query: str, page_size: int = 10,
                     page_token: str = "") -> Dict[str, Any]:
        """Search chats (works with tenant or user token).

        Returns ``{"items": [...], "page_token": str, "has_more": bool}``.
        """
        token = self._best_token()
        if not token:
            return {"items": [], "page_token": "", "has_more": False}
        url = (
            f"/open-apis/im/v1/chats"
            f"?query={urllib.parse.quote(query)}&page_size={page_size}"
        )
        if page_token:
            url += f"&page_token={urllib.parse.quote(page_token)}"
        r = self._api("GET", url, token=token)
        d = r.get("data", {})
        items = [
            {
                "type": "chat",
                "name": c.get("name", ""),
                "id": c.get("chat_id", ""),
                "subtitle": f"{c.get('user_count', '?')} 人",
            }
            for c in d.get("items", [])
        ]
        return {
            "items": items,
            "page_token": d.get("page_token", ""),
            "has_more": d.get("has_more", False),
        }

    def search_all(self, query: str, page_size: int = 10,
                   page_tokens: Optional[Dict[str, str]] = None,
                   ) -> Dict[str, Any]:
        """Search users + chats concurrently with pagination.

        Returns ``{"items": [...], "page_tokens": {"user": str, "chat": str},
        "has_more": bool}``.
        """
        if not query.strip() or not self.is_configured:
            return {"items": [], "page_tokens": {}, "has_more": False}

        token = self._best_token()
        if not token:
            return {"items": [], "page_tokens": {}, "has_more": False}

        page_tokens = page_tokens or {}
        buckets: Dict[str, Dict[str, Any]] = {}
        lock = threading.Lock()

        def _run(fn, key):
            try:
                result = fn(query, page_size, page_tokens.get(key, ""))
                with lock:
                    buckets[key] = result
            except Exception:
                with lock:
                    buckets[key] = {"items": [], "page_token": "", "has_more": False}

        tasks = [(self.search_chats, "chat")]
        if self._ensure_user_token():
            tasks.insert(0, (self.search_users, "user"))

        threads = [threading.Thread(target=_run, args=(fn, k)) for fn, k in tasks]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=8)

        items = buckets.get("user", {}).get("items", []) + \
                buckets.get("chat", {}).get("items", [])
        new_tokens = {
            k: buckets.get(k, {}).get("page_token", "")
            for k in ("user", "chat")
        }
        any_more = any(buckets.get(k, {}).get("has_more", False) for k in ("user", "chat"))

        return {"items": items, "page_tokens": new_tokens, "has_more": any_more}
