#!/usr/bin/env python3
"""GHOST OPS TERMINAL - Windows live desktop log wallpaper

Poll the Windows Event Log and draw the stream, plus a live system
snapshot (CPU / memory / disk). Runs as a normal window (--preview) or
embedded into the desktop underneath the icons (--embed).
"""
import argparse
import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
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

LEVEL_COLORS = {1: RED, 2: RED, 3: YELLOW, 4: GREEN, 5: DIM}
LOG_NAMES = ["System", "Application", "Security"]
POLL_INTERVAL = 2.0

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="run as a normal window")
    ap.add_argument("--embed", action="store_true", help="embed into the desktop (under icons)")
    ap.add_argument("--size", default="", help="explicit width x height")
    return ap.parse_args()


ARGS = parse_args()

FONT_CANDIDATES = [
    r"C:/Windows/Fonts/consola.ttf",
    r"C:/Windows/Fonts/cour.ttf",
    r"C:/Windows/Fonts/DejaVuSansMono.ttf",
]
FONT_PATH = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)

if ARGS.size and "x" in ARGS.size:
    try:
        WIDTH, HEIGHT = (int(v) for v in ARGS.size.lower().split("x")[:2])
    except Exception:
        WIDTH, HEIGHT = 1280, 720
elif not ARGS.preview:
    WIDTH = user32.GetSystemMetrics(0)
    HEIGHT = user32.GetSystemMetrics(1)
else:
    WIDTH, HEIGHT = 1280, 720

try:
    import pygame
except ImportError:
    print("[!] pygame is required:  python -m pip install pygame")
    sys.exit(1)

pygame.init()
flags = 0 if ARGS.preview else pygame.NOFRAME
disp = pygame.display.set_mode((WIDTH, HEIGHT), flags)
WIDTH, HEIGHT = disp.get_size()
pygame.display.set_caption("GHOST OPS TERMINAL")
clock = pygame.time.Clock()


def font(size):
    return pygame.font.Font(FONT_PATH, size)


F_SMALL = font(13)
F_MID = font(16)


def niceval(n):
    for i, unit in enumerate(["B", "K", "M", "G", "T"]):
        if n < 1024 or i == 4:
            return f"{n:.1f}{unit}" if i else f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


