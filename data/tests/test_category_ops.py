from django.test import SimpleTestCase

from data import category_ops


class FakeAql:
    """Records every AQL call and answers plan_rename's two lookup queries
    (the target node's own doc, then one descendant scan per collection)
    from a small in-memory Categories/ResearchQuestions dataset."""

    def __init__(self, categories, research_questions):
        self.categories = categories
        self.research_questions = research_questions
        self.executed = []

    def execute(self, query, bind_vars=None):
        bind_vars = bind_vars or {}
        self.executed.append((query, bind_vars))

        if "FOR c IN Categories FILTER c.id == @id RETURN c" in query:
            return iter([c for c in self.categories if c["id"] == bind_vars["id"]])

        if "FILTER @id IN (c.hierarchy_ids || [])" in query:
            collection = self.categories if bind_vars["@collection"] == "Categories" else self.research_questions
            target_id = bind_vars["id"]
            old_name = bind_vars["old_name"]
            matches = []
            for doc in collection:
                ids = doc.get("hierarchy_ids", [])
                if target_id not in ids:
                    continue
                depth = ids.index(target_id)
                if doc["hierarchy"][depth] == old_name:
                    matches.append(doc)
            return iter(matches)

        if "UPDATE d WITH @patch IN @@collection" in query:
            collection = self.categories if bind_vars["@collection"] == "Categories" else self.research_questions
            for doc in collection:
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
    def __init__(self, categories, research_questions):
        self.aql = FakeAql(categories, research_questions)
        self._collections = {}

    def has_collection(self, name):
        return name in self._collections

    def create_collection(self, name):
        self._collections[name] = FakeCollection()

    def collection(self, name):
        return self._collections.setdefault(name, FakeCollection())


def _make_tree():
    # Adverbs > Local > Adverbial (id 471, mislabeled) > inside (id 472,
    # a Categories-only branch node) > Stative (id 473, a leaf mirrored
    # in ResearchQuestions too) — mirrors the real "Adverbs > Local >
    # Adverbials" mislabel (24 Sept 2026 agenda) at a much smaller scale.
    categories = [
        {"id": 471, "name": "Adverbial", "parent_id": 470,
         "hierarchy": ["RLB", "Adverbs", "Local", "Adverbial"],
         "hierarchy_ids": [1, 469, 470, 471]},
        {"id": 472, "name": "inside", "parent_id": 471,
         "hierarchy": ["RLB", "Adverbs", "Local", "Adverbial", "inside"],
         "hierarchy_ids": [1, 469, 470, 471, 472]},
        {"id": 473, "name": "Stative", "parent_id": 472,
         "hierarchy": ["RLB", "Adverbs", "Local", "Adverbial", "inside", "Stative"],
         "hierarchy_ids": [1, 469, 470, 471, 472, 473]},
        # An unrelated node that happens to also have "Adverbial" as its own
        # name (not this subtree) — must not be touched by the rename.
        {"id": 900, "name": "Adverbial", "parent_id": 1,
         "hierarchy": ["RLB", "Adverbial"],
         "hierarchy_ids": [1, 900]},
    ]
    research_questions = [
        {"id": 473, "name": "Stative", "parent_id": 472,
         "hierarchy": ["RLB", "Adverbs", "Local", "Adverbial", "inside", "Stative"],
         "hierarchy_ids": [1, 469, 470, 471, 472, 473]},
    ]
    return categories, research_questions


class PlanRenameTests(SimpleTestCase):
    def test_raises_for_unknown_id(self):
        db = FakeDb(*_make_tree())
        with self.assertRaises(ValueError):
            category_ops.plan_rename(db, 99999, "Local")

    def test_same_name_is_a_noop(self):
        db = FakeDb(*_make_tree())
        plan = category_ops.plan_rename(db, 471, "Adverbial")
        self.assertTrue(plan.is_noop())

    def test_renames_node_and_cascades_hierarchy_to_descendants(self):
        db = FakeDb(*_make_tree())
        plan = category_ops.plan_rename(db, 471, "Local")
        self.assertFalse(plan.is_noop())

        by_target = {(c.collection, c.doc_id, c.field): c for c in plan.changes}

        # The node itself: both name and hierarchy change.
        self.assertEqual(by_target[("Categories", 471, "name")].after, "Local")
        self.assertEqual(
            by_target[("Categories", 471, "hierarchy")].after,
            ["RLB", "Adverbs", "Local", "Local"],
        )

        # A Categories-only descendant: hierarchy updates, no name change
        # (its own name, "inside", is untouched).
        self.assertEqual(
            by_target[("Categories", 472, "hierarchy")].after,
            ["RLB", "Adverbs", "Local", "Local", "inside"],
        )
        self.assertNotIn(("Categories", 472, "name"), by_target)

        # A leaf mirrored in ResearchQuestions: both collections' hierarchy
        # arrays update, in lockstep.
        self.assertEqual(
            by_target[("Categories", 473, "hierarchy")].after,
            ["RLB", "Adverbs", "Local", "Local", "inside", "Stative"],
        )
        self.assertEqual(
            by_target[("ResearchQuestions", 473, "hierarchy")].after,
            ["RLB", "Adverbs", "Local", "Local", "inside", "Stative"],
        )

    def test_does_not_touch_unrelated_node_with_the_same_old_name(self):
        db = FakeDb(*_make_tree())
        plan = category_ops.plan_rename(db, 471, "Local")
        touched_ids = {c.doc_id for c in plan.changes if c.collection == "Categories"}
        self.assertNotIn(900, touched_ids)


class ApplyPlanTests(SimpleTestCase):
    def test_apply_writes_changes_and_logs_to_category_edits(self):
        categories, research_questions = _make_tree()
        db = FakeDb(categories, research_questions)
        plan = category_ops.plan_rename(db, 471, "Local")

        category_ops.apply_plan(db, plan, actor="mundstein@post.harvard.edu")

        node = next(c for c in categories if c["id"] == 471)
        self.assertEqual(node["name"], "Local")
        self.assertEqual(node["hierarchy"], ["RLB", "Adverbs", "Local", "Local"])

        leaf_mirror = next(q for q in research_questions if q["id"] == 473)
        self.assertEqual(
            leaf_mirror["hierarchy"],
            ["RLB", "Adverbs", "Local", "Local", "inside", "Stative"],
        )

        log = db.collection("CategoryEdits").inserted
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["op"], "rename")
        self.assertEqual(log[0]["target_id"], 471)
        self.assertEqual(log[0]["actor"], "mundstein@post.harvard.edu")
        self.assertEqual(len(log[0]["changes"]), len(plan.changes))

    def test_apply_noop_plan_writes_nothing(self):
        categories, research_questions = _make_tree()
        db = FakeDb(categories, research_questions)
        plan = category_ops.plan_rename(db, 471, "Adverbial")  # same name

        category_ops.apply_plan(db, plan)

        self.assertFalse(db.has_collection("CategoryEdits"))
