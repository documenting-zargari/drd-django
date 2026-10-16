from django.test import SimpleTestCase

from data.management.commands.drop_research_questions import find_drift


def _q(id, name="Stative", parent_id=472, hierarchy=None, hierarchy_ids=None):
    return {
        "id": id, "name": name, "parent_id": parent_id,
        "hierarchy": hierarchy or ["RLB", "Adverbs", name],
        "hierarchy_ids": hierarchy_ids or [1, 469, id],
    }


class FindDriftTests(SimpleTestCase):
    """The drop must refuse whenever the ResearchQuestions copy disagrees
    with the Categories leaves — that drift is what caused the 'Adverbs >
    Local > Adverbials' mislabel in the first place."""

    def test_identical_copies_have_no_drift(self):
        self.assertEqual(find_drift([_q(1), _q(2)], [_q(2), _q(1)]), [])

    def test_ignores_dead_tag_fields_on_the_copy(self):
        rq = dict(_q(1), tag_ids=[5], tag_words=["x"])
        self.assertEqual(find_drift([rq], [_q(1)]), [])

    def test_reports_an_id_only_in_the_copy(self):
        problems = find_drift([_q(1), _q(2)], [_q(1)])
        self.assertEqual(len(problems), 1)
        self.assertIn("id=2", problems[0])
        self.assertIn("not a Categories leaf", problems[0])

    def test_reports_a_leaf_missing_from_the_copy(self):
        problems = find_drift([_q(1)], [_q(1), _q(2)])
        self.assertEqual(len(problems), 1)
        self.assertIn("missing from ResearchQuestions", problems[0])

    def test_reports_a_renamed_ancestor_in_one_copy_only(self):
        stale = _q(473, hierarchy=["RLB", "Adverbs", "Local", "Adverbial", "Stative"])
        fixed = _q(473, hierarchy=["RLB", "Adverbs", "Local", "Local", "Stative"])
        problems = find_drift([stale], [fixed])
        self.assertEqual(len(problems), 1)
        self.assertIn("id=473 hierarchy", problems[0])
