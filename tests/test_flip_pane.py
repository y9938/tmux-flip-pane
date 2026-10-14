"""Regression tests; set TMUX_TEST_BINARIES to test additional tmux builds."""
from contextlib import contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flip-pane.py"
SPEC = importlib.util.spec_from_file_location("flip_pane", SCRIPT)
flip = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flip)


def legacy(body):
    return f"{flip.checksum(body):04x},{body}"


def leaves(node):
    if node["t"] == "p":
        return [node]
    return [leaf for child in node["c"] for leaf in leaves(child)]


def check_geometry(test, node):
    if node["t"] == "p":
        test.assertGreaterEqual(node["w"], 1)
        test.assertGreaterEqual(node["h"], 1)
        return
    horizontal = node["t"] == "h"
    offset = node["x" if horizontal else "y"]
    for child in flip.tiled_children(node):
        if horizontal:
            test.assertEqual((child["h"], child["y"], child["x"]),
                             (node["h"], node["y"], offset))
        else:
            test.assertEqual((child["w"], child["x"], child["y"]),
                             (node["w"], node["x"], offset))
        offset += child["w" if horizontal else "h"] + 1
        check_geometry(test, child)
    test.assertEqual(offset - 1, node["x" if horizontal else "y"]
                     + node["w" if horizontal else "h"])


class LayoutTests(unittest.TestCase):
    def test_legacy_roundtrip_retains_multi_digit_ids(self):
        layout = legacy("100x40,0,0{50x40,0,0,18,49x40,51,0,205}")
        root, doc = flip.parse_layout(layout)
        self.assertEqual([n["I"] for n in leaves(root)], ["%18", "%205"])
        self.assertEqual(flip.dump_layout(root, doc), layout)
        self.assertTrue(flip.flip_focused(root, "%205"))
        check_geometry(self, root)

    def test_focus_is_selected_by_id_in_nested_layout(self):
        root, doc = flip.parse_layout(legacy(
            "100x40,0,0{50x40,0,0,8,49x40,51,0[49x20,51,0,90,49x19,51,21,26]}"))
        unchanged = copy.deepcopy(root["c"][0])
        self.assertTrue(flip.flip_focused(root, "%26"))
        self.assertEqual(root["t"], "h")
        self.assertEqual(root["c"][1]["t"], "h")
        self.assertEqual(root["c"][0], unchanged)
        check_geometry(self, root)
        flip.parse_layout(flip.dump_layout(root, doc))

    def test_neighbor_subtree_is_resized_recursively(self):
        root, _ = flip.parse_layout(legacy(
            "100x40,0,0{50x40,0,0,8,49x40,51,0[49x20,51,0,90,49x19,51,21,26]}"))
        flip.flip_focused(root, "%8")
        self.assertEqual(root["t"], "v")
        check_geometry(self, root)

    def test_three_siblings_only_flip_selected_pair(self):
        root, _ = flip.parse_layout(legacy(
            "100x40,0,0{33x40,0,0,10,33x40,34,0,20,32x40,68,0,30}"))
        unchanged = copy.deepcopy(root["c"][0])
        flip.flip_focused(root, "%30")
        self.assertEqual(root["c"][0], unchanged)
        self.assertEqual(root["c"][1]["t"], "v")
        check_geometry(self, root)

    def test_json_retains_metadata_and_floating_geometry(self):
        doc = {"V": 2, "extra": "keep", "L": {
            "t": "h", "w": 100, "h": 40, "x": 0, "y": 0, "c": [
                {"t": "p", "w": 50, "h": 40, "x": 0, "y": 0,
                 "i": 1, "I": "%8", "a": True},
                {"t": "p", "w": 49, "h": 40, "x": 51, "y": 0,
                 "i": 2, "I": "%90", "l": 0, "extra": "keep"},
                {"t": "p", "w": 20, "h": 10, "x": -2, "y": 3,
                 "i": 3, "I": "%26", "z": 0, "l": 1},
            ]}}
        root, original = flip.parse_layout(json.dumps(doc))
        self.assertFalse(flip.flip_focused(root, "%26"))
        flip.flip_focused(root, "%90")
        output = json.loads(flip.dump_layout(root, original))
        self.assertEqual(output["extra"], "keep")
        self.assertEqual(output["L"]["c"][2], doc["L"]["c"][2])
        for before, after in zip(doc["L"]["c"], output["L"]["c"]):
            for key in ("i", "I", "a", "l", "z", "extra"):
                self.assertEqual(after.get(key), before.get(key))
        check_geometry(self, root)

    def test_minimum_space_and_invalid_formats(self):
        root, _ = flip.parse_layout(legacy("1x5,0,0[1x2,0,0,0,1x2,0,3,1]"))
        with self.assertRaisesRegex(ValueError, "not enough space"):
            flip.flip_focused(root, "%0")
        for layout in ("", "0000,100x40,0,0,0", '{"V":3,"L":{}}',
                       '{"V":2,"L":{}}', legacy("100x40,0,0{50x40,0,0,0}")):
            with self.subTest(layout=layout), self.assertRaises(ValueError):
                flip.parse_layout(layout)

    def test_nested_minimum_overrides_equal_split(self):
        root, _ = flip.parse_layout(legacy(
            "100x12,0,0{90x12,0,0,10,9x12,91,0[9x2,91,0,20,9x2,91,3,30,"
            "9x2,91,6,40,9x1,91,9,50,9x1,91,11,60]}"))
        flip.flip_focused(root, "%10")
        self.assertEqual([ch["h"] for ch in root["c"]], [2, 9])
        check_geometry(self, root)

    def test_insufficient_space_does_not_apply_layout(self):
        layout = legacy("1x5,0,0[1x2,0,0,0,1x2,0,3,1]")
        with patch.object(flip, "tmux", return_value="@5\t%0\t" + layout) as tmux:
            with self.assertRaisesRegex(ValueError, "not enough space"):
                flip.main()
        self.assertEqual(tmux.call_count, 1)

    def test_single_and_missing_pane_are_noops(self):
        root, _ = flip.parse_layout(legacy("100x40,0,0,18"))
        self.assertFalse(flip.flip_focused(root, "%18"))
        self.assertFalse(flip.flip_focused(root, "%19"))

    def test_main_applies_to_captured_window(self):
        layout = legacy("100x40,0,0{50x40,0,0,8,49x40,51,0,26}")
        with patch.object(flip, "tmux", side_effect=["@5\t%26\t" + layout, ""]) as tmux:
            flip.main()
        self.assertEqual(tmux.call_args.args[:3], ("select-layout", "-t", "@5"))


class TmuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        configured = os.environ.get("TMUX_TEST_BINARIES")
        self.binaries = ([value for value in configured.split(os.pathsep) if value]
                         if configured is not None else [shutil.which("tmux")])
        self.binaries = [binary for binary in self.binaries if binary]
        if not self.binaries:
            self.skipTest("no tmux binaries available")

    @contextmanager
    def server(self, binary):
        with tempfile.TemporaryDirectory() as directory:
            # Resolve before prepending our wrapper to PATH, including when a
            # caller supplies a command name rather than an absolute path.
            resolved = shutil.which(binary)
            self.assertIsNotNone(resolved, f"tmux binary not found: {binary}")
            command = [str(Path(resolved).resolve()), "-S", str(Path(directory) / "socket"),
                       "-f", "/dev/null"]
            env = dict(os.environ)
            env.pop("TMUX", None)
            env.pop("TMUX_PANE", None)
            wrapper = Path(directory) / "tmux"
            wrapper.write_text("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n')
            wrapper.chmod(0o755)
            env["PATH"] = directory + os.pathsep + env["PATH"]

            def tmux(*args):
                return subprocess.check_output(command + list(args), text=True, env=env).strip()

            try:
                tmux("new-session", "-d", "-s", "test", "-x", "100", "-y", "40")
                yield tmux, env
            finally:
                subprocess.run(command + ["kill-server"], capture_output=True, env=env)

    def run_flip(self, tmux, env):
        result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                                text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        root, _ = flip.parse_layout(tmux("display-message", "-p", "#{window_layout}"))
        check_geometry(self, root)
        return root

    def test_single_pane(self):
        for binary in self.binaries:
            with self.subTest(binary=binary), self.server(binary) as (tmux, env):
                before = tmux("display-message", "-p", "#{window_layout}")
                self.run_flip(tmux, env)
                self.assertEqual(tmux("display-message", "-p", "#{window_layout}"), before)

    def test_nested_focus_with_nonzero_base_index(self):
        for binary in self.binaries:
            with self.subTest(binary=binary), self.server(binary) as (tmux, env):
                tmux("set-option", "-g", "pane-base-index", "1")
                tmux("split-window", "-h")
                tmux("split-window", "-v")
                before = tmux("list-panes", "-F", "#{pane_index}:#{pane_id}")
                active = tmux("display-message", "-p", "#{pane_id}")
                root = self.run_flip(tmux, env)
                self.assertEqual(root["t"], "h")
                self.assertEqual(root["c"][1]["t"], "h")
                self.assertEqual(tmux("list-panes", "-F", "#{pane_index}:#{pane_id}"), before)
                self.assertEqual(tmux("display-message", "-p", "#{pane_id}"), active)

    def test_resize_neighboring_subtree(self):
        for binary in self.binaries:
            with self.subTest(binary=binary), self.server(binary) as (tmux, env):
                first = tmux("display-message", "-p", "#{pane_id}")
                tmux("split-window", "-h")
                tmux("split-window", "-v")
                before = tmux("list-panes", "-F", "#{pane_id}")
                tmux("select-pane", "-t", first)
                root = self.run_flip(tmux, env)
                self.assertEqual(root["t"], "v")
                self.assertEqual(tmux("list-panes", "-F", "#{pane_id}"), before)
                self.assertEqual(tmux("display-message", "-p", "#{pane_id}"), first)

    def test_three_siblings_flip_only_selected_pair(self):
        for binary in self.binaries:
            with self.subTest(binary=binary), self.server(binary) as (tmux, env):
                tmux("split-window", "-h")
                tmux("split-window", "-h")
                tmux("select-layout", "even-horizontal")
                active = tmux("display-message", "-p", "#{pane_id}")
                before, _ = flip.parse_layout(tmux("display-message", "-p", "#{window_layout}"))
                root = self.run_flip(tmux, env)
                self.assertEqual(root["c"][0], before["c"][0])
                self.assertEqual(len(root["c"]), 2)
                self.assertEqual(root["c"][1]["t"], "v")
                self.assertEqual(tmux("display-message", "-p", "#{pane_id}"), active)

    def test_floating_pane_is_preserved(self):
        for binary in self.binaries:
            with self.subTest(binary=binary), self.server(binary) as (tmux, env):
                if not tmux("display-message", "-p", "#{window_layout}").startswith("{"):
                    raise unittest.SkipTest("floating pane support requires JSON layouts")
                first = tmux("display-message", "-p", "#{pane_id}")
                tmux("split-window", "-h")
                tmux("new-pane")
                floating = tmux("display-message", "-p", "#{pane_id}")
                before = tmux("display-message", "-p", "#{window_layout}")
                self.run_flip(tmux, env)
                self.assertEqual(tmux("display-message", "-p", "#{window_layout}"), before)
                tmux("select-pane", "-t", first)
                old_root, _ = flip.parse_layout(tmux("display-message", "-p", "#{window_layout}"))
                old_float = next(cell for cell in leaves(old_root) if cell["I"] == floating)
                root = self.run_flip(tmux, env)
                new_float = next(cell for cell in leaves(root) if cell["I"] == floating)
                self.assertEqual(new_float, old_float)
                self.assertEqual(tmux("display-message", "-p", "#{pane_id}"), first)


class IntegrationAvailabilityTests(unittest.TestCase):
    def test_no_tmux_skips_instead_of_reporting_success(self):
        for env in ({}, {"TMUX_TEST_BINARIES": ""}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with patch.object(shutil, "which", return_value=None):
                    with self.assertRaises(unittest.SkipTest):
                        TmuxIntegrationTests("test_single_pane").setUp()


if __name__ == "__main__":
    unittest.main()
