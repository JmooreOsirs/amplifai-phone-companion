"""Whole verified contact snapshots, with an unchanged Mac Foundation oracle."""
from __future__ import annotations

import copy
import json
import sys
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contact_workspace import ContactWorkspace


def preview(identifier, name=None, phone_count=1):
    return {"id": identifier, "name": name if name is not None else f"Person {identifier}",
            "phoneCount": phone_count, "phoneEnds": ["0123"] * phone_count}


def snapshot(workspace):
    return {
        "group": workspace.group, "query": workspace.query,
        "knownIDs": sorted(workspace.known_ids), "mainIDs": sorted(workspace.main_ids),
        "reviewIDs": sorted(workspace.review_ids), "filteredIDs": sorted(workspace.filtered_ids),
        "visibleIDs": [item["id"] for item in workspace.visible],
        "mainCount": workspace.main_count, "reviewCount": workspace.review_count,
        "filteredCount": workspace.filtered_count, "pageStart": workspace.page_start,
        "hasNextPage": workspace.has_next_page, "hasPreviousPage": workspace.has_previous_page,
        "isScoped": workspace.is_scoped, "scopeLabel": workspace.scope_label,
        "reasons": {str(identifier): workspace.reason(identifier) for identifier in sorted(workspace.known_ids)},
    }


class MacContactGroupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent / "fixtures/mac-contact-groups.json").read_text(encoding="utf-8"))

    def workspace(self):
        rows = self.fixture["previews"]
        return ContactWorkspace(rows, len(rows))

    def test_exact_mac_default_group_counts_and_reasons(self):
        self.assertEqual(snapshot(self.workspace()), self.fixture["initial"])

    def test_exact_mac_search_case_accents_unicode_and_scope_labels(self):
        workspace = self.workspace()
        for case in self.fixture["searches"]:
            with self.subTest(group=case["group"], query=case["query"]):
                workspace.show(case["group"])
                workspace.search(case["query"])
                actual = snapshot(workspace)
                self.assertEqual({key: actual[key] for key in case["expected"]}, case["expected"])

    def test_mac_unicode_version_boundary_queries(self):
        cases = self.fixture["versionBoundaryCases"]
        workspace = ContactWorkspace(cases["previews"], len(cases["previews"]))
        workspace.show("All")
        for case in cases["searches"]:
            with self.subTest(query=case["query"]):
                workspace.search(case["query"])
                self.assertEqual(sorted(workspace.filtered_ids), case["expectedIDs"])

    def test_long_grapheme_names_keep_search_storage_bounded(self):
        rows = [preview(identifier, "A" * 237 + "👩‍💻") for identifier in range(1000)]
        workspace = ContactWorkspace(rows, len(rows))
        seen = set()

        def retained(value):
            if id(value) in seen:
                return 0
            seen.add(id(value))
            result = sys.getsizeof(value)
            if isinstance(value, dict):
                return result + sum(retained(key) + retained(item) for key, item in value.items())
            if isinstance(value, (list, tuple, set, frozenset)):
                return result + sum(retained(item) for item in value)
            return result

        self.assertLess(retained(workspace.__dict__), 2 * 1024 * 1024)
        workspace.search("💻")
        self.assertEqual(workspace.filtered_count, 0)
        workspace.search("👩‍💻")
        self.assertEqual(workspace.filtered_count, len(rows))

    def test_exact_mac_moves_preserve_reasons_and_reject_mixed_sources(self):
        workspace = self.workspace()
        for case in self.fixture["moves"]:
            with self.subTest(move=case["operation"]):
                self.assertEqual(workspace.move(**case["operation"]), case["success"])
                self.assertEqual(snapshot(workspace), case["expected"])

    def test_exact_mac_search_respects_complex_character_boundaries(self):
        fixture = self.fixture["boundaryCases"]
        workspace = ContactWorkspace(fixture["previews"], len(fixture["previews"]))
        workspace.show("All")
        for case in fixture["searches"]:
            with self.subTest(query=case["query"]):
                workspace.search(case["query"])
                self.assertEqual([item["id"] for item in workspace.visible], case["expectedIDs"])

    def test_snapshot_rejects_incomplete_duplicate_or_invalid_identity(self):
        for rows, count in (([preview(1)], 2), ([preview(1), preview(1)], 2),
                            ([preview(True)], 1), ([preview(-1)], 1), ([preview("1")], 1),
                            ([preview(1)], True), ([], -1)):
            with self.subTest(rows=rows, count=count), self.assertRaises(ValueError):
                ContactWorkspace(rows, count)
        for key, value in (("name", None), ("phoneCount", True), ("phoneCount", -1)):
            row = preview(1)
            row[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                ContactWorkspace([row], 1)

    def test_empty_snapshot_and_page_boundaries_are_stable(self):
        workspace = ContactWorkspace([], 0)
        workspace.next_page()
        workspace.previous_page()
        self.assertEqual((workspace.visible, workspace.page_start, workspace.filtered_count), ([], 0, 0))
        self.assertFalse(workspace.has_next_page)
        self.assertFalse(workspace.has_previous_page)

    def test_full_scope_and_stable_order_cover_more_than_one_page(self):
        rows = [preview(identifier) for identifier in range(625, 0, -1)]
        workspace = ContactWorkspace(rows, 625)
        self.assertEqual([item["id"] for item in workspace.visible], list(range(625, 425, -1)))
        self.assertEqual(workspace.filtered_ids, set(range(1, 626)))
        workspace.next_page()
        self.assertEqual(workspace.page_start, 200)
        workspace.next_page()
        workspace.next_page()
        self.assertEqual([item["id"] for item in workspace.visible], list(range(25, 0, -1)))
        workspace.next_page()
        self.assertEqual(workspace.page_start, 600)
        workspace.previous_page()
        self.assertEqual(workspace.page_start, 400)
        workspace.show("All")
        self.assertEqual(workspace.page_start, 0)
        self.assertFalse(workspace.is_scoped)
        workspace.next_page()
        workspace.search(" Person 6 ")
        self.assertEqual(workspace.page_start, 0)
        self.assertTrue(workspace.is_scoped)
        self.assertEqual([item["id"] for item in workspace.visible],
                         [item["id"] for item in rows if "person 6" in item["name"].lower()])

    def test_cross_page_move_is_atomic_and_rebuilds_current_scope(self):
        workspace = ContactWorkspace([preview(identifier) for identifier in range(425)], 425)
        workspace.next_page()
        self.assertFalse(workspace.move({1, 1000}, "Main", "Review"))
        self.assertEqual(workspace.page_start, 200)
        self.assertEqual(workspace.review_ids, set())
        moved = {1, 200, 424}
        self.assertTrue(workspace.move(moved, "Main", "Review"))
        self.assertEqual(workspace.page_start, 0)
        self.assertEqual((workspace.main_count, workspace.review_count), (422, 3))
        workspace.show("Review")
        self.assertEqual([item["id"] for item in workspace.visible], [1, 200, 424])
        for identifier in moved:
            self.assertEqual(workspace.reason(identifier), "Moved to Review by you")
        self.assertFalse(workspace.move({1, 2}, "Review", "Main"))
        self.assertEqual(workspace.review_ids, moved)
        self.assertTrue(workspace.move(moved, "Review", "Main"))
        self.assertEqual(workspace.visible, [])

    def test_invalid_groups_and_moves_never_change_workspace(self):
        workspace = ContactWorkspace([preview(1)], 1)
        initial = snapshot(workspace)
        for source, destination in (("Main", "Main"), ("All", "Review"), ("Main", "All"),
                                    ("Unknown", "Review"), ("Main", "Unknown")):
            self.assertFalse(workspace.move({1}, source, destination))
        self.assertFalse(workspace.move({True}, "Main", "Review"))
        self.assertFalse(workspace.move([], "Main", "Review"))
        with self.assertRaises(ValueError):
            workspace.show("Unknown")
        self.assertEqual(snapshot(workspace), initial)
        self.assertTrue(workspace.contains(1))
        self.assertFalse(workspace.contains(True))
        self.assertFalse(workspace.is_in_review(2))
        self.assertIsNone(workspace.reason(2))

    def test_verified_snapshot_cannot_be_mutated_through_input_or_output(self):
        rows = [preview(1, "Avery")]
        original = copy.deepcopy(rows)
        workspace = ContactWorkspace(rows, 1)
        rows[0]["id"] = 5
        rows[0]["name"] = "Acme LLC"
        rows[0]["phoneEnds"][0] = "9999"
        output = workspace.previews
        output[0]["name"] = "Replacement"
        output[0]["phoneEnds"].clear()
        self.assertEqual(workspace.previews, original)
        visible = workspace.visible
        visible[0]["name"] = "Also replaced"
        self.assertEqual(workspace.visible, original)


if __name__ == "__main__":
    unittest.main()
