# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe


def execute():
	for profile in frappe.get_all("POS Profile", fields=["name", "currency"]):
		if not profile.currency:
			continue
		for doctype in ("POS Opening Entry", "POS Closing Entry"):
			frappe.db.set_value(
				doctype,
				{"pos_profile": profile.name, "currency": ("is", "not set")},
				"currency",
				profile.currency,
				update_modified=False,
			)
