"""
Hidden Path — обход блокировок для Windows: zapret (Flowseal) + свой сервер VLESS (Xray).

При запуске:
  1. если релиза ещё нет — скачивает последний с GitHub;
  2. запускает ранее найденную рабочую стратегию (general*.bat) и проверяет её;
  3. если не работает (или первый запуск) — перебирает все стратегии
     и оставляет ту, что реально открывает YouTube/Discord.

Вкладки:
  «Главная»   — «Проверить и запустить», «Обновить», «Остановить», «Выход»,
                галочки «Автозапуск» (Планировщик задач) и «Telegram-прокси»
                (Flowseal/tg-ws-proxy, локальный прокси для Telegram Desktop);
  «Настройки» — то же, что меню service.bat: Game Filter, IPSet Filter,
                Auto-Update Check, Replace active fakes. Хранятся в config.json
                и записываются в файлы релиза перед каждым запуском обхода;
  «Сайты»     — свои домены для обхода (lists/list-general-user.txt);
  «Через VPN» — свой сервер VLESS (ссылка из 3x-ui) только для выбранных
                сайтов: Xray-core + системный прокси Windows, всё остальное
                идёт напрямую.

Интерфейс — ui.html в окне WebView2 (pip install pywebview), логика — только
стандартная библиотека Python. Нужны права администратора.
Запуск с ключом --auto стартует свёрнутым (так работает автозапуск).
"""
import base64
import ctypes
import ipaddress
import json
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "Flowseal/zapret-discord-youtube"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
APP_NAME = "HiddenPath"
APP_TITLE = "Hidden Path"  # заголовок окна; по нему второй запуск находит первый
TASK = "HiddenPathAutostart"

# обновления самой программы: последний релиз на GitHub, версия — в теге vX.Y
APP_VERSION = "1.1.1"  # держать равной AppVersion в installer.iss
APP_REPO = "NapsterFO/HiddenPath"
APP_SETUP = "HiddenPath-Setup.exe"
APP_PRIVATE = False

TG_REPO = "Flowseal/tg-ws-proxy"
TG_API = f"https://api.github.com/repos/{TG_REPO}/releases/latest"

# Что считаем «разблокированным». Проверяем TLS-рукопожатие на 443 порту.
TARGETS = [
    "www.youtube.com",
    "i.ytimg.com",
    "redirector.googlevideo.com",
    "discord.com",
    "gateway.discord.gg",
    "cdn.discordapp.com",
]
WARMUP = 4  # секунд ждать после запуска стратегии перед проверкой

# Настройки — те же файлы и значения, что использует service.bat
IPSET_URL = ("https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/"
             "refs/heads/main/.service/ipset-service.txt")
IPSET_NONE = "203.0.113.113/32"  # «пустышка»: режим none
GAME_MODES = {"disabled": "Отключён", "all": "TCP и UDP", "tcp": "Только TCP", "udp": "Только UDP"}
IPSET_MODES = {
    "none": "none — только домены из списков",
    "any": "any — для всех IP (агрессивно, может ломать сайты/игры)",
    "loaded": "loaded — по списку IP из релиза",
}
DEFAULT_PORTS = "1024-65535"
FAKES = {"fake_discord": "ACTIVE_DISCORD_UDP", "fake_game": "ACTIVE_GAME_UDP"}
NO_FAKE = "(не менять)"

XRAY_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
XRAY_ASSET = "Xray-windows-64.zip"
XRAY_BIN = "hp-xray.exe"  # своё имя, чтобы не убить чужой xray (v2rayN и т.п.)
# не 10808/10809: их по умолчанию занимают v2rayN и другие клиенты Xray
XRAY_SOCKS, XRAY_HTTP = 17818, 17819
XRAY_TEST = 17820  # всё с этого порта — только через сервер (кнопка «Проверить сервер»)
TUN_NAME = "HiddenPath"
# режим «Программы через VPN»: имена процессов без .exe (регистр важен)
DEFAULT_APPS = ["Telegram", "AyuGram", "claude", "Claude", "ChatGPT"]
PROXY_ADDR = f"127.0.0.1:{XRAY_HTTP}"
PROXY_BYPASS = ";".join(["localhost", "127.*", "10.*", "192.168.*"]
                        + [f"172.{i}.*" for i in range(16, 32)] + ["<local>"])
INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

USER_LIST = "list-general-user.txt"
PLACEHOLDER = "domain.example.abc"
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$")

NOWIN = 0x08000000  # CREATE_NO_WINDOW
DEVNULL = subprocess.DEVNULL

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
ZAPRET = APP_DIR / "zapret"
TG_DIR = APP_DIR / "tgws"
XRAY_DIR = APP_DIR / "xray"
CONFIG = APP_DIR / "config.json"


def sh(cmd, **kw):
    """Тихий запуск системной команды."""
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="ignore",
        creationflags=NOWIN, **kw,
    )


def probe(host, timeout=5):
    try:
        with socket.create_connection((host, 443), timeout=timeout) as s:
            with SSL_CTX.wrap_socket(s, server_hostname=host):
                return True
    except Exception:
        return False


def hide_console_windows():
    """Запасной вариант: спрятать окна консоли с заголовком «zapret: …»."""
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(h, _):
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, buf, 256)
        if buf.value.lower().startswith("zapret:") and u.IsWindowVisible(h):
            found.append(h)
        return True

    u.EnumWindows(each, None)
    for h in found:
        u.ShowWindow(h, 0)  # SW_HIDE
    return len(found)


def winws_running():
    out = sh(["tasklist", "/FI", "IMAGENAME eq winws.exe", "/NH"]).stdout
    return "winws.exe" in out.lower()


def valid_ports(text):
    """Порты/диапазоны как в service.bat: 1024-1934,1936-65535. None — если неверно."""
    text = text.replace(" ", "")
    if not text:
        return None
    for item in text.split(","):
        parts = item.split("-")
        if len(parts) > 2 or not all(p.isdigit() and p[0] != "0" for p in parts):
            return None
        a, b = int(parts[0]), int(parts[-1])
        if b > 65535 or a > b:
            return None
    return text


def is_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def parse_domains(text):
    """Домены из текста: через пробел/запятую/перенос, можно целыми ссылками."""
    good, bad = [], []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        t = tok if "://" in tok else "//" + tok
        try:
            host = (urllib.parse.urlsplit(t).hostname or "").strip(".")
        except ValueError:
            host = ""
        if host.startswith("www."):
            host = host[4:]  # zapret и так ловит все поддомены
        try:
            host = host.encode("idna").decode("ascii")  # сайт.рф → xn--...
        except UnicodeError:
            pass
        if DOMAIN_RE.match(host) or is_ip(host):
            if host not in good:
                good.append(host)
        else:
            bad.append(tok)
    return good, bad


# ---------- VLESS ----------
def parse_vless(link):
    """Ссылка vless:// из 3x-ui → (outbound для Xray, имя, адрес сервера)."""
    u = urllib.parse.urlsplit(link.strip())
    if u.scheme.lower() != "vless" or not u.username or not u.hostname:
        raise ValueError("это не ссылка vless://")
    try:
        port = u.port
    except ValueError:
        port = None
    if not port:
        raise ValueError("в ссылке нет порта")
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    net, sec = q.get("type", "tcp").lower(), q.get("security", "none").lower()
    path, host = q.get("path", ""), q.get("host", "")

    user = {"id": urllib.parse.unquote(u.username), "encryption": q.get("encryption", "none")}
    if q.get("flow"):
        user["flow"] = q["flow"]
    stream = {"network": net, "security": sec}

    if sec == "reality":
        if not q.get("pbk"):
            raise ValueError("в ссылке Reality нет ключа pbk")
        r = {"serverName": q.get("sni", ""), "fingerprint": q.get("fp") or "chrome",
             "publicKey": q["pbk"], "shortId": q.get("sid", ""), "spiderX": q.get("spx", "")}
        if q.get("pqv"):
            r["mldsa65Verify"] = q["pqv"]
        stream["realitySettings"] = r
    elif sec == "tls":
        t = {"serverName": q.get("sni") or host or u.hostname,
             "fingerprint": q.get("fp") or "chrome"}
        if q.get("alpn"):
            t["alpn"] = q["alpn"].split(",")
        if q.get("allowInsecure") in ("1", "true"):
            t["allowInsecure"] = True
        stream["tlsSettings"] = t
    elif sec != "none":
        raise ValueError(f"защита {sec} не поддерживается")

    if net == "ws":
        stream["wsSettings"] = {"path": path or "/", "host": host}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": q.get("serviceName", ""),
                                  "multiMode": q.get("mode") == "multi"}
    elif net in ("xhttp", "splithttp"):
        stream["network"] = "xhttp"
        x = {"path": path or "/", "host": host, "mode": q.get("mode") or "auto"}
        if q.get("extra"):
            x["extra"] = json.loads(q["extra"])
        stream["xhttpSettings"] = x
    elif net == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": path or "/", "host": host}
    elif net in ("tcp", "raw"):
        if q.get("headerType") == "http":
            stream["tcpSettings"] = {"header": {"type": "http", "request": {
                "path": [path or "/"], "headers": {"Host": host.split(",") if host else []}}}}
    else:
        raise ValueError(f"транспорт {net} не поддерживается")

    outbound = {"tag": "proxy", "protocol": "vless", "streamSettings": stream,
                "settings": {"vnext": [{"address": u.hostname, "port": port, "users": [user]}]}}
    return outbound, urllib.parse.unquote(u.fragment) or u.hostname, u.hostname