def human_time(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def truncate(s, n):
    return s if len(s) <= n else s[: n - 1] + "..."


def wrap_text(txt, fnt, max_w):
    out = []
    cur = ""
    for ch in txt:
        if fnt.size(cur + ch)[0] > max_w:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


# --------------------------------------------------------------------------
# Windows system stats (no third-party deps)
# --------------------------------------------------------------------------

class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]

    def ticks(self):
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class SysStats:
    def __init__(self):
        self.cpu = 0.0
        self.mem_used = 0
        self.mem_total = 0
        self.swap_used = 0
        self.swap_total = 0
        self.uptime = 0
        self.cpu_cores = []
        self.temp = None
        self.top_proc = ("", 0.0)
        self.disk = []
        self._prev_idle = None
        self._prev_kernel = None
        self._prev_user = None
        self.refresh()

    def refresh(self):
        try:
            kernel = FILETIME()
            idle = FILETIME()
            user = FILETIME()
            if kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                tot = (kernel.ticks() - self._prev_kernel) + (
                    user.ticks() - self._prev_user
                ) if self._prev_kernel is not None else 0
                idle_d = idle.ticks() - self._prev_idle if self._prev_idle is not None else 0
                if tot > 0:
                    self.cpu = max(0.0, min(100.0, 100 * (1 - idle_d / tot)))
                self._prev_idle, self._prev_kernel, self._prev_user = (
                    idle.ticks(),
                    kernel.ticks(),
                    user.ticks(),
                )
        except Exception:
            pass
        try:
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(m)
            if kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                self.mem_total = m.ullTotalPhys
                self.mem_used = m.ullTotalPhys - m.ullAvailPhys
                self.swap_total = m.ullTotalPageFile
                self.swap_used = m.ullTotalPageFile - m.ullAvailPageFile
        except Exception:
            pass
        try:
            self.uptime = kernel32.GetTickCount64() // 1000
            self.cpu_cores = self._core_load()
        except Exception:
            pass
        try:
            self._refresh_top_and_disk()
        except Exception:
            pass

    def _core_load(self):
        try:
            out = subprocess.run(
                ["wmic", "cpu", "get", "LoadPercentage", "/value"],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
            m = re.findall(r"LoadPercentage=(\d+)", out)
            if m:
                return [float(m[0])]
        except Exception:
            pass
        return []

    def _refresh_top_and_disk(self):
        try:
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            rows = []
            for ln in out.splitlines():
                parts = ln.split('","')
                if len(parts) < 5:
                    continue
                name = parts[0].strip('"')
                try:
                    mem = int(parts[4].replace(",", "").strip('"'))
                except ValueError:
                    continue
                rows.append((name, mem))
            if rows:
                rows.sort(key=lambda r: r[1], reverse=True)
                total = sum(r[1] for r in rows) or 1
                self.top_proc = (rows[0][0], 100 * rows[0][1] / total)
        except Exception:
            pass
        self.disk = []
        try:
            drives = kernel32.GetLogicalDrives()
            for i in range(26):
                if not drives & (1 << i):
                    continue
                root = f"{chr(65 + i)}:\\"
                free = ctypes.c_ulonglong(0)
                total = ctypes.c_ulonglong(0)
                if kernel32.GetDiskFreeSpaceExW(
                    root, ctypes.byref(ctypes.c_ulonglong(0)),
                    ctypes.byref(total), ctypes.byref(free),
                ) and total.value:
                    self.disk.append((root + " ", niceval(total.value - free.value)))
        except Exception:
            pass


# --------------------------------------------------------------------------
# Event Log feed
# --------------------------------------------------------------------------

class Feed:
    def __init__(self, q):
        self.q = q
        self.buf = deque(maxlen=400)
        self._lock = threading.Lock()

    def drain(self):
        while True:
            try:
                item = self.q.get_nowait()
                with self._lock:
                    self.buf.append(item)
            except queue.Empty:
                break

    def lines(self):
        with self._lock:
            return list(self.buf)


def wevtool(channel, count):
    return subprocess.run(
        ["wevtutil", "qe", channel, "/c:%d" % count, "/rd:true", "/f:xml"],
        capture_output=True,
        text=True,
        timeout=8,
    ).stdout


def parse_events(raw, channel, seen):
    if not raw.strip():
        return []
    try:
        root = ET.fromstring("<Events>" + raw.strip() + "</Events>")
    except ET.ParseError:
        return []
    items = []
    for ev in root.iter():
        if not ev.tag.rpartition("}")[2] == "Event":
            continue
        data = {"channel": channel, "level": 4, "time": 0, "text": "", "provider": "", "rid": None}
        try:
            sys_ = None
            for child in ev:
                if child.tag.rpartition("}")[2] == "System":
                    sys_ = child
            if sys_ is not None:
                for f in sys_.iter():
                    tag = f.tag.rpartition("}")[2]
                    if tag == "EventRecordID":
                        data["rid"] = f.text.strip() if f.text else None
                    elif tag == "Level":
                        try:
                            data["level"] = int(f.text) if f.text else 4
                        except ValueError:
                            data["level"] = 4
                    elif tag == "TimeCreated":
                        data["time"] = f.get("SystemTime", "")
                    elif tag == "Provider":
                        data["provider"] = f.get("Name", "")
            for f in ev.iter():
                tag = f.tag.rpartition("}")[2]
                if tag == "Message" and f.text:
                    data["text"] = " ".join(f.text.split())
        except Exception:
            continue
        t = data["time"]
        try:
            from datetime import datetime, timezone
            stamp = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        except Exception:
            stamp = time.time()
        if data["rid"] is not None:
            key = (channel, data["rid"])
            if key in seen:
                continue
            seen.add(key)
        msg = data["text"] or f"({channel} event {data['rid']})"
        tag = data["provider"] or channel
        lvl = data["level"]
        if channel in ("Security",) or any(
            s in msg.lower() for s in ("logon", "login", "auth", "sudo", "access denied")
        ):
            lvl = 5
        items.append((stamp, tag, msg, lvl))
    return items


def log_feeder(q, stop):
    seen = set()
    while not stop.is_set():
        for channel in LOG_NAMES:
            try:
                raw = wevtool(channel, 15)
                items = parse_events(raw, channel, seen)
                for it in reversed(items):
                    try:
                        q.put(it, timeout=0.5)
                    except queue.Full:
                        break
            except Exception:
                pass
        stop.wait(POLL_INTERVAL)


def add_sample_lines(q, stop):
    """Short fake-first-boot burst so the screen is not empty on first login session."""
    time.sleep(1)
    for ln in [
        ("WALLPAPER", "GHOST OPS TERMINAL online", 4),
        ("CORE", "watching System/Application/Security event logs", 4),
    ]:
        try:
            q.put((time.time(), ln[0], ln[1], ln[2]), timeout=0.5)
        except queue.Full:
            break


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def draw_feed(screen, lines, y_top, y_bot):
    line_h = F_SMALL.get_height() + 3
    max_rows = (y_bot - y_top) // line_h
    visible = lines[-max_rows:]
    max_w = WIDTH - 34 - 28
    y = y_bot - line_h
    for item in reversed(visible):
        t, tag, msg, lvl = item
        txt = f"  [{human_time(t)}] {tag}: {msg}"
        color = LEVEL_COLORS.get(lvl, GREEN)
        segs = wrap_text(txt, F_SMALL, max_w)
        for i, seg in enumerate(segs):
            s = F_SMALL.render(seg, False, color)
            screen.blit(s, (34, y))
            if i < len(segs) - 1:
                y -= line_h
        y -= line_h
    return y


def fmt_uptime(sec):
    d, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def stat_bar(screen, x, y, label, frac, width=300):
    screen.blit(F_SMALL.render(label, False, WHITE), (x, y))
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
    return y + 45


def draw_stats(screen, x, y, SYS):
    s = F_MID.render("[ SYSTEM SNAPSHOT ]", False, CYAN)
    screen.blit(s, (x, y))
    y += F_MID.get_height() + 10
    y = stat_bar(screen, x, y, f"CPU {SYS.cpu:5.1f}%", SYS.cpu / 100)
    y = stat_bar(screen, x, y, f"MEM {niceval(SYS.mem_used)}/{niceval(SYS.mem_total)}",
                 SYS.mem_used / SYS.mem_total if SYS.mem_total else 0)
    if SYS.swap_total:
        y = stat_bar(screen, x, y, f"PAGEFILE {niceval(SYS.swap_used)}/{niceval(SYS.swap_total)}",
                     SYS.swap_used / SYS.swap_total if SYS.swap_total else 0)
    y += 4
    screen.blit(F_SMALL.render(f"UPTIME : {fmt_uptime(SYS.uptime)}", False, WHITE), (x, y))
    y += 22
    topname = truncate(SYS.top_proc[0], 22)
    screen.blit(F_SMALL.render(f"TOP : {topname} ({SYS.top_proc[1]:.1f}%)", False, WHITE), (x, y))
    y += 26
    cores = SYS.cpu_cores
    if cores:
        n = len(cores)
        bw = 22
        gap = 6
        tw = n * (bw + gap)
        screen.blit(F_SMALL.render("CORE", False, WHITE), (x, y))
        yt = y + 22
        for i, v in enumerate(cores[:6]):
            ch = int(max(3, (F_SMALL.get_height() - 2) * (v / 100)))
            pygame.draw.rect(screen, (10, 50, 60), (x + i * (bw + gap), yt, bw, F_SMALL.get_height() - 2), 1)
            if ch > 0:
                pygame.draw.rect(screen, CYAN, (x + i * (bw + gap) + 1, yt + F_SMALL.get_height() - 2 - ch, bw - 2, ch))
        y = yt + F_SMALL.get_height() + 12
    if SYS.disk:
        screen.blit(F_MID.render("[ STORAGE ]", False, CYAN), (x, y))
        y += F_MID.get_height() + 8
        for path, used in SYS.disk[:4]:
            screen.blit(F_SMALL.render(f"{path:<4} {used}", False, WHITE), (x, y))
            y += 20
    return y


# --------------------------------------------------------------------------
# Wallpaper embed (WorkerW trick)
# --------------------------------------------------------------------------

def embed_into_desktop(hwnd):
    def find_workerw():
        progman = user32.FindWindowW("Progman", None)
        if progman:
            result = ctypes.c_ulonglong()
            user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(result))
        workerw = None
        while True:
            workerw = user32.FindWindowExW(None, workerw, "WorkerW", None)
            if not workerw:
                break
            if user32.FindWindowExW(workerw, None, "SHELLDLL_DefView", None):
                return workerw
        return user32.FindWindowExW(progman, None, "WorkerW", None) if progman else None

    workerw = find_workerw()
    if not workerw:
        raise RuntimeError("WorkerW desktop window not found")
    GWL_STYLE, GWL_EXSTYLE = -16, -20
    WS_CHILD, WS_POPUP, WS_VISIBLE = 0x40000000, 0x80000000, 0x10000000
    WS_EX_NOACTIVATE = 0x08000000
    user32.SetWindowLongPtrW.restype = ctypes.c_longlong
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    style = (style & ~WS_POPUP) | WS_CHILD | WS_VISIBLE
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
    if not user32.SetParent(hwnd, workerw):
        raise RuntimeError("SetParent failed")
    user32.SetWindowPos(hwnd, 0, 0, 0, WIDTH, HEIGHT,
                        0x0004 | 0x0010 | 0x0020 | 0x0040)  # NOZORDER|NOACTIVATE|FRAMECHANGED|SHOWWINDOW
    user32.ShowWindow(hwnd, 5)


