# tmux-flip-pane

Flip **only** the split that contains the focused pane — vertical to horizontal or the other way around. The rest of your layout stays put.

**Why bother?** tmux already has `M-1` through `M-5` for preset layouts (even-horizontal, even-vertical, main-horizontal, main-vertical, tiled). Those are great when you want to nuke the whole window and apply a template. This plugin is for when you’ve got a custom layout and just want to flip *one* division: “this pane was beside that one, now put it below” (or above, left, right). No reshuffling of everything else.

**Requirements:** tmux, Python 3. Nothing fancy.

Tested with tmux 3.7c and 3.8.

---

## Install

### Option A: TPM (recommended)

Add to `~/.tmux.conf`:

```bash
set -g @plugin 'y9938/tmux-flip-pane'
```

Then reload tmux and hit `prefix + I` to install. Default key: **F**.

### Option B: Manual

1. Clone or download this repo (e.g. into `~/.tmux/plugins/tmux-flip-pane`).
2. Make the script executable: `chmod +x scripts/flip-pane.py`.
3. In `~/.tmux.conf`:

```bash
bind-key F run -b '/path/to/tmux-flip-pane/scripts/flip-pane.py'
```

Use the real path to `flip-pane.py`.

---

## Customize the key

TPM users: set the key before the plugin line (or in a separate config block that runs first):

```bash
set -g @flip-pane-key 'M-f'   # Meta+f instead of F
set -g @plugin 'y9938/tmux-flip-pane'
```

Manual users: change `F` in the `bind-key` line to whatever you like.

---

## Behaviour

- **2 panes:** the split between them is flipped (e.g. side-by-side → stacked).
- **3+ panes:** the split between the *focused* pane and one neighbour is flipped; the rest of the tree is unchanged.
- Single pane: nothing to flip, script exits quietly.
- Panes are identified by their IDs, so any `pane-base-index` works.
- In JSON layouts, floating panes retain their geometry and state; focusing a
  floating pane does nothing. Legacy floating-pane layouts are not supported.
- Nested splits are resized along with their containing split.
- On failure the script prints the reason to stderr and exits with code 1,
  for example if there is insufficient space or the layout format is unsupported.

## Development

Run tests locally:

```bash
python3 -m unittest discover -s tests -v
```

Run the [Compatibility tests](.github/workflows/tests.yml) workflow
manually in GitHub Actions to check supported releases and tmux master.
