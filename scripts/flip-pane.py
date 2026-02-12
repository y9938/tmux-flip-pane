#!/usr/bin/env python3
"""Flip the split that contains the focused pane (parent of focused pane's cell)."""
import re
import subprocess
import sys


def checksum(s):
    csum = 0
    for c in s:
        csum = ((csum >> 1) + ((csum & 1) << 15) + ord(c)) & 0xFFFF
    return csum


def parse_cell(s, i):
    """Parse one cell from layout body starting at index i. Returns (node, new_index)."""
    m = re.match(r"(\d+)x(\d+),(\d+),(\d+)", s[i:])
    if not m:
        return None, i
    sx, sy, xoff, yoff = (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)),
    )
    i += m.end()
    # Optional pane_id: comma then digits, but not if followed by 'x' (next cell)
    if i < len(s) and s[i] == ",":
        start = i
        i += 1
        while i < len(s) and s[i].isdigit():
            i += 1
        if i < len(s) and s[i] == "x":
            i = start
    if i < len(s) and s[i] in "[{":
        br_open = s[i]
        br_close = "]" if br_open == "[" else "}"
        i += 1
        children = []
        while i < len(s) and s[i] != br_close:
            child, i = parse_cell(s, i)
            if child is None:
                return None, i
            children.append(child)
            if i < len(s) and s[i] == ",":
                i += 1
        if i >= len(s) or s[i] != br_close:
            return None, i
        i += 1
        return {
            "leaf": False,
            "sx": sx,
            "sy": sy,
            "xoff": xoff,
            "yoff": yoff,
            "type": br_open,
            "children": children,
        }, i
    else:
        return {
            "leaf": True,
            "sx": sx,
            "sy": sy,
            "xoff": xoff,
            "yoff": yoff,
            "pane_index": None,
        }, i


def assign_pane_indices(node, counter):
    """Assign pane_index to leaves in DFS order. Returns next counter."""
    if node["leaf"]:
        node["pane_index"] = counter[0]
        counter[0] += 1
        return
    for ch in node["children"]:
        assign_pane_indices(ch, counter)


def find_parent_of_focused(node, focused_index, parent=None, child_index=None):
    """Return (parent, child_index) of the leaf with pane_index == focused_index, or None."""
    if node["leaf"]:
        if node["pane_index"] == focused_index:
            return (parent, child_index)
        return None
    for i, ch in enumerate(node["children"]):
        found = find_parent_of_focused(ch, focused_index, node, i)
        if found is not None:
            return found
    return None


def flip_container(node):
    """Flip container type and recalc direct children sizes."""
    if node["leaf"] or len(node["children"]) == 0:
        return
    n = len(node["children"])
    sx, sy = node["sx"], node["sy"]
    xoff, yoff = node["xoff"], node["yoff"]
    if node["type"] == "[":
        # TOPBOTTOM -> LEFTRIGHT: same height, split width
        width_each = (sx - (n - 1)) // n
        remainder = (sx - (n - 1)) % n
        node["type"] = "{"
        cx = 0
        for i, ch in enumerate(node["children"]):
            w = width_each + (1 if i < remainder else 0)
            ch["sx"] = w
            ch["sy"] = sy
            ch["xoff"] = xoff + cx
            ch["yoff"] = yoff
            cx += w + 1
    else:
        # LEFTRIGHT -> TOPBOTTOM: same width, split height
        height_each = (sy - (n - 1)) // n
        remainder = (sy - (n - 1)) % n
        node["type"] = "["
        cy = 0
        for i, ch in enumerate(node["children"]):
            h = height_each + (1 if i < remainder else 0)
            ch["sx"] = sx
            ch["sy"] = h
            ch["xoff"] = xoff
            ch["yoff"] = yoff + cy
            cy += h + 1


def dump_cell(node):
    """Serialize node to layout body fragment (no checksum)."""
    if node["leaf"]:
        return f"{node['sx']}x{node['sy']},{node['xoff']},{node['yoff']}"
    br_open = node["type"]
    br_close = "]" if br_open == "[" else "}"
    parts = [dump_cell(ch) for ch in node["children"]]
    return f"{node['sx']}x{node['sy']},{node['xoff']},{node['yoff']}{br_open}" + ",".join(parts) + br_close


def main():
    layout_result = subprocess.run(
        ["tmux", "display-message", "-p", "#{window_layout}"],
        capture_output=True,
        text=True,
    )
    if layout_result.returncode != 0:
        sys.exit(1)
    layout = layout_result.stdout.strip()

    focused_result = subprocess.run(
        ["tmux", "display-message", "-p", "#{pane_index}"],
        capture_output=True,
        text=True,
    )
    if focused_result.returncode != 0:
        sys.exit(1)
    focused_index = int(focused_result.stdout.strip())

    if "," not in layout:
        sys.exit(1)
    csum_prefix, body = layout.split(",", 1)

    root, end = parse_cell(body, 0)
    if root is None or end != len(body):
        sys.exit(1)

    if root["leaf"]:
        # Single pane, nothing to flip
        sys.exit(0)

    assign_pane_indices(root, [0])
    result = find_parent_of_focused(root, focused_index)
    if result is None:
        sys.exit(0)
    parent, child_index = result

    if len(parent["children"]) == 2:
        flip_container(parent)
    else:
        # 3+ children: flip only the split between focused pane and one neighbor
        prev_index = child_index - 1
        if prev_index < 0:
            i1, i2 = 0, 1
        else:
            i1, i2 = prev_index, child_index

        c_left = parent["children"][i1]
        c_right = parent["children"][i2]

        if parent["type"] == "{":  # LEFTRIGHT
            new_sx = c_left["sx"] + 1 + c_right["sx"]
            new_sy = c_left["sy"]
            new_xoff = c_left["xoff"]
            new_yoff = c_left["yoff"]
        else:  # TOPBOTTOM
            new_sx = c_left["sx"]
            new_sy = c_left["sy"] + 1 + c_right["sy"]
            new_xoff = c_left["xoff"]
            new_yoff = c_left["yoff"]

        new_node = {
            "leaf": False,
            "sx": new_sx,
            "sy": new_sy,
            "xoff": new_xoff,
            "yoff": new_yoff,
            "type": parent["type"],
            "children": [c_left, c_right],
        }
        parent["children"] = (
            parent["children"][:i1] + [new_node] + parent["children"][i2 + 1 :]
        )
        flip_container(new_node)

    new_body = dump_cell(root)
    new_csum = f"{checksum(new_body):04x}"
    new_layout = f"{new_csum},{new_body}"
    subprocess.run(["tmux", "select-layout", new_layout], check=True)


if __name__ == "__main__":
    main()
