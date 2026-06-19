# Copyright (c) 2015, Frappe Technologies and contributors
# For license information, please see license.txt


import frappe
from frappe.model.document import Document
from frappe.query_builder import DocType


class PartyType(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_type: DF.Literal["Payable", "Receivable"]
		party_type: DF.Link
	# end: auto-generated types

	pass


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_party_type(doctype, txt, searchfield, start, page_len, filters):
	PartyType = DocType("Party Type")
	query = frappe.qb.from_(PartyType).select(PartyType.name).orderby(PartyType.name)

	if filters and filters.get("account"):
		account_type = frappe.db.get_value("Account", filters.get("account"), "account_type")
		if account_type in ["Receivable", "Payable"]:
			# Include Employee regardless of its configured account_type, but still respect the text filter
			query = query.where((PartyType.account_type == account_type) | (PartyType.name == "Employee"))
		else:
			query = query.where(PartyType.account_type == account_type)

	query = query.where(getattr(PartyType, searchfield).like(f"%{txt}%"))
	query = query.limit(page_len).offset(start)

	return query.run() or []
