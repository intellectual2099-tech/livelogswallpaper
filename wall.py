#!/usr/bin/env python3
import argparse
import random
import re
import subprocess
import sys
import time
import threading
import queue
import os
import glob
from collections import deque

GREEN = (0, 255, 65)
DIM = (0, 160, 40)
DARK = (0, 90, 22)
RED = (255, 60, 40)
YELLOW = (255, 200, 40)
CYAN = (60, 255, 255)
PURPLE = (170, 130, 255)
WHITE = (210, 255, 220)
BLACK = (0, 0, 0)


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wid", default="", help="external X11 window id to embed into")
    ap.add_argument("--size", default="", help="explicit width x height")
    ap.add_argument("--preview", action="store_true", help="own window (no embed)")
    return ap.parse_args()


ARGS = parse_args()

if ARGS.wid:
    os.environ["SDL_VIDEODRIVER"] = "x11"
    os.environ["SDL_WINDOWID"] = ARGS.wid

import pygame

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
if not os.path.exists(FONT_PATH):
    FONT_PATH = None

WIDTH, HEIGHT = None, None
if ARGS.size and "x" in ARGS.size:
    try:
        tw, th = ARGS.size.lower().split("x")[:2]
        WIDTH, HEIGHT = int(tw), int(th)
    except Exception:
        pass

pygame.init()
if ARGS.wid:
    del os.environ["SDL_WINDOWID"]

# No explicit --size: ask the display what the real screen size is so the
# wallpaper always adapts to the user's display instead of guessing 1920x1080.
if WIDTH is None:
    try:
        _info = pygame.display.Info()
        if _info.current_w > 0 and _info.current_h > 0:
            WIDTH, HEIGHT = _info.current_w, _info.current_h
    except Exception:
        pass
if WIDTH is None:
    WIDTH, HEIGHT = 1920, 1080

if ARGS.wid:
    flags = pygame.NOFRAME
else:
    flags = pygame.FULLSCREEN if not ARGS.preview else 0
if ARGS.wid:
    try:
        disp = pygame.display.set_mode((WIDTH, HEIGHT), flags)
    except Exception:
        disp = pygame.display.set_mode((0, 0), flags)
else:
    disp = pygame.display.set_mode((WIDTH, HEIGHT), flags)
WIDTH, HEIGHT = disp.get_size()
pygame.display.set_caption("GHOST OPS TERMINAL")
clock = pygame.time.Clock()

def font(size):
    return pygame.font.Font(FONT_PATH, size)

F_SMALL = font(13)
F_MID = font(16)


def hostinfo():
    host = os.uname().nodename
    user = os.getlogin() if hasattr(os, "getlogin") else os.environ.get("USER", "ghost")
    kernel = os.uname().release
    rel = ""
    try:
        with open("/etc/os-release") as f:
            for ln in f:
                if ln.startswith("PRETTY_NAME="):
                    rel = ln.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        pass
    ips = []
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
        ips = out[:3]
    except Exception:
        pass
    return user, host, kernel, rel, ips

USERNAME, HOSTNAME, KERNEL, OSREL, CLIENT_IPS = hostinfo()
XORG_DISPLAY = os.environ.get("DISPLAY", "?")
SESSION = os.environ.get("XDG_SESSION_TYPE", "?")

HEXCHARS = "0123456789ABCDEF"


def niceval(n):
    if n >= 10**9:
        return f"{n/10**9:.2f}G"
    if n >= 10**6:
        return f"{n/10**6:.2f}M"
    if n >= 10**3:
        return f"{n/10**3:.1f}K"
    return str(int(n))


