#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pillow",
#   "scikit-image",
#   "pyobjc-framework-Quartz; sys_platform == 'darwin'",
# ]
# ///
"""Trace a signature image into pen strokes and replay them as a mouse drag.

Usage:
  sig_replay.py SIGNATURE.png                       # pick the pad corners, then draw
  sig_replay.py SIGNATURE.png --preview             # look at the trace first, then draw
  sig_replay.py SIGNATURE.png --box 400,500,900,650 # reuse corners from a previous run
  sig_replay.py SIGNATURE.png --debug               # see how the image is trimmed and traced, no drawing
  sig_replay.py SIGNATURE.png --svg demo.svg        # write an animated SVG of the strokes, no drawing

Writes nothing to disk except a temporary preview image, deleted again in the same run
(and the --svg file, when asked for).

Abort mid-draw by grabbing the mouse and moving it: any deviation from where the
script put the cursor releases the button and stops.

Needs your terminal app enabled under System Settings -> Privacy & Security ->
Accessibility (then quit and reopen the terminal). See README.md for full setup.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

import numpy as np
from PIL import Image, ImageDraw
from skimage.filters import threshold_otsu
from skimage.morphology import remove_small_holes, remove_small_objects, skeletonize

Point = tuple[float, float]  # (x, y) in image pixels

ORTHO = [(-1, 0), (1, 0), (0, -1), (0, 1)]
DIAG = [(-1, -1), (-1, 1), (1, -1), (1, 1)]


# ---------------------------------------------------------------- image -> mask
def ink_mask(path: str, threshold: float | None) -> tuple[np.ndarray, str]:
    """Full-size ink mask of the image, plus a note on how ink was told from background."""
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        rgba = np.asarray(img).astype(float)
        alpha = rgba[..., 3]
        if alpha.min() < 250:  # transparent background: ink is what's opaque *and* dark-ish
            lum = rgba[..., :3].mean(axis=2)
            return _clean((alpha > 128) & (lum < 230)), "transparent background: opaque pixels are ink"
        img = img.convert("RGB")
    gray = np.asarray(img.convert("L")).astype(float)
    t = threshold if threshold is not None else threshold_otsu(gray)
    how = f"brightness below {t:.0f} is ink ({'--threshold' if threshold is not None else 'automatic'})"
    return _clean(gray < t), how


def _clean(mask: np.ndarray) -> np.ndarray:
    mask = remove_small_objects(mask, max_size=8)
    return remove_small_holes(mask, max_size=12)  # pinholes inside thick strokes become fake loops


def ink_box(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        sys.exit("No ink found in image (try --threshold).")
    return xs.min(), ys.min(), xs.max(), ys.max()


def load_mask(path: str, threshold: float | None) -> np.ndarray:
    """Ink mask cropped to the ink: surrounding background is dropped before scaling."""
    mask, _ = ink_mask(path, threshold)
    x0, y0, x1, y1 = ink_box(mask)
    return mask[y0 : y1 + 1, x0 : x1 + 1]


# ------------------------------------------------------------ skeleton -> paths
def trace(mask: np.ndarray) -> list[list[Point]]:
    skel = skeletonize(mask)
    pix = set(zip(*np.nonzero(skel)))  # (row, col)

    def neighbours(p):
        r, c = p
        out = [(r + dr, c + dc) for dr, dc in ORTHO if (r + dr, c + dc) in pix]
        for dr, dc in DIAG:
            q = (r + dr, c + dc)
            # skip a diagonal step when an orthogonal path already joins the two
            if q in pix and (r + dr, c) not in pix and (r, c + dc) not in pix:
                out.append(q)
        return out

    adj = {p: neighbours(p) for p in pix}
    used: set[frozenset] = set()

    def free(p):
        return [q for q in adj[p] if frozenset((p, q)) not in used]

    def walk(start):
        path, cur, d = [start], start, None
        while True:
            cands = free(cur)
            if not cands:
                return path
            if d is None:
                nxt = min(cands, key=lambda q: (abs(q[0] - cur[0]) + abs(q[1] - cur[1]), q[1]))
            else:  # keep going the straightest way through junctions
                nxt = max(cands, key=lambda q: (q[0] - cur[0]) * d[0] + (q[1] - cur[1]) * d[1]
                          / (math.hypot(q[0] - cur[0], q[1] - cur[1]) or 1))
            used.add(frozenset((cur, nxt)))
            nd = (nxt[0] - cur[0], nxt[1] - cur[1])
            d = nd if d is None else (0.6 * d[0] + 0.4 * nd[0], 0.6 * d[1] + 0.4 * nd[1])
            path.append(nxt)
            cur = nxt

    # Isolated single pixels (dots) have no edges; draw them as tiny strokes.
    strokes = [[p] for p in pix if not adj[p]]
    last_end = None
    while True:
        if last_end is not None and free(last_end):
            start = last_end  # continue from where the pen is
        else:
            live = [p for p in adj if free(p)]
            if not live:
                break
            odd = [p for p in live if len(free(p)) % 2 == 1]
            start = min(odd or live, key=lambda p: (p[1], p[0]))  # leftmost first
        path = walk(start)
        strokes.append(path)
        last_end = path[-1]

    return [[(c, r) for r, c in s] for s in strokes]  # -> (x, y)


def prune_spurs(strokes, mask, skel_len):
    """Drop short branches that hang off another stroke (skeleton artefacts at stroke ends)."""
    width = mask.sum() / max(skel_len, 1)
    owner: dict = {}
    for i, s in enumerate(strokes):
        for p in s:
            owner.setdefault(p, set()).add(i)

    def touches_other(i, p):
        x, y = p
        return any(owner.get((x + dx, y + dy), {i}) - {i}
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    out = []
    for i, s in enumerate(strokes):
        length = sum(math.dist(a, b) for a, b in zip(s, s[1:]))
        if length < max(width * 1.2, 3) and (touches_other(i, s[0]) or touches_other(i, s[-1])):
            continue  # stub hanging off a real stroke; a free-standing dot survives
        out.append(s)
    return out


def smooth(s: list[Point], sigma=1.6) -> list[Point]:
    if len(s) < 5:
        return s
    a = np.array(s, dtype=float)
    k = np.arange(-3 * int(sigma + 1), 3 * int(sigma + 1) + 1)
    w = np.exp(-(k**2) / (2 * sigma**2))
    w /= w.sum()
    pad = len(k) // 2
    ap = np.pad(a, ((pad, pad), (0, 0)), mode="edge")
    sm = np.stack([np.convolve(ap[:, i], w, mode="valid") for i in range(2)], axis=1)
    sm[0], sm[-1] = a[0], a[-1]
    return [tuple(p) for p in sm]


def rdp(s: list[Point], eps=0.5) -> list[Point]:
    if len(s) < 3:
        return s
    a, b = np.array(s[0]), np.array(s[-1])
    ab = b - a
    n = np.linalg.norm(ab)
    pts = np.array(s)
    if n == 0:
        d = np.linalg.norm(pts - a, axis=1)
    else:
        d = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / n
    i = int(d.argmax())
    if d[i] > eps:
        return rdp(s[: i + 1], eps)[:-1] + rdp(s[i:], eps)
    return [s[0], s[-1]]


def build_strokes(path: str, threshold: float | None):
    mask = load_mask(path, threshold)
    raw = trace(mask)
    skel_len = sum(max(len(s) - 1, 1) for s in raw)
    strokes = prune_spurs(raw, mask, skel_len)
    strokes = [rdp(smooth(s)) for s in strokes]
    return mask, strokes


# --------------------------------------------------------------------- preview
def show_image(img: Image.Image, prompt: str):
    """Open an image in the system viewer from a temp file; delete it once the user is done."""
    import os
    import subprocess
    import tempfile

    fd, path = tempfile.mkstemp(prefix="sig-replay-", suffix=".png")
    os.close(fd)
    try:
        img.save(path)
        opener = {"darwin": ["open"], "win32": ["cmd", "/c", "start", ""]}.get(sys.platform, ["xdg-open"])
        subprocess.run([*opener, path], check=False)
        input(prompt)
    finally:
        os.remove(path)


def stroke_image(mask, strokes, scale=1.0):
    """Traced pen paths over a faint copy of the ink; colour + number = stroke and drawing order."""
    h, w = mask.shape
    W, H = max(1, round(w * scale)), max(1, round(h * scale))
    img = Image.fromarray(np.where(mask, 225, 255).astype(np.uint8)).convert("RGB").resize((W, H), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    font = _font(18)
    lw = 3
    for i, s in enumerate(strokes):
        col = tuple(int(255 * v) for v in _hsv((i * 0.61803) % 1, 0.85, 0.75))
        pts = [(x * scale, y * scale) for x, y in s]
        if len(pts) == 1:
            x, y = pts[0]
            d.ellipse((x - lw, y - lw, x + lw, y + lw), fill=col)
        else:
            d.line(pts, fill=col, width=lw, joint="curve")
        d.text((pts[0][0] + 4, pts[0][1] - 4), str(i + 1), fill=col, font=font, anchor="ls")
    return img


def debug_sheet(path, threshold, mask, strokes, width=1400):
    """One image showing each step: the file as saved with its ink box, the ink cropped out
    of it, and the pen paths traced from that ink (what the mouse will draw)."""
    full, how = ink_mask(path, threshold)
    x0, y0, x1, y1 = ink_box(full)
    src = Image.open(path).convert("RGBA")
    W, H = src.size
    # show transparency as a checkerboard so it's visible
    yy, xx = np.mgrid[0:H, 0:W]
    checker = np.where(((yy // 16 + xx // 16) % 2) == 0, 255, 232).astype(np.uint8)
    base = Image.fromarray(checker).convert("RGBA")
    base.alpha_composite(src)
    base = base.convert("RGB")

    k = min(1.0, (width - 40) / W)
    font = _font(22)
    red = (220, 40, 40)

    def panel(img, title, detail, box=None):
        if img.width != round(mask.shape[1] * k) or box is not None:  # not drawn at sheet size yet
            img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
        d = ImageDraw.Draw(img)
        if box is not None:
            d.rectangle([round(v * k) for v in box], outline=red, width=3)
        else:
            d.rectangle((0, 0, img.width - 1, img.height - 1), outline=red, width=3)
        out = Image.new("RGB", (width, img.height + 90), "white")
        dd = ImageDraw.Draw(out)
        dd.text((20, 14), title, fill="black", font=_font(26))
        dd.text((20, 50), detail, fill=(90, 90, 90), font=font)
        out.paste(img, (20, 84))
        return out

    p1 = panel(base, f"1. {path.rsplit('/', 1)[-1]} as saved: {W} x {H} px",
               f"Red = the ink's bounding box. Trimmed away: left {x0}, right {W - 1 - x1}, "
               f"top {y0}, bottom {H - 1 - y1} px", box=(x0, y0, x1, y1))
    crop = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8)).convert("RGB")
    p2 = panel(crop, f"2. Ink found and trimmed: {mask.shape[1]} x {mask.shape[0]} px",
               f"{how}. This area is scaled to fit the box you mark.")
    p3 = panel(stroke_image(mask, strokes, scale=k), f"3. Traced pen paths: {len(strokes)} strokes",
               "What the mouse draws, along the middle of the ink. Colour + number = stroke and drawing order.")
    gap = 16
    sheet = Image.new("RGB", (width, p1.height + p2.height + p3.height + 2 * gap), (225, 225, 225))
    y = 0
    for im in (p1, p2, p3):
        sheet.paste(im, (0, y))
        y += im.height + gap
    return sheet


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _bezier_path(pts, sx, sy):
    """Smooth path through the points (Catmull-Rom converted to cubic Bezier)."""
    P = [(x * sx, y * sy) for x, y in pts]
    if len(P) == 1:
        x, y = P[0]
        return f"M{x:.1f} {y:.1f}l0.1 0.1"
    d = [f"M{P[0][0]:.1f} {P[0][1]:.1f}"]
    for i in range(len(P) - 1):
        p0, p1, p2 = P[max(i - 1, 0)], P[i], P[i + 1]
        p3 = P[min(i + 2, len(P) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}")
    return "".join(d)


def write_svg(mask, strokes, out, width=720, speed=500.0, lift=0.12, hold=2.0, fade=0.4,
              label="A signature being drawn stroke by stroke"):
    """Animated SVG of the replay: each stroke draws itself in order (CSS keyframes on
    stroke-dashoffset), holds, fades and loops. Works inside a GitHub README <img>:
    scripting is off there, declarative animation isn't. prefers-reduced-motion shows the
    finished signature. Black ink on a white card reads the same in light and dark mode."""
    from xml.sax.saxutils import escape

    h, w = mask.shape
    pad = 12
    k = (width - 2 * pad) / w
    W, H = width, round(h * k + 2 * pad)
    ink_w = max(1.6, (mask.sum() / max(np.count_nonzero(skeletonize(mask)), 1)) * k * 0.8)

    lens = [sum(math.dist(a, b) for a, b in zip(s, s[1:])) * k for s in strokes]
    draw_t = [max(L / speed, 0.03) for L in lens]
    total = sum(draw_t) + lift * (len(strokes) - 1) + hold + fade
    T = total * 1000

    def pct(t):
        return f"{t / total * 100:.3f}%"

    css, paths, t = [], [], 0.0
    for i, (s, dt) in enumerate(zip(strokes, draw_t)):
        d = _bezier_path([(x + pad / k, y + pad / k) for x, y in s], k, k)
        css.append(f"#s{i}{{animation:d{i} {T:.0f}ms linear infinite}}"
                   f"@keyframes d{i}{{0%,{pct(t)}{{stroke-dashoffset:1}}{pct(t + dt)},100%{{stroke-dashoffset:0}}}}")
        paths.append(f'<path id="s{i}" pathLength="1" d="{d}"/>')
        t += dt + lift
    t_end = total - fade
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}">
  <style>
    path{{fill:none;stroke:#000;stroke-width:{ink_w:.2f};stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1 1;stroke-dashoffset:1}}
    g{{animation:f {T:.0f}ms linear infinite}}
    @keyframes f{{0%,{pct(t_end)}{{opacity:1}}100%{{opacity:0}}}}
    {"".join(css)}
    @media (prefers-reduced-motion:reduce){{g,path{{animation:none!important}}path{{stroke-dashoffset:0}}}}
  </style>
  <rect width="{W}" height="{H}" rx="8" fill="#fff"/>
  <g>
    {chr(10).join("    " + p for p in paths).strip()}
  </g>
</svg>
"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)


def _hsv(h, s, v):
    import colorsys
    return colorsys.hsv_to_rgb(h, s, v)


# ----------------------------------------------------------------- mouse (macOS)
class Mouse:
    def __init__(self):
        import Quartz as Q
        self.Q = Q
        self.down = False
        self.last = None

    def pos(self):
        loc = self.Q.CGEventGetLocation(self.Q.CGEventCreate(None))
        return loc.x, loc.y

    def _post(self, kind, x, y):
        Q = self.Q
        ev = Q.CGEventCreateMouseEvent(None, kind, (x, y), Q.kCGMouseButtonLeft)
        Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, 1)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)
        self.last = (x, y)

    def check_hijack(self, tol=25):
        if self.last is None:
            return
        x, y = self.pos()
        if math.dist((x, y), self.last) > tol:
            self.up(*self.last)
            raise KeyboardInterrupt("mouse moved by user")

    def can_post(self) -> bool:
        return bool(self.Q.CGPreflightPostEventAccess())

    def request_access(self):
        self.Q.CGRequestPostEventAccess()  # shows the system prompt / adds the app to the list

    def move(self, x, y):
        self._post(self.Q.kCGEventMouseMoved, x, y)

    def move_and_verify(self, x, y, timeout=0.5) -> bool:
        """Move the pointer and confirm it got there (it won't if macOS drops our events)."""
        self.move(x, y)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if math.dist(self.pos(), (x, y)) <= 2:
                return True
            time.sleep(0.01)
        return False

    def press(self, x, y):
        self._post(self.Q.kCGEventLeftMouseDown, x, y)
        self.down = True

    def drag(self, x, y):
        self._post(self.Q.kCGEventLeftMouseDragged, x, y)

    def up(self, x, y):
        self._post(self.Q.kCGEventLeftMouseUp, x, y)
        self.down = False


def watch_escape():
    """Stop the tool when Escape is pressed, wherever keyboard focus is (while drawing it's
    in the target app, not this terminal). Polls the key state; raises KeyboardInterrupt in
    the main thread, which releases the mouse button and exits."""
    import _thread
    import threading

    import Quartz as Q
    ESC = 53  # macOS virtual key code

    def poll():
        while True:
            if Q.CGEventSourceKeyState(Q.kCGEventSourceStateCombinedSessionState, ESC):
                print("\nEscape pressed.", flush=True)
                _thread.interrupt_main()
                return
            time.sleep(0.03)

    threading.Thread(target=poll, daemon=True).start()


def no_permission_msg(reason: str) -> str:
    import os
    app = {"iTerm.app": "iTerm", "Apple_Terminal": "Terminal", "ghostty": "Ghostty",
           "vscode": "Visual Studio Code"}.get(os.environ.get("TERM_PROGRAM", ""), "your terminal app")
    return (f"{reason}\n\n"
            f"How to allow it (opening the settings page for you now):\n"
            f"  1. System Settings -> Privacy & Security -> Accessibility.\n"
            f"  2. Find {app} in the list and switch it ON.\n"
            f"     Not listed? Click +, pick {app} in /Applications, then switch it on.\n"
            f"     Already on but still failing? Select it, click -, and add it again.\n"
            f"  3. Confirm with your password or Touch ID if asked.\n"
            f"  4. Quit {app} completely (Cmd-Q, not just close the window) and reopen it.\n"
            f"  5. Run this command again.\n"
            f"Toggle greyed out or switching itself off: the Mac's management (Jamf) blocks it;\n"
            f"use a Mac you administer instead.\n"
            f"Done signing? Switch {app} off again in the same place.")


def open_accessibility_settings():
    import subprocess
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                   check=False)


