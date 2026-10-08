# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html


class TaxCategory(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		disabled: DF.Check
		title: DF.Data
	# end: auto-generated types


def get_enabled_tax_category(tax_category):
	if tax_category and not frappe.get_cached_value("Tax Category", tax_category, "disabled"):
		return tax_category
	return ""


def validate_tax_category(doc, method=None):
	tax_category = doc.get("tax_category")
	if tax_category and not get_enabled_tax_category(tax_category) and not is_original_return_category(doc):
		frappe.throw(
			_("Tax Category {0} is disabled. Please select an enabled Tax Category.").format(
				frappe.bold(escape_html(tax_category))
			),
			title=_("Disabled Tax Category"),
		)


def is_original_return_category(doc):
	party_fields = {
		"Sales Invoice": "customer",
		"POS Invoice": "customer",
		"Delivery Note": "customer",
		"Purchase Invoice": "supplier",
		"Purchase Receipt": "supplier",
	}
	party_field = party_fields.get(doc.doctype)
	if not party_field or not doc.get("is_return") or not doc.get("return_against"):
		return False

	return bool(
		frappe.db.exists(
			doc.doctype,
			{
				"name": doc.return_against,
				"docstatus": 1,
				"is_return": 0,
				"company": doc.company,
				party_field: doc.get(party_field),
				"tax_category": doc.tax_category,
			},
		)
	)
