# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.stock.reorder_item import create_material_request, get_items_for_reorder, reorder_item


class TestReorderItem(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.db.rollback)
		self.item_codes = []
		frappe.db.set_single_value("Stock Settings", "auto_indent", 1)
		self.enterContext(patch.object(frappe.local, "reorder_email_notify", 0, create=True))
		self.enterContext(
			patch("erpnext.stock.reorder_item.get_items_for_reorder", side_effect=self.get_test_items)
		)
		self.notify_errors = self.enterContext(patch("erpnext.stock.reorder_item.notify_errors"))

	def test_default_is_submitted(self):
		item = self.make_reorder_item()
		self.assertEqual(item.reorder_levels[0].create_material_request_as_draft, 0)
		mr = self.run_reorder()[0]
		self.assertEqual(mr.docstatus, 1)
		self.assertEqual(mr.status, "Pending")
		self.assertEqual(mr.auto_created_via_reorder, 1)
		self.assertEqual(self.get_requested_qty(item), 20)
		self.assertEqual(self.run_reorder(), [])

	def test_draft_can_be_reviewed_and_submitted(self):
		item = self.make_reorder_item(create_material_request_as_draft=1)
		mr = self.run_reorder()[0]
		self.assertEqual(mr.docstatus, 0)
		self.assertEqual(mr.status, "Draft")
		self.assertEqual(self.get_requested_qty(item), 0)
		self.assertEqual(self.run_reorder(), [])

		mr.items[0].qty = 30
		mr.save()
		mr.submit()
		self.assertEqual(self.get_requested_qty(item), 30)
		self.assertEqual(self.run_reorder(), [])

	def test_grouping_by_company_type_and_draft_preference(self):
		other_warehouse = create_warehouse(
			"_Test Reorder Warehouse", {"parent_warehouse": None}, company="_Test Company 1"
		)
		expected = {}
		for company, warehouse in (
			("_Test Company", "_Test Warehouse - _TC"),
			("_Test Company 1", other_warehouse),
		):
			for request_type in ("Purchase", "Transfer", "Material Issue", "Manufacture"):
				for draft in (0, 1):
					item_codes = set()
					for _ in range(2):
						item = self.make_reorder_item(
							warehouse=warehouse,
							material_request_type=request_type,
							create_material_request_as_draft=draft,
						)
						item_codes.add(item.name)
					mr_type = "Material Transfer" if request_type == "Transfer" else request_type
					expected[(company, mr_type, 1 - draft)] = item_codes

		requests = self.run_reorder()
		self.assertEqual(len(requests), len(expected))
		for mr in requests:
			self.assertEqual(
				{row.item_code for row in mr.items},
				expected[(mr.company, mr.material_request_type, mr.docstatus)],
			)

	def test_pending_drafts_prevent_repeated_requests_for_all_types(self):
		for request_type in ("Purchase", "Transfer", "Material Issue", "Manufacture"):
			self.make_reorder_item(material_request_type=request_type, create_material_request_as_draft=1)
		requests = self.run_reorder()
		self.assertEqual(len(requests), 4)
		self.assertTrue(all(mr.docstatus == 0 for mr in requests))
		self.assertEqual(self.run_reorder(), [])

	def test_pending_draft_is_scoped_to_item_warehouse_and_type(self):
		item = self.make_reorder_item(create_material_request_as_draft=1)
		self.run_reorder()
		item.append(
			"reorder_levels",
			dict(
				item.reorder_levels[0].as_dict(),
				name=None,
				warehouse="_Test Warehouse 1 - _TC",
				warehouse_group=None,
			),
		)
		item.append(
			"reorder_levels",
			dict(item.reorder_levels[0].as_dict(), name=None, material_request_type="Transfer"),
		)
		item.save()
		other_item = self.make_reorder_item(create_material_request_as_draft=1)

		requests = self.run_reorder()
		self.assertEqual(
			{(row.item_code, row.warehouse, mr.material_request_type) for mr in requests for row in mr.items},
			{
				(item.name, "_Test Warehouse 1 - _TC", "Purchase"),
				(item.name, "_Test Warehouse - _TC", "Material Transfer"),
				(other_item.name, "_Test Warehouse - _TC", "Purchase"),
			},
		)

	def test_changing_preference_does_not_bypass_pending_draft(self):
		item = self.make_reorder_item(create_material_request_as_draft=1)
		mr = self.run_reorder()[0]
		item.reorder_levels[0].create_material_request_as_draft = 0
		item.save()
		self.assertEqual(self.run_reorder(), [])
		mr.reload()
		self.assertEqual(mr.docstatus, 0)

		mr.delete()
		self.assertEqual(self.run_reorder()[0].docstatus, 1)

	def test_manual_draft_does_not_block_auto_reorder(self):
		self.make_reorder_item(create_material_request_as_draft=1)
		mr = self.run_reorder()[0]
		mr.auto_created_via_reorder = 0
		mr.save()
		self.assertEqual(len(self.run_reorder()), 1)

	def test_cancelled_request_does_not_block_auto_reorder(self):
		item = self.make_reorder_item(create_material_request_as_draft=1)
		mr = self.run_reorder()[0]
		mr.submit()
		mr.cancel()
		self.assertEqual(self.get_requested_qty(item), 0)
		self.assertEqual(len(self.run_reorder()), 1)

	def test_draft_preserves_purchase_uom_rounding_and_lead_time(self):
		item = self.make_reorder_item(
			properties={"stock_uom": "Kg", "purchase_uom": "Nos", "lead_time_days": 3},
			uoms=[{"uom": "Nos", "conversion_factor": 5}],
			warehouse_reorder_qty=12,
			create_material_request_as_draft=1,
		)
		mr = self.run_reorder()[0]
		row = mr.items[0]
		self.assertEqual((row.uom, row.stock_uom, row.conversion_factor), ("Nos", "Kg", 5))
		self.assertEqual((row.qty, row.stock_qty), (3, 15))
		self.assertEqual(getdate(row.schedule_date), getdate(add_days(nowdate(), 3)))
		self.assertEqual(getdate(mr.schedule_date), getdate(row.schedule_date))
		self.assertEqual(self.get_requested_qty(item), 0)
		self.assertEqual(self.run_reorder(), [])

	def test_auto_reorder_disabled(self):
		self.make_reorder_item(create_material_request_as_draft=1)
		frappe.db.set_single_value("Stock Settings", "auto_indent", 0)
		self.assertIsNone(reorder_item())

	def test_empty_groups_do_not_create_requests(self):
		self.assertEqual(
			create_material_request({"Purchase": {"_Test Company": None, "_Test Company 1": []}}), []
		)
		self.notify_errors.assert_not_called()

	def test_draft_respects_group_warehouse_availability(self):
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		item = self.make_reorder_item(
			warehouse="_Test Warehouse Group-C1 - _TC",
			warehouse_group="_Test Warehouse Group - _TC",
			warehouse_reorder_level=10,
			create_material_request_as_draft=1,
		)
		stock_entry = make_stock_entry(
			item_code=item.name, target="_Test Warehouse Group-C2 - _TC", qty=15, basic_rate=10
		)
		self.assertEqual(self.run_reorder(), [])
		stock_entry.cancel()
		mr = self.run_reorder()[0]
		self.assertEqual(mr.items[0].warehouse, "_Test Warehouse Group-C1 - _TC")
		self.assertEqual(mr.docstatus, 0)

	def test_draft_uses_deficiency_when_above_reorder_quantity(self):
		self.make_reorder_item(warehouse_reorder_level=50, create_material_request_as_draft=1)
		mr = self.run_reorder()[0]
		self.assertEqual(mr.items[0].qty, 50)
		self.assertEqual(mr.items[0].reorder_qty, 20)
		self.assertEqual(mr.items[0].reorder_level, 50)
		self.assertEqual(mr.items[0].projected_on_hand, 0)
		self.assertEqual(self.run_reorder(), [])

	def test_ineligible_items_and_disabled_warehouses_are_skipped(self):
		for properties in ({"disabled": 1}, {"end_of_life": add_days(nowdate(), -1)}):
			self.make_reorder_item(properties=properties, create_material_request_as_draft=1)
		warehouse = create_warehouse("_Test Disabled Reorder Warehouse")
		self.make_reorder_item(warehouse=warehouse, create_material_request_as_draft=1)
		frappe.db.set_value("Warehouse", warehouse, "disabled", 1)
		self.assertEqual(self.run_reorder(), [])

	def test_variant_inherits_draft_preference(self):
		from erpnext.controllers.item_variant import create_variant
		from erpnext.stock.doctype.item.test_item import set_item_variant_settings

		set_item_variant_settings([{"field_name": "reorder_levels"}])
		template = self.make_reorder_item(
			properties={"has_variants": 1, "attributes": [{"attribute": "Test Size"}]},
			create_material_request_as_draft=1,
		)
		variant = create_variant(template.name, {"Test Size": "Small"}).insert()
		self.item_codes.append(variant.name)
		self.assertEqual(variant.reorder_levels[0].create_material_request_as_draft, 1)
		mr = self.run_reorder()[0]
		self.assertEqual(mr.docstatus, 0)
		self.assertEqual([row.item_code for row in mr.items], [variant.name])

		variant.set("reorder_levels", [])
		variant.update_template_tables()
		self.assertEqual(variant.reorder_levels[0].create_material_request_as_draft, 1)

		mr.delete()
		variant.reorder_levels[0].create_material_request_as_draft = 0
		variant.save()
		self.assertEqual(self.run_reorder()[0].docstatus, 1)

	def test_draft_follows_approval_workflow(self):
		from frappe.model.workflow import apply_workflow

		self.addCleanup(frappe.clear_cache, doctype="Material Request")
		for state in ("Draft", "Pending"):
			if not frappe.db.exists("Workflow State", state):
				frappe.get_doc(doctype="Workflow State", workflow_state_name=state).insert()
		if not frappe.db.exists("Workflow Action Master", "Approve"):
			frappe.get_doc(doctype="Workflow Action Master", workflow_action_name="Approve").insert()
		frappe.get_doc(
			doctype="Workflow",
			workflow_name="_Test Reorder Approval",
			document_type="Material Request",
			workflow_state_field="status",
			is_active=1,
			states=[
				{"state": "Draft", "doc_status": "0", "allow_edit": "Stock Manager"},
				{"state": "Pending", "doc_status": "1", "allow_edit": "Stock Manager"},
			],
			transitions=[
				{"state": "Draft", "action": "Approve", "next_state": "Pending", "allowed": "Stock Manager"}
			],
		).insert()
		item = self.make_reorder_item(create_material_request_as_draft=1)
		mr = self.run_reorder()[0]
		self.assertEqual((mr.docstatus, mr.status), (0, "Draft"))
		self.assertEqual(self.get_requested_qty(item), 0)
		mr = apply_workflow(mr, "Approve")
		self.assertEqual((mr.docstatus, mr.status), (1, "Pending"))
		self.assertEqual(self.get_requested_qty(item), 20)

	def test_budget_is_checked_when_draft_is_submitted(self):
		from erpnext.accounts.doctype.budget.budget import BudgetError
		from erpnext.accounts.doctype.budget.test_budget import make_budget
		from erpnext.tests.assertions import assert_raises_with_savepoint

		frappe.db.set_single_value("Accounts Settings", "use_legacy_budget_controller", 0)
		make_budget(
			budget_against="Cost Center",
			applicable_on_material_request=1,
			applicable_on_purchase_order=1,
			action_if_annual_budget_exceeded_on_mr="Stop",
			submit_budget=True,
		)
		item = self.make_reorder_item(create_material_request_as_draft=1)
		mr = self.run_reorder()[0]
		mr.items[0].update(
			{
				"rate": 200001,
				"expense_account": "_Test Account Cost for Goods Sold - _TC",
				"cost_center": "_Test Cost Center - _TC",
			}
		)
		mr.save()
		with assert_raises_with_savepoint(self, BudgetError):
			mr.submit()
		mr.reload()
		self.assertEqual(mr.docstatus, 0)
		self.assertEqual(self.get_requested_qty(item), 0)

	def test_failed_submission_rolls_back_only_its_group(self):
		from erpnext.stock.doctype.material_request.material_request import MaterialRequest

		failed_item = self.make_reorder_item()
		draft_item = self.make_reorder_item(create_material_request_as_draft=1)
		on_submit = MaterialRequest.on_submit

		def fail_after_submit(doc):
			on_submit(doc)
			raise frappe.ValidationError("Reorder submission failed")

		with patch.object(MaterialRequest, "on_submit", fail_after_submit), patch("frappe.log_error"):
			requests = reorder_item()
		self.notify_errors.assert_called_once()
		self.assertEqual(len(requests), 1)
		self.assertEqual(requests[0].docstatus, 0)
		self.assertEqual(requests[0].items[0].item_code, draft_item.name)
		self.assertFalse(frappe.db.exists("Material Request Item", {"item_code": failed_item.name}))
		self.assertEqual(self.get_requested_qty(failed_item), 0)

	def test_notifications_include_both_draft_and_submitted_requests(self):
		self.make_reorder_item()
		self.make_reorder_item(create_material_request_as_draft=1)
		with (
			patch.object(frappe.local, "reorder_email_notify", 1),
			patch("erpnext.stock.reorder_item.send_email_notification") as notify,
		):
			requests = self.run_reorder()
		notify.assert_called_once_with({"_Test Company": requests})
		self.assertEqual({mr.docstatus for mr in requests}, {0, 1})
		for mr in requests:
			message = frappe.render_template("templates/emails/reorder_item.html", {"mr_list": [mr]})
			self.assertIn("Draft" if mr.docstatus == 0 else "Submitted", message)

	def run_reorder(self):
		requests = reorder_item()
		self.notify_errors.assert_not_called()
		return requests

	def make_reorder_item(self, properties=None, uoms=None, **reorder):
		item_code = f"_Test Reorder {self._testMethodName.removeprefix('test_')} {len(self.item_codes)}"
		item = make_item(
			item_code,
			properties={"is_stock_item": 1, "stock_uom": "Nos", **(properties or {})},
			uoms=uoms,
		)
		item.append(
			"reorder_levels",
			{
				"warehouse": "_Test Warehouse - _TC",
				"warehouse_reorder_level": 0,
				"warehouse_reorder_qty": 20,
				"material_request_type": "Purchase",
				**reorder,
			},
		)
		item.save()
		self.item_codes.append(item.name)
		return item

	def get_test_items(self):
		return {name: rows for name, rows in get_items_for_reorder().items() if name in self.item_codes}

	def get_requested_qty(self, item):
		return (
			frappe.db.get_value(
				"Bin", {"item_code": item.name, "warehouse": item.reorder_levels[0].warehouse}, "indented_qty"
			)
			or 0
		)
