from django.test import SimpleTestCase

from data import category_ops
from data.category_tree import CategoryTree


class FakeAql:
    """Records every AQL call and answers plan_rename's node lookup and
    apply_plan's update from a small in-memory Categories dataset."""

    def __init__(self, categories):
        self.categories = categories
        self.executed = []

    def execute(self, query, bind_vars=None):
        bind_vars = bind_vars or {}
        self.executed.append((query, bind_vars))

        if "FOR c IN Categories FILTER c.id == @id RETURN c" in query:
            return iter([c for c in self.categories if c["id"] == bind_vars["id"]])

        if "UPDATE d WITH @patch IN @@collection" in query:
            assert bind_vars["@collection"] == "Categories"
            for doc in self.categories:
                if doc["id"] == bind_vars["id"]:
                    doc.update(bind_vars["patch"])
            return iter([])

        raise AssertionError(f"Unexpected AQL query in test: {query}")


class FakeCollection:
    def __init__(self):
        self.inserted = []

    def insert(self, doc):
        self.inserted.append(doc)


class FakeDb:
    def __init__(self, categories):
        self.aql = FakeAql(categories)
        self._collections = {}

    def has_collection(self, name):
        return name in self._collections

    def create_collection(self, name):
        self._collections[name] = FakeCollection()

    def collection(self, name):
        return self._collections.setdefault(name, FakeCollection())


def _make_tree():
    # Adverbs > Local > Adverbial (id 471, mislabeled) > inside (id 472,
    # a branch node) > Stative (id 473, a research question, is_leaf) — mirrors
    # the real "Adverbs > Local > Adverbials" mislabel (24 Sept 2026 agenda)
    # at a much smaller scale.
    return [
        {"id": 1, "name": "RLB", "parent_id": None},
        {"id": 469, "name": "Adverbs", "parent_id": 1},
        {"id": 470, "name": "Local", "parent_id": 469},
        {"id": 471, "name": "Adverbial", "parent_id": 470},
        {"id": 472, "name": "inside", "parent_id": 471},
        {"id": 473, "name": "Stative", "parent_id": 472, "is_leaf": True},
        # An unrelated node that happens to also have "Adverbial" as its own
        # name (not this subtree) — must not be touched by the rename.
        {"id": 900, "name": "Adverbial", "parent_id": 1},
    ]


class PlanRenameTests(SimpleTestCase):
    def test_raises_for_unknown_id(self):
        db = FakeDb(_make_tree())
        with self.assertRaises(ValueError):
            category_ops.plan_rename(db, 99999, "Local")

    def test_same_name_is_a_noop(self):
        db = FakeDb(_make_tree())
        plan = category_ops.plan_rename(db, 471, "Adverbial")
        self.assertTrue(plan.is_noop())

    def test_renames_only_the_node_itself(self):
        # Descendants no longer store their ancestors' names (the hierarchy
        # is computed from parent_id), so a rename is one field change.
        db = FakeDb(_make_tree())
        plan = category_ops.plan_rename(db, 471, "Local")
        self.assertEqual(
            [(c.collection, c.doc_id, c.field, c.before, c.after) for c in plan.changes],
            [("Categories", 471, "name", "Adverbial", "Local")],
        )

    def test_only_touches_categories(self):
        # Regression: the ResearchQuestions mirror was folded into Categories
        # (2026-10); a rename must not plan writes to any other collection.
        db = FakeDb(_make_tree())
        plan = category_ops.plan_rename(db, 471, "Local")
        self.assertEqual({c.collection for c in plan.changes}, {"Categories"})

    def test_does_not_touch_unrelated_node_with_the_same_old_name(self):
        db = FakeDb(_make_tree())
        plan = category_ops.plan_rename(db, 471, "Local")
        touched_ids = {c.doc_id for c in plan.changes if c.collection == "Categories"}
        self.assertNotIn(900, touched_ids)


class ApplyPlanTests(SimpleTestCase):
    def test_apply_writes_changes_and_logs_to_category_edits(self):
        categories = _make_tree()
        db = FakeDb(categories)
        plan = category_ops.plan_rename(db, 471, "Local")

        category_ops.apply_plan(db, plan, actor="mundstein@post.harvard.edu")

        node = next(c for c in categories if c["id"] == 471)
        self.assertEqual(node["name"], "Local")

        # Descendants see the new name through the computed hierarchy.
        self.assertEqual(
            CategoryTree(categories).hierarchy(473),
            ["RLB", "Adverbs", "Local", "Local", "inside", "Stative"],
        )

        log = db.collection("CategoryEdits").inserted
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["op"], "rename")
        self.assertEqual(log[0]["target_id"], 471)
        self.assertEqual(log[0]["actor"], "mundstein@post.harvard.edu")
        self.assertEqual(len(log[0]["changes"]), len(plan.changes))

    def test_apply_noop_plan_writes_nothing(self):
        categories = _make_tree()
        db = FakeDb(categories)
        plan = category_ops.plan_rename(db, 471, "Adverbial")  # same name

        category_ops.apply_plan(db, plan)

        self.assertFalse(db.has_collection("CategoryEdits"))
