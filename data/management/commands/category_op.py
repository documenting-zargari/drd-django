"""
CLI for the Categories/ResearchQuestions tree mutation primitives in
data/category_ops.py. Subcommand structure so it grows into the future
Category/ResearchQuestion editor's data-manipulation toolkit without a
rewrite — `move`/`create`/`delete` add subcommands here, each backed by a
new `plan_*`/`apply_plan` pair in category_ops.py.

Every subcommand prints its diff first; nothing is written without --apply.

Usage:
    python manage.py category_op rename --id 471 --name Local
    python manage.py category_op rename --id 471 --name Local --apply
"""

from django.core.management.base import BaseCommand, CommandError

from data import category_ops
from data.models import Category


class Command(BaseCommand):
    help = "Inspect and apply structural edits to the Categories/ResearchQuestions tree."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="subcommand", required=True)

        rename = subparsers.add_parser("rename", help="Rename one category node.")
        rename.add_argument("--id", type=int, required=True, help="Categories.id of the node to rename.")
        rename.add_argument("--name", type=str, required=True, help="New name for the node.")
        rename.add_argument("--apply", action="store_true", help="Write the changes (default: dry-run).")
        rename.add_argument("--actor", type=str, default=None, help="Recorded in CategoryEdits as who made the change.")

    def handle(self, *args, **options):
        if options["subcommand"] == "rename":
            self._handle_rename(options)

    def _handle_rename(self, options):
        db = Category.db()
        try:
            plan = category_ops.plan_rename(db, options["id"], options["name"])
        except ValueError as e:
            raise CommandError(str(e))

        self.stdout.write(plan.describe())

        if plan.is_noop():
            return

        if options["apply"]:
            category_ops.apply_plan(db, plan, actor=options["actor"])
            self.stdout.write(self.style.SUCCESS(f"Applied {len(plan.changes)} change(s)."))
        else:
            self.stdout.write(self.style.WARNING("Dry-run only — re-run with --apply to write these changes."))
