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

## 2. Get the signature as an image (once)

For a signature saved in Preview (Tools → Annotate → Signatures):

1. Open a blank white page in Preview. This makes one in a temp folder:
   `uv run --with pillow python -c "from PIL import Image; Image.new('RGB',(2400,1200),'white').save('/tmp/blank.png')" && open -a Preview /tmp/blank.png`
2. Insert the signature and drag it **large** (more pixels give a cleaner trace).
3. Click empty space to deselect it, so the selection handles aren't captured.
4. Press ⌘⇧4, drag a box around the signature, and keep the screenshot somewhere private,
   e.g. `signature.png`. Close the blank page without saving and delete it:
   `rm /tmp/blank.png`.

PNG or JPG both work, with or without a transparent background. This image is the only
file the tool needs, and it never writes anything next to it.

## 3. Sign

1. Open the page with the signature field.
2. Run:
   ```bash
   ./sig_replay.py signature.png --preview
   ```
3. The trace opens (each stroke has its own colour and a number for its drawing order).
   Press **Enter** if it looks right. The preview file is deleted straight away.
   Leave out `--preview` once you trust the trace.
4. Hover over the **top-left** of the signature field and press **Enter**. Don't click:
   the terminal keeps keyboard focus while the mouse is over the other window.
5. Hover over the **bottom-right** and press **Enter**.
6. During the countdown, click the target window's **title bar** (not the field) and let
   go of the mouse.
7. It draws, scaled to fit the field with its proportions kept. Done.

**Abort** by moving the mouse yourself: it releases the button and stops.
It prints `--box x1,y1,x2,y2`; pass that to skip steps 4–5 if you need to redo it.

| Option | Default | |
|---|---|---|
| `--preview` | off | show the trace and ask before drawing |
| `--speed` | 600 | pen speed in points/s; lower it (e.g. 300) if curves come out jagged |
| `--margin` | 0.08 | padding inside the field, as a fraction |
| `--lift-ms` | 60 | pause between strokes |
| `--wait` | 5 | countdown before drawing, seconds |
| `--threshold` | Otsu | ink threshold 0–255 for opaque images |

Try it once on a scratch canvas (a Paint app, or a public signature-pad demo) before the
real document.
