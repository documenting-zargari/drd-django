from unittest.mock import MagicMock

from django.test import SimpleTestCase

from data import category_tree
from data.category_tree import CategoryTree, get_tree
from data.management.commands.drop_redundant_fields import find_hierarchy_mismatches

NODES = [
    {"id": 1, "name": "RLB", "parent_id": None},
    {"id": 469, "name": "Adverbs", "parent_id": 1},
    {"id": 470, "name": "Local", "parent_id": 469},
    {"id": 471, "name": "Local", "parent_id": 470, "is_leaf": True},
    # 'better' > Adjective: is_leaf means "research question", and a
    # question can have child questions of its own.
    {"id": 500, "name": "better", "parent_id": 1, "is_leaf": True},
    {"id": 501, "name": "Adjective", "parent_id": 500, "is_leaf": True},
    {"id": 600, "name": "Adpositions", "parent_id": 1},
    {"id": 601, "name": "andre", "parent_id": 600, "is_leaf": True},
]


class CategoryTreeTests(SimpleTestCase):
    def setUp(self):
        self.tree = CategoryTree(NODES)

    def test_hierarchy_is_root_first_and_includes_the_node(self):
        self.assertEqual(self.tree.hierarchy_ids(471), [1, 469, 470, 471])
        # A name repeated in its own path is kept (intentional in the taxonomy).
        self.assertEqual(self.tree.hierarchy(471), ["RLB", "Adverbs", "Local", "Local"])

    def test_root_and_unknown_ids(self):
        self.assertEqual(self.tree.hierarchy(1), ["RLB"])
        self.assertEqual(self.tree.hierarchy_ids(99999), [])

    def test_cycle_does_not_hang(self):
        tree = CategoryTree([{"id": 5, "name": "a", "parent_id": 6}, {"id": 6, "name": "b", "parent_id": 5}])
        self.assertEqual(tree.hierarchy_ids(5), [6, 5])

    def test_returned_lists_are_copies(self):
        self.tree.hierarchy_ids(471).append(0)
        self.assertEqual(self.tree.hierarchy_ids(471), [1, 469, 470, 471])

    def test_with_hierarchy_overrides_stored_arrays(self):
        stale = {"id": 471, "name": "Local", "hierarchy": ["RLB", "Adverbs", "Local", "Adverbials"]}
        doc = self.tree.with_hierarchy(stale)
        self.assertEqual(doc["hierarchy"], ["RLB", "Adverbs", "Local", "Local"])
        self.assertEqual(doc["hierarchy_ids"], [1, 469, 470, 471])
        self.assertEqual(stale["hierarchy"][-1], "Adverbials")  # input untouched

    def test_is_question(self):
        self.assertTrue(self.tree.is_question(501))
        self.assertFalse(self.tree.is_question(600))
        self.assertFalse(self.tree.is_question(99999))

    def test_search_questions_matches_own_or_ancestor_name(self):
        self.assertEqual(self.tree.search_questions("ANDRE"), [601])
        # Ancestor match surfaces the questions under the category, not the category.
        self.assertEqual(self.tree.search_questions("adposit"), [601])
        self.assertEqual(self.tree.search_questions("better"), [500, 501])

    def test_search_questions_is_literal_not_regex(self):
        self.assertEqual(self.tree.search_questions(".*"), [])

    def test_search_questions_limit(self):
        self.assertEqual(self.tree.search_questions("l", limit=2), [471, 500])


class GetTreeCacheTests(SimpleTestCase):
    def setUp(self):
        category_tree._cache = (None, None)
        self.addCleanup(setattr, category_tree, "_cache", (None, None))

    def _db(self, revision):
        db = MagicMock()
        db.collection.return_value.revision.return_value = revision
        db.aql.execute.side_effect = lambda q, **kw: iter(NODES)
        return db

    def test_reuses_the_tree_while_the_revision_is_unchanged(self):
        db = self._db("r1")
        first = get_tree(db)
        self.assertIs(get_tree(db), first)
        self.assertEqual(db.aql.execute.call_count, 1)
        db.collection.assert_called_with("Categories")

    def test_reloads_when_the_revision_changes(self):
        db = self._db("r1")
        first = get_tree(db)
        db.collection.return_value.revision.return_value = "r2"
        self.assertIsNot(get_tree(db), first)
        self.assertEqual(db.aql.execute.call_count, 2)


class HierarchyMismatchTests(SimpleTestCase):
    def _with_stored(self, overrides=None):
        tree = CategoryTree(NODES)
        docs = [tree.with_hierarchy(n) for n in NODES]
        for d in docs:
            d.update((overrides or {}).get(d["id"], {}))
        return docs

    def test_consistent_stored_arrays(self):
        self.assertEqual(find_hierarchy_mismatches(self._with_stored()), [])

    def test_reports_stale_stored_name(self):
        docs = self._with_stored({471: {"hierarchy": ["RLB", "Adverbs", "Local", "Adverbials"]}})
        problems = find_hierarchy_mismatches(docs)
        self.assertEqual(len(problems), 1)
        self.assertIn("id=471 hierarchy", problems[0])

    def test_skips_nodes_without_stored_arrays(self):
        self.assertEqual(find_hierarchy_mismatches([dict(n) for n in NODES]), [])