def _pb_fields(buf, pos=0, end=None):
    """Мини-разбор protobuf: (номер поля, значение) — для geosite.dat хватает."""
    end = len(buf) if end is None else end
    while pos < end:
        key = shift = 0
        while True:
            b = buf[pos]; pos += 1
            key |= (b & 0x7F) << shift; shift += 7
            if b < 0x80:
                break
        num, wt = key >> 3, key & 7
        if wt == 0:
            val = shift = 0
            while True:
                b = buf[pos]; pos += 1
                val |= (b & 0x7F) << shift; shift += 7
                if b < 0x80:
                    break
            yield num, val
        elif wt == 2:
            n = shift = 0
            while True:
                b = buf[pos]; pos += 1
                n |= (b & 0x7F) << shift; shift += 7
                if b < 0x80:
                    break
            yield num, (pos, pos + n)
            pos += n
        elif wt == 5:
            pos += 4
        elif wt == 1:
            pos += 8
        else:
            return


_GEOSITE = {}


def geosite_index(path):
    """домен → группы geosite.dat (только «корневые» и точные домены) и размеры групп."""
    key = (str(path), path.stat().st_mtime) if path.exists() else None
    if not key:
        return {}, {}
    if _GEOSITE.get("key") != key:
        buf = path.read_bytes()
        index, sizes = {}, {}
        for num, (a, b) in _pb_fields(buf):
            if num != 1:
                continue
            code, doms = "", []
            for n2, v2 in _pb_fields(buf, a, b):
                if n2 == 1:
                    code = buf[v2[0]:v2[1]].decode(errors="ignore").lower()
                elif n2 == 2:
                    typ, val = 0, ""
                    for n3, v3 in _pb_fields(buf, *v2):
                        if n3 == 1:
                            typ = v3
                        elif n3 == 2:
                            val = buf[v3[0]:v3[1]].decode(errors="ignore").lower()
                    if typ in (2, 3) and val:  # Domain / Full
                        doms.append(val)
            # общие сборники (category-*, geolocation-*, cn…) — не «родня» сайта
            if not code or code.startswith(("category-", "geolocation-", "tld-")) \
                    or code in ("cn", "private") or "!" in code or "@" in code:
                continue
            sizes[code] = len(doms)
            for dm in doms:
                index.setdefault(dm, set()).add(code)
        _GEOSITE.update(key=key, index=index, sizes=sizes)
    return _GEOSITE["index"], _GEOSITE["sizes"]


def site_groups(sites, path):
    """Сайт → самая узкая группа geosite, где он есть: claude.com → anthropic
    (claude.ai, anthropic.com, …), youtube.com → youtube (googlevideo, ytimg, …).
    Без этого сайт из списка открывается лишь наполовину: его API и CDN идут напрямую."""
    try:
        index, sizes = geosite_index(path)
    except Exception:
        return {}
    out = {}
    for s in sites:
        if is_ip(s):
            continue
        parts = s.split(".")
        cands = set()
        for i in range(len(parts) - 1):  # www.claude.com → claude.com (но не com)
            cands |= index.get(".".join(parts[i:]), set())
        cands = [c for c in cands if sizes.get(c, 0) <= 3000]
        if cands:
            out[s] = min(cands, key=lambda c: (sizes[c], c))
    return out


def xray_config(outbound, sites, all_mode=False, groups=(), apps=(), tun=False):
    """Через сервер — только сайты из списка, всё остальное напрямую.
    all_mode: наоборот, через сервер всё, кроме российских сайтов и локальной сети.
    groups: группы geosite с «роднёй» сайтов из списка (см. site_groups).
    tun: виртуальный адаптер — в Xray попадает трафик всех программ, а не только тех,
    что слушают системный прокси. apps: имена программ (Telegram, claude), которые
    тогда идут через сервер целиком."""
    sniff = {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": True}
    rules = [{"type": "field", "inboundTag": ["test"], "outboundTag": "proxy"}]
    if tun and apps:
        rules.append({"type": "field", "outboundTag": "proxy", "process": list(apps)})
    domains = [s for s in sites if not is_ip(s)]
    ips = [s for s in sites if is_ip(s)]  # ссылки вида https://1.2.3.4:port/… — правило по IP
    if domains:
        rules.append({"type": "field", "outboundTag": "proxy",
                      "domain": [f"domain:{s}" for s in domains]  # с поддоменами
                      + [f"geosite:{g}" for g in sorted(set(groups))]})
    if ips:
        rules.append({"type": "field", "outboundTag": "proxy", "ip": ips})
    if all_mode:  # всё через сервер, кроме российского и локальной сети
        rules += [
            {"type": "field", "outboundTag": "direct", "ip": ["geoip:private"]},
            {"type": "field", "outboundTag": "direct",
             "domain": ["geosite:category-ru", "domain:ru", "domain:su", "domain:xn--p1ai"]},
            {"type": "field", "outboundTag": "direct", "ip": ["geoip:ru"]},
        ]
    # DPI провайдера обрывает TLS с SNI до зарубежного IP. Если резать ClientHello
    # на кусочки по 1–5 байт, фильтр не узнаёт SNI и пропускает соединение.
    outbound = json.loads(json.dumps(outbound))
    outbound.setdefault("streamSettings", {}).setdefault("sockopt", {})["dialerProxy"] = "frag"
    frag = {"tag": "frag", "protocol": "freedom",
            "settings": {"fragment": {"packets": "tlshello", "length": "1-5", "interval": "1"}}}
    direct = {"tag": "direct", "protocol": "freedom"}
    inbounds = [
        {"tag": "http", "listen": "127.0.0.1", "port": XRAY_HTTP,
         "protocol": "http", "sniffing": sniff},
        {"tag": "socks", "listen": "127.0.0.1", "port": XRAY_SOCKS,
         "protocol": "socks", "settings": {"udp": True}, "sniffing": sniff},
        {"tag": "test", "listen": "127.0.0.1", "port": XRAY_TEST, "protocol": "http"},
    ]
    if tun:  # только IPv4: с IPv6 на TUN программы пытались бы ходить по v6, которого у провайдера часто нет
        inbounds.append({"tag": "tun", "protocol": "tun", "sniffing": sniff, "settings": {
            "name": TUN_NAME, "desc": TUN_NAME, "mtu": 1500, "gateway": ["10.66.66.1/30"],
            "autoSystemRoutingTable": ["0.0.0.0/0"],
            "autoOutboundsInterface": "auto"}})  # сам Xray — мимо TUN, иначе петля
    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        # первый outbound — по умолчанию: напрямую, а в режиме «всё через VLESS» — сервер
        "outbounds": ([outbound, direct] if all_mode else [direct, outbound]) + [frag],
        # IPIfNonMatch: домен без правила резолвится и сверяется с geoip:ru
        "routing": {"domainStrategy": "IPIfNonMatch" if all_mode else "AsIs", "rules": rules},
    }


def default_links():
    """keys.txt, вшитый в exe при сборке (build_installer.bat), — по ключу на строку."""
    f = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "keys.txt"
    if not f.exists():
        return []
    lines = (l.strip() for l in f.read_text(encoding="utf-8", errors="ignore").splitlines())
    return [l for l in lines if l.lower().startswith(("vless://", "https://", "http://", "panel:"))]


def update_token():
    """Токен GitHub только на чтение (строка token:… в keys.txt) — для релизов приватного репозитория."""
    f = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "keys.txt"
    if not f.exists():
        return ""
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().lower().startswith("token:"):
            return line.strip()[6:].strip()
    return ""


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def github_get(url, accept="application/vnd.github+json"):
    """Запрос к API GitHub; токен — только самому GitHub, на редиректах (файлы релиза) он отбрасывается."""
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME, "Accept": accept})
    tok = update_token()
    if tok:
        req.add_unredirected_header("Authorization", f"Bearer {tok}")
    return urlopen(req, timeout=30)


def link_id(link):
    """Ключ без sid/spx: 3x-ui на каждый запрос подписки подставляет случайный
    shortId из разрешённых, и это всё тот же ключ."""
    u = urllib.parse.urlsplit(link.strip())
    q = sorted((k, v) for k, v in urllib.parse.parse_qsl(u.query) if k not in ("sid", "spx"))
    return (u.netloc, tuple(q))


def link_title(link):
    try:
        _, name, host = parse_vless(link)
        return f"{name} ({host})"
    except (ValueError, json.JSONDecodeError):
        return "ключ с ошибкой"


def proxy_get():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY) as k:
        def val(name, default):
            try:
                return winreg.QueryValueEx(k, name)[0]
            except OSError:
                return default
        return {"enable": int(val("ProxyEnable", 0)), "server": val("ProxyServer", ""),
                "override": val("ProxyOverride", "")}


def proxy_set(p):
    """Системный прокси Windows (его берут браузеры и большинство программ)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, int(p["enable"]))
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, p["server"])
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, p["override"])
    inet = ctypes.windll.wininet
    inet.InternetSetOptionW(None, 39, None, 0)  # SETTINGS_CHANGED
    inet.InternetSetOptionW(None, 37, None, 0)  # REFRESH


def _ssl_context():
    """Хранилище Windows + набор certifi. Windows докачивает корневые
    сертификаты только для браузера, и нужного может не оказаться."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


SSL_CTX = _ssl_context()


def _reraise(e):
    """Понятная подсказка вместо CERTIFICATE_VERIFY_FAILED, остальное — как есть."""
    if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
        raise urllib.error.URLError(
            "сертификат сайта не прошёл проверку. Скорее всего, HTTPS перехватывает "
            "антивирус (проверка защищённых соединений в Kaspersky, ESET и т. п.) "
            "или другой VPN — добавьте Hidden Path в исключения или выключите его") from e
    raise e


def urlopen(req, timeout=20):
    """Свои запросы — напрямую: системный прокси может указывать на выключенный
    клиент (порт закрыт — WinError 10061). Через него — только запасной путь."""
    try:
        return _DIRECT.open(req, timeout=timeout)
    except urllib.error.URLError as e:
        p = proxy_get()
        if not p["enable"] or not p["server"] or p["server"] == PROXY_ADDR:
            _reraise(e)
        srv = p["server"].split(";")[0].split("=")[-1]
        px = srv if "://" in srv else "http://" + srv
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": px, "https": px}),
            urllib.request.HTTPSHandler(context=SSL_CTX))
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.URLError as e2:
            _reraise(e2)


