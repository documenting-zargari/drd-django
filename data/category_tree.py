"""
In-memory view of the ``Categories`` tree.

A node's ancestor chain — ``hierarchy`` (names, root first) and
``hierarchy_ids`` — is derived here from ``parent_id`` at read time rather
than stored on every document. Storing it meant a rename had to rewrite
every descendant, and copies of it drifted (see ``category_ops``); since
2026-11 ``parent_id`` + ``name`` are the only source of truth and the API
still returns both arrays, computed.

The whole tree is small (~3.6k nodes, ~20ms to load), so each process keeps
one copy and reloads it only when the collection's revision changes (a ~2ms
check). That also picks up writes made by other processes or pods. Call
``get_tree`` once per request/action and reuse the result.
"""

_LOAD_AQL = "FOR c IN Categories RETURN {id: c.id, name: c.name, parent_id: c.parent_id, is_leaf: c.is_leaf}"

_cache = (None, None)  # (collection revision, CategoryTree)


class CategoryTree:
    def __init__(self, nodes):
        self.nodes = {n["id"]: n for n in nodes}
        self._ids_cache = {}

    def get(self, category_id):
        return self.nodes.get(category_id)

    def is_question(self, category_id):
        node = self.nodes.get(category_id)
        return bool(node and node.get("is_leaf") is True)

    def hierarchy_ids(self, category_id):
        """Root-first ids from the root down to `category_id` itself ([] if
        unknown). A walk that hits a missing parent or a cycle stops there."""
        ids = self._ids_cache.get(category_id)
        if ids is None:
            ids, seen, current = [], set(), category_id
            while current in self.nodes and current not in seen:
                seen.add(current)
                ids.append(current)
                current = self.nodes[current].get("parent_id")
            ids.reverse()
            self._ids_cache[category_id] = ids
        return list(ids)

    def hierarchy(self, category_id):
        return [self.nodes[i]["name"] for i in self.hierarchy_ids(category_id)]

    def with_hierarchy(self, doc):
        """A copy of a Categories doc with its computed hierarchy arrays."""
        return {
            **doc,
            "hierarchy": self.hierarchy(doc["id"]),
            "hierarchy_ids": self.hierarchy_ids(doc["id"]),
        }

    def search_questions(self, query, limit=50):
        """Ids of research questions whose own name or any ancestor's name
        contains `query` (case-insensitive), sorted by id."""
        needle = query.casefold()
        hits = (
            i for i in sorted(self.nodes)
            if self.is_question(i)
            and any(needle in (name or "").casefold() for name in self.hierarchy(i))
        )
        return [i for _, i in zip(range(limit), hits)]


def get_tree(db):
    """The process-wide CategoryTree, reloaded if Categories changed."""
    global _cache
    revision = db.collection("Categories").revision()
    cached_revision, tree = _cache
    if tree is None or revision != cached_revision:
        tree = CategoryTree(db.aql.execute(_LOAD_AQL))
        _cache = (revision, tree)
    return tree
