# Changelog

All notable changes to this project are recorded here with the date and the
files affected.

## 2026-09-20 — Wallpaper failed to start / wrong size / off-screen

**Symptom:** `start-wall` launched the wallpaper at `1920x1080` while the X
screen is `1280x720`, so the window was oversized, placed off-screen
(`+1249+35`), kept as `NORMAL` (not `DESKTOP`) by the WM, and effectively
invisible. `.wall.log` also showed `ValueError: subsurface rectangle outside
surface area` in `apply_glitch` (stale, from an older build) and xwinwrap
`BadMatch`/`BadValue` `X_CreateWindow` errors.

**Files changed:**
- `start-wall`
- `wall.py`

**What changed and why:**
- `start-wall`: size detection now tries, in order, (1) xrandr primary output,
  (2) any connected xrandr output, (3) `xwininfo -root` geometry, (4)
  `xdpyinfo` dimensions. It only falls back to `1920x1080` (with a warning) if
  every method fails. Previously a failed `xrandr` parse hardcoded `1920x1080`,
  which was wrong for non-1920x1080 displays and caused the off-screen/oversized
  window.
- `start-wall`: launch wrapped in a 3-attempt retry loop to survive the
  intermittent xwinwrap `BadWindow` race (parent window GC'd before SDL set up).
- `start-wall`: `-fs` removed from xwinwrap (it conflicted with explicit `-g`
  geometry on non-fullscreen-equivalent sizes, producing the `BadMatch`
  `X_CreateWindow` errors) and `_NET_WM_WINDOW_TYPE=DESKTOP` is applied to the
  GHOST window so KWin pins it behind the desktop.
- `wall.py`: when no `--size` is passed, the real screen size is now queried via
  `pygame.display.Info()` instead of hardcoding `1920x1080`, so the renderer
  always matches the display.