class SysStats:
    def __init__(self):
        self.cpu_all = 0.0
        self.cpu_cores = []
        self.mem_used = 0
        self.mem_total = 0
        self.swap_used = 0
        self.swap_total = 0
        self.disk = []
        self.net = {}
        self.load = [0.0, 0.0, 0.0]
        self.temp = 0.0
        self.top = ("", 0.0)
        self.uptime = 0
        self.up = time.time()
        self._last_cpu = {}
        self._last_net = {}
        self._lnet = time.time()

    def _cpu(self):
        try:
            with open("/proc/stat") as f:
                raw = {}
                lines = f.readlines()
                for ln in lines:
                    if ln.startswith("cpu"):
                        parts = ln.split()
                        core = parts[0]
                        vals = [int(v) for v in parts[1:]]
                        ttl = sum(vals)
                        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                        prev = self._last_cpu.get(core)
                        self._last_cpu[core] = (ttl, idle)
                        if prev:
                            dt = ttl - prev[0]
                            if dt > 0:
                                raw[core] = round(100 * (1 - (idle - prev[1]) / dt), 1)
                self.cpu_all = raw.get("cpu", 0.0)
                self.cpu_cores = [v for k, v in raw.items() if k != "cpu"]
        except Exception:
            pass

    def _mem(self):
        try:
            with open("/proc/meminfo") as f:
                d = {}
                for ln in f:
                    k, _, v = ln.partition(":")
                    d[k] = int(v.strip().split()[0]) * 1024
            self.mem_total = d.get("MemTotal", 0)
            self.mem_used = self.mem_total - d.get("MemAvailable", d.get("MemFree", 0))
            self.swap_total = d.get("SwapTotal", 0)
            self.swap_used = self.swap_total - d.get("SwapFree", 0)
        except Exception:
            pass

    def _disk(self):
        try:
            out = subprocess.run(["df", "-BG", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs",
                                  "-x", "overlay", "-x", "efivarfs"], capture_output=True, text=True).stdout
            self.disk = []
            for ln in out.splitlines()[1:]:
                p = ln.split()
                if len(p) >= 5:
                    self.disk.append((p[5], p[2]))
        except Exception:
            pass

    def _net(self):
        try:
            with open("/proc/net/dev") as f:
                now = time.time()
                lines = f.readlines()[2:]
                delta = max(now - self._lnet, 0.01)
                net = {}
                for ln in lines:
                    p = ln.split(":")
                    if len(p) != 2:
                        continue
                    iface = p[0].strip()
                    if iface.startswith("lo"):
                        continue
                    v = p[1].split()
                    rx, tx = int(v[0]), int(v[8])
                    prev = self._last_net.get(iface, (rx, tx))
                    self._last_net[iface] = (rx, tx)
                    net[iface] = (int((rx - prev[0]) / delta), int((tx - prev[1]) / delta))
                self.net = net
                self._lnet = now
        except Exception:
            pass

    def _load(self):
        try:
            with open("/proc/loadavg") as f:
                self.load = [float(x) for x in f.read().split()[:3]]
        except Exception:
            pass

    def _temp(self):
        for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            try:
                with open(path) as f:
                    t = int(f.read().strip()) / 1000
                    self.temp = t
                    return
            except Exception:
                pass
        self.temp = 0.0

    def _top(self):
        try:
            out = subprocess.run(["ps", "-eo", "comm,%cpu", "--sort=-%cpu", "--no-headers"],
                                 capture_output=True, text=True).stdout.splitlines()
            if out:
                n, p = out[0].rsplit(maxsplit=1)
                self.top = (n, float(p))
        except Exception:
            pass

    def _uptime(self):
        try:
            with open("/proc/uptime") as f:
                self.uptime = int(float(f.read().split()[0]))
        except Exception:
            pass

    def refresh(self):
        self._cpu()
        self._mem()
        self._disk()
        self._net()
        self._load()
        self._temp()
        self._top()
        self._uptime()


SYS = SysStats()


def fmt_uptime(sec):
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, _ = divmod(sec, 60)
    return f"{d}d {h:02d}h {m:02d}m"


LOG_TAG = r"^(?:\x1b\[[0-9;]*m){0,2}(.{0,64}?)[:\[](\d+)(]?)\]?:\s?(.+)$"
SILENT = re.compile(r"systemd-logind\[|dbus-daemon|org.gnome|wayland|pipewire|pulseaudio|mutter|gsd-", re.I)


def clean_journal_line(raw):
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
    return raw


LEVEL_COLORS = {
    "error": RED, "crit": RED, "alert": RED, "emerg": RED,
    "warning": YELLOW, "notice": PURPLE, "info": GREEN, "debug": DIM,
}


def classify(ln):
    low = ln.lower()
    if "error" in low or "fail" in low or "denied" in low or "timeout" in low or "exception" in low:
        return "error"
    if "warn" in low or "deprecated" in low:
        return "warning"
    if "ssh" in low or "sudo" in low or "auth" in low or "login" in low:
        return "notice"
    return "info"


