# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _, bold
from frappe.model.document import Document


class UOM(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		category: DF.Link | None
		common_code: DF.Data | None
		description: DF.SmallText | None
		enabled: DF.Check
		must_be_whole_number: DF.Check
		symbol: DF.Data | None
		uom_name: DF.Data
	# end: auto-generated types

	pass


def validate_uoms_are_enabled(doc):
	"""Block disabled UOMs in the document's UOM link fields, including child rows.

	Only values that are new or changed in this save are checked, so documents that
	already use a UOM which was later disabled can still be saved.
	"""
	if doc.get("is_return"):
		# returns keep the UOMs of the voucher they are made against
		return

	doc_before_save = doc.get_doc_before_save()
	old_rows = {row.name: row for row in doc_before_save.get_all_children()} if doc_before_save else {}

	to_check = _get_changed_uom_values(doc, doc_before_save)
	for row in doc.get_all_children():
		to_check.extend(_get_changed_uom_values(row, old_rows.get(row.name)))

	_throw_for_disabled_uoms(doc, to_check)


def validate_row_uoms_are_enabled(parent, row, old_row=None):
	"""Row-level check for flows that save child rows directly, like Update Items on submitted orders."""
	_throw_for_disabled_uoms(parent, _get_changed_uom_values(row, old_row))


def warn_disabled_uoms_in_use(doc):
	"""Warn when a saved document still uses UOMs that were disabled after they were set.

	Run after `validate_uoms_are_enabled`, so any disabled UOM left here was not changed in this save.
	"""
	in_use = _get_changed_uom_values(doc, None)
	for row in doc.get_all_children():
		in_use.extend(_get_changed_uom_values(row, None))

	disabled = _get_disabled_uom_values(in_use)
	if not disabled:
		return

	fields = "".join(f"<li>{_describe_uom_field(doc, *entry)}</li>" for entry in disabled)
	frappe.msgprint(
		_("This {0} still uses disabled UOMs:").format(_(doc.doctype))
		+ f"<ul>{fields}</ul>"
		+ _("New transactions using these UOMs will be blocked."),
		title=_("Disabled UOM"),
		indicator="orange",
	)


def _throw_for_disabled_uoms(doc, to_check):
	for entry in _get_disabled_uom_values(to_check):
		frappe.throw(
			_("{0} is disabled.").format(_describe_uom_field(doc, *entry))
			+ " "
			+ _("Enable it or select a different UOM."),
			title=_("Disabled UOM"),
		)


def _get_disabled_uom_values(entries):
	if not entries:
		return []

	disabled_uoms = set(
		frappe.get_all(
			"UOM",
			filters={"name": ["in", list({value for value, _df, _row in entries})], "enabled": 0},
			pluck="name",
		)
	)
	return [entry for entry in entries if entry[0] in disabled_uoms]


def _describe_uom_field(doc, value, df, row):
	if row.get("parentfield"):
		return _("Row #{0}: {1} {2} in {3}").format(
			row.idx, _(df.label), bold(value), _(doc.meta.get_label(row.parentfield))
		)

	return _("{0} {1}").format(_(df.label), bold(value))


def _get_changed_uom_values(doc, old_doc):
	changed = []
	for df in doc.meta.get_link_fields():
		if df.options != "UOM":
			continue

		value = doc.get(df.fieldname)
		if value and (not old_doc or old_doc.get(df.fieldname) != value):
			changed.append((value, df, doc))

	return changed
