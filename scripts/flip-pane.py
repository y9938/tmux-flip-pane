#!/usr/bin/env python3
"""Flip the split containing the focused tiled pane, in either tmux layout format."""
import json
import re
import subprocess
import sys


def checksum(s):
    csum = 0
    for c in s:
        csum = ((csum >> 1) + ((csum & 1) << 15) + ord(c)) & 0xFFFF
    return csum


def parse_cell(s, i):
    """Convert a legacy cell to the JSON layout structure used internally."""
    m = re.match(r"(\d+)x(\d+),(\d+),(\d+)", s[i:])
    if not m:
        raise ValueError("invalid legacy layout cell")
    w, h, x, y = map(int, m.groups())
    if w < 1 or h < 1:
        raise ValueError("invalid pane size")
    cell = {"w": w, "h": h, "x": x, "y": y}
    i += m.end()
    if i < len(s) and s[i] in "[{":
        cell["t"] = "v" if s[i] == "[" else "h"
        close = "]" if s[i] == "[" else "}"
        i += 1
        children = []
        while True:
            child, i = parse_cell(s, i)
            children.append(child)
            if i < len(s) and s[i] == close:
                i += 1
                break
            if i >= len(s) or s[i] != ",":
                raise ValueError("invalid legacy layout children")
            i += 1
        if len(children) < 2:
            raise ValueError("split must contain at least two cells")
        cell["c"] = children
    else:
        m = re.match(r",(\d+)", s[i:])
        if not m:
            raise ValueError("legacy layout pane ID is missing")
        cell.update(t="p", I="%" + m.group(1))
        i += m.end()
    return cell, i


def validate_json_cell(cell):
    """Check supported cell structure without copying or converting its fields."""
    if not isinstance(cell, dict) or cell.get("t") not in ("p", "h", "v"):
        raise ValueError("invalid JSON layout cell type")
    if any(type(cell.get(key)) is not int for key in ("w", "h", "x", "y")):
        raise ValueError("invalid JSON layout geometry")
    if cell["w"] < 1 or cell["h"] < 1:
        raise ValueError("invalid pane size")
    if cell["t"] == "p":
        if not isinstance(cell.get("I"), str) or not re.fullmatch(r"%\d+", cell["I"]):
            raise ValueError("JSON layout pane ID is missing")
        if type(cell.get("i")) is not int or cell["i"] < 0 or "c" in cell:
            raise ValueError("invalid JSON layout pane index or children")
        if "z" in cell and (type(cell["z"]) is not int or cell["z"] < 0):
            raise ValueError("invalid floating pane z-index")
    else:
        children = cell.get("c")
        if not isinstance(children, list) or len(children) < 2:
            raise ValueError("split must contain at least two cells")
        for child in children:
            validate_json_cell(child)


def parse_layout(layout):
    """Return the layout tree and its JSON envelope, or None for legacy input."""
    layout = layout.strip()
    if layout.startswith("{"):
        document = json.loads(layout)
        if type(document.get("V")) is not int or document["V"] != 2:
            raise ValueError("unsupported JSON layout version")
        root = document.get("L")
        validate_json_cell(root)
        return root, document
    prefix, sep, body = layout.partition(",")
    if not sep or not re.fullmatch(r"[0-9a-fA-F]{4}", prefix):
        raise ValueError("invalid legacy layout header")
    if int(prefix, 16) != checksum(body):
        raise ValueError("invalid legacy layout checksum")
    root, end = parse_cell(body, 0)
    if end != len(body):
        raise ValueError("trailing data in legacy layout")
    return root, None


def dump_cell(cell):
    """Serialize a cell in the legacy format."""
    geometry = f"{cell['w']}x{cell['h']},{cell['x']},{cell['y']}"
    if cell["t"] == "p":
        return geometry + "," + cell["I"][1:]
    opening, closing = ("{", "}") if cell["t"] == "h" else ("[", "]")
    return geometry + opening + ",".join(map(dump_cell, cell["c"])) + closing


def dump_layout(root, document):
    if document is not None:
        return json.dumps(dict(document, L=root), separators=(",", ":"))
    body = dump_cell(root)
    return f"{checksum(body):04x},{body}"


def tiled_children(cell):
    return [child for child in cell.get("c", []) if "z" not in child]


