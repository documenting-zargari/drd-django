"""
Mutation primitives for the Categories tree.

``Categories`` is the full tree: branch categories and research questions
(the ``is_leaf`` nodes). Until 2026-10 the research questions were also
duplicated into a separate ``ResearchQuestions`` collection, and the two
copies drifted apart — see the "Adverbs > Local > Adverbials" mislabel
(24 Sept 2026 agenda), where Categories.id 471 held a different `name` on
the Köln server than locally. Going through this module (never hand edits
or one-off AQL) is what keeps the stored hierarchy arrays consistent.

Every mutation here is expressed as a ``Plan`` (a list of before/after field
changes) that can be inspected before it's applied, and applying it logs the
plan to the ``CategoryEdits`` collection — an audit trail that upload/merge
tooling for a future Category editor can build on directly instead of
retrofitting one later.

Only ``rename`` is implemented today. ``move``/``create``/``delete`` share
the same shape (build a Plan against Categories, log it on apply) and are left as follow-ups for when the editor work starts.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class FieldChange:
    collection: str
    doc_id: int  # the stable `id` field, not Arango's `_key`
    field: str
    before: object
    after: object


@dataclass
class Plan:
    op: str
    target_id: int
    changes: list = field(default_factory=list)

    def is_noop(self):
        return not self.changes

    def describe(self):
        if self.is_noop():
            return f"{self.op} {self.target_id}: no changes (already up to date)"
        lines = [f"{self.op} {self.target_id}: {len(self.changes)} field change(s)"]
        for c in self.changes:
            lines.append(f"  [{c.collection}] id={c.doc_id} {c.field}: {c.before!r} -> {c.after!r}")
        return "\n".join(lines)


def _get_category(db, category_id):
    cursor = db.aql.execute(
        "FOR c IN Categories FILTER c.id == @id RETURN c",
        bind_vars={"id": category_id},
    )
    docs = list(cursor)
    return docs[0] if docs else None


def plan_rename(db, category_id, new_name):
    """Build the diff for renaming one category node, without writing.

    A node's name appears at a fixed depth (its own position in
    ``hierarchy_ids``) in the ``hierarchy`` array of itself and every
    descendant. ``hierarchy_ids`` itself never changes — ids are stable
    identifiers independent of the name. The depth is recomputed per
    affected document (rather than assumed constant) so a malformed
    hierarchy array can't silently corrupt the wrong slot.
    """
    node = _get_category(db, category_id)
    if node is None:
        raise ValueError(f"No Category with id={category_id}")

    old_name = node["name"]
    plan = Plan(op="rename", target_id=category_id)
    if old_name == new_name:
        return plan

    cursor = db.aql.execute(
        """
        FOR c IN Categories
          FILTER @id IN (c.hierarchy_ids || [])
          LET depth = POSITION(c.hierarchy_ids, @id, true)
          FILTER depth >= 0 AND c.hierarchy[depth] == @old_name
          RETURN c
        """,
        bind_vars={"id": category_id, "old_name": old_name},
    )
    for doc in cursor:
        depth = doc["hierarchy_ids"].index(category_id)
        new_hierarchy = list(doc["hierarchy"])
        new_hierarchy[depth] = new_name
        plan.changes.append(
            FieldChange("Categories", doc["id"], "hierarchy", doc["hierarchy"], new_hierarchy)
        )
        if doc["id"] == category_id:
            plan.changes.append(FieldChange("Categories", doc["id"], "name", doc["name"], new_name))

    return plan


def apply_plan(db, plan, actor=None):
    """Write every change in `plan` and log it to CategoryEdits.

    A no-op plan writes nothing and is not logged.
    """
    if plan.is_noop():
        return

    for change in plan.changes:
        db.aql.execute(
            """
            FOR d IN @@collection
              FILTER d.id == @id
              UPDATE d WITH @patch IN @@collection
            """,
            bind_vars={
                "@collection": change.collection,
                "id": change.doc_id,
                "patch": {change.field: change.after},
            },
        )

    _log_edit(db, plan, actor)


def _log_edit(db, plan, actor):
    if not db.has_collection("CategoryEdits"):
        db.create_collection("CategoryEdits")
    db.collection("CategoryEdits").insert(
        {
            "op": plan.op,
            "target_id": plan.target_id,
            "actor": actor,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "changes": [
                {
                    "collection": c.collection,
                    "id": c.doc_id,
                    "field": c.field,
                    "before": c.before,
                    "after": c.after,
                }
                for c in plan.changes
            ],
        }
    )
