# sig-replay

Draws a stored signature on an online signature pad by tracing the image into pen
strokes and replaying them as a real mouse drag.

- **Platform:** drawing is macOS only (it posts mouse events through Quartz). Tracing and
  `--preview` work anywhere Python runs.
- **Requirements:** [uv](https://docs.astral.sh/uv/). The script declares its own
  dependencies inline; `uv` installs them on first run.
- **Your own signature only.** Signature images are personal data: the `.gitignore` keeps
  all images out of the repo, so keep yours outside version control too.

## 1. One-time setup: allow your terminal to control the mouse

macOS silently drops synthetic mouse events from apps that lack the **Accessibility**
permission. The script checks this first, and if it's missing it opens the settings
page and prints these steps.

1. Open **System Settings → Privacy & Security → Accessibility**.
   Shortcut: `open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"`
2. Find your terminal app (iTerm, Terminal, Ghostty, …) and switch it **on**.
   - Not listed: click **+**, choose the app in `/Applications`, switch it on.
   - Already on but still failing: select it, click **−**, then add it again with **+**.
     (An app update can leave a stale entry behind.)
3. Confirm with your password or Touch ID.
4. **Quit the terminal completely (⌘Q)** and reopen it. The permission only takes
   effect for a freshly started app.
5. Check it worked:
   ```bash
   uv run --with pyobjc-framework-Quartz python -c "import Quartz; print(Quartz.CGPreflightPostEventAccess())"
   ```
   `True` = allowed.

Toggle greyed out, or switching itself back off: the Mac is managed (Jamf/MDM) and
blocks it. Use a Mac you administer.

**When you're done signing, switch it off again.** While it's on, anything run from
that terminal can control your mouse and keyboard.

## 2. Get the signature as an image

For a signature saved in Preview (Tools → Annotate → Signatures):

1. Open a blank white image in Preview. To make one:
   `uv run --with pillow python -c "from PIL import Image; Image.new('RGB',(2400,1200),'white').save('blank.png')" && open -a Preview blank.png`
2. Insert the signature and drag it **large** (more pixels give a cleaner trace).
3. Click empty space to deselect it, so the selection handles aren't captured.
4. Press ⌘⇧4, drag a box around it, and move the screenshot here, e.g. `sig1.png`.
   Don't save `blank.png`.

PNG or JPG both work, with or without a transparent background.

## 3. Check the trace

```bash
./sig_replay.py sig1.png --preview sig1-preview.png && open sig1-preview.png
```

Each stroke has its own colour and a number for its drawing order.

## 4. Draw

```bash
./sig_replay.py sig1.png
```

1. Hover over the **top-left** of the signature area and press **Enter**. Don't click:
   the terminal keeps keyboard focus while the mouse is over the other window.
2. Hover over the **bottom-right** and press **Enter**.
3. During the countdown, click the target window's **title bar** (not the pad) and let go
   of the mouse.
4. It draws. The signature is scaled to fit the area with its proportions kept.

It prints `--box x1,y1,x2,y2`. Pass that to skip step 1–2 next time.
**Abort** by moving the mouse yourself: it releases the button and stops.

| Option | Default | |
|---|---|---|
| `--speed` | 600 | pen speed in points/s; lower it (e.g. 300) if curves come out jagged |
| `--margin` | 0.08 | padding inside the area, as a fraction |
| `--lift-ms` | 60 | pause between strokes |
| `--wait` | 5 | countdown before drawing, seconds |
| `--threshold` | Otsu | ink threshold 0–255 for opaque images |

Test on a scratch canvas (a Paint app, or a public signature-pad demo) before the real
document.
