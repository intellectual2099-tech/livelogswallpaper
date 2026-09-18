# GHOST OPS TERMINAL

Live desktop wallpaper that streams your system logs in a terminal-style feed,
plus a translucent live system snapshot panel.

- **Linux**: journald + auth logs, embedded with `xwinwrap`
- **Windows**: Event Log (System / Application / Security), embedded behind the desktop icons (WorkerW trick)
- **Android**: planned (native Live Wallpaper app)

## Colors

- Green — info / normal log lines
- Purple — security & auth events (Logon, sudo, auth, access denied)
- Red — errors / critical / denied / timeouts
- Yellow — warnings
- Snapshot panel — cyan titles, white data, no green

## Linux

Requires: `python3`, `pygame`, an X11 session (Wayland needs Xwayland), and `xwinwrap`.

```bash
sudo apt install python3-pygame         # or: pip install pygame
# build xwinwrap and place it here as ./xwinwrap
./start-wall              # set as wallpaper
./start-wall -p           # preview as a normal window
./start-wall stop         # stop it
```

## Windows (win/)

Requires Python 3.10+ with `pygame` and PyInstaller (installed by the build script).

```bat
cd win
build.bat                # builds dist\GHOSTOPS.exe
start.bat                # runs it embedded as your wallpaper
```

Or run straight from source:

```bat
python win\wall_win.py --preview   # normal window
python win\wall_win.py --embed     # desktop wallpaper (behind icons)
```

Press `Esc` (or `q` in preview) to quit.

## Layout

- Left: live wrapped log feed, full screen height
- Top-right: translucent `[ SYSTEM SNAPSHOT ]` (CPU, memory, uptime, top proc, disk)

## License

MIT — see LICENSE.