def find_parent_of_focused(cell, pane_id):
    for i, child in enumerate(cell.get("c", [])):
        if child["t"] == "p" and child.get("I") == pane_id and "z" not in child:
            return cell, i
        found = find_parent_of_focused(child, pane_id)
        if found is not None:
            return found
    return None


def minimum_size(cell):
    """Smallest rectangle that can contain the tiled subtree and its borders."""
    children = tiled_children(cell)
    if not children:
        return 1, 1
    widths, heights = zip(*(minimum_size(child) for child in children))
    if cell["t"] == "h":
        return sum(widths) + len(children) - 1, max(heights)
    return max(widths), sum(heights) + len(children) - 1


def allocate_sizes(available, weights, minima):
    """Divide available cells proportionally, respecting subtree minimum sizes."""
    if available < sum(minima):
        raise ValueError("not enough space to flip this split")
    sizes = [0] * len(weights)
    pending = list(range(len(weights)))
    while pending:
        total = sum(weights[i] for i in pending)
        small = [i for i in pending if available * weights[i] < minima[i] * total]
        if small:
            for i in small:
                sizes[i] = minima[i]
                available -= sizes[i]
                pending.remove(i)
            continue
        shares = [divmod(available * weights[i], total) for i in pending]
        for i, (size, _) in zip(pending, shares):
            sizes[i] = size
        remainder = available - sum(sizes[i] for i in pending)
        order = sorted(range(len(pending)), key=lambda j: -shares[j][1])
        for j in order[:remainder]:
            sizes[pending[j]] += 1
        break
    return sizes


def resize_cell(cell, w, h, x, y, equal=False):
    """Resize tiled descendants; floating panes retain their own geometry."""
    min_w, min_h = minimum_size(cell)
    if w < min_w or h < min_h:
        raise ValueError("not enough space to flip this split")
    cell.update(w=w, h=h, x=x, y=y)
    children = tiled_children(cell)
    if not children:
        return
    horizontal = cell["t"] == "h"
    dimension = "w" if horizontal else "h"
    available = (w if horizontal else h) - len(children) + 1
    minima = [minimum_size(child)[0 if horizontal else 1] for child in children]
    weights = [1 if equal else child[dimension] for child in children]
    offset = 0
    for child, size in zip(children, allocate_sizes(available, weights, minima)):
        if horizontal:
            resize_cell(child, size, h, x + offset, y)
        else:
            resize_cell(child, w, size, x, y + offset)
        offset += size + 1


def flip_container(cell):
    cell["t"] = "h" if cell["t"] == "v" else "v"
    resize_cell(cell, cell["w"], cell["h"], cell["x"], cell["y"], equal=True)


def flip_focused(root, pane_id):
    """Flip the focused tiled pane's split, or its pair within a larger split."""
    result = find_parent_of_focused(root, pane_id)
    if result is None:
        return False
    parent, child_index = result
    indices = [i for i, child in enumerate(parent["c"]) if "z" not in child]
    if len(indices) < 2:
        return False
    if len(indices) == 2:
        flip_container(parent)
        return True
    neighbor = max(0, indices.index(child_index) - 1)
    i1, i2 = indices[neighbor:neighbor + 2]
    left, right = parent["c"][i1], parent["c"][i2]
    horizontal = parent["t"] == "h"
    pair = {
        "t": parent["t"],
        "w": left["w"] + right["w"] + 1 if horizontal else left["w"],
        "h": left["h"] if horizontal else left["h"] + right["h"] + 1,
        "x": left["x"], "y": left["y"], "c": [left, right],
    }
    flip_container(pair)
    parent["c"][i1] = pair
    del parent["c"][i2]
    return True


def tmux(*args):
    result = subprocess.run(["tmux", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "tmux command failed")
    return result.stdout.strip()


def main():
    # Capture the window with its layout so switching windows before select-layout
    # does not change the target of this background script.
    state = tmux("display-message", "-p", "#{window_id}\t#{pane_id}\t#{window_layout}")
    fields = state.split("\t", 2)
    if len(fields) != 3 or not fields[0] or not fields[1]:
        raise ValueError("could not read the focused tmux pane")
    window_id, pane_id, layout = fields
    root, document = parse_layout(layout)
    if flip_focused(root, pane_id):
        tmux("select-layout", "-t", window_id, dump_layout(root, document))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"tmux-flip-pane: {exc}", file=sys.stderr)
        sys.exit(1)
