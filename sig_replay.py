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
  sig_replay.py SIGNATURE.png --gif demo.gif        # write an animation of the strokes, no drawing

Writes nothing to disk except a temporary preview image, deleted again in the same run
(and the --gif file, when asked for).

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
def load_mask(path: str, threshold: float | None) -> np.ndarray:
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        rgba = np.asarray(img).astype(float)
        alpha = rgba[..., 3]
        if alpha.min() < 250:  # transparent background: ink is what's opaque *and* dark-ish
            lum = rgba[..., :3].mean(axis=2)
            mask = (alpha > 128) & (lum < 230)
            return _clean(mask)
        img = img.convert("RGB")
    gray = np.asarray(img.convert("L")).astype(float)
    t = threshold if threshold is not None else threshold_otsu(gray)
    return _clean(gray < t)


def _clean(mask: np.ndarray) -> np.ndarray:
    mask = remove_small_objects(mask, max_size=8)
    mask = remove_small_holes(mask, max_size=12)  # pinholes inside thick strokes become fake loops
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        sys.exit("No ink found in image (try --threshold).")
    return mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


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
def show_preview(mask, strokes):
    """Render the trace to a temp file, open it, and delete it once the user has looked."""
    import os
    import subprocess
    import tempfile

    fd, path = tempfile.mkstemp(prefix="sig-replay-", suffix=".png")
    os.close(fd)
    try:
        render_preview(mask, strokes, path)
        opener = {"darwin": ["open"], "win32": ["cmd", "/c", "start", ""]}.get(sys.platform, ["xdg-open"])
        subprocess.run([*opener, path], check=False)
        try:
            input("Preview opened (colour + number = stroke and drawing order). "
                  "Enter to continue, Ctrl-C to stop: ")
        except (KeyboardInterrupt, EOFError):
            sys.exit("\nStopped.")
    finally:
        os.remove(path)


def render_preview(mask, strokes, out, scale=3):
    h, w = mask.shape
    bg = Image.fromarray(np.where(mask, 225, 255).astype(np.uint8)).convert("RGB")
    bg = bg.resize((w * scale, h * scale), Image.NEAREST)
    d = ImageDraw.Draw(bg)
    for i, s in enumerate(strokes):
        hue = (i * 0.61803) % 1
        col = tuple(int(255 * v) for v in _hsv(hue, 0.85, 0.75))
        pts = [(x * scale + scale / 2, y * scale + scale / 2) for x, y in s]
        if len(pts) == 1:
            x, y = pts[0]
            d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=col)
        else:
            d.line(pts, fill=col, width=3)
        d.text((pts[0][0] + 3, pts[0][1] - 12), str(i + 1), fill=col)
    bg.save(out)