def countdown(msg, secs):
    for i in range(secs, 0, -1):
        print(f"\r{msg} {i}… ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * (len(msg) + 8) + "\r", end="")


def pick_point(mouse, label):
    """Show the live pointer position until Enter is pressed (the terminal keeps keyboard
    focus while the pointer hovers over another app)."""
    import threading

    stop = threading.Event()

    def live():
        while not stop.is_set():
            x, y = mouse.pos()
            print(f"\r  Hover over the {label}, press Enter (Esc quits).  pointer = {x:5.0f},{y:5.0f} ",
                  end="", flush=True)
            time.sleep(0.05)

    t = threading.Thread(target=live, daemon=True)
    t.start()
    input()
    stop.set()
    t.join()
    x, y = mouse.pos()
    print(f"  {label:<13}= {x:.0f},{y:.0f}")
    return x, y


def pick_box(mouse):
    print("Mark the signature area (keep this terminal focused; just move the mouse):")
    x1, y1 = pick_point(mouse, "TOP-LEFT")
    x2, y2 = pick_point(mouse, "BOTTOM-RIGHT")
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def width_speed_fn(mask, slow, fast):
    """Map ink thickness to pen speed: thick ink -> slow, thin -> fast.

    Velocity-based signature pads (e.g. signature_pad, used by many e-sign sites) draw
    thinner lines the faster the pointer moves, so this reproduces the original's
    thick/thin variation."""
    from scipy.ndimage import distance_transform_edt, maximum_filter

    thick = maximum_filter(distance_transform_edt(mask), size=5)  # local half-width of the ink
    vals = thick[mask]
    lo, hi = np.percentile(vals, 10), np.percentile(vals, 95)
    if hi - lo < 0.5:  # uniform line: nothing to vary
        return lambda p: math.sqrt(slow * fast)
    ls, lf = math.log(slow), math.log(fast)
    H, W = thick.shape

    def fn(p):
        x = min(max(int(round(p[0])), 0), W - 1)
        y = min(max(int(round(p[1])), 0), H - 1)
        r = min(max((thick[y, x] - lo) / (hi - lo), 0.0), 1.0)
        return math.exp(lf + r * (ls - lf))
    return fn


def draw(strokes, size, box, speed_fn, margin, lift_ms):
    w, h = size
    bx1, by1, bx2, by2 = box
    bw, bh = (bx2 - bx1) * (1 - 2 * margin), (by2 - by1) * (1 - 2 * margin)
    k = min(bw / w, bh / h)
    ox = bx1 + (bx2 - bx1 - w * k) / 2
    oy = by1 + (by2 - by1 - h * k) / 2

    def tf(p):
        return ox + p[0] * k, oy + p[1] * k

    mouse = Mouse()
    dt = 0.004   # target interval between drag events
    tau = 0.05   # speed changes ease in over ~50 ms, like a hand would

    def trace_stroke(img_pts):
        """Drag along the stroke, moving by real elapsed time so the speed is accurate."""
        scr = [tf(p) for p in img_pts]
        seg = [math.dist(a, b) for a, b in zip(scr, scr[1:])]
        total = sum(seg)
        i, base, pos = 0, 0.0, 0.0  # segment index, arc length at its start, arc position
        cur = speed_fn(img_pts[0])
        last = time.monotonic()
        while pos < total:
            time.sleep(dt)
            now = time.monotonic()
            el, last = now - last, now
            while i < len(seg) - 1 and base + seg[i] < pos:
                base += seg[i]
                i += 1
            t = (pos - base) / seg[i] if seg[i] else 0.0
            a, b = img_pts[i], img_pts[i + 1]
            target = speed_fn((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            cur += (target - cur) * min(1.0, el / tau)
            pos = min(total, pos + cur * el)
            while i < len(seg) - 1 and base + seg[i] < pos:
                base += seg[i]
                i += 1
            t = (pos - base) / seg[i] if seg[i] else 1.0
            a, b = scr[i], scr[i + 1]
            mouse.check_hijack()
            mouse.drag(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        return scr[-1]

    def settle(p):
        """Rest on the end point before lifting. Pads that sample on a timer (e.g. every
        16 ms) otherwise drop the last ~20-30 ms of movement, which erases the end of
        every stroke and all of a short, fast one."""
        for _ in range(2):
            time.sleep(0.018)
            mouse.drag(*p)
        time.sleep(0.006)

    pen = None  # screen position where the pen is down, or None
    try:
        for s in strokes:
            start = tf(s[0])
            if pen is None or math.dist(pen, start) > 1.5:
                if pen is not None:
                    settle(pen)
                    mouse.up(*pen)
                    time.sleep(lift_ms / 1000)
                if not mouse.move_and_verify(*start):
                    if pen is None:  # very first move: the pointer never budged
                        open_accessibility_settings()
                        sys.exit("\n" + no_permission_msg(
                            "The pointer didn't move when the script moved it, so macOS is "
                            "dropping its mouse events."))
                    raise KeyboardInterrupt("mouse moved by user")
                mouse.press(*start)
                time.sleep(0.03)
            if len(s) == 1:  # a dot
                mouse.drag(start[0] + 0.6, start[1] + 0.6)
                time.sleep(0.02)
                pen = start
            else:
                pen = trace_stroke(s)
        if pen is not None:
            settle(pen)
            mouse.up(*pen)
    except KeyboardInterrupt as e:
        if mouse.down and mouse.last:
            mouse.up(*mouse.last)
        sys.exit(f"\nAborted{': ' + str(e) if str(e) else ''}. Mouse button released.")


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--preview", action="store_true",
                    help="show how the image is trimmed and traced, then ask before drawing "
                         "(temporary image, deleted afterwards)")
    ap.add_argument("--debug", nargs="?", const="", metavar="OUT.png",
                    help="show how the image is trimmed and traced, then exit without drawing "
                         "(no mouse control; works on any OS). With a filename, save the sheet there instead")
    ap.add_argument("--svg", metavar="OUT.svg",
                    help="write an animated SVG of the strokes being drawn and exit (no mouse control)")
    ap.add_argument("--box", help="x1,y1,x2,y2 screen points of the signature area (skips picking)")
    ap.add_argument("--speed", type=float,
                    help="constant pen speed in points/s; turns off thickness-based speed")
    ap.add_argument("--speed-range", default="80,1000", metavar="SLOW,FAST",
                    help="points/s used for the thickest and thinnest ink (default 80,1000)")
    ap.add_argument("--margin", type=float, default=0.08, help="padding inside the box, fraction (default 0.08)")
    ap.add_argument("--lift-ms", type=float, default=60, help="pause between strokes (default 60)")
    ap.add_argument("--wait", type=int, default=5, help="countdown before drawing, seconds (default 5)")
    ap.add_argument("--calibrate", action="store_true",
                    help="draw test lines at increasing speeds (top slow, bottom fast) instead of the "
                         "signature, to see how a pad's line width responds to speed")
    ap.add_argument("--threshold", type=float, help="ink threshold 0-255 for opaque images (default: Otsu)")
    args = ap.parse_args()
    if sys.platform == "darwin" and not args.svg and not args.debug:  # interactive run
        watch_escape()

    mask, strokes = build_strokes(args.image, args.threshold)
    n_pts = sum(len(s) for s in strokes)
    print(f"Traced {len(strokes)} strokes, {n_pts} points, image {mask.shape[1]}x{mask.shape[0]} px")

    if args.svg:
        write_svg(mask, strokes, args.svg)
        print(f"Animation written to {args.svg}")
        return

    if args.debug:  # a filename: save the sheet and stop
        debug_sheet(args.image, args.threshold, mask, strokes).save(args.debug)
        print(f"Trace sheet written to {args.debug}")
        return
    if args.debug is not None or args.preview:
        debug = args.debug is not None
        sheet = debug_sheet(args.image, args.threshold, mask, strokes)
        try:
            show_image(sheet, "Opened the trace sheet. Press Enter to close it" +
                       (": " if debug else " and continue to drawing, Ctrl-C to stop: "))
        except (KeyboardInterrupt, EOFError):
            sys.exit("\nStopped.")
        if debug:
            return

    if sys.platform != "darwin":
        sys.exit("Drawing is implemented for macOS only.")

    mouse = Mouse()
    if not mouse.can_post():
        mouse.request_access()
        open_accessibility_settings()
        sys.exit(no_permission_msg("macOS says this terminal may not control the mouse."))
    box = tuple(float(v) for v in args.box.split(",")) if args.box else pick_box(mouse)
    if box[2] - box[0] < 20 or box[3] - box[1] < 10:
        sys.exit(f"The two corners are only {box[2] - box[0]:.0f} x {box[3] - box[1]:.0f} points apart "
                 f"({box[0]:.0f},{box[1]:.0f} -> {box[2]:.0f},{box[3]:.0f}); the pointer probably "
                 "didn't move between them. Run again and move the mouse before each Enter.")
    print(f"Box: --box {','.join(f'{v:.0f}' for v in box)}")
    countdown("Click the target window's title bar now to focus it (not the pad). Esc stops at any time. Drawing in", args.wait)
    if args.calibrate:
        speeds = [60, 120, 250, 500, 1000, 2000]
        bw, bh = box[2] - box[0], box[3] - box[1]
        rows = [bh * (i + 0.5) / len(speeds) for i in range(len(speeds))]
        lines = [[(bw * 0.05, y), (bw * 0.95, y)] for y in rows]
        print("Calibration lines, top to bottom (points/s): " + ", ".join(map(str, speeds)))
        draw(lines, (bw, bh), box, lambda p: speeds[min(range(len(rows)), key=lambda i: abs(rows[i] - p[1]))],
             0.0, args.lift_ms)
        print("Done.")
        return
    if args.speed:
        speed_fn = lambda p: args.speed  # noqa: E731
    else:
        slow, fast = (float(v) for v in args.speed_range.split(","))
        speed_fn = width_speed_fn(mask, slow, fast)
    draw(strokes, (mask.shape[1], mask.shape[0]), box, speed_fn, args.margin, args.lift_ms)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nStopped.")