def add_line(q, tag, msg, lvl="info"):
    try:
        q.put((time.time(), tag, msg, lvl), timeout=0.5)
    except queue.Full:
        pass


def journal_feeder(q, stop):
    proc = None
    try:
        proc = subprocess.Popen(
            ["journalctl", "-f", "-n", "40", "--no-pager"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except Exception:
        pass
    if proc:
        try:
            for raw in proc.stdout:
                if stop.is_set():
                    break
                line = clean_journal_line(raw)
                if not line:
                    continue
                m = re.match(r"^([A-Z][a-z]{2} \d{2} \d{2}:\d{2}:\d{2})\s+(\S+)\s+(.+)$", line)
                time.sleep(random.uniform(0.0, 0.06))
                if m:
                    add_line(q, m.group(2), m.group(3), classify(m.group(3)))
                else:
                    add_line(q, "journal", line, classify(line))
        except Exception:
            pass
        proc.kill()


def auth_feeder(q, stop):
    targets = ["/var/log/auth.log", "/var/log/syslog"]
    tailed = [p for p in targets if os.path.exists(p)]
    if not tailed:
        return
    procs = []
    try:
        for t in tailed:
            procs.append(subprocess.Popen(["tail", "-F", "-n", "0", t],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1))
    except Exception:
        return
    try:
        while not stop.is_set():
            for p in procs:
                if p.poll() is not None or not p.stdout:
                    continue
                raw = p.stdout.readline()
                if not raw:
                    continue
                line = clean_journal_line(raw)
                if SILENT.search(line):
                    continue
                if any(k in line.lower() for k in ("sshd", "sudo", "su[", "login", "failed", "break")):
                    add_line(q, "SECURITY", line, "notice")
    except Exception:
        pass
    for p in procs:
        p.kill()


NARRATIVES = [
    ("CHRONOS", "quantum clock locked :: drift 0.0003 ms/s"),
    ("CHRONOS", "rewinding telemetry buffer .. 64K frames ready"),
    ("SEER", "predictive model confidence 98.7% on anomaly window"),
    ("SEER", "training isolation forest @ 4.2M events/min"),
    ("NETD", "packet signature database refreshed :: 1,024 sigs"),
    ("NETD", "TLS fingerprint cache warmed :: 812 hosts"),
    ("GUARD", "host IDS eager feed attached :: zero drops"),
    ("GUARD", "heuristic engine v9 online :: watchdaemon up"),
    ("KRMEL", "BPF probes compiled in 0.31s :: 41 tracepoints armed"),
    ("KRMEL", "kprobe kernel_read armed :: tid 0x123"),
    ("KRMEL", "pagewalker sweep complete :: 0 anomalies"),
    ("MEMEX", "heap canary re-encrypted :: entropy pool 4096 bits"),
    ("MEMEX", "mlock region guard installed @ 0x7fff98f00000"),
    ("FIREW", "nftables set synced :: input/output/forward locked"),
    ("FIREW", "port scan burst on eth0 throttled :: 19 srcs"),
    ("SYSML", "integrity baseline SHA-256 verified :: 0 drift"),
    ("SYSML", "fim watch registered on /etc :: 0 mutations"),
    ("TPM", "PCR attestation valid :: measured boot intact"),
    ("RCON", "secure shell tunnel alive :: 2 peers"),
    ("RCON", "telemetry uplink encrypted :: AES-256-GCM"),
]

def narrator(q, stop):
    while not stop.is_set():
        time.sleep(random.uniform(4.0, 11.0))
        tag, msg = random.choice(NARRATIVES)
        add_line(q, tag, msg, random.choice(["info", "notice"]))


def render_char(c, color, shadow=True, font=F_SMALL):
    s = pygame.Surface((font.size(c)[0] + 1, font.size(c)[1] + 1), pygame.SRCALPHA)
    if shadow:
        s.blit(font.render(c, False, (0, 60, 15)), (1, 1))
    s.blit(font.render(c, False, color), (0, 0))
    return s


class MatrixRain:
    def __init__(self, width, height, font, speed_scale=3.0, alpha=110):
        self.width = width
        self.height = height
        self.font = font
        self.char_w = font.size("A")[0] + 1
        self.char_h = font.size("A")[1] + 1
        self.cols = max(1, width // self.char_w)
        self.rows = max(1, height // self.char_h)
        self.alpha = alpha
        self.surface = pygame.Surface((self.cols * self.char_w, self.height), pygame.SRCALPHA)
        self.cache = {}
        self.speeds = [random.uniform(0.4, 1.6) * speed_scale / 10 for _ in range(self.cols)]
        self.pos = [random.randint(-self.rows, 0) for _ in range(self.cols)]
        self.enabled = True

    def update(self, dt):
        if not self.enabled:
            return
        self.surface.fill((0, 0, 0, 0))
        for i in range(self.cols):
            self.pos[i] += self.speeds[i] * dt * 60
            if self.pos[i] > self.rows:
                self.pos[i] = random.randint(-10, -4)
            for r in range(3):
                if r == 0:
                    col = (180, 255, 210)
                elif r == 1:
                    col = (0, 255, 100)
                else:
                    col = (0, 130, 45)
                y = int((self.pos[i] - r) * self.char_h)
                if y < -self.char_h or y > self.height:
                    continue
                c = chr(random.choice((0x30, 0x31, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x3B, 0x2F, 0x7C)))
                key = (c, r)
                glyph = self.cache.get(key)
                if glyph is None:
                    glyph = render_char(c, col, font=self.font)
                    self.cache[key] = glyph
                self.surface.blit(glyph, (i * self.char_w, y))

    def draw(self, screen):
        if self.enabled:
            screen.blit(self.surface, (0, 0))


def make_scanlines(width, height):
    s = pygame.Surface((width, height), pygame.SRCALPHA)
    for y in range(0, height, 3):
        s.fill((0, 0, 0, 16), (0, y, width, 1))
    return s


class Feed:
    def __init__(self, q, maxlen=240):
        self.q = q
        self.maxlen = maxlen
        self.buf = deque(maxlen=maxlen)

    def drain(self):
        for _ in range(16):
            try:
                self.buf.append(self.q.get_nowait())
            except queue.Empty:
                break

    def lines(self):
        return list(self.buf)


def banner_text():
    name = f"{USERNAME}@{HOSTNAME}"
    txt = [
        "  ▄████  ██░ ██ ▒█████   ██████ ▄▄▄█████▓ ▄▄▄       ██▀███",
        " ██▒ ▀█ ▓██░ ██▒▒██▒  ██▒▒██    ▒ ▓  ██▒ ▓▒▒████▄    ▓██ ▒ ██▒",
        "▒██░▄▄▄░▒██▀▀██░▒██░  ██▒░ ▓██▄   ▒ ▓██░ ▒░▒██  ▀█▄  ▓██ ░▄█ ▒",
        "░▓█  ██▓░▓█ ░██ ░██   ██░  ▒   ██▒░ ▓██▓ ░ ░██▄▄▄▄██ ▒██▀▀█▄",
        "░▒▓███▀▒░▓█▒░██▓░ ████▓▒░▒██████▒▒  ▒██▒ ░  ▓█   ▓██▒░██▓ ▒██▒",
        " ░▒   ▒  ▒ ░░▒░▒░ ▒░▒░▒░ ▒ ▒▓▒ ▒ ░  ▒ ░░    ▒▒   ▓▒█░░ ▒▓ ░▒▓░",
        "  ░   ░  ▒ ░▒░ ░  ░ ▒ ▒░ ░ ░▒  ░ ░    ░      ▒   ▒▒ ░  ░▒ ░ ▒░",
        "░ ░   ░  ░  ░░ ░░ ░ ░ ▒    ░  ░  ░    ░        ░   ▒     ░░   ░",
        "      ░  ░  ░  ░    ░ ░      ░                       ░  ░     ░",
    ]
    head = txt[0]
    return txt


BANNER = banner_text()


def human_time(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def format_line(item):
    t, tag, msg, lvl = item
    core = f"  [{human_time(t)}] "
    txt = f"{tag}: {msg}"
    return core + txt, LEVEL_COLORS.get(lvl, GREEN)


def wrap_text(txt, font, max_w):
    out = []
    cur = ""
    for ch in txt:
        if font.size(cur + ch)[0] > max_w:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def stat_bar(screen, x, y, label, frac, width=300, label_color=WHITE):
    screen.blit(F_SMALL.render(label, False, label_color), (x, y))
    yy = y + 20
    fw = width
    fh = 11
    pygame.draw.rect(screen, (10, 50, 60), (x, yy, fw, fh), 1)
    fill = int(max(0, min(1, frac)) * (fw - 2))
    if fill > 0:
        pygame.draw.rect(screen, CYAN if frac < 0.8 else YELLOW if frac < 0.95 else RED,
                         (x + 1, yy + 1, fill, fh - 2))
    pct = f"{frac*100:5.1f}%"
    sp = F_SMALL.render(pct, False, WHITE)
    screen.blit(sp, (x + fw + 10, y))
    return y + yy - y + fh + 14


def draw_stats(screen, x, y):
    s = F_MID.render("[ SYSTEM SNAPSHOT ]", False, CYAN)
    screen.blit(s, (x, y))
    y += F_MID.get_height() + 10
    y = stat_bar(screen, x, y, f"CPU {SYS.cpu_all:5.1f}%", SYS.cpu_all / 100)
    y = stat_bar(screen, x, y, f"MEM {niceval(SYS.mem_used)}/{niceval(SYS.mem_total)}",
                 SYS.mem_used / SYS.mem_total if SYS.mem_total else 0)
    if SYS.swap_total:
        y = stat_bar(screen, x, y, f"SWAP {niceval(SYS.swap_used)}/{niceval(SYS.swap_total)}",
                     SYS.swap_used / SYS.swap_total if SYS.swap_total else 0)
    cores = SYS.cpu_cores
    if cores:
        chunks = [cores[i:i+6] for i in range(0, len(cores), 6)]
        row = chunks[0]
        n = len(row)
        bw = 22
        gap = 6
        tw = n * (bw + gap)
        yt = y
        screen.blit(F_SMALL.render("CORE", False, WHITE), (x, yt))
        screen.blit(F_SMALL.render("TEMP", False, WHITE), (x + tw + 20, yt))
        yt += 22
        for i, v in enumerate(row):
            ch = int(max(3, (F_SMALL.get_height() - 2) * (v / 100)))
            pygame.draw.rect(screen, (10, 50, 60), (x + i * (bw + gap), yt, bw, F_SMALL.get_height() - 2), 1)
            if ch > 0:
                pygame.draw.rect(screen, CYAN, (x + i * (bw + gap) + 1, yt + F_SMALL.get_height() - 2 - ch, bw - 2, ch))
        temp_s = F_SMALL.render(f"{SYS.temp:5.1f}C" if SYS.temp else " n/a", False, WHITE)
        screen.blit(temp_s, (x + tw + 20, yt + 4))
        y = yt + F_SMALL.get_height() + 14
    y += 4
    screen.blit(F_SMALL.render(f"LOAD : {SYS.load[0]:.2f} {SYS.load[1]:.2f} {SYS.load[2]:.2f}", False, WHITE), (x, y))
    y += 22
    screen.blit(F_SMALL.render(f"UPTIME : {fmt_uptime(SYS.uptime)}", False, WHITE), (x, y))
    y += 22
    topname = truncate(SYS.top[0], 20)
    screen.blit(F_SMALL.render(f"TOP : {topname} ({SYS.top[1]:.1f}%)", False, WHITE), (x, y))
    y += 32
    if SYS.net:
        screen.blit(F_MID.render("[ NETFLOW ]", False, CYAN), (x, y))
        y += F_MID.get_height() + 8
        for iface, (rx, tx) in list(SYS.net.items())[:2]:
            y = stat_bar(screen, x, y, f"{iface} RX {niceval(rx)}/s", min(rx / (50 * 10**6), 1.0), width=230, label_color=WHITE)
            y = stat_bar(screen, x, y, f"{iface} TX {niceval(tx)}/s", min(tx / (50 * 10**6), 1.0), width=230, label_color=WHITE)
    if SYS.disk:
        screen.blit(F_MID.render("[ STORAGE ]", False, CYAN), (x, y))
        y += F_MID.get_height() + 8
        for path, used in SYS.disk[:3]:
            frac = 0.0
            try:
                with open("/proc/mounts") as f:
                    pass
                out = subprocess.run(["df", "-BG", path], capture_output=True, text=True).stdout.splitlines()[-1].split()
                if len(out) >= 5 and out[4].endswith("%"):
                    frac = int(out[4].rstrip("%")) / 100
            except Exception:
                pass
            line = f"{path:<10} {used}"
            screen.blit(F_SMALL.render(line, False, WHITE), (x, y))
            y += 20
    return y


def truncate(s, n):
    return s if len(s) <= n else s[:n-1] + "…"


def draw_feed(screen, lines, y_top, y_bot):
    line_h = F_SMALL.get_height() + 3
    max_rows = (y_bot - y_top) // line_h
    visible = lines[-max_rows:]
    max_w = WIDTH - 34 - 28
    y = y_bot - line_h
    for item in reversed(visible):
        txt, color = format_line(item)
        segs = wrap_text(txt, F_SMALL, max_w)
        for i, seg in enumerate(segs):
            s = F_SMALL.render(seg, False, color)
            screen.blit(s, (34, y))
            if i < len(segs) - 1:
                y -= line_h
        y -= line_h
    return y


GLITCH_AT = time.time() + random.uniform(3, 9)
GLITCH_MODE = False
GLITCH_TTL = 0

import pygame as _pg


def apply_glitch(screen, now):
    global GLITCH_AT, GLITCH_MODE, GLITCH_TTL
    if not GLITCH_MODE:
        if now >= GLITCH_AT:
            GLITCH_MODE = True
            GLITCH_TTL = now + random.uniform(0.05, 0.25)
            GLITCH_AT = now + random.uniform(4, 12)
        return
    if now >= GLITCH_TTL:
        GLITCH_MODE = False
        return
    if random.random() < 0.5:
        return
    r = random.random()
    pick = random.random()
    if pick < 0.6:
        row_h = random.randint(2, 14)
        ry = random.randint(0, HEIGHT - row_h)
        strip_w = random.randint(200, WIDTH)
        sx = random.randint(0, max(0, WIDTH - strip_w))
        strip = screen.subsurface((sx, ry, strip_w, row_h)).copy()
        ox = random.choice([-80, -50, -20, 10, 20, 40, 60])
        nx = sx + ox
        if nx < 0:
            nx = 0
        if nx + strip_w > WIDTH:
            nx = WIDTH - strip_w
        screen.blit(strip, (nx, ry))
    elif pick < 0.85:
        sw2 = random.randint(80, 300)
        sx = random.randint(0, max(0, WIDTH - sw2))
        strip = screen.subsurface((sx, 0, sw2, HEIGHT)).copy()
        ox = random.randint(-12, 12)
        nx = sx + ox
        if nx < 0:
            nx = 0
        if nx + sw2 > WIDTH:
            nx = WIDTH - sw2
        screen.blit(strip, (nx, 0))
    else:
        col = random.choice([CYAN, RED, (150, 255, 0)])
        for _ in range(random.randint(3, 9)):
            x = random.randint(0, WIDTH)
            y = random.randint(0, HEIGHT)
            _pg.draw.line(screen, col, (x, y), (x + random.randint(5, 40), y), 1)


def main():
    stop = threading.Event()
    feed_q = queue.Queue(maxsize=2000)
    threads = [
        threading.Thread(target=journal_feeder, args=(feed_q, stop), daemon=True),
        threading.Thread(target=auth_feeder, args=(feed_q, stop), daemon=True),
        threading.Thread(target=narrator, args=(feed_q, stop), daemon=True),
    ]
    for t in threads:
        t.start()
    feed = Feed(feed_q)
    scanlines = make_scanlines(WIDTH, HEIGHT)
    stats_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    stats_overlay.set_alpha(150)
    SYS.refresh()
    last_stat = time.time()
    frame = 0
    speedup_until = 0

    while True:
        now = time.time()
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                stop.set()
                pygame.quit()
                sys.exit(0)
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                stop.set()
                pygame.quit()
                sys.exit(0)

        if now - last_stat > 1.0:
            SYS.refresh()
            last_stat = now

        feed.drain()
        if now < speedup_until:
            feed.drain()

        disp.fill(BLACK)

        stats_overlay.fill((0, 0, 0, 0))
        draw_stats(stats_overlay, WIDTH - 360, 14)
        draw_feed(disp, feed.lines(), 8, HEIGHT - 10)
        disp.blit(stats_overlay, (0, 0))

        disp.blit(scanlines, (0, 0))
        apply_glitch(disp, now)

        pygame.display.flip()
        frame += 1
        clock.tick(25)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pygame.quit()
        sys.exit(0)