import frappe


def execute():
	frappe.reload_doc("stock", "doctype", "bin")

	bins = frappe.get_all("Bin", fields=["name", "warehouse"], filters={"company": ["in", ["", None]]})

	for b in bins:
		if not b.warehouse:
			continue

		company = frappe.db.get_value("Warehouse", b.warehouse, "company")

		if not company:
			continue

		frappe.db.set_value("Bin", b.name, "company", company, update_modified=False)
