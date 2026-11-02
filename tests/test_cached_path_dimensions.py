"""Reproduce and lock down the v1.7.2+atk.3 cached-path size regressions.

No Nuke. Uses local tk-core TemplatePath the same way the handler calls
``render_template.get_fields(cached_path)`` during incomplete construction.

Run::

    PYTHONPATH=/Users/patxi/devel/automatik_dev/tk-core/python \\
      python3 -m unittest tests.test_cached_path_dimensions -v
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

TK_CORE_PYTHON = Path("/Users/patxi/devel/automatik_dev/tk-core/python")


def _load_tank_templates():
    """Load TemplatePath without importing the full (incomplete) tank package."""
    if "tank.template" in sys.modules:
        from tank.errors import TankError
        from tank.template import TemplatePath
        from tank.templatekey import make_keys

        return TankError, TemplatePath, make_keys

    tank_root = TK_CORE_PYTHON / "tank"
    tank_pkg = types.ModuleType("tank")
    tank_pkg.__path__ = [str(tank_root)]
    sys.modules["tank"] = tank_pkg

    def load(fullname: str, path: Path):
        spec = importlib.util.spec_from_file_location(fullname, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    # Minimal util surface used by template.py / templatekey.py.
    util_pkg = types.ModuleType("tank.util")
    util_pkg.__path__ = [str(tank_root / "util")]
    sys.modules["tank.util"] = util_pkg
    platforms = load("tank.util.platforms", tank_root / "util" / "platforms.py")
    sgre = load("tank.util.sgre", tank_root / "util" / "sgre.py")
    util_pkg.is_linux = platforms.is_linux
    util_pkg.is_macos = platforms.is_macos
    util_pkg.is_windows = platforms.is_windows
    util_pkg.sgre = sgre

    errors = load("tank.errors", tank_root / "errors.py")
    constants = load("tank.constants", tank_root / "constants.py")
    tank_pkg.errors = errors
    tank_pkg.constants = constants

    load("tank.templatekey", tank_root / "templatekey.py")
    load("tank.template_path_parser", tank_root / "template_path_parser.py")
    template = load("tank.template", tank_root / "template.py")
    templatekey = sys.modules["tank.templatekey"]
    return errors.TankError, template.TemplatePath, templatekey.make_keys


TankError, TemplatePath, make_keys = _load_tank_templates()


# Paths from the artist repro / screenshots.
SANDBOX_ROOT = "/mnt/projects/global_sandbox"
FAILING_CACHED_PATH = (
    f"{SANDBOX_ROOT}/sequences/NukeTemplates/MMPublish/CMP/work/images/"
    "template/v004/3200x1800/"
    "MMPublish_CMP_template_STMapTEST_v004.%04d.exr"
)
PROXY_SHAPED_PATH = (
    f"{SANDBOX_ROOT}/sequences/NukeTemplates/MMPublish/CMP/work/images/"
    "template/v004/1600x900/"
    "MMPublish_CMP_template_STMapTEST_proxy_v004.%04d.exr"
)

# Studio definitions (resolved @shot_root).
SHOT_RENDER_DEF = (
    "sequences/{Sequence}/{Shot}/{Step}/work/images/{name}/v{version}/"
    "{width}x{height}/{Shot}_{Step}_{name}_{nuke.output}_v{version}."
    "{SEQ}.{image_extension}"
)
SHOT_PROXY_DEF = (
    "sequences/{Sequence}/{Shot}/{Step}/work/images/{name}/v{version}/"
    "{width}x{height}/{Shot}_{Step}_{name}_{nuke.output}_proxy_v{version}."
    "{SEQ}.{image_extension}"
)
# Same shape as the Nuke dialog text (output / extension aliases expanded).
DIALOG_STYLE_DEF = (
    "sequences/{Sequence}/{Shot}/{Step}/work/images/{name}/v{version}/"
    "{width}x{height}/{Shot}_{Step}_{name}_{output}_v{version}."
    "{SEQ}.{extension}"
)
# Review-style template: no width/height — KeyError candidate once get_fields works.
NO_SIZE_DEF = (
    "sequences/{Sequence}/{Shot}/{Step}/review/"
    "{Shot}_{Step}_{name}_{nuke.output}_v{version}.mov"
)


def _studio_keys() -> Dict[str, Any]:
    """Keys matching atk-config-default/core/templates.yml for these templates."""
    return make_keys(
        {
            "Sequence": {"type": "str"},
            "Shot": {"type": "str"},
            "Step": {"type": "str"},
            "name": {"type": "str", "filter_by": "alphanumeric"},
            "version": {"type": "int", "format_spec": "03"},
            "width": {"type": "int"},
            "height": {"type": "int"},
            "SEQ": {"type": "sequence", "format_spec": "04"},
            "image_extension": {
                "type": "str",
                "choices": {
                    "exr": "EXR (.exr)",
                    "tif": "TIF (.tif)",
                    "dpx": "DPX (.dpx)",
                },
                "default": "exr",
                "alias": "extension",
            },
            "nuke.output": {
                "type": "str",
                "filter_by": "alphanumeric",
                "alias": "output",
            },
            # Plain keys for the dialog-style definition that already uses aliases.
            "output": {"type": "str", "filter_by": "alphanumeric"},
            "extension": {
                "type": "str",
                "choices": {"exr": "EXR (.exr)", "mov": "MOV (.mov)"},
                "default": "exr",
            },
        }
    )


def make_template(definition: str, name: str = "test") -> TemplatePath:
    return TemplatePath(definition, _studio_keys(), SANDBOX_ROOT, name=name)


def atk3_read_size(
    render_template: Optional[TemplatePath], cached_path: str
) -> Tuple[int, int]:
    """v1.7.2+atk.3 inline logic from __gather_render_settings."""
    fields = render_template.get_fields(cached_path)
    return fields["width"], fields["height"]


def atk2_soft_read_size(
    render_template: Optional[TemplatePath], cached_path: str
) -> Tuple[int, int]:
    """Pre-c887a90 __get_cached_dimensions soft fallback."""
    if not render_template or not cached_path:
        return 0, 0
    try:
        fields = render_template.get_fields(cached_path)
    except TankError:
        return 0, 0
    return fields.get("width", 0), fields.get("height", 0)


class TestSandboxPathAgainstStudioTemplates(unittest.TestCase):
    def test_failing_path_fits_shot_render_work(self):
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        fields = tmpl.get_fields(FAILING_CACHED_PATH)
        self.assertEqual(fields["width"], 3200)
        self.assertEqual(fields["height"], 1800)
        self.assertEqual(fields["Shot"], "MMPublish")
        self.assertEqual(fields["Step"], "CMP")
        self.assertEqual(fields["name"], "template")
        self.assertEqual(fields["output"], "STMapTEST")
        self.assertEqual(fields["version"], 4)

    def test_failing_path_fits_dialog_style_aliases(self):
        """Error dialog showed {output}/{extension}; still parses."""
        tmpl = make_template(DIALOG_STYLE_DEF, "nuke_shot_render_work")
        fields = tmpl.get_fields(FAILING_CACHED_PATH)
        self.assertEqual((fields["width"], fields["height"]), (3200, 1800))

    def test_atk3_succeeds_when_path_fits(self):
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        self.assertEqual(atk3_read_size(tmpl, FAILING_CACHED_PATH), (3200, 1800))

    def test_hash_padding_also_fits(self):
        path = FAILING_CACHED_PATH.replace("%04d", "####")
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        self.assertEqual(tmpl.get_fields(path)["width"], 3200)

    def test_literal_frame_number_fits(self):
        path = FAILING_CACHED_PATH.replace("%04d", "1108")
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        self.assertEqual(tmpl.get_fields(path)["SEQ"], 1108)


class TestProxyMismatch(unittest.TestCase):
    def test_full_res_path_against_proxy_template_raises_tank_error(self):
        proxy = make_template(SHOT_PROXY_DEF, "nuke_shot_render_proxy_work")
        with self.assertRaises(TankError) as ctx:
            proxy.get_fields(FAILING_CACHED_PATH)
        msg = str(ctx.exception)
        self.assertIn("does not fit the template", msg)

    def test_atk3_proxy_mismatch_raises_tank_error_not_keyerror(self):
        proxy = make_template(SHOT_PROXY_DEF, "nuke_shot_render_proxy_work")
        with self.assertRaises(TankError):
            atk3_read_size(proxy, FAILING_CACHED_PATH)

    def test_atk2_soft_proxy_mismatch_returns_zeros(self):
        proxy = make_template(SHOT_PROXY_DEF, "nuke_shot_render_proxy_work")
        self.assertEqual(atk2_soft_read_size(proxy, FAILING_CACHED_PATH), (0, 0))

    def test_proxy_shaped_path_fits_proxy_template(self):
        proxy = make_template(SHOT_PROXY_DEF, "nuke_shot_render_proxy_work")
        fields = proxy.get_fields(PROXY_SHAPED_PATH)
        self.assertEqual((fields["width"], fields["height"]), (1600, 900))

    def test_proxy_path_against_full_template_raises(self):
        full = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        with self.assertRaises(TankError):
            full.get_fields(PROXY_SHAPED_PATH)


class TestKeyErrorWidthCases(unittest.TestCase):
    def test_template_without_width_keyerrors_on_atk3(self):
        """Hypothesis 4: get_fields succeeds, fields['width'] KeyErrors."""
        review_path = (
            f"{SANDBOX_ROOT}/sequences/NukeTemplates/MMPublish/CMP/review/"
            "MMPublish_CMP_template_STMapTEST_v004.mov"
        )
        tmpl = make_template(NO_SIZE_DEF, "nuke_shot_review_movie")
        fields = tmpl.get_fields(review_path)
        self.assertNotIn("width", fields)
        with self.assertRaises(KeyError) as ctx:
            atk3_read_size(tmpl, review_path)
        self.assertEqual(ctx.exception.args[0], "width")

    def test_atk2_soft_missing_width_returns_zeros(self):
        review_path = (
            f"{SANDBOX_ROOT}/sequences/NukeTemplates/MMPublish/CMP/review/"
            "MMPublish_CMP_template_STMapTEST_v004.mov"
        )
        tmpl = make_template(NO_SIZE_DEF, "nuke_shot_review_movie")
        self.assertEqual(atk2_soft_read_size(tmpl, review_path), (0, 0))

    def test_empty_cached_path_never_enters_atk3_branch(self):
        """atk.3 only enters the get_fields branch when cached_path is truthy."""
        cached_path = ""
        incomplete = True
        entered = bool(cached_path) and incomplete
        self.assertFalse(entered)


class TestPathVariantsThatBreakParse(unittest.TestCase):
    def test_wrong_root_still_parses_if_definition_is_relative_under_root(self):
        """TemplatePath strips root_path; wrong project root fails validation."""
        tmpl = TemplatePath(
            SHOT_RENDER_DEF, _studio_keys(), "/mnt/projects/other_show", name="x"
        )
        with self.assertRaises(TankError):
            tmpl.get_fields(FAILING_CACHED_PATH)

    def test_name_with_underscore_fails_alphanumeric_filter(self):
        bad = FAILING_CACHED_PATH.replace("/template/", "/my_template/").replace(
            "_template_", "_my_template_"
        )
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        with self.assertRaises(TankError):
            tmpl.get_fields(bad)

    def test_output_with_underscore_fails_alphanumeric_filter(self):
        bad = FAILING_CACHED_PATH.replace("STMapTEST", "ST_Map")
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        with self.assertRaises(TankError):
            tmpl.get_fields(bad)

    def test_atk2_soft_survives_alphanumeric_failure(self):
        bad = FAILING_CACHED_PATH.replace("STMapTEST", "ST_Map")
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        self.assertEqual(atk2_soft_read_size(tmpl, bad), (0, 0))
        with self.assertRaises(TankError):
            atk3_read_size(tmpl, bad)


class TestGatherBranchMatrix(unittest.TestCase):
    """Truth table for the atk.3 if/elif in __gather_render_settings."""

    def _branch(
        self,
        cached_path: str,
        fully_constructed: bool,
        is_proxy: bool,
    ) -> str:
        if cached_path and not fully_constructed:
            return "cached_fields"
        if is_proxy:
            return "proxy_dims"
        return "node_width_height"

    def test_incomplete_with_cache_uses_fields(self):
        self.assertEqual(self._branch("/some/path", False, False), "cached_fields")
        self.assertEqual(self._branch("/some/path", False, True), "cached_fields")

    def test_incomplete_without_cache_falls_through(self):
        self.assertEqual(self._branch("", False, False), "node_width_height")
        self.assertEqual(self._branch("", False, True), "proxy_dims")

    def test_complete_never_uses_cached_fields(self):
        self.assertEqual(self._branch("/some/path", True, False), "node_width_height")
        self.assertEqual(self._branch("/some/path", True, True), "proxy_dims")


class TestResetRenderPathDualUpdate(unittest.TestCase):
    """reset_render_path always force-updates BOTH proxy and full paths.

    From handler.py::reset_render_path — when Nuke is in proxy mode (the
    screenshot state), the first update uses tk_cached_proxy_path + proxy
    template. That is the TankError "path does not fit" candidate if the
    knob still holds the full-res path.
    """

    def test_proxy_mode_order_hits_proxy_cache_first(self):
        node_proxy_flag = True  # Nuke proxy mode on, as in the screenshot
        order = [node_proxy_flag, not node_proxy_flag]
        self.assertEqual(order, [True, False])

    def test_proxy_mode_full_path_in_proxy_knob_tankerrors_like_dialog(self):
        proxy = make_template(SHOT_PROXY_DEF, "nuke_shot_render_proxy_work")
        # Artist path from the dialog — no `_proxy_` token.
        with self.assertRaises(TankError) as ctx:
            atk3_read_size(proxy, FAILING_CACHED_PATH)
        self.assertIn("does not fit the template", str(ctx.exception))
        # Soft path would have kept init alive:
        self.assertEqual(atk2_soft_read_size(proxy, FAILING_CACHED_PATH), (0, 0))

    def test_empty_proxy_cache_skips_fields_and_would_call_proxy_dims(self):
        self.assertEqual(self._branch_proxy(""), "proxy_dims")

    def _branch_proxy(self, cached_proxy_path: str) -> str:
        fully_constructed = False
        is_proxy = True
        if cached_proxy_path and not fully_constructed:
            return "cached_fields"
        if is_proxy:
            return "proxy_dims"
        return "node_width_height"


class TestKeepCachedWhenSizeUnknown(unittest.TestCase):
    """#12 fix: soft (0, 0) during setup must NOT force-reset into a 0x0 folder."""

    def _update_decision(
        self,
        fully_constructed: bool,
        cached_path: str,
        width: int,
        height: int,
        force_reset: bool,
    ) -> str:
        keep_cached_during_setup = (
            not fully_constructed and cached_path and not (width and height)
        )
        if keep_cached_during_setup:
            return "keep_cached"
        if force_reset:
            return "recompute"
        return "cache_or_recompute"

    def test_incomplete_unknown_size_keeps_cached_even_on_force_reset(self):
        self.assertEqual(
            self._update_decision(False, FAILING_CACHED_PATH, 0, 0, True),
            "keep_cached",
        )

    def test_incomplete_known_size_still_recomputes(self):
        self.assertEqual(
            self._update_decision(False, FAILING_CACHED_PATH, 3200, 1800, True),
            "recompute",
        )

    def test_complete_unknown_size_does_not_use_keep_cached_guard(self):
        # Fully constructed nodes may still compute / warn via TkComputePathError.
        self.assertEqual(
            self._update_decision(True, FAILING_CACHED_PATH, 0, 0, True),
            "recompute",
        )

    def test_incomplete_no_cache_does_not_keep_empty(self):
        self.assertEqual(self._update_decision(False, "", 0, 0, True), "recompute")

    def test_soft_proxy_mismatch_feeds_the_keep_cached_guard(self):
        proxy = make_template(SHOT_PROXY_DEF, "nuke_shot_render_proxy_work")
        width, height = atk2_soft_read_size(proxy, FAILING_CACHED_PATH)
        self.assertEqual((width, height), (0, 0))
        self.assertEqual(
            self._update_decision(False, FAILING_CACHED_PATH, width, height, True),
            "keep_cached",
        )


