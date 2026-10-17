# Copyright (c) 2017, Velometro Mobility Inc and contributors
# For license information, please see license.txt

import frappe

from erpnext.tests.utils import ERPNextTestSuite


class TestWorkOrderStockReport(ERPNextTestSuite):
	def test_report_executes_and_lists_work_order(self):
		# get_item_list aggregates build_qty with Max() and groups by item_code, so it returns one
		# row per item_code and runs on Postgres (which rejects non-grouped selected columns).
		# This exercises that query on both engines.
		from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record
		from erpnext.manufacturing.report.work_order_stock_report.work_order_stock_report import execute

		wo = make_wo_order_test_record(
			production_item="_Test FG Item", qty=1, source_warehouse="_Test Warehouse - _TC"
		)

		columns, data = execute(frappe._dict(warehouse="_Test Warehouse - _TC"))

		self.assertTrue(columns)
		self.assertIn(wo.name, {row["work_order"] for row in data})

	def test_item_listed_twice_in_bom_is_counted_once(self):
		# A BOM may list the same raw item on multiple lines at different qty (validate_materials does
		# not dedupe). get_item_list aggregates the qty columns and groups by item_code only, so the
		# report stays one row per item — matching the original MariaDB output. The pre-fix multi-column
		# GROUP BY split such an item into one row per distinct stock_qty, inflating "# Req'd Items".
		from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
		from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record
		from erpnext.manufacturing.report.work_order_stock_report.work_order_stock_report import execute
		from erpnext.stock.doctype.item.test_item import make_item

		fg_item = make_item("_Test WO Stock Dup FG", {"is_stock_item": 1}).name
		rm_item = "_Test Item"

		bom = make_bom(item=fg_item, raw_materials=[rm_item], rm_qty=1, currency="INR", do_not_save=True)
		# a second line for the same raw item at a different qty -> a different stock_qty
		first = bom.items[0]
		bom.append(
			"items",
			{
				"item_code": rm_item,
				"qty": 2,
				"uom": first.uom,
				"stock_uom": first.stock_uom,
				"rate": first.rate,
			},
		)
		bom.insert(ignore_permissions=True)
		bom.submit()

		wo = make_wo_order_test_record(
			production_item=fg_item,
			bom_no=bom.name,
			qty=1,
			source_warehouse="_Test Warehouse - _TC",
			skip_transfer=1,
		)

		columns, data = execute(frappe._dict(warehouse="_Test Warehouse - _TC"))

		wo_rows = [row for row in data if row["work_order"] == wo.name]
		self.assertTrue(wo_rows)
		# the duplicated raw item must be counted once per work order item, not once per BOM line
		for row in wo_rows:
			self.assertEqual(row["req_items"], 1)

	def test_selected_warehouse_of_another_company_is_not_counted(self):
		from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
		from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record
		from erpnext.manufacturing.report.work_order_stock_report.work_order_stock_report import execute
		from erpnext.stock.doctype.item.test_item import make_item
		from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import (
			create_stock_reconciliation,
		)
		from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse

		rm_item = make_item(properties={"is_stock_item": 1, "valuation_rate": 10}).name
		fg_item = make_item(properties={"is_stock_item": 1, "valuation_rate": 10}).name
		make_bom(item=fg_item, raw_materials=[rm_item], rm_qty=1)
		other = create_warehouse("_Test WO Stock Report Other", company="_Test Company 1")
		create_stock_reconciliation(
			item_code=rm_item, warehouse=other, qty=500, rate=10, company="_Test Company 1"
		)

		wo = make_wo_order_test_record(production_item=fg_item, qty=1, do_not_submit=True)
		for item in wo.required_items:
			item.source_warehouse = None
		wo.submit()

		filters = frappe._dict(company="_Test Company", warehouse=["_Test Warehouse - _TC", other])
		row = next(row for row in execute(filters)[1] if row["work_order"] == wo.name)
		self.assertEqual(row["ready_to_build"], "N")