_DIRECT = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=SSL_CTX))


def proxy_ours():
    p = proxy_get()
    return bool(p["enable"]) and p["server"] == PROXY_ADDR


class Engine:
    def __init__(self, log, status):
        self.log = log
        self.set_status = status
        self.cfg = {}
        if CONFIG.exists():
            try:
                self.cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            except Exception:
                self.cfg = {}
        defaults = default_links()
        if "sub_url" not in self.cfg:  # строка https://… в keys.txt — подписка
            subs = [l for l in defaults if l.lower().startswith("http")]
            self.cfg["sub_url"] = subs[0] if subs else ""
        if "vless_links" not in self.cfg:  # первый запуск: ключи, вшитые при сборке
            links = [l for l in defaults if l.lower().startswith("vless://")]
            cur = self.cfg.get("vless_link")
            if cur and cur not in links:
                links.insert(0, cur)
            self.cfg["vless_links"] = links
            if links and not cur:
                self.cfg["vless_link"] = links[0]
        if "panel_url" not in self.cfg:  # строка panel:https://… в keys.txt — панель 3x-ui
            panels = [l[6:].strip() for l in defaults if l.lower().startswith("panel:")]
            self.cfg["panel_url"] = panels[0] if panels else ""
            if panels:  # панель открывается через свой сервер
                host = parse_domains(panels[0])[0]
                sites = self.cfg.setdefault("vless_sites", [])
                sites += [h for h in host if h not in sites]

    # ---------- служебное ----------
    def save(self):
        CONFIG.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def installed(self):
        return (ZAPRET / "general.bat").exists()

    def strategies(self):
        bats = sorted(ZAPRET.glob("general*.bat"), key=lambda p: p.name.lower())
        first = []
        best = self.cfg.get("best")
        for name in (best, "general.bat"):
            for b in bats:
                if b.name == name and b not in first:
                    first.append(b)
        return first + [b for b in bats if b not in first]

    def stop(self):
        sh(["taskkill", "/F", "/IM", "winws.exe"])
        for svc in ("WinDivert", "WinDivert14"):
            sh(["net", "stop", svc])

    def start(self, bat):
        self.stop()
        self.apply_settings()
        run = self.hidden_copy(bat)
        # двойные кавычки нужны, иначе cmd ломается на именах вида "general (ALT).bat"
        subprocess.Popen(
            f'cmd /c ""{run}""', cwd=str(ZAPRET),
            stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, creationflags=NOWIN,
        )
        for _ in range(20):
            time.sleep(0.5)
            hide_console_windows()
            if winws_running():
                time.sleep(0.3)
                hide_console_windows()
                return True
        return False

    def hidden_copy(self, bat):
        """Копия стратегии рядом с оригиналом, где winws стартует без своего окна:
        `start "zapret: …" /min` → `start "zapret: …" /B` (внутри нашей скрытой консоли).
        Окно winws легко закрыть случайно — и обход выключится."""
        data = bat.read_bytes()
        new, n = re.subn(rb'(start\s+"[^"]*"\s+)/min\b', rb"\1/B", data, flags=re.I)
        if not n:
            return bat  # другой формат — запустим как есть, окно спрячет hide_console_windows
        run = ZAPRET / "_hidden_run.bat"  # не general*.bat — в список стратегий не попадёт
        run.write_bytes(new)
        return run

    def score(self):
        with ThreadPoolExecutor(len(TARGETS)) as ex:
            return sum(ex.map(probe, TARGETS))

    # ---------- проверка стратегий ----------
    def check_all(self):
        if not self.installed():
            self.log("Релиз не установлен — нажмите «Обновить».")
            return False
        self.set_status("Подбираю стратегию…")
        self.stop()
        time.sleep(1)
        base = self.score()
        self.log(f"Без обхода открывается: {base} из {len(TARGETS)}")
        if base == len(TARGETS):
            # всё равно включаем: блокировки бывают не постоянными, а включён
            # ли сейчас другой VPN, по проверке не понять
            self.log("YouTube/Discord открываются и без обхода — всё равно включаю обход.")

        results = []
        for bat in self.strategies():
            self.set_status(f"Проверяю: {bat.name}")
            if not self.start(bat):
                self.log(f"{bat.name}: winws не запустился")
                results.append((0, bat))
                continue
            time.sleep(WARMUP)
            s = self.score()
            self.log(f"{bat.name}: {s} из {len(TARGETS)}")
            results.append((s, bat))
            if s == len(TARGETS):
                break  # идеальный вариант — дальше не ищем

        s, bat = max(results, key=lambda r: r[0], default=(0, None))
        if bat is None or (s <= base and s < len(TARGETS)):
            self.stop()
            self.log("Ни одна стратегия не помогла. Возможно, блокировка по IP/DNS, "
                     "или нужно обновить релиз.")
            self.set_status("Не удалось разблокировать")
            return False

        self.start(bat)
        self.cfg["best"] = bat.name
        self.cfg["score"] = s
        self.save()
        self.log(f"Выбрана и запущена: {bat.name} ({s} из {len(TARGETS)})")
        self.set_status(f"Работает: {bat.name}")
        return True

    def restart(self):
        """Перезапуск сохранённой стратегии (после смены настроек или списка сайтов)."""
        best = self.cfg.get("best")
        if best and (ZAPRET / best).exists():
            self.set_status(f"Перезапускаю: {best}")
            if self.start(ZAPRET / best):
                self.log("Обход перезапущен с новыми настройками.")
                self.set_status(f"Работает: {best}")
                return
        self.check_all()

    # ---------- настройки (как в service.bat) ----------
    def settings(self):
        """Текущие значения: из config.json, иначе — из файлов релиза."""
        mode, tcp, udp = "disabled", DEFAULT_PORTS, DEFAULT_PORTS
        gf = ZAPRET / "utils" / "game_filter.enabled"
        if gf.exists():
            for line in gf.read_text(encoding="utf-8", errors="ignore").splitlines():
                k, _, v = line.strip().partition("=")
                k = k.lower()
                if k == "mode" and v.lower() in GAME_MODES:
                    mode = v.lower()
                elif k in ("tcp", "udp") and valid_ports(v):
                    tcp, udp = (v, udp) if k == "tcp" else (tcp, v)
        c = self.cfg
        return {
            "game_mode": c.get("game_mode", mode),
            "game_tcp": c.get("game_tcp", tcp),
            "game_udp": c.get("game_udp", udp),
            "ipset": c.get("ipset", self.ipset_status()),
            "check_updates": c.get("check_updates", False),
            "fake_discord": c.get("fake_discord") or self.active_fake("ACTIVE_DISCORD_UDP"),
            "fake_game": c.get("fake_game") or self.active_fake("ACTIVE_GAME_UDP"),
        }

    def apply_settings(self):
        """Записать настройки из config.json в файлы релиза (переживает обновления)."""
        if not self.installed():
            return
        c = self.cfg
        utils = ZAPRET / "utils"
        utils.mkdir(exist_ok=True)
        if "game_mode" in c:
            (utils / "game_filter.enabled").write_text(
                f"mode={c['game_mode']}\ntcp={c.get('game_tcp', DEFAULT_PORTS)}\n"
                f"udp={c.get('game_udp', DEFAULT_PORTS)}\n", encoding="ascii")
        # bat запускается скрыто, и его собственная проверка обновлений
        # может повиснуть с невидимым вопросом — по умолчанию выключаем
        flag = utils / "check_updates.enabled"
        if c.get("check_updates", False):
            flag.write_text("ENABLED\n", encoding="ascii")
        elif flag.exists():
            flag.unlink()
        if "ipset" in c:
            try:
                self.set_ipset(c["ipset"])
            except Exception as e:
                self.log(f"IPSet: не удалось применить режим {c['ipset']}: {e}")
        for key, active in FAKES.items():
            src = ZAPRET / "bin" / f"{c.get(key)}.bin"
            if c.get(key) and src.exists():
                shutil.copyfile(src, ZAPRET / "bin" / f"{active}.bin")

    def ipset_status(self):
        f = ZAPRET / "lists" / "ipset-all.txt"
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return "any"  # как в service.bat: нет файла = пустой = any
        if not text.strip():
            return "any"
        return "none" if IPSET_NONE in text else "loaded"

    def set_ipset(self, mode):
        lists = ZAPRET / "lists"
        f, backup = lists / "ipset-all.txt", lists / "ipset-all.txt.backup"
        cur = self.ipset_status()
        if cur == mode:
            return
        if cur == "loaded":
            shutil.copyfile(f, backup)  # настоящий список не теряем
        if mode == "none":
            f.write_bytes(IPSET_NONE.encode() + b"\r\n")
        elif mode == "any":
            f.write_bytes(b"")
        else:
            if not backup.exists():
                self.ipset_download()
            shutil.copyfile(backup, f)
        self.log(f"IPSet Filter: {mode}")

    def ipset_download(self):
        """Свежий список IP (как пункт Update IPSet List). Кладём в .backup,
        а в рабочий файл — только если сейчас режим loaded."""
        self.log("Скачиваю список IPSet…")
        req = urllib.request.Request(IPSET_URL, headers={"User-Agent": APP_NAME})
        with urlopen(req, timeout=20) as r:
            data = r.read()
        if not data.strip():
            raise RuntimeError("пришёл пустой список IPSet")
        lists = ZAPRET / "lists"
        lists.mkdir(exist_ok=True)
        (lists / "ipset-all.txt.backup").write_bytes(data)
        if self.ipset_status() == "loaded":
            (lists / "ipset-all.txt").write_bytes(data)
        self.log(f"Список IPSet обновлён: {len(data.splitlines())} записей.")

    def ipset_update_and_restart(self):
        self.ipset_download()
        if winws_running():
            self.restart()

    def fakes(self):
        return sorted(p.stem for p in (ZAPRET / "bin").glob("*.bin")
                      if not p.stem.upper().startswith("ACTIVE_"))

    def active_fake(self, active):
        """Какой из fake-файлов сейчас скопирован в ACTIVE_*.bin."""
        p = ZAPRET / "bin" / f"{active}.bin"
        if not p.exists():
            return None
        data = p.read_bytes()
        return next((n for n in self.fakes()
                     if (ZAPRET / "bin" / f"{n}.bin").read_bytes() == data), None)

    # ---------- свои сайты ----------
    def user_list(self):
        return ZAPRET / "lists" / USER_LIST

    def user_sites(self):
        p = self.user_list()
        if not p.exists():
            return []
        lines = (l.strip() for l in p.read_text(encoding="utf-8", errors="ignore").splitlines())
        return [l for l in lines if l and not l.startswith("#") and l != PLACEHOLDER]

    def save_user_sites(self, sites):
        p = self.user_list()
        p.parent.mkdir(parents=True, exist_ok=True)
        # пустым файл оставлять нельзя (так требует zapret) — тогда пишем заглушку
        lines = ["# Never leave this file empty"] + (sites or [PLACEHOLDER])
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- обновление ----------
    def latest_release(self, api=API):
        req = urllib.request.Request(api, headers={"User-Agent": APP_NAME})
        with urlopen(req, timeout=20) as r:
            return json.load(r)

    def update(self, force=False):
        self.set_status("Проверяю обновления…")
        rel = self.latest_release()
        tag = rel["tag_name"]
        if not force and self.installed() and self.cfg.get("version") == tag:
            self.log(f"Установлена последняя версия ({tag}).")
            return False
        asset = next((a for a in rel.get("assets", [])
                      if a["name"].lower().endswith(".zip")), None)
        url = asset["browser_download_url"] if asset else rel["zipball_url"]
        self.log(f"Скачиваю {tag}…")
        self.set_status(f"Скачиваю {tag}…")
        self.stop()

        tmp = APP_DIR / "_tmp_update"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        zpath = tmp / "release.zip"
        req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
        with urlopen(req, timeout=30) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, shown = 0, 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and done * 100 // total >= shown + 25:
                    shown = done * 100 // total
                    self.log(f"  {shown}%")
        self.log("Распаковываю…")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp / "x")
        found = next((p.parent for p in (tmp / "x").rglob("general.bat")), None)
        if not found:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("В архиве не нашёлся general.bat")

        # сохраняем пользовательские списки (lists/*user*.txt)
        keep = {}
        old_lists = ZAPRET / "lists"
        if old_lists.exists():
            for p in old_lists.glob("*user*.txt"):
                keep[p.name] = p.read_bytes()
        shutil.rmtree(ZAPRET, ignore_errors=True)
        shutil.move(str(found), str(ZAPRET))
        if keep:
            (ZAPRET / "lists").mkdir(exist_ok=True)
            for name, data in keep.items():
                (ZAPRET / "lists" / name).write_bytes(data)
        shutil.rmtree(tmp, ignore_errors=True)

        self.cfg["version"] = tag
        self.save()
        self.log(f"Установлена версия {tag}.")
        return True

    def update_and_check(self):
        try:
            self.update(force=False)
        except Exception as e:
            self.log(f"Не удалось обновить: {e}")
            if not self.installed():
                return
        if self.cfg.get("tg_enabled"):
            try:
                if self.tg_update():
                    self.tg_start()
            except Exception as e:
                self.log(f"tg-ws-proxy: не удалось обновить: {e}")
        if self.cfg.get("vless_enabled"):
            try:
                self.xray_update()
            except Exception as e:
                self.log(f"Xray: не удалось обновить: {e}")
        self.check_all()

    # ---------- Telegram (tg-ws-proxy) ----------
    def tg_exe(self):
        rel = self.cfg.get("tg_exe")
        p = TG_DIR / rel if rel else None
        return p if p and p.exists() else None

    def tg_running(self):
        p = getattr(self, "_tg_proc", None)
        return p is not None and p.poll() is None

    def tg_update(self, force=False):
        rel = self.latest_release(TG_API)
        tag = rel["tag_name"]
        if not force and self.tg_exe() and self.cfg.get("tg_version") == tag:
            self.log(f"tg-ws-proxy: установлена последняя версия ({tag}).")
            return False

        def pick(ext):
            bad = ("linux", "macos", "darwin", "android", "apple")
            c = [a for a in rel.get("assets", [])
                 if a["name"].lower().endswith(ext)
                 and not any(b in a["name"].lower() for b in bad)]
            c.sort(key=lambda a: ("win" not in a["name"].lower(), a["name"]))
            return c[0] if c else None

        asset = pick(".exe") or pick(".zip")
        if not asset:
            raise RuntimeError("в релизе tg-ws-proxy нет файла для Windows (.exe/.zip)")
        self.log(f"Скачиваю tg-ws-proxy {tag} ({asset['name']})…")
        self.tg_stop()
        TG_DIR.mkdir(exist_ok=True)
        dest = TG_DIR / asset["name"]
        req = urllib.request.Request(asset["browser_download_url"],
                                     headers={"User-Agent": APP_NAME})
        with urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        exe = dest
        if dest.suffix.lower() == ".zip":
            with zipfile.ZipFile(dest) as z:
                z.extractall(TG_DIR)
            dest.unlink()
            exes = sorted(TG_DIR.rglob("*.exe"), key=lambda q: ("tg" not in q.name.lower(), len(q.name)))
            if not exes:
                raise RuntimeError("в архиве tg-ws-proxy нет .exe")
            exe = exes[0]
        self.cfg["tg_exe"] = str(exe.relative_to(TG_DIR))
        self.cfg["tg_version"] = tag
        self.save()
        self.log(f"tg-ws-proxy {tag} установлен.")
        return True

    def tg_start(self):
        exe = self.tg_exe()
        if not exe:
            raise RuntimeError("tg-ws-proxy не установлен")
        if self.tg_running():
            return
        sh(["taskkill", "/F", "/IM", exe.name])  # убрать старый экземпляр, если был
        self._tg_proc = subprocess.Popen(
            [str(exe)], cwd=str(exe.parent),
            stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, creationflags=NOWIN,
        )
        self.log("tg-ws-proxy запущен (значок в трее).")

    def tg_stop(self):
        exe = self.tg_exe()
        if exe:
            sh(["taskkill", "/F", "/IM", exe.name, "/T"])
        self._tg_proc = None

    def tg_enable(self):
        self.set_status("Telegram-прокси…")
        if not self.tg_exe():
            self.tg_update(force=True)
        self.tg_start()
        self.cfg["tg_enabled"] = True
        self.save()
        self.set_status("Telegram-прокси запущен")
        self.log("Теперь подключите Telegram Desktop к прокси: в меню значка "
                 "tg-ws-proxy в трее есть пункт для подключения (ссылка tg://proxy). "
                 "Вручную: Настройки → Продвинутые → Тип соединения → свой прокси; "
                 "сервер 127.0.0.1, порт и secret — из настроек tg-ws-proxy "
                 "(по умолчанию порт 1443).")

    def tg_disable(self):
        self.tg_stop()
        self.cfg["tg_enabled"] = False
        self.save()
        self.set_status("Telegram-прокси остановлен")
        self.log("tg-ws-proxy остановлен. Верните в Telegram обычное подключение.")

    # ---------- VLESS (свой сервер, только для сайтов из списка) ----------
    def xray_exe(self):
        p = XRAY_DIR / XRAY_BIN
        return p if p.exists() else None

    def xray_running(self):
        p = getattr(self, "_xray_proc", None)
        return p is not None and p.poll() is None

    def xray_update(self, force=False):
        rel = self.latest_release(XRAY_API)
        tag = rel["tag_name"]
        if not force and self.xray_exe() and self.cfg.get("xray_version") == tag:
            self.log(f"Xray: установлена последняя версия ({tag}).")
            return False
        asset = next((a for a in rel.get("assets", []) if a["name"] == XRAY_ASSET), None)
        if not asset:
            raise RuntimeError(f"в релизе Xray нет {XRAY_ASSET}")
        self.log(f"Скачиваю Xray {tag}…")
        XRAY_DIR.mkdir(exist_ok=True)
        zpath = XRAY_DIR / "xray.zip"
        req = urllib.request.Request(asset["browser_download_url"],
                                     headers={"User-Agent": APP_NAME})
        with urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            shutil.copyfileobj(r, f)
        was = self.xray_running()
        self.xray_stop()  # только теперь: exe будет перезаписан
        with zipfile.ZipFile(zpath) as z:
            z.extractall(XRAY_DIR)
        zpath.unlink()
        os.replace(XRAY_DIR / "xray.exe", XRAY_DIR / XRAY_BIN)
        self.cfg["xray_version"] = tag
        self.save()
        self.log(f"Xray {tag} установлен.")
        if was:
            self.xray_start()
        return True

    def xray_start(self):
        """(Пере)запуск Xray с текущей ссылкой и списком сайтов."""
        link = self.cfg.get("vless_link")
        if not link:
            raise RuntimeError("сначала вставьте ссылку vless:// во вкладке «Через VPN»")
        if not self.xray_exe():
            self.xray_update(force=True)
        outbound, name, _ = parse_vless(link)
        conf = XRAY_DIR / "config.json"
        sites = self.cfg.get("vless_sites", [])
        groups = site_groups(sites, XRAY_DIR / "geosite.dat")
        conf.write_text(json.dumps(xray_config(outbound, sites, bool(self.cfg.get("vless_all")),
                                               groups.values(), self.vless_apps(),
                                               bool(self.cfg.get("vless_tun"))),
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        if groups and groups != getattr(self, "_groups_logged", None):
            self._groups_logged = groups
            self.log("Вместе с сайтами через сервер пойдут их служебные домены: "
                     + ", ".join(f"{s} → {g}" for s, g in groups.items()))
        self.xray_stop()
        time.sleep(0.3)
        for port in (XRAY_SOCKS, XRAY_HTTP, XRAY_TEST):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError(f"порт {port} занят другой программой — закройте её")
        if self.cfg.get("vless_tun") and not (XRAY_DIR / "wintun.dll").exists():
            self.xray_update(force=True)  # wintun.dll идёт в архиве Xray
        logf = XRAY_DIR / "xray.log"
        with open(logf, "w", encoding="utf-8") as f:
            self._xray_proc = subprocess.Popen(
                [str(XRAY_DIR / XRAY_BIN), "run", "-c", str(conf)], cwd=str(XRAY_DIR),
                stdin=DEVNULL, stdout=f, stderr=subprocess.STDOUT, creationflags=NOWIN,
            )
        time.sleep(3 if self.cfg.get("vless_tun") else 1.5)  # адаптер TUN поднимается дольше
        if not self.xray_running():
            tail = logf.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-5:]
            tip = (" Если включён другой VPN в режиме TUN (Happ, v2RayTun, Amnezia) — выключите его."
                   if self.cfg.get("vless_tun") else "")
            raise RuntimeError("Xray не запустился: " + " | ".join(tail) + tip)
        return name

    def vless_apps(self):
        return self.cfg.get("vless_apps", DEFAULT_APPS)

    def xray_stop(self):
        sh(["taskkill", "/F", "/IM", XRAY_BIN])  # и «сироту» после сбоя
        self._xray_proc = None

    def proxy_on(self):
        if not proxy_ours():
            self.cfg["proxy_prev"] = proxy_get()  # вернём как было
            self.save()
        proxy_set({"enable": 1, "server": PROXY_ADDR, "override": PROXY_BYPASS})

    def proxy_off(self):
        if proxy_ours():
            prev = self.cfg.pop("proxy_prev", None)
            if not prev or prev.get("server") == PROXY_ADDR:
                prev = {"enable": 0, "server": "", "override": ""}
            proxy_set(prev)
            self.save()

    def vless_exclude_server(self):
        """IP своего сервера — в исключения zapret, чтобы обход не портил VLESS."""
        if not self.installed() or not self.cfg.get("vless_link"):
            return
        host = parse_vless(self.cfg["vless_link"])[2]
        try:
            ips = sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})
        except OSError:
            return
        p = ZAPRET / "lists" / "ipset-exclude-user.txt"
        cur = [l.strip() for l in p.read_text(encoding="utf-8", errors="ignore").splitlines()
               if l.strip()] if p.exists() else [IPSET_NONE]
        old = set(self.cfg.get("vless_excluded", []))
        new = [l for l in cur if l not in old]
        new += [ip for ip in ips if ip not in new]
        self.cfg["vless_excluded"] = ips
        self.save()
        if new != cur:
            p.write_text("\n".join(new) + "\n", encoding="utf-8")
            if winws_running() and self.cfg.get("best"):
                self.restart()

    def vless_enable(self):
        self.set_status("Подключаю VLESS…")
        name = self.xray_start()
        self.proxy_on()
        self.cfg["vless_enabled"] = True
        self.save()
        if self.server_probe() is None:  # ключ не работает — ищем живой
            if not self.vless_heal():  # не оставлять браузеры на мёртвом сервере
                raise RuntimeError("VLESS не включён: сервер не отвечает ни по одному ключу.")
            name = link_title(self.cfg.get("vless_link", ""))
        self.vless_exclude_server()
        sites = self.cfg.get("vless_sites", [])
        if self.cfg.get("vless_tun"):
            self.log("Программы через VPN: " + ", ".join(self.vless_apps()))
        if self.cfg.get("vless_all"):
            self.log(f"VLESS включён ({name}). Через сервер — весь интернет, кроме российских сайтов.")
        else:
            self.log(f"VLESS включён ({name}). Через сервер: "
                     + (", ".join(sites) if sites else "пока ничего — добавьте сайты"))
        self.set_status("VLESS включён")

    # ---------- подписка 3x-ui и автовыбор ключа ----------
    def all_links(self):
        """Ключи из подписки + добавленные вручную, без повторов (с точностью до sid)."""
        out, seen = [], set()
        for l in self.cfg.get("sub_links", []) + self.cfg.get("vless_links", []):
            if link_id(l) not in seen:
                seen.add(link_id(l))
                out.append(l)
        return out

    def sub_due(self):
        if not self.cfg.get("sub_url"):
            return False
        hours = self.cfg.get("sub_interval", 12)
        return time.time() - self.cfg.get("sub_time", 0) > hours * 3600

    def sub_refresh(self):
        """Скачать ключи из подписки. Логин/пароль панели не нужны и не хранятся."""
        url = self.cfg.get("sub_url")
        if not url:
            return False
        req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
        with urlopen(req, timeout=20) as r:  # с обычной проверкой TLS
            body = r.read().strip()
            hdr = r.headers
        try:
            text = base64.b64decode(body + b"=" * (-len(body) % 4), validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            text = body.decode(errors="ignore")
        links, skipped = [], 0
        for l in (x.strip() for x in text.splitlines()):
            if not l:
                continue
            try:
                parse_vless(l)
                links.append(l)
            except (ValueError, json.JSONDecodeError):
                skipped += 1  # vmess/trojan и т.п. — не поддерживаем
        if not links:
            raise RuntimeError("в подписке нет ключей vless://")
        try:
            self.cfg["sub_interval"] = max(1, int(hdr.get("Profile-Update-Interval", 12)))
        except ValueError:
            pass
        info = dict(p.strip().split("=", 1) for p in
                    (hdr.get("Subscription-Userinfo") or "").split(";") if "=" in p)
        self.cfg["sub_info"] = {k: int(v) for k, v in info.items() if v.strip().isdigit()}
        cur = self.cfg.get("vless_link")
        old = self.cfg.get("sub_links", [])
        # тот же ключ с другим sid — оставляем прежнюю строку, Xray не перезапускаем
        old_by_id = {link_id(l): l for l in old}
        links = [old_by_id.get(link_id(l), l) for l in links]
        changed = links != old
        self.cfg["sub_links"] = links
        self.cfg["sub_time"] = time.time()
        if cur not in self.all_links():
            same = [l for l in self.all_links() if cur and link_id(l) == link_id(cur)]
            self.cfg["vless_link"] = same[0] if same else links[0]
        self.save()
        self.log(f"Подписка обновлена: ключей {len(links)}"
                 + (f" (пропущено не-vless: {skipped})" if skipped else "")
                 + (", есть изменения." if changed else ", без изменений."))
        if self.cfg.get("vless_link") != cur and self.xray_running():
            self.vless_apply()
        return changed

    def server_probe(self, timeout=12):
        """Запрос через сервер (порт XRAY_TEST). Возвращает {ip, loc} или None."""
        if not self.xray_running():
            return None
        px = f"http://127.0.0.1:{XRAY_TEST}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px}))
        try:
            # http, а не https: внутри VLESS всё и так шифруется,
            # а Python на некоторых ПК не видит системные сертификаты
            with opener.open("http://cloudflare.com/cdn-cgi/trace", timeout=timeout) as r:
                text = r.read().decode(errors="ignore")
        except Exception:
            return None
        d = dict(l.split("=", 1) for l in text.splitlines() if "=" in l)
        if d.get("ip"):
            self.cfg["vless_loc"] = d.get("loc", "")
        return d if d.get("ip") else None

    def vless_heal(self):
        """Текущий ключ не работает: обновить подписку и перебрать все ключи."""
        self.set_status("Ищу рабочий ключ…")
        self.log("Сервер не отвечает — ищу рабочий ключ…")
        if self.cfg.get("sub_url"):
            try:
                self.sub_refresh()
            except Exception as e:
                self.log(f"Подписка не обновилась: {e}")
        start = self.cfg.get("vless_link")
        links = self.all_links()
        order = ([start] if start in links else []) + [l for l in links if l != start]
        for link in order:
            self.cfg["vless_link"] = link
            try:
                self.xray_start()
            except Exception as e:
                self.log(f"{link_title(link)}: {e}")
                continue
            d = self.server_probe()
            if d:
                self.save()
                self.log(f"Работает ключ {link_title(link)}: IP {d.get('ip')}, страна {d.get('loc')}.")
                self.set_status("VLESS включён")
                if link != start:
                    self.vless_exclude_server()
                return True
            self.log(f"{link_title(link)}: не отвечает.")
        self.cfg["vless_link"] = start
        self.save()
        if start:
            try:
                self.xray_start()
            except Exception:
                pass
        self.log("Ни один ключ не работает. Скорее всего, провайдер заблокировал сервер: "
                 "смените в 3x-ui порт/SNI или IP — программа подхватит новые ключи "
                 "из подписки сама. Остальной интернет работает как обычно.")
        self.set_status("VLESS: нет рабочих ключей")
        return False

    def vless_disable(self):
        self.proxy_off()
        self.xray_stop()
        self.cfg["vless_enabled"] = False
        self.save()
        self.log("VLESS выключен, системный прокси возвращён как был.")
        self.set_status("VLESS выключен")

    def vless_apply(self):
        """После смены ссылки или списка сайтов."""
        if self.cfg.get("vless_enabled"):
            self.xray_start()
            self.vless_exclude_server()
            self.log("VLESS: настройки применены.")

    def vless_check(self):
        """Запрос через сервер: работает ли он и какой у него IP/страна."""
        tmp = not self.xray_running()
        if tmp:
            self.xray_start()
        try:
            d = self.server_probe(timeout=15)
            if d:
                self.log(f"Сервер работает ({link_title(self.cfg['vless_link'])}): "
                         f"выход в интернет с IP {d.get('ip')} (страна {d.get('loc')}).")
                self.set_status("Сервер VLESS работает")
            elif tmp or not self.vless_heal():
                self.log(f"Сервер не отвечает. Подробности — в {XRAY_DIR / 'xray.log'}")
                self.set_status("Сервер VLESS не отвечает")
        finally:
            if tmp:
                self.xray_stop()

    # ---------- сценарий при запуске ----------
    def startup_flow(self):
        if proxy_ours():
            self.proxy_off()  # остался после сбоя/выключения ПК — Xray сейчас не запущен
        if self.cfg.get("sub_url"):
            try:
                self.sub_refresh()
            except Exception as e:
                self.log(f"Подписка не обновилась: {e}")
        if self.cfg.get("vless_enabled"):
            try:
                self.vless_enable()
            except Exception as e:
                self.log(f"VLESS: {e}")
                self.proxy_off()
        if self.cfg.get("tg_enabled"):
            try:
                if not self.tg_exe():
                    self.tg_update(force=True)
                self.tg_start()
            except Exception as e:
                self.log(f"tg-ws-proxy: {e}")
        if not self.installed():
            self.log("Релиз не найден, скачиваю с GitHub…")
            try:
                self.update(force=True)
            except Exception as e:
                self.log(f"Не удалось скачать: {e}")
                self.log(f"Можно вручную распаковать релиз в {ZAPRET} "
                         "(чтобы general.bat лежал прямо в этой папке).")
                self.set_status("Нет релиза")
                return
        else:
            threading.Thread(target=self._notify_new, daemon=True).start()

        best = self.cfg.get("best")
        if best and (ZAPRET / best).exists():
            self.log(f"Запускаю сохранённую стратегию: {best}")
            self.set_status(f"Запускаю: {best}")
            if self.start(ZAPRET / best):
                time.sleep(WARMUP)
                s = self.score()
                if s >= max(1, self.cfg.get("score", 0) - 1):
                    self.log(f"Работает ({s} из {len(TARGETS)}).")
                    self.set_status(f"Работает: {best}")
                    return
            self.log("Сохранённая стратегия не работает, подбираю заново…")
        self.check_all()

    def _notify_new(self):
        try:
            tag = self.latest_release()["tag_name"]
            if tag != self.cfg.get("version"):
                self.log(f"Доступна новая версия {tag} — нажмите «Обновить».")
        except Exception:
            pass


# ---------- автозапуск ----------
def autostart_enabled():
    return sh(["schtasks", "/Query", "/TN", TASK]).returncode == 0


def set_autostart(on):
    """Задача Планировщика «при входе в Windows», с правами администратора.
    Через XML, а не ключи schtasks: по умолчанию задача не стартует от батареи
    и снимает программу через 72 часа работы — здесь это выключено."""
    if not on:
        sh(["schtasks", "/Delete", "/TN", TASK, "/F"])
        return
    if getattr(sys, "frozen", False):
        cmd, args = sys.executable, "--auto"
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        cmd = str(pyw if pyw.exists() else Path(sys.executable))
        args = f'"{Path(__file__).resolve()}" --auto'
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{APP_NAME}: запуск обхода при входе в Windows</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{esc(user)}</UserId><Delay>PT10S</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{esc(user)}</UserId><LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <Enabled>true</Enabled>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{esc(cmd)}</Command><Arguments>{esc(args)}</Arguments></Exec>
  </Actions>
</Task>
"""
    path = Path(os.environ.get("TEMP", str(APP_DIR))) / f"{APP_NAME}-task.xml"
    path.write_text(xml, encoding="utf-16")
    try:
        r = sh(["schtasks", "/Create", "/TN", TASK, "/XML", str(path), "/F"])
    finally:
        path.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)


def autostart_points_here():
    """Задача есть и запускает именно этот exe (после переезда папки — нет)."""
    r = sh(["schtasks", "/Query", "/TN", TASK, "/XML"])
    return r.returncode == 0 and sys.executable.lower() in r.stdout.lower()


# ---------- интерфейс: ui.html в окне WebView2 (pywebview) ----------
def resource(name):
    """Файл, вшитый в exe (ui.html, keys.txt), или лежащий рядом со скриптом."""
    return Path(getattr(sys, "_MEIPASS", APP_DIR)) / name


class Api:
    """Мост для ui.html: эти методы JS вызывает как window.pywebview.api.*.
    Всё служебное — с подчёркиванием, чтобы pywebview не отдавал это в JS."""

    def __init__(self, auto):
        self._log = []
        self._busy = False
        self._lock = threading.Lock()
        self._status = "Запуск…"
        self._eng = Engine(self._add_log, self._set_status)
        self._auto = auto
        self._autostart = autostart_enabled()
        self._running = False
        self._counts = (0, 0)
        self._quitting = False
        self._window = None
        self._update = None      # {"version", "url", "size"} — есть версия новее
        self._update_msg = ""    # итог последней проверки для вкладки «Настройки»
        self._update_told = ""   # о какой версии уже сообщили

    # ---------- служебное ----------
    def _add_log(self, m):
        self._log.append((time.strftime("%H:%M:%S"), m))

    def _set_status(self, m):
        self._status = m

    @staticmethod
    def _ok(msg=""):
        return {"ok": True, "msg": msg}

    @staticmethod
    def _err(msg):
        return {"ok": False, "msg": msg}

    def _work(self, fn):
        with self._lock:
            if self._busy:
                return False
            self._busy = True

        def run():
            try:
                fn()
            except Exception as e:
                self._add_log(f"Ошибка: {e}")
                self._status = "Ошибка"
            finally:
                self._running = winws_running()
                self._busy = False

        threading.Thread(target=run, daemon=True).start()
        return True

    def _busy_msg(self):
        return self._err("Сейчас идёт другая операция — подождите несколько секунд.")

    def _monitor(self):
        """Фон: состояние winws, сторож Xray (каждые 3 с), подписка и сервер (раз в 5 мин)."""
        last_health = time.time()
        while not self._quitting:
            e = self._eng
            try:
                self._running = winws_running()
                self._counts = (len(e.user_sites()), len(e.cfg.get("vless_sites", [])))
                if not self._busy and e.cfg.get("vless_enabled") and not e.xray_running():
                    # Xray упал — убираем системный прокси, иначе пропадёт интернет
                    e.proxy_off()
                    e.cfg["vless_enabled"] = False
                    e.save()
                    self._add_log("Xray остановился — VLESS выключен, прокси убран. "
                                  "Включите его снова или нажмите «Проверить сервер».")
                if time.time() - last_health > 300 and not self._busy:
                    last_health = time.time()
                    if e.sub_due():
                        self._work(e.sub_refresh)
                    elif e.cfg.get("vless_enabled") and e.xray_running():
                        if e.server_probe() is None and e.server_probe() is None:
                            self._work(e.vless_heal)
            except Exception as ex:
                self._add_log(f"Фоновая проверка: {ex}")
            self._tray_refresh()
            time.sleep(3)

    def _on_closing(self):
        if self._quitting:
            return True
        self._hide()  # Alt+F4 и т.п. — тоже в трей, выход — кнопкой «Выход»
        return False

    # ---------- значок в трее: работа в фоне при закрытом окне ----------
    def _tray_start(self):
        try:
            import pystray
            from PIL import Image
        except ImportError:
            self._tray = None
            return
        M = pystray.MenuItem
        menu = pystray.Menu(
            M("Открыть Hidden Path", lambda: self._show(), default=True),
            pystray.Menu.SEPARATOR,
            M("Обход блокировок", lambda: self._tray_bypass(), checked=lambda _: self._running),
            M("Сайты через мой сервер", lambda: self._tray_vless(),
              checked=lambda _: bool(self._eng.cfg.get("vless_enabled"))),
            M("Весь интернет через VLESS (кроме RU)", lambda: self._tray_vless_all(),
              checked=lambda _: bool(self._eng.cfg.get("vless_enabled") and self._eng.cfg.get("vless_all"))),
            M("Программы через VPN (Telegram, Claude…)", lambda: self._tray_flag("vless_tun"),
              checked=lambda _: bool(self._eng.cfg.get("vless_enabled") and self._eng.cfg.get("vless_tun"))),
            pystray.Menu.SEPARATOR,
            M("Выход", lambda: self.action("exit")),
        )
        self._tray = pystray.Icon(APP_NAME, Image.open(resource("icon.png")), APP_TITLE, menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _tray_refresh(self):
        t = getattr(self, "_tray", None)
        if not t:
            return
        c = self._eng.cfg
        title = f"{APP_TITLE} — " + ("обход включён" if self._running else "обход выключен")
        if c.get("vless_enabled"):
            title += " · VLESS" + (f" {c['vless_loc']}" if c.get("vless_loc") else "")
        if t.title != title:
            t.title = title
        t.update_menu()

    def _notify(self, text):
        t = getattr(self, "_tray", None)
        if t:
            try:
                t.notify(text, APP_TITLE)
            except Exception:
                pass

    def _hide(self):
        self._window.hide()
        if not getattr(self, "_hint_shown", False):
            self._hint_shown = True
            self._notify("Работаю в фоне. Открыть окно или выйти — значок у часов.")

    def _show(self):
        self._window.show()
        # и через WinAPI: show() у скрытого окна иногда не поднимает его наверх
        u = ctypes.windll.user32
        for _ in range(10):
            h = u.FindWindowW(None, APP_TITLE)
            if h:
                u.ShowWindow(h, 9)  # SW_RESTORE
                u.SetForegroundWindow(h)
                if u.IsWindowVisible(h):
                    return
            time.sleep(0.1)

    def _tray_bypass(self):
        if self._busy:
            return self._notify("Подождите — идёт другая операция.")
        if self._running:
            self.action("stop")
            self._notify("Обход выключен.")
        else:
            self._work(self._eng.restart)
            self._notify("Включаю обход…")

    def _tray_vless(self):
        on = not self._eng.cfg.get("vless_enabled")
        r = self.set_flag("vless", on)
        self._notify(r["msg"] if not r["ok"] else
                     ("Включаю VLESS для сайтов из списка…" if on else "VLESS выключается…"))

    def _tray_vless_all(self):
        c = self._eng.cfg
        on = not (c.get("vless_enabled") and c.get("vless_all"))
        r = self.set_flag("vless_all", on)
        self._notify(r["msg"] if not r["ok"] else
                     ("Весь интернет через VLESS, кроме RU…" if on else "Через VLESS — только сайты из списка."))

    def _tray_flag(self, key):
        c = self._eng.cfg
        r = self.set_flag(key, not (c.get("vless_enabled") and c.get(key)))
        self._notify(r["msg"] if not r["ok"] else "Применяю…")

    # ---------- своё окно (системной рамки нет) ----------
    def win(self, cmd):
        w = self._window
        if cmd == "min":
            w.minimize()
        elif cmd == "close":  # закрыть = в трей, программа работает дальше
            self._hide()
        elif cmd == "max":
            if getattr(self, "_maxed", False):
                w.restore()
            else:
                w.maximize()
            self._maxed = not getattr(self, "_maxed", False)
        return self._ok()

    def resize(self, width, height):
        self._maxed = False
        self._window.resize(max(880, int(width)), max(620, int(height)))
        return self._ok()

    def _start(self):
        self._tray_start()
        threading.Thread(target=self._monitor, daemon=True).start()
        threading.Thread(target=self._heal_autostart, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        self._work(self._eng.startup_flow)

    # ---------- обновление самой программы ----------
    def _update_loop(self):
        time.sleep(20)  # после запуска обхода, чтобы не мешать подбору стратегии
        while not self._quitting:
            self._update_check(quiet=True)
            time.sleep(6 * 3600)

    def _update_check(self, quiet=False):
        """Есть ли релиз новее APP_VERSION. quiet — фоновая проверка: без записей в журнал, кроме находки."""
        stamp = time.strftime("%d.%m %H:%M")
        if APP_PRIVATE and not update_token():
            self._update_msg = "Проверка обновлений недоступна: в сборке нет токена GitHub (строка token: в keys.txt)."
            return
        try:
            with github_get(f"https://api.github.com/repos/{APP_REPO}/releases/latest") as r:
                rel = json.load(r)
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                self._update_msg = f"{stamp}: релизов пока нет или нет доступа к репозиторию."
            else:
                self._update_msg = f"{stamp}: GitHub ответил ошибкой {ex.code}."
            if not quiet:
                self._add_log("Обновления: " + self._update_msg)
            return
        except Exception as ex:
            self._update_msg = f"{stamp}: не удалось проверить обновления ({ex})."
            if not quiet:
                self._add_log("Обновления: " + self._update_msg)
            return
        tag = rel.get("tag_name", "")
        asset = next((a for a in rel.get("assets", []) if a.get("name") == APP_SETUP), None)
        if asset and ver_tuple(tag) > ver_tuple(APP_VERSION):
            ver = tag.lstrip("vV")
            self._update = {"version": ver, "url": asset["url"], "size": asset.get("size", 0)}
            self._update_msg = f"{stamp}: вышла версия {ver}."
            if self._update_told != ver:
                self._update_told = ver
                self._add_log(f"Вышло обновление {APP_TITLE} {ver} (установлена {APP_VERSION}). "
                              "Нажмите «Обновить приложение».")
                self._notify(f"Вышло обновление {ver}. Откройте программу и нажмите «Обновить приложение».")
        else:
            self._update = None
            self._update_msg = f"{stamp}: у вас последняя версия."
            if not quiet:
                self._add_log(f"Обновлений нет: {APP_VERSION} — последняя версия.")

    def _update_install(self):
        self._status = "Проверяю обновления…"
        self._update_check()
        u = self._update
        if not u:
            self._status = "Обновлений нет"
            return
        self._status = f"Скачиваю версию {u['version']}…"
        self._add_log(f"Скачиваю {APP_TITLE} {u['version']}…")
        dst = Path(os.environ.get("TEMP", str(APP_DIR))) / APP_SETUP
        with github_get(u["url"], accept="application/octet-stream") as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if u["size"] and dst.stat().st_size != u["size"]:
            raise RuntimeError("установщик скачался не полностью, попробуйте ещё раз")
        self._add_log("Устанавливаю обновление. Программа закроется и запустится заново.")
        self._status = "Устанавливаю обновление…"
        # установщик сам вернёт прокси, остановит обход и запустит новую версию
        subprocess.Popen([str(dst), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED | NEW_GROUP
        time.sleep(1.5)
        self.action("exit")

    def _heal_autostart(self):
        """Автозапуск включён по умолчанию; если задача пропала или ведёт на старый
        путь (программу переустановили в другую папку) — создать заново."""
        if not getattr(sys, "frozen", False) or not self._eng.cfg.get("autostart", True):
            return
        if not autostart_points_here():
            try:
                set_autostart(True)
                self._add_log("Автозапуск при входе в Windows включён.")
            except Exception as ex:
                self._add_log(f"Не удалось включить автозапуск: {ex}")
        self._autostart = autostart_enabled()

    # ---------- данные для экрана ----------
    def poll(self, n=0):
        c = self._eng.cfg
        info = c.get("sub_info", {})
        used = (info.get("download", 0) + info.get("upload", 0)) / 1024 ** 3
        n = max(0, int(n or 0))
        return {
            "status": self._status, "busy": self._busy, "running": self._running,
            "score": c.get("score", len(TARGETS)), "total": len(TARGETS),
            "strategy": c.get("best", ""),
            "versions": {"zapret": c.get("version"), "tg": c.get("tg_version"),
                         "xray": c.get("xray_version")},
            "autostart": self._autostart, "tg": bool(c.get("tg_enabled")),
            "vless": bool(c.get("vless_enabled")), "vless_loc": c.get("vless_loc", ""),
            "vless_all": bool(c.get("vless_all")), "vless_tun": bool(c.get("vless_tun")),
            "sites_n": self._counts[0], "vpn_sites_n": self._counts[1],
            "traffic": f"{used:.1f} ГБ" if "download" in info else "",
            "log": self._log[n:], "log_n": len(self._log),
            "app": {"version": APP_VERSION, "update": (self._update or {}).get("version", ""),
                    "msg": self._update_msg},
        }

    def sites(self, kind):
        if kind == "zapret":
            return self._eng.user_sites()
        if kind == "apps":
            return list(self._eng.vless_apps())
        return list(self._eng.cfg.get("vless_sites", []))

    def vpn(self):
        e, c = self._eng, self._eng.cfg
        links, subl = e.all_links(), c.get("sub_links", [])
        sub_ids = {link_id(l) for l in subl}
        cur = c.get("vless_link")
        active = next((i for i, l in enumerate(links) if cur and link_id(l) == link_id(cur)), -1)
        info = c.get("sub_info", {})
        used = (info.get("download", 0) + info.get("upload", 0)) / 1024 ** 3
        if not c.get("sub_url"):
            text = "Подписка не задана. 3x-ui: Клиенты → «Поделиться» → ссылка подписки."
        else:
            text = f"Ключей из подписки: {len(subl)}"
            if c.get("sub_time"):
                text += ", обновлена " + time.strftime("%d.%m %H:%M", time.localtime(c["sub_time"]))
            if "download" in info:
                text += f", трафик {used:.1f} ГБ"
                if info.get("total"):
                    text += f" из {info['total'] / 1024 ** 3:.0f} ГБ"
            if info.get("expire"):
                text += ", до " + time.strftime("%d.%m.%Y", time.localtime(info["expire"]))
        return {"keys": [{"title": link_title(l), "sub": link_id(l) in sub_ids} for l in links],
                "active": active, "sub_set": bool(c.get("sub_url")), "sub_text": text,
                "used_gb": used, "total_gb": info.get("total", 0) / 1024 ** 3,
                "panel": bool(c.get("panel_url"))}

    def settings(self):
        s = self._eng.settings()
        s["fakes"] = self._eng.fakes() if self._eng.installed() else []
        return s

    # ---------- действия ----------
    def action(self, name):
        e = self._eng
        jobs = {"check": e.check_all, "update": e.update_and_check, "sub_refresh": e.sub_refresh,
                "vless_check": e.vless_check, "ipset_update": e.ipset_update_and_restart}
        if name in jobs:
            return self._ok() if self._work(jobs[name]) else self._busy_msg()
        if name == "app_check":
            return self._ok() if self._work(lambda: self._update_check()) else self._busy_msg()
        if name == "app_update":
            return self._ok("Обновляю программу…") if self._work(self._update_install) else self._busy_msg()
        if name == "stop":
            e.stop()
            self._running = False
            self._status = "Остановлено"
            self._add_log("Обход остановлен.")
            return self._ok("Обход остановлен")
        if name == "open_list":
            if not e.installed():
                return self._err("Релиз ещё не установлен — нажмите «Обновить».")
            if not e.user_list().exists():
                e.save_user_sites([])
            os.startfile(str(e.user_list()))
            return self._ok()
        if name == "open_panel":
            url = e.cfg.get("panel_url", "")
            if not re.match(r"^https?://\S+$", url):
                return self._err("Вставьте ссылку на свою панель 3x-ui в поле ниже.")
            os.startfile(url)
            return self._ok()
        if name == "exit":
            self._quitting = True
            e.stop()
            e.tg_stop()
            try:
                e.proxy_off()  # vless_enabled остаётся — при запуске включится снова
            except OSError:
                pass
            e.xray_stop()
            if getattr(self, "_tray", None):
                self._tray.visible = False  # убрать сразу, иначе у часов остаётся «призрак»
                self._tray.stop()
            self._window.destroy()
            return self._ok()
        return self._err(f"неизвестное действие {name}")

    def set_flag(self, key, on):
        e = self._eng
        if key == "autostart":
            try:
                set_autostart(bool(on))
            except Exception as ex:
                return self._err(f"Не удалось изменить автозапуск: {ex}")
            e.cfg["autostart"] = bool(on)
            e.save()
            self._autostart = autostart_enabled()
            self._add_log("Автозапуск " + ("включён." if on else "выключен."))
            return self._ok()
        if key == "tg":
            return self._ok() if self._work(e.tg_enable if on else e.tg_disable) else self._busy_msg()
        if key == "vless":
            if on and not e.cfg.get("vless_link"):
                return self._err("Сначала добавьте ключ или подписку во вкладке «Через VPN».")

            def fn():
                try:
                    (e.vless_enable if on else e.vless_disable)()
                except Exception:
                    e.proxy_off()  # не оставлять интернет на мёртвом прокси
                    e.xray_stop()
                    e.cfg["vless_enabled"] = False
                    e.save()
                    raise
            return self._ok() if self._work(fn) else self._busy_msg()
        if key in ("vless_all", "vless_tun"):
            if on and not e.cfg.get("vless_link"):
                return self._err("Сначала добавьте ключ или подписку во вкладке «Через VPN».")
            msg = {("vless_all", True): "Весь интернет через VLESS, кроме российских сайтов.",
                   ("vless_all", False): "Через VLESS снова только сайты из списка.",
                   ("vless_tun", True): "Программы через VPN включены: " + ", ".join(e.vless_apps()),
                   ("vless_tun", False): "Программы через VPN выключены."}[(key, bool(on))]

            def fn():
                e.cfg[key] = bool(on)
                e.save()
                if e.cfg.get("vless_enabled") and e.xray_running():
                    try:
                        e.xray_start()
                    except Exception:
                        if key == "vless_tun" and on:  # TUN не поднялся — вернуть как было
                            e.cfg[key] = False
                            e.save()
                            e.xray_start()
                        raise
                    e.log(msg)
                elif on:  # VLESS был выключен — включаем сразу в этом режиме
                    try:
                        e.vless_enable()
                    except Exception:
                        e.proxy_off()
                        e.xray_stop()
                        e.cfg["vless_enabled"] = False
                        e.save()
                        raise
            return self._ok() if self._work(fn) else self._busy_msg()
        return self._err(f"неизвестный флаг {key}")

    def add_sites(self, kind, text):
        e = self._eng
        if kind == "zapret" and not e.installed():
            return self._err("Релиз ещё не установлен — нажмите «Обновить».")
        if kind == "apps":  # имена программ: «Telegram», «Telegram.exe», путь к exe
            good, bad = [], []
            for w in re.split(r"[,;\n]+", text or ""):
                w = w.strip().strip('"')
                w = re.sub(r"(?i)\.exe$", "", re.split(r"[\\/]", w)[-1]) if w else ""
                if w:
                    (good if re.match(r"^[\w .+()-]{1,64}$", w) else bad).append(w)
        else:
            good, bad = parse_domains(text)
        if kind == "zapret":  # список zapret — только домены
            bad += [d for d in good if is_ip(d)]
            good = [d for d in good if not is_ip(d)]
        cur = self.sites(kind)
        new = [d for d in good if d not in cur]
        if new:
            self._save_sites(kind, cur + new)
            self._add_log("Добавлено: " + ", ".join(new))
        if bad:
            tip = " (IP-адреса добавляйте во вкладку «Через VPN»)" if kind == "zapret" else ""
            what = "имя программы" if kind == "apps" else "домен"
            return self._err(f"Не похоже на {what}: " + ", ".join(bad) + tip)
        return self._ok("Добавлено: " + ", ".join(new) if new else "Уже в списке")

    def del_site(self, kind, site):
        cur = self.sites(kind)
        if site in cur:
            cur.remove(site)
            self._save_sites(kind, cur)
            self._add_log(f"Удалено: {site}")
        return self._ok()

    def _save_sites(self, kind, sites):
        e = self._eng
        if kind == "zapret":
            e.save_user_sites(sites)
            if winws_running() and not self._work(e.restart):
                self._add_log("Список применится при следующем перезапуске обхода.")
        elif kind == "apps":
            e.cfg["vless_apps"] = sites
            e.save()
            if e.cfg.get("vless_tun") and e.xray_running() and not self._work(e.vless_apply):
                self._add_log("Список применится при следующем запуске VLESS.")
        else:
            e.cfg["vless_sites"] = sites
            e.save()
            if e.xray_running() and not self._work(e.vless_apply):
                self._add_log("Список применится при следующем запуске VLESS.")

    def save_panel(self, url):
        url = (url or "").strip()
        if url and not re.match(r"^https?://\S+$", url):
            return self._err("Ссылка на панель должна начинаться с https://")
        self._eng.cfg["panel_url"] = url
        self._eng.save()
        return self._ok("Ссылка на панель сохранена" if url else "Ссылка на панель убрана")

    def save_sub(self, url):
        url = (url or "").strip()
        if url and not re.match(r"^https?://\S+$", url):
            return self._err("Ссылка подписки должна начинаться с https://")
        c = self._eng.cfg
        c["sub_url"] = url
        if not url:
            c["sub_links"] = []
        self._eng.save()
        if not url:
            return self._ok("Подписка убрана")
        self._work(self._eng.sub_refresh)
        return self._ok("Подписка сохранена, загружаю ключи…")

    def _set_active(self, link):
        e = self._eng
        e.cfg["vless_link"] = link
        e.save()
        if e.cfg.get("vless_enabled"):
            if link:
                self._work(e.vless_apply)
            else:
                self._work(e.vless_disable)

    def add_key(self, link):
        link = (link or "").strip()
        try:
            parse_vless(link)
        except (ValueError, json.JSONDecodeError) as ex:
            return self._err(f"Не получилось разобрать ключ: {ex}")
        manual = self._eng.cfg.setdefault("vless_links", [])
        if link not in manual:
            manual.append(link)
        self._add_log(f"Ключ добавлен и выбран: {link_title(link)}")
        self._set_active(link)
        return self._ok("Ключ добавлен")

    def pick_key(self, i):
        links = self._eng.all_links()
        if 0 <= i < len(links) and links[i] != self._eng.cfg.get("vless_link"):
            self._add_log(f"Активный ключ: {link_title(links[i])}")
            self._set_active(links[i])
        return self._ok()

    def del_key(self, i):
        e = self._eng
        links = e.all_links()
        if not (0 <= i < len(links)):
            return self._ok()
        manual = e.cfg.get("vless_links", [])
        mine = [l for l in manual if link_id(l) == link_id(links[i])]
        if not mine:
            return self._err("Этот ключ пришёл из подписки — уберите его в 3x-ui.")
        for l in mine:
            manual.remove(l)
        self._add_log(f"Ключ удалён: {link_title(links[i])}")
        rest = e.all_links()
        cur = e.cfg.get("vless_link")
        self._set_active(cur if cur in rest else (rest[0] if rest else None))
        return self._ok("Ключ удалён")

    def apply_settings(self, s):
        tcp, udp = valid_ports(s.get("game_tcp", "")), valid_ports(s.get("game_udp", ""))
        if not tcp or not udp:
            return self._err("Неверные порты. Пример: 1024-1934,1936-65535")
        if s.get("game_mode") not in GAME_MODES or s.get("ipset") not in IPSET_MODES:
            return self._err("Неверные настройки")
        c = self._eng.cfg
        c.update(game_mode=s["game_mode"], game_tcp=tcp, game_udp=udp, ipset=s["ipset"],
                 check_updates=bool(s.get("check_updates")),
                 fake_discord=s.get("fake_discord") or None, fake_game=s.get("fake_game") or None)
        self._eng.save()
        self._add_log("Настройки сохранены.")
        if not self._work(self._eng.restart):
            return self._ok("Сохранено. Применится при следующем запуске обхода.")
        return self._ok("Настройки сохранены, перезапускаю обход…")


def run_window(auto):
    import webview
    api = Api(auto)
    win = webview.create_window(
        APP_TITLE, url=str(resource("ui.html")), js_api=api,
        width=1120, height=740, min_size=(880, 620), background_color="#120b30",
        hidden=auto, frameless=True, easy_drag=False, shadow=True,  # автозапуск — сразу в трей
    )
    api._window = win
    win.events.closing += api._on_closing
    icon = resource("icon.ico")
    webview.start(api._start, icon=str(icon) if icon.exists() else None)


# ---------- запуск ----------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)


def main():
    if sys.platform != "win32":
        sys.exit("Приложение рассчитано на Windows.")
    if "--reset-proxy" in sys.argv:  # для деинсталлятора
        Engine(lambda m: None, lambda m: None).proxy_off()
        sys.exit(0)
    if not is_admin():
        elevate()
    if "--set-autostart" in sys.argv:  # для установщика: --set-autostart on|off
        on = sys.argv[sys.argv.index("--set-autostart") + 1:][:1] != ["off"]
        eng = Engine(lambda m: None, lambda m: None)
        eng.cfg["autostart"] = on
        eng.save()
        try:
            set_autostart(on)
        except Exception:
            sys.exit(1)
        sys.exit(0)
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\HiddenPathMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # уже запущено — показать то окно
        h = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
        if h:
            ctypes.windll.user32.ShowWindow(h, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(h)
        sys.exit(0)
    run_window("--auto" in sys.argv)


if __name__ == "__main__":
    main()