def main():
    stop = threading.Event()
    q = queue.Queue(maxsize=2000)
    threads = [
        threading.Thread(target=log_feeder, args=(q, stop), daemon=True),
        threading.Thread(target=add_sample_lines, args=(q, stop), daemon=True),
    ]
    for t in threads:
        t.start()
    feed = Feed(q)
    SYS = SysStats()
    stats_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    stats_overlay.set_alpha(150)
    last_stat = time.time()
    frame = 0

    if ARGS.embed and sys.platform == "win32":
        try:
            embed_into_desktop(pygame.display.get_wm_info()["window"])
        except Exception as exc:
            print(f"[!] embed failed, running as normal window: {exc}")

    while True:
        now = time.time()
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                stop.set()
                pygame.quit()
                sys.exit(0)
            if (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE) or (ARGS.preview and e.type == pygame.KEYDOWN and e.key == pygame.K_q):
                stop.set()
                pygame.quit()
                sys.exit(0)

        if now - last_stat > 1.0:
            SYS.refresh()
            last_stat = now

        feed.drain()
        disp.fill(BLACK)
        draw_feed(disp, feed.lines(), 8, HEIGHT - 10)
        stats_overlay.fill((0, 0, 0, 0))
        draw_stats(stats_overlay, WIDTH - 360, 14, SYS)
        disp.blit(stats_overlay, (0, 0))
        pygame.display.flip()
        frame += 1
        clock.tick(25)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pygame.quit()