def write_gif(mask, strokes, out, width=720, seconds=4.0, fps=25, hold=1.5):
    """Animate the replay: strokes appear in drawing order over a faint copy of the image."""
    h, w = mask.shape
    ss = 2  # draw at 2x, downsample each frame for anti-aliasing
    pad = 24
    k = (width - 2 * pad) / w
    W, H = width, int(h * k) + 2 * pad
    ghost = Image.fromarray(np.where(mask, 232, 255).astype(np.uint8)).convert("RGB")
    ghost = ghost.resize((int(w * k), int(h * k)), Image.LANCZOS)
    base = Image.new("RGB", (W, H), "white")
    base.paste(ghost, (pad, pad))
    base = base.resize((W * ss, H * ss), Image.LANCZOS)

    def tf(p):
        return ((pad + p[0] * k) * ss, (pad + p[1] * k) * ss)

    segs = []  # (stroke index, a, b, length)
    for i, s in enumerate(strokes):
        pts = [tf(p) for p in s] if len(s) > 1 else [tf(s[0]), tf((s[0][0] + 0.5, s[0][1] + 0.5))]
        for a, b in zip(pts, pts[1:]):
            segs.append((a, b, max(math.dist(a, b), 0.01)))
    total = sum(sg[2] for sg in segs)
    n = int(seconds * fps)
    ink, tip, lw = (18, 32, 110), (220, 60, 60), 3 * ss

    canvas = base.copy()
    d = ImageDraw.Draw(canvas)
    frames, done, j = [], 0.0, 0
    for f in range(1, n + 1):
        target = total * f / n
        pen = None
        while j < len(segs) and done + segs[j][2] <= target:
            a, b, L = segs[j]
            d.line([a, b], fill=ink, width=lw)
            d.ellipse((b[0] - lw / 2, b[1] - lw / 2, b[0] + lw / 2, b[1] + lw / 2), fill=ink)
            done += L
            pen = b
            j += 1
        frame = canvas.copy()
        if j < len(segs):  # partial segment + pen tip
            a, b, L = segs[j]
            t = (target - done) / L
            pen = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            ImageDraw.Draw(frame).line([a, pen], fill=ink, width=lw)
        if pen and f < n:
            r = 5 * ss
            ImageDraw.Draw(frame).ellipse((pen[0] - r, pen[1] - r, pen[0] + r, pen[1] + r), fill=tip)
        frames.append(frame.resize((W, H), Image.LANCZOS).quantize(colors=64, dither=Image.NONE))
    durations = [int(1000 / fps)] * (len(frames) - 1) + [int(hold * 1000)]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0,
                   optimize=True, disposal=1)


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
            print(f"\r  Hover over the {label}, press Enter.  pointer = {x:5.0f},{y:5.0f} ",
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


def draw(strokes, size, box, speed, margin, lift_ms):
    w, h = size
    bx1, by1, bx2, by2 = box
    bw, bh = (bx2 - bx1) * (1 - 2 * margin), (by2 - by1) * (1 - 2 * margin)
    k = min(bw / w, bh / h)
    ox = bx1 + (bx2 - bx1 - w * k) / 2
    oy = by1 + (by2 - by1 - h * k) / 2

    def tf(p):
        return ox + p[0] * k, oy + p[1] * k

    mouse = Mouse()
    dt = 0.004
    step = max(speed * dt, 0.5)
    pen = None  # screen position where the pen is down, or None
    try:
        for s in strokes:
            pts = [tf(p) for p in s]
            if pen is None or math.dist(pen, pts[0]) > 1.5:
                if pen is not None:
                    mouse.up(*pen)
                    time.sleep(lift_ms / 1000)
                if not mouse.move_and_verify(*pts[0]):
                    if pen is None:  # very first move: the pointer never budged
                        open_accessibility_settings()
                        sys.exit("\n" + no_permission_msg(
                            "The pointer didn't move when the script moved it, so macOS is "
                            "dropping its mouse events."))
                    raise KeyboardInterrupt("mouse moved by user")
                mouse.press(*pts[0])
                time.sleep(0.03)
            if len(pts) == 1:  # a dot
                mouse.drag(pts[0][0] + 0.6, pts[0][1] + 0.6)
                time.sleep(0.02)
            for a, b in zip(pts, pts[1:]):
                n = max(1, int(math.dist(a, b) / step))
                for i in range(1, n + 1):
                    t = i / n
                    mouse.check_hijack()
                    mouse.drag(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                    time.sleep(dt)
            pen = pts[-1]
        if pen is not None:
            mouse.up(*pen)
    except KeyboardInterrupt as e:
        if mouse.down and mouse.last:
            mouse.up(*mouse.last)
        sys.exit(f"\nAborted: {e or 'Ctrl-C'}")


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--preview", action="store_true",
                    help="show the traced strokes first and ask before drawing (nothing is kept on disk)")
    ap.add_argument("--gif", metavar="OUT.gif",
                    help="write an animation of the strokes being drawn and exit (no mouse control)")
    ap.add_argument("--box", help="x1,y1,x2,y2 screen points of the signature area (skips picking)")
    ap.add_argument("--speed", type=float, default=600, help="pen speed in screen points/s (default 600)")
    ap.add_argument("--margin", type=float, default=0.08, help="padding inside the box, fraction (default 0.08)")
    ap.add_argument("--lift-ms", type=float, default=60, help="pause between strokes (default 60)")
    ap.add_argument("--wait", type=int, default=5, help="countdown before drawing, seconds (default 5)")
    ap.add_argument("--threshold", type=float, help="ink threshold 0-255 for opaque images (default: Otsu)")
    args = ap.parse_args()

    mask, strokes = build_strokes(args.image, args.threshold)
    n_pts = sum(len(s) for s in strokes)
    print(f"Traced {len(strokes)} strokes, {n_pts} points, image {mask.shape[1]}x{mask.shape[0]} px")

    if args.gif:
        write_gif(mask, strokes, args.gif)
        print(f"Animation written to {args.gif}")
        return

    if args.preview:
        show_preview(mask, strokes)

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
    countdown("Click the target window's title bar now to focus it (not the pad). Drawing in", args.wait)
    draw(strokes, (mask.shape[1], mask.shape[0]), box, args.speed, args.margin, args.lift_ms)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nStopped.")