class TestHandlerDiffContract(unittest.TestCase):
    """Pin the behavioural contract change between atk.2 soft helper and atk.3."""

    CASES = [
        ("fits", FAILING_CACHED_PATH, SHOT_RENDER_DEF, "ok"),
        ("proxy_mismatch", FAILING_CACHED_PATH, SHOT_PROXY_DEF, "tank"),
        (
            "no_width",
            f"{SANDBOX_ROOT}/sequences/NukeTemplates/MMPublish/CMP/review/"
            "MMPublish_CMP_template_STMapTEST_v004.mov",
            NO_SIZE_DEF,
            "keyerror",
        ),
        (
            "bad_output",
            FAILING_CACHED_PATH.replace("STMapTEST", "ST_Map"),
            SHOT_RENDER_DEF,
            "tank",
        ),
    ]

    def test_contract_matrix(self):
        report = []
        for label, path, definition, expected in self.CASES:
            tmpl = make_template(definition, label)
            soft = "ok"
            try:
                soft_size = atk2_soft_read_size(tmpl, path)
            except Exception as exc:  # pragma: no cover - soft must not raise
                soft = type(exc).__name__
                soft_size = None

            hard = "ok"
            hard_size = None
            try:
                hard_size = atk3_read_size(tmpl, path)
            except KeyError:
                hard = "keyerror"
            except TankError:
                hard = "tank"
            except Exception as exc:  # pragma: no cover
                hard = type(exc).__name__

            report.append(
                f"{label}: soft={soft}/{soft_size} hard={hard}/{hard_size} expect={expected}"
            )
            if expected == "ok":
                self.assertEqual(soft, "ok")
                self.assertEqual(hard, "ok")
                self.assertEqual(soft_size, hard_size)
            else:
                self.assertEqual(soft, "ok")
                self.assertEqual(soft_size, (0, 0))
                self.assertEqual(hard, expected)

        # Visible in -v failure output / stdout when run as main.
        print("\n".join(report))


class TestApplyFieldsRoundTrip(unittest.TestCase):
    def test_round_trip_matches_artist_path(self):
        tmpl = make_template(SHOT_RENDER_DEF, "nuke_shot_render_work")
        fields = {
            "Sequence": "NukeTemplates",
            "Shot": "MMPublish",
            "Step": "CMP",
            "name": "template",
            "version": 4,
            "width": 3200,
            "height": 1800,
            "output": "STMapTEST",
            "SEQ": "FORMAT: %d",
            "extension": "exr",
        }
        built = tmpl.apply_fields(fields)
        self.assertEqual(built, FAILING_CACHED_PATH)
        again = tmpl.get_fields(built)
        self.assertEqual(again["width"], 3200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
