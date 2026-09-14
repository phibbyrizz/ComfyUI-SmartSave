import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from PIL.PngImagePlugin import PngInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTO_SORT_PATH = PROJECT_ROOT / "auto_sort.py"

spec = importlib.util.spec_from_file_location("smartsave_auto_sort", AUTO_SORT_PATH)
auto_sort = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = auto_sort
spec.loader.exec_module(auto_sort)


class AutoSortRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "output"
        self.output.mkdir(parents=True)
        auto_sort.PROMPT_CACHE.clear()
        auto_sort.ALIASES.clear()

    def tearDown(self):
        auto_sort.PROMPT_CACHE.clear()
        auto_sort.ALIASES.clear()
        self.temp.cleanup()

    def make_png(self, relative_path, prompt=None):
        path = self.output / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (8, 8), "white")
        if prompt is None:
            image.save(path)
        else:
            meta = PngInfo()
            meta.add_text("user_positive_prompt", prompt)
            image.save(path, pnginfo=meta)
        return path

    def make_binary(self, relative_path, content=b"test"):
        path = self.output / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    @staticmethod
    def ollama_response(text):
        return {"message": {"content": text}}

    def test_full_tree_scan_includes_loose_and_canonical_files(self):
        loose = self.make_png("loose.png")
        canonical = self.make_png("Raw/Alice/Alice_00001.png")
        found = set(auto_sort.collect_media_files(str(self.output)))
        self.assertIn(str(loose.resolve()), found)
        self.assertIn(str(canonical.resolve()), found)

    def test_canonical_file_is_kept(self):
        self.make_png("Raw/Alice/Alice_00001.png")
        plans = auto_sort.plan_sort(str(self.output))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].action, "KEEP")
        self.assertEqual(plans[0].subject, "Alice")

    def test_png_metadata_recovers_generic_unsorted_file(self):
        self.make_png(
            "Raw/Unsorted/Unsorted_00001.png",
            "Alice standing near a window",
        )
        with mock.patch.object(
            auto_sort,
            "query_ollama",
            return_value=auto_sort.Classification("Alice", "test", "mock"),
        ):
            plans = auto_sort.plan_sort(str(self.output))
        self.assertEqual(plans[0].action, "MOVE")
        self.assertEqual(plans[0].subject, "Alice")
        self.assertTrue(plans[0].dst.endswith(os.path.join("Raw", "Alice", "Alice_00001.png")))

    def test_unknown_generic_file_stays_unsorted(self):
        self.make_png("Raw/Unsorted/Unsorted_00001.png")
        plans = auto_sort.plan_sort(str(self.output))
        self.assertEqual(plans[0].action, "KEEP")
        self.assertEqual(plans[0].subject, "Unsorted")

    def test_alias_from_filename(self):
        auto_sort.ALIASES["ali"] = "Alice"
        self.make_png("Raw/Ali/Ali_00001.png")
        plans = auto_sort.plan_sort(str(self.output))
        self.assertEqual(plans[0].action, "MOVE")
        self.assertEqual(plans[0].subject, "Alice")

    def test_generic_source_number_is_not_treated_as_generation_number(self):
        self.make_png(
            "Raw/Unsorted/Unsorted_00099.png",
            "Alice standing outdoors",
        )
        with mock.patch.object(
            auto_sort,
            "query_ollama",
            return_value=auto_sort.Classification("Alice", "test", "mock"),
        ):
            plans = auto_sort.plan_sort(str(self.output))
        self.assertTrue(plans[0].dst.endswith("Alice_00001.png"))
        self.assertNotIn("Alice_00099.png", plans[0].dst)

    def test_video_counterpart_preserves_generation_number(self):
        workflow = self.make_png(
            "Raw Video/Alice/Alice_00004_workflow.png",
            "Alice standing outdoors",
        )
        webm = self.make_binary("Webm/Unsorted/Unsorted_00022.webm")
        now = time.time()
        os.utime(workflow, (now, now))
        os.utime(webm, (now + 0.5, now + 0.5))

        with mock.patch.object(
            auto_sort,
            "query_ollama",
            return_value=auto_sort.Classification("Alice", "test", "mock"),
        ):
            plans = auto_sort.plan_sort(str(self.output))

        webm_plan = next(p for p in plans if p.src == str(webm.resolve()))
        self.assertEqual(webm_plan.preferred_index, 4)
        self.assertTrue(webm_plan.dst.endswith("Alice_00004.webm"))

    def test_collision_does_not_overwrite_preferred_destination(self):
        workflow = self.make_png(
            "Raw Video/Alice/Alice_00004_workflow.png",
            "Alice standing outdoors",
        )
        existing = self.make_binary("Webm/Alice/Alice_00004.webm", b"existing")
        webm = self.make_binary("Webm/Unsorted/Unsorted_00022.webm", b"new")
        now = time.time()
        os.utime(workflow, (now, now))
        os.utime(webm, (now + 0.5, now + 0.5))

        with mock.patch.object(
            auto_sort,
            "query_ollama",
            return_value=auto_sort.Classification("Alice", "test", "mock"),
        ):
            plans = auto_sort.plan_sort(str(self.output))

        webm_plan = next(p for p in plans if p.src == str(webm.resolve()))
        self.assertNotEqual(webm_plan.dst, str(existing.resolve()))
        self.assertTrue(existing.exists())
        self.assertEqual(existing.read_bytes(), b"existing")

    def test_apply_then_second_scan_is_idempotent(self):
        source = self.make_png(
            "Raw/Unsorted/Unsorted_00001.png",
            "Alice standing outdoors",
        )
        with mock.patch.object(
            auto_sort,
            "query_ollama",
            return_value=auto_sort.Classification("Alice", "test", "mock"),
        ):
            first = auto_sort.plan_sort(str(self.output))
            self.assertEqual(sum(p.action == "MOVE" for p in first), 1)
            ok = auto_sort.apply_plan(first, str(self.output), purge_empty=True)
            self.assertTrue(ok)
            second = auto_sort.plan_sort(str(self.output))

        self.assertFalse(source.exists())
        self.assertEqual(sum(p.action == "MOVE" for p in second), 0)

    def test_dry_run_does_not_move_files(self):
        source = self.make_png(
            "Raw/Unsorted/Unsorted_00001.png",
            "Alice standing outdoors",
        )
        with mock.patch.object(
            auto_sort,
            "query_ollama",
            return_value=auto_sort.Classification("Alice", "test", "mock"),
        ):
            plans = auto_sort.sort_directory(
                str(self.output),
                apply=False,
                purge_empty=True,
                report_path=None,
                verbose=False,
            )
        self.assertEqual(sum(p.action == "MOVE" for p in plans), 1)
        self.assertTrue(source.exists())

    def test_forensic_report_detects_candidate_outside_two_second_window(self):
        webm = self.make_binary("Webm/Unsorted/Unsorted_00022.webm")
        raw = self.make_png("Raw/Alice/Alice_00004.png", "Alice outdoors")
        now = time.time()
        os.utime(webm, (now, now))
        os.utime(raw, (now + 10.0, now + 10.0))

        diagnostic = auto_sort.unresolved_diagnostic(str(webm), str(self.output))
        self.assertIn(
            "outside the current 2-second counterpart match window",
            diagnostic["likely_failure_stage"],
        )
        self.assertTrue(diagnostic["nearest_candidates"])

    def test_single_word_soupytime_recovers_from_unsorted_metadata(self):
        source = self.make_png(
            "Raw/Unsorted/Unsorted_00007.png",
            "photorealistic candid beach scene, Soupytime walking along the shoreline, natural expression",
        )
        with mock.patch.object(
            auto_sort.ollama,
            "chat",
            return_value=self.ollama_response("Unsorted"),
        ):
            plans = auto_sort.plan_sort(str(self.output))

        plan = next(p for p in plans if p.src == str(source.resolve()))
        self.assertEqual(plan.action, "MOVE")
        self.assertEqual(plan.subject, "Soupytime")
        self.assertTrue(plan.dst.endswith("Soupytime_00001.png"))

    def test_wrong_plausible_lowercase_subject_turns_into_soupytime(self):
        source = self.make_png(
            "Raw/turned/turned_00001.png",
            "photorealistic candid beach scene, Soupytime walking along the shoreline, natural expression",
        )
        with mock.patch.object(
            auto_sort.ollama,
            "chat",
            return_value=self.ollama_response("Unsorted"),
        ):
            plans = auto_sort.plan_sort(str(self.output))

        plan = next(p for p in plans if p.src == str(source.resolve()))
        self.assertEqual(plan.action, "MOVE")
        self.assertEqual(plan.subject, "Soupytime")
        self.assertTrue(
            plan.dst.endswith(os.path.join("Raw", "Soupytime", "Soupytime_00001.png"))
        )

    def test_established_canonical_subject_is_not_reclassified_from_noisy_metadata(self):
        source = self.make_png(
            "Raw/Caitlin_Clark/Caitlin_Clark_00008.png",
            "Soupytime walking along the shoreline",
        )
        with mock.patch.object(auto_sort.ollama, "chat") as ollama_chat:
            plans = auto_sort.plan_sort(str(self.output))

        plan = next(p for p in plans if p.src == str(source.resolve()))
        self.assertEqual(plan.action, "KEEP")
        self.assertEqual(plan.subject, "Caitlin_Clark")
        ollama_chat.assert_not_called()

    def test_unresolved_result_is_not_cached_and_cannot_mask_later_recovery(self):
        prompt = "mysterious subject with no usable identity"

        with mock.patch.object(
            auto_sort.ollama,
            "chat",
            return_value=self.ollama_response("Unsorted"),
        ):
            first = auto_sort.query_ollama(prompt)

        self.assertEqual(first.subject, "Unsorted")
        self.assertNotIn(prompt, auto_sort.PROMPT_CACHE)

        with mock.patch.object(
            auto_sort.ollama,
            "chat",
            return_value=self.ollama_response("Soupytime"),
        ):
            second = auto_sort.query_ollama(prompt)

        self.assertEqual(second.subject, "Soupytime")
        self.assertIn(prompt, auto_sort.PROMPT_CACHE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
