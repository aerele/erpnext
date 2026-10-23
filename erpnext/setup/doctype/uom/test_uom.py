# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json

import frappe

from erpnext.accounts.services.child_item_update import update_child_qty_rate
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.tests.utils import ERPNextTestSuite

DISABLED_UOM = "_Test Disabled UOM"


class TestUOM(ERPNextTestSuite):
	def setUp(self):
		if not frappe.db.exists("UOM", DISABLED_UOM):
			frappe.get_doc({"doctype": "UOM", "uom_name": DISABLED_UOM}).insert()
		frappe.db.set_value("UOM", DISABLED_UOM, "enabled", 0)

	def test_disabled_uom_blocked_as_item_stock_uom(self):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": frappe.generate_hash(length=10),
				"item_group": "Products",
				"stock_uom": DISABLED_UOM,
			}
		)
		self.assertRaisesRegex(frappe.ValidationError, "is disabled", item.insert)

	def test_disabled_uom_blocked_in_item_uoms_table(self):
		item = make_item(properties={"stock_uom": "Nos"})
		item.append("uoms", {"uom": DISABLED_UOM, "conversion_factor": 2})
		self.assertRaisesRegex(frappe.ValidationError, "is disabled", item.save)

	def test_item_with_later_disabled_uom_can_still_be_saved(self):
		frappe.db.set_value("UOM", DISABLED_UOM, "enabled", 1)
		item = make_item(properties={"stock_uom": DISABLED_UOM})
		frappe.db.set_value("UOM", DISABLED_UOM, "enabled", 0)

		item.reload()
		item.description = "edited after the UOM was disabled"
		frappe.clear_messages()
		item.save()

		warnings = [frappe.parse_json(m).get("message", "") for m in frappe.message_log]
		self.assertTrue(any("still uses disabled UOMs" in m and DISABLED_UOM in m for m in warnings))

	def test_disabled_uom_blocked_on_sales_order(self):
		so = make_sales_order(item_code="_Test Item", uom=DISABLED_UOM, do_not_save=True)
		self.assertRaisesRegex(frappe.ValidationError, "is disabled", so.insert)

	def test_disabled_uom_blocked_on_update_items(self):
		so = make_sales_order(item_code="_Test Item", qty=2)
		row = so.items[0]

		changed_row = {"docname": row.name, "item_code": row.item_code, "qty": row.qty, "rate": row.rate}
		changed_row["uom"] = DISABLED_UOM
		self.assertRaisesRegex(
			frappe.ValidationError,
			"is disabled",
			update_child_qty_rate,
			"Sales Order",
			json.dumps([changed_row]),
			so.name,
		)

		existing_row = {"docname": row.name, "item_code": row.item_code, "qty": row.qty, "rate": row.rate}
		new_row = {"item_code": "_Test Item 2", "qty": 1, "rate": 100, "uom": DISABLED_UOM}
		self.assertRaisesRegex(
			frappe.ValidationError,
			"is disabled",
			update_child_qty_rate,
			"Sales Order",
			json.dumps([existing_row, new_row]),
			so.name,
		)

	def test_update_items_allows_row_with_later_disabled_uom(self):
		frappe.db.set_value("UOM", DISABLED_UOM, "enabled", 1)
		so = make_sales_order(item_code="_Test Item", qty=2, uom=DISABLED_UOM)
		frappe.db.set_value("UOM", DISABLED_UOM, "enabled", 0)

		row = so.items[0]
		trans_items = [{"docname": row.name, "item_code": row.item_code, "qty": 3, "rate": row.rate}]
		trans_items[0]["uom"] = row.uom
		update_child_qty_rate("Sales Order", json.dumps(trans_items), so.name)

		so.reload()
		self.assertEqual(so.items[0].qty, 3)
