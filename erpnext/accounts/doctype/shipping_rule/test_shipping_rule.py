# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe

from erpnext.accounts.doctype.shipping_rule.shipping_rule import (
	FromGreaterThanToError,
	ManyBlankToValuesError,
	OverlappingConditionError,
)
from erpnext.tests.utils import ERPNextTestSuite


class TestShippingRule(ERPNextTestSuite):
	def setUp(self):
		self.load_test_records("Shipping Rule")

	def test_account_company_on_insert(self):
		for rule_type in ("Selling", "Buying"):
			with self.subTest(shipping_rule_type=rule_type):
				shipping_rule = frappe.copy_doc(self.globalTestRecords["Shipping Rule"][0])
				shipping_rule.label = f"{rule_type} Delivery"
				shipping_rule.shipping_rule_type = rule_type
				shipping_rule.company = "_Test Company 1"
				shipping_rule.cost_center = None
				with self.assertRaisesRegex(frappe.ValidationError, "does not belong to Company"):
					shipping_rule.insert()

	def test_switch_rule_preserves_manual_charges_and_tax_row_references(self):
		for doctype, rule_type in (("Quotation", "Selling"), ("Purchase Order", "Buying")):
			with self.subTest(doctype=doctype):
				first = create_shipping_rule(rule_type, f"_Test First {rule_type} Shipping")
				second = create_shipping_rule(rule_type, f"_Test Second {rule_type} Shipping")
				second.account = "_Test Account Service Tax - _TC"
				second.save()
				doc = frappe.new_doc(doctype)
				doc.company = first.company
				doc.shipping_rule = first.name
				manual = doc.append(
					"taxes",
					{
						"charge_type": "Actual",
						"account_head": first.account,
						"description": "Manual handling",
						"tax_amount": 25,
						"cost_center": first.cost_center,
					},
				)
				first.add_shipping_rule_to_tax_table(doc, 100)
				shipping = doc.taxes[-1]
				shipping.description = "Delivery instructions"
				first.add_shipping_rule_to_tax_table(doc, 100)
				self.assertEqual(shipping.description, "Delivery instructions")
				dependent = doc.append("taxes", {"charge_type": "On Previous Row Amount", "row_id": "2"})
				doc.shipping_rule = second.name
				second.add_shipping_rule_to_tax_table(doc, 250)
				second.add_shipping_rule_to_tax_table(doc, 250)
				self.assertEqual(len(doc.taxes), 3)
				self.assertEqual(manual.tax_amount, 25)
				self.assertIs(doc.taxes[1], shipping)
				self.assertEqual(shipping.account_head, second.account)
				self.assertEqual(shipping.tax_amount, 250)
				self.assertEqual(shipping.shipping_rule, second.name)
				self.assertEqual(shipping.description, second.label)
				self.assertEqual(dependent.row_id, "2")

	def test_switch_saved_legacy_shipping_charge(self):
		from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order

		first = create_shipping_rule("Selling", "_Test Legacy Shipping")
		second = create_shipping_rule("Selling", "_Test Replacement Shipping")
		second.account = "_Test Account Service Tax - _TC"
		second.save()
		order = make_sales_order(do_not_save=True)
		order.shipping_rule = first.name
		order.save()
		charge_name = order.taxes[-1].name
		frappe.db.set_value("Sales Taxes and Charges", charge_name, "shipping_rule", None)
		order.reload()
		order.shipping_rule = second.name
		order.save()
		order.reload()
		self.assertEqual(len(order.taxes), 1)
		self.assertEqual(order.taxes[0].name, charge_name)
		self.assertEqual(order.taxes[0].account_head, second.account)
		self.assertEqual(order.taxes[0].shipping_rule, second.name)
		order.taxes[0].description = "Deliver to the loading dock"
		order.save()
		order.reload()
		self.assertEqual(order.taxes[0].description, "Deliver to the loading dock")

	def test_account_company_on_update(self):
		shipping_rule = create_shipping_rule("Selling", "Standard Delivery")
		shipping_rule.company = "_Test Company 1"
		shipping_rule.cost_center = None
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong to Company"):
			shipping_rule.save()

		shipping_rule.reload()
		shipping_rule.company = "_Test Company 1"
		shipping_rule.account = "_Test Account Shipping Charges - _TC1"
		shipping_rule.cost_center = None
		shipping_rule.save()
		shipping_rule.reload()
		self.assertEqual(shipping_rule.company, "_Test Company 1")
		self.assertEqual(shipping_rule.account, "_Test Account Shipping Charges - _TC1")

		shipping_rule.account = "_Test Account Shipping Charges - _TC"
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong to Company"):
			shipping_rule.save()

	def test_from_greater_than_to(self):
		shipping_rule = frappe.copy_doc(self.globalTestRecords["Shipping Rule"][0])
		shipping_rule.name = self.globalTestRecords["Shipping Rule"][0].get("name")
		shipping_rule.get("conditions")[0].from_value = 101
		self.assertRaises(FromGreaterThanToError, shipping_rule.insert)

	def test_many_zero_to_values(self):
		shipping_rule = frappe.copy_doc(self.globalTestRecords["Shipping Rule"][0])
		shipping_rule.name = self.globalTestRecords["Shipping Rule"][0].get("name")
		shipping_rule.get("conditions")[0].to_value = 0
		self.assertRaises(ManyBlankToValuesError, shipping_rule.insert)

	def test_overlapping_conditions(self):
		for range_a, range_b in [
			((50, 150), (0, 100)),
			((50, 150), (100, 200)),
			((50, 150), (75, 125)),
			((50, 150), (25, 175)),
			((50, 150), (50, 150)),
		]:
			shipping_rule = frappe.copy_doc(self.globalTestRecords["Shipping Rule"][0])
			shipping_rule.name = self.globalTestRecords["Shipping Rule"][0].get("name")
			shipping_rule.get("conditions")[0].from_value = range_a[0]
			shipping_rule.get("conditions")[0].to_value = range_a[1]
			shipping_rule.get("conditions")[1].from_value = range_b[0]
			shipping_rule.get("conditions")[1].to_value = range_b[1]
			self.assertRaises(OverlappingConditionError, shipping_rule.insert)


def create_shipping_rule(shipping_rule_type, shipping_rule_name):
	if frappe.db.exists("Shipping Rule", shipping_rule_name):
		return frappe.get_doc("Shipping Rule", shipping_rule_name)

	sr = frappe.new_doc("Shipping Rule")
	sr.account = "_Test Account Shipping Charges - _TC"
	sr.calculate_based_on = "Net Total"
	sr.company = "_Test Company"
	sr.cost_center = "_Test Cost Center - _TC"
	sr.label = shipping_rule_name
	sr.name = shipping_rule_name
	sr.shipping_rule_type = shipping_rule_type

	sr.append(
		"conditions",
		{
			"doctype": "Shipping Rule Condition",
			"from_value": 0,
			"parentfield": "conditions",
			"shipping_amount": 50.0,
			"to_value": 100,
		},
	)
	sr.append(
		"conditions",
		{
			"doctype": "Shipping Rule Condition",
			"from_value": 101,
			"parentfield": "conditions",
			"shipping_amount": 100.0,
			"to_value": 200,
		},
	)
	sr.append(
		"conditions",
		{
			"doctype": "Shipping Rule Condition",
			"from_value": 201,
			"parentfield": "conditions",
			"shipping_amount": 200.0,
			"to_value": 2000,
		},
	)
	sr.insert(ignore_permissions=True)

	return sr
