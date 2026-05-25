# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
import json
from frappe.tests import IntegrationTestCase

from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.get_item_details import get_item_details
from erpnext.accounts.doctype.pricing_rule.pricing_rule import apply_margin_rule
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.accounts.doctype.pricing_rule.utils import validate_coupon_applicability
from erpnext.accounts.doctype.pricing_rule.utils import coupon_rule_has_matching_items
from erpnext.accounts.doctype.pricing_rule.utils import get_coupon_pricing_rule
from erpnext.accounts.doctype.pricing_rule.pricing_rule import apply_price_discount_rule
from erpnext.accounts.doctype.pricing_rule.pricing_rule import remove_pricing_rule_for_item
from erpnext.accounts.doctype.pricing_rule.pricing_rule import get_pricing_rule_for_item
from erpnext.accounts.doctype.pricing_rule.utils import get_applied_pricing_rules
from erpnext.accounts.doctype.pricing_rule.pricing_rule import apply_price_discount_rule
from erpnext.accounts.doctype.pricing_rule.pricing_rule import remove_pricing_rule_for_item
from erpnext.accounts.doctype.pricing_rule.utils import apply_pricing_rule_for_free_items
from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
from frappe.utils import add_days, nowdate


class TestPricingRule(IntegrationTestCase):
	def setUp(self):
		delete_existing_pricing_rules()
		setup_pricing_rule_data()
		self.enterClassContext(self.change_settings("Selling Settings", validate_selling_price=0))

	def tearDown(self):
		delete_existing_pricing_rules()

	def test_pricing_rule_for_discount(self):
		from frappe import MandatoryError

		from erpnext.stock.get_item_details import get_item_details

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"items": [{"item_code": "_Test Item"}],
			"currency": "USD",
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"discount_percentage": 10,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		args = frappe._dict(
			{
				"item_code": "_Test Item",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
			}
		)
		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 10)

		prule = frappe.get_doc(test_record.copy())
		prule.priority = 1
		prule.applicable_for = "Customer"
		prule.title = "_Test Pricing Rule for Customer"
		self.assertRaises(MandatoryError, prule.insert)

		prule.customer = "_Test Customer"
		prule.discount_percentage = 20
		prule.insert()
		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 20)

		prule = frappe.get_doc(test_record.copy())
		prule.apply_on = "Item Group"
		prule.items = []
		prule.append("item_groups", {"item_group": "All Item Groups"})
		prule.title = "_Test Pricing Rule for Item Group"
		prule.discount_percentage = 15
		prule.insert()

		args.customer = "_Test Customer 1"
		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 10)

		prule = frappe.get_doc(test_record.copy())
		prule.applicable_for = "Campaign"
		prule.campaign = "_Test Campaign"
		prule.title = "_Test Pricing Rule for Campaign"
		prule.discount_percentage = 5
		prule.priority = 8
		prule.insert()

		args.campaign = "_Test Campaign"
		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 5)

		frappe.db.sql("update `tabPricing Rule` set priority=NULL where campaign='_Test Campaign'")
		from erpnext.accounts.doctype.pricing_rule.utils import MultiplePricingRuleConflict

		self.assertRaises(MultiplePricingRuleConflict, get_item_details, args)

		args.item_code = "_Test Item 2"
		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 15)

	def test_pricing_rule_for_margin(self):
		from erpnext.stock.get_item_details import get_item_details

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"items": [
				{
					"item_code": "_Test FG Item 2",
				}
			],
			"selling": 1,
			"currency": "USD",
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"margin_type": "Percentage",
			"margin_rate_or_amount": 10,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		item_price = frappe.get_doc(
			{
				"doctype": "Item Price",
				"price_list": "_Test Price List 2",
				"item_code": "_Test FG Item 2",
				"price_list_rate": 100,
			}
		)

		item_price.insert(ignore_permissions=True)

		args = frappe._dict(
			{
				"item_code": "_Test FG Item 2",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
			}
		)
		details = get_item_details(args)
		self.assertEqual(details.get("margin_type"), "Amount")
		self.assertEqual(details.get("margin_rate_or_amount"), 10)

	def test_mixed_conditions_for_item_group(self):
		for item in ["Mixed Cond Item 1", "Mixed Cond Item 2"]:
			make_item(item, {"item_group": "Products"})
			make_item_price(item, "_Test Price List", 100)

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule for Item Group",
			"apply_on": "Item Group",
			"item_groups": [
				{
					"item_group": "Products",
				},
				{
					"item_group": "_Test Item Group",
				},
			],
			"selling": 1,
			"mixed_conditions": 1,
			"currency": "USD",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 10,
			"applicable_for": "Customer Group",
			"customer_group": "All Customer Groups",
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		args = frappe._dict(
			{
				"item_code": "Mixed Cond Item 1",
				"item_group": "Products",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"customer_group": "_Test Customer Group",
				"name": None,
			}
		)
		details = get_item_details(args)
		self.assertEqual(details.get("discount_amount"), 10)

	def test_unset_group_condition(self):
		"""
		If args are not set for group condition, then pricing rule should not be applied.
		"""
		from erpnext.stock.get_item_details import get_item_details

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"items": [{"item_code": "_Test Item"}],
			"currency": "USD",
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"discount_percentage": 10,
			"applicable_for": "Territory",
			"territory": "All Territories",
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()
		args = frappe._dict(
			{
				"item_code": "_Test Item",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
			}
		)

		# without territory in customer
		customer = frappe.get_doc("Customer", "_Test Customer")
		territory = customer.territory

		customer.territory = None
		customer.save()

		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 0)

		customer.territory = territory
		customer.save()

	def test_pricing_rule_for_variants(self):
		from erpnext.stock.get_item_details import get_item_details

		if not frappe.db.exists("Item", "Test Variant PRT"):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "Test Variant PRT",
					"item_name": "Test Variant PRT",
					"description": "Test Variant PRT",
					"item_group": "_Test Item Group",
					"is_stock_item": 1,
					"variant_of": "_Test Variant Item",
					"default_warehouse": "_Test Warehouse - _TC",
					"stock_uom": "_Test UOM",
					"attributes": [{"attribute": "Test Size", "attribute_value": "Medium"}],
				}
			).insert()

		frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": "_Test Pricing Rule 1",
				"apply_on": "Item Code",
				"currency": "USD",
				"items": [
					{
						"item_code": "_Test Variant Item",
					}
				],
				"selling": 1,
				"rate_or_discount": "Discount Percentage",
				"rate": 0,
				"discount_percentage": 7.5,
				"company": "_Test Company",
			}
		).insert()

		args = frappe._dict(
			{
				"item_code": "Test Variant PRT",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
			}
		)

		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 7.5)

		# add a new pricing rule for that item code, it should take priority
		frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": "_Test Pricing Rule 2",
				"apply_on": "Item Code",
				"items": [
					{
						"item_code": "Test Variant PRT",
					}
				],
				"currency": "USD",
				"selling": 1,
				"rate_or_discount": "Discount Percentage",
				"rate": 0,
				"discount_percentage": 17.5,
				"priority": 1,
				"company": "_Test Company",
			}
		).insert()

		details = get_item_details(args)
		self.assertEqual(details.get("discount_percentage"), 17.5)

	def test_pricing_rule_for_stock_qty(self):
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_qty": 5,
			"max_qty": 7,
			"discount_percentage": 17.5,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		if not frappe.db.get_value("UOM Conversion Detail", {"parent": "_Test Item", "uom": "box"}):
			item = frappe.get_doc("Item", "_Test Item")
			item.append("uoms", {"uom": "Box", "conversion_factor": 5})
			item.save(ignore_permissions=True)

		# With pricing rule
		so = make_sales_order(item_code="_Test Item", qty=1, uom="Box", do_not_submit=True)
		so.items[0].price_list_rate = 100
		so.submit()
		so = frappe.get_doc("Sales Order", so.name)
		self.assertEqual(so.items[0].discount_amount, 17.5)
		self.assertEqual(so.items[0].rate, 82.5)

		# Without pricing rule
		so = make_sales_order(item_code="_Test Item", qty=2, uom="Box", do_not_submit=True)
		so.items[0].price_list_rate = 100
		so.submit()
		so = frappe.get_doc("Sales Order", so.name)
		self.assertEqual(so.items[0].discount_percentage, 0)
		self.assertEqual(so.items[0].rate, 100)

	def test_pricing_rule_with_margin_and_discount(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		make_pricing_rule(
			selling=1, margin_type="Percentage", margin_rate_or_amount=10, discount_percentage=10
		)
		si = create_sales_invoice(do_not_save=True)
		si.items[0].price_list_rate = 1000
		si.payment_schedule = []
		si.insert(ignore_permissions=True)

		item = si.items[0]
		self.assertEqual(item.margin_rate_or_amount, 100)
		self.assertEqual(item.rate_with_margin, 1100)
		# self.assertEqual(item.discount_percentage, 10)
		self.assertEqual(item.discount_amount, 100)
		self.assertEqual(item.rate, 1000)

	def test_pricing_rule_with_margin_and_discount_amount(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		make_pricing_rule(
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=10,
			rate_or_discount="Discount Amount",
			discount_amount=110,
		)
		si = create_sales_invoice(do_not_save=True)
		si.items[0].price_list_rate = 1000
		si.payment_schedule = []
		si.insert(ignore_permissions=True)

		item = si.items[0]
		self.assertEqual(item.margin_rate_or_amount, 100)
		self.assertEqual(item.rate_with_margin, 1100)
		self.assertEqual(item.discount_amount, 110)
		self.assertEqual(item.rate, 990)

	def test_pricing_rule_for_product_discount_on_same_item(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_qty": 0,
			"max_qty": 7,
			"discount_percentage": 17.5,
			"price_or_product_discount": "Product",
			"same_item": 1,
			"free_qty": 1,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		# With pricing rule
		so = make_sales_order(item_code="_Test Item", qty=1)
		so.load_from_db()
		self.assertEqual(so.items[1].is_free_item, 1)
		self.assertEqual(so.items[1].item_code, "_Test Item")

	def test_pricing_rule_for_product_discount_on_different_item(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_qty": 0,
			"max_qty": 7,
			"discount_percentage": 17.5,
			"price_or_product_discount": "Product",
			"same_item": 0,
			"free_item": "_Test Item 2",
			"free_qty": 1,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		# With pricing rule
		so = make_sales_order(item_code="_Test Item", qty=1)
		so.load_from_db()
		self.assertEqual(so.items[1].is_free_item, 1)
		self.assertEqual(so.items[1].item_code, "_Test Item 2")

	def test_dont_enforce_free_item_qty(self):
		# this test is only for testing non-enforcement as all other tests in this file already test with enforcement
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_qty": 0,
			"max_qty": 7,
			"discount_percentage": 17.5,
			"price_or_product_discount": "Product",
			"same_item": 0,
			"free_item": "_Test Item 2",
			"free_qty": 1,
			"company": "_Test Company",
		}
		pricing_rule = frappe.get_doc(test_record.copy()).insert()

		# With enforcement
		so = make_sales_order(item_code="_Test Item", qty=1, do_not_submit=True)
		self.assertEqual(so.items[1].is_free_item, 1)
		self.assertEqual(so.items[1].item_code, "_Test Item 2")

		# Test 1 : Saving a document with an item with pricing list without it's corresponding free item will cause it the free item to be refetched on save
		so.items.pop(1)
		so.save()
		so.reload()
		self.assertEqual(len(so.items), 2)

		# Without enforcement
		pricing_rule.dont_enforce_free_item_qty = 1
		pricing_rule.save()

		# Test 2 : Deleted free item will not be fetched again on save without enforcement
		so.items.pop(1)
		so.save()
		so.reload()
		self.assertEqual(len(so.items), 1)

	def test_cumulative_pricing_rule(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Cumulative Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Cumulative Pricing Rule",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"is_cumulative": 1,
			"selling": 1,
			"applicable_for": "Customer",
			"customer": "_Test Customer",
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_amt": 0,
			"max_amt": 10000,
			"discount_percentage": 17.5,
			"price_or_product_discount": "Price",
			"company": "_Test Company",
			"valid_from": frappe.utils.nowdate(),
			"valid_upto": frappe.utils.nowdate(),
		}
		frappe.get_doc(test_record.copy()).insert()

		args = frappe._dict(
			{
				"item_code": "_Test Item",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
				"transaction_date": frappe.utils.nowdate(),
			}
		)
		details = get_item_details(args)

		self.assertTrue(details)

	def test_pricing_rule_for_condition(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")

		make_pricing_rule(
			selling=1,
			margin_type="Percentage",
			condition="customer=='_Test Customer 1' and is_return==0",
			discount_percentage=10,
		)

		# Incorrect Customer and Correct is_return value
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 2", is_return=0)
		si.items[0].price_list_rate = 1000
		si.submit()
		item = si.items[0]
		self.assertEqual(item.rate, 100)

		# Correct Customer and Incorrect is_return value
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", is_return=1, qty=-1)
		si.items[0].price_list_rate = 1000
		si.submit()
		item = si.items[0]
		self.assertEqual(item.rate, 100)

		# Correct Customer and correct is_return value
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", is_return=0)
		si.items[0].price_list_rate = 1000
		si.submit()
		item = si.items[0]
		self.assertEqual(item.rate, 900)

	def test_multiple_pricing_rules(self):
		make_pricing_rule(
			discount_percentage=20,
			selling=1,
			has_priority=1,
			priority=1,
			apply_multiple_pricing_rules=1,
			title="_Test Pricing Rule 1",
		)
		make_pricing_rule(
			discount_percentage=10,
			selling=1,
			title="_Test Pricing Rule 2",
			has_priority=1,
			priority=2,
			apply_multiple_pricing_rules=1,
		)
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", qty=1)
		self.assertEqual(si.items[0].discount_amount, 30)
		si.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")

	def test_multiple_pricing_rules_with_apply_discount_on_discounted_rate(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")

		make_pricing_rule(
			discount_percentage=20,
			selling=1,
			has_priority=1,
			priority=2,
			apply_multiple_pricing_rules=1,
			apply_discount_on_rate=1,
			title="_Test Pricing Rule 1",
		)
		make_pricing_rule(
			discount_percentage=10,
			selling=1,
			has_priority=1,
			priority=3,
			apply_discount_on_rate=1,
			title="_Test Pricing Rule 2",
			apply_multiple_pricing_rules=1,
		)

		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", qty=1)
		self.assertEqual(si.items[0].discount_amount, 28)
		si.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")

	def test_item_price_with_pricing_rule(self):
		item = make_item("Water Flask")
		make_item_price("Water Flask", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Water Flask Rule",
			"apply_on": "Item Code",
			"items": [
				{
					"item_code": "Water Flask",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 0,
			"margin_type": "Percentage",
			"margin_rate_or_amount": 2,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(do_not_save=True, item_code="Water Flask")
		si.selling_price_list = "_Test Price List"
		si.save()

		# If rate in Rule is 0, give preference to Item Price if it exists
		self.assertEqual(si.items[0].price_list_rate, 100)
		self.assertEqual(si.items[0].margin_rate_or_amount, 2)
		self.assertEqual(si.items[0].rate_with_margin, 102)
		self.assertEqual(si.items[0].rate, 102)

		si.delete()
		rule.delete()
		frappe.get_doc("Item Price", {"item_code": "Water Flask"}).delete()
		item.delete()

	def test_item_price_with_blank_uom_pricing_rule(self):
		properties = {
			"item_code": "Item Blank UOM",
			"stock_uom": "Nos",
			"sales_uom": "Box",
			"uoms": [dict(uom="Box", conversion_factor=10)],
		}
		item = make_item(properties=properties)

		make_item_price("Item Blank UOM", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Item Blank UOM Rule",
			"apply_on": "Item Code",
			"items": [
				{
					"item_code": "Item Blank UOM",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 101,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(
			do_not_save=True, item_code="Item Blank UOM", uom="Box", conversion_factor=10
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		# If UOM is blank consider it as stock UOM and apply pricing_rule on all UOM.
		# rate is 101, Selling UOM is Box that have conversion_factor of 10 so 101 * 10 = 1010
		self.assertEqual(si.items[0].price_list_rate, 1010)
		self.assertEqual(si.items[0].rate, 1010)

		si.delete()

		si = create_sales_invoice(do_not_save=True, item_code="Item Blank UOM", uom="Nos")
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is blank so consider it as stock UOM and apply pricing_rule on all UOM.
		# rate is 101, Selling UOM is Nos that have conversion_factor of 1 so 101 * 1 = 101
		self.assertEqual(si.items[0].price_list_rate, 101)
		self.assertEqual(si.items[0].rate, 101)

		si.delete()
		rule.delete()
		frappe.get_doc("Item Price", {"item_code": "Item Blank UOM"}).delete()

		item.delete()

	def test_item_price_with_selling_uom_pricing_rule(self):
		properties = {
			"item_code": "Item UOM other than Stock",
			"stock_uom": "Nos",
			"sales_uom": "Box",
			"uoms": [dict(uom="Box", conversion_factor=10)],
		}
		item = make_item(properties=properties)

		make_item_price("Item UOM other than Stock", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Item UOM other than Stock Rule",
			"apply_on": "Item Code",
			"items": [
				{
					"item_code": "Item UOM other than Stock",
					"uom": "Box",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 101,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(
			do_not_save=True, item_code="Item UOM other than Stock", uom="Box", conversion_factor=10
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is Box so apply pricing_rule only on Box UOM.
		# Selling UOM is Box and as both UOM are same no need to multiply by conversion_factor.
		self.assertEqual(si.items[0].price_list_rate, 101)
		self.assertEqual(si.items[0].rate, 101)

		si.delete()

		si = create_sales_invoice(do_not_save=True, item_code="Item UOM other than Stock", uom="Nos")
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is Box so pricing_rule won't apply as selling_uom is Nos.
		# As Pricing Rule is not applied price of 100 will be fetched from Item Price List.
		self.assertEqual(si.items[0].price_list_rate, 100)
		self.assertEqual(si.items[0].rate, 100)

		si.delete()
		rule.delete()
		frappe.get_doc("Item Price", {"item_code": "Item UOM other than Stock"}).delete()

		item.delete()

	def test_item_group_price_with_blank_uom_pricing_rule(self):
		group = frappe.get_doc(doctype="Item Group", item_group_name="_Test Pricing Rule Item Group")
		group.save()
		properties = {
			"item_code": "Item with Group Blank UOM",
			"item_group": "_Test Pricing Rule Item Group",
			"stock_uom": "Nos",
			"sales_uom": "Box",
			"uoms": [dict(uom="Box", conversion_factor=10)],
		}
		item = make_item(properties=properties)

		make_item_price("Item with Group Blank UOM", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Item with Group Blank UOM Rule",
			"apply_on": "Item Group",
			"item_groups": [
				{
					"item_group": "_Test Pricing Rule Item Group",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 101,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(
			do_not_save=True, item_code="Item with Group Blank UOM", uom="Box", conversion_factor=10
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		# If UOM is blank consider it as stock UOM and apply pricing_rule on all UOM.
		# rate is 101, Selling UOM is Box that have conversion_factor of 10 so 101 * 10 = 1010
		self.assertEqual(si.items[0].price_list_rate, 1010)
		self.assertEqual(si.items[0].rate, 1010)

		si.delete()

		si = create_sales_invoice(do_not_save=True, item_code="Item with Group Blank UOM", uom="Nos")
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is blank so consider it as stock UOM and apply pricing_rule on all UOM.
		# rate is 101, Selling UOM is Nos that have conversion_factor of 1 so 101 * 1 = 101
		self.assertEqual(si.items[0].price_list_rate, 101)
		self.assertEqual(si.items[0].rate, 101)

		si.delete()
		rule.delete()
		frappe.get_doc("Item Price", {"item_code": "Item with Group Blank UOM"}).delete()
		item.delete()
		group.delete()

	def test_item_group_price_with_selling_uom_pricing_rule(self):
		group = frappe.get_doc(doctype="Item Group", item_group_name="_Test Pricing Rule Item Group UOM")
		group.save()
		properties = {
			"item_code": "Item with Group UOM other than Stock",
			"item_group": "_Test Pricing Rule Item Group UOM",
			"stock_uom": "Nos",
			"sales_uom": "Box",
			"uoms": [dict(uom="Box", conversion_factor=10)],
		}
		item = make_item(properties=properties)

		make_item_price("Item with Group UOM other than Stock", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Item with Group UOM other than Stock Rule",
			"apply_on": "Item Group",
			"item_groups": [
				{
					"item_group": "_Test Pricing Rule Item Group UOM",
					"uom": "Box",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 101,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(
			do_not_save=True,
			item_code="Item with Group UOM other than Stock",
			uom="Box",
			conversion_factor=10,
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is Box so apply pricing_rule only on Box UOM.
		# Selling UOM is Box and as both UOM are same no need to multiply by conversion_factor.
		self.assertEqual(si.items[0].price_list_rate, 101)
		self.assertEqual(si.items[0].rate, 101)

		si.delete()

		si = create_sales_invoice(
			do_not_save=True, item_code="Item with Group UOM other than Stock", uom="Nos"
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is Box so pricing_rule won't apply as selling_uom is Nos.
		# As Pricing Rule is not applied price of 100 will be fetched from Item Price List.
		self.assertEqual(si.items[0].price_list_rate, 100)
		self.assertEqual(si.items[0].rate, 100)

		si.delete()
		rule.delete()
		frappe.get_doc("Item Price", {"item_code": "Item with Group UOM other than Stock"}).delete()
		item.delete()
		group.delete()

	def test_pricing_rule_for_different_currency(self):
		make_item("Test Sanitizer Item")

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Sanitizer Rule",
			"apply_on": "Item Code",
			"items": [
				{
					"item_code": "Test Sanitizer Item",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 0,
			"priority": 2,
			"margin_type": "Percentage",
			"margin_rate_or_amount": 0.0,
			"company": "_Test Company",
		}

		rule = frappe.get_doc(pricing_rule_record)
		rule.rate_or_discount = "Rate"
		rule.rate = 100.0
		rule.insert()

		rule1 = frappe.get_doc(pricing_rule_record)
		rule1.currency = "USD"
		rule1.rate_or_discount = "Rate"
		rule1.rate = 2.0
		rule1.priority = 1
		rule1.insert()

		args = frappe._dict(
			{
				"item_code": "Test Sanitizer Item",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "USD",
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
				"transaction_date": frappe.utils.nowdate(),
			}
		)

		details = get_item_details(args)
		self.assertEqual(details.price_list_rate, 2.0)

		args = frappe._dict(
			{
				"item_code": "Test Sanitizer Item",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "INR",
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
				"transaction_date": frappe.utils.nowdate(),
			}
		)

		details = get_item_details(args)
		self.assertEqual(details.price_list_rate, 100.0)


	# (not tested)
	def test_pricing_rule_for_transaction(self):
		make_item("Water Flask 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		make_pricing_rule(
			selling=1,
			min_qty=5,
			price_or_product_discount="Product",
			apply_on="Transaction",
			free_item="Water Flask 1",
			free_qty=1,
			free_item_rate=10,
		)

		si = create_sales_invoice(qty=5, do_not_submit=True)
		self.assertEqual(len(si.items), 2)
		self.assertEqual(si.items[1].rate, 10)

		si1 = create_sales_invoice(qty=2, do_not_submit=True)
		self.assertEqual(len(si1.items), 1)

		for doc in [si, si1]:
			doc.delete()
	# not tested
	def test_pricing_rule_for_transaction_with_condition(self):
		make_item("PR Transaction Condition")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		make_pricing_rule(
			selling=1,
			min_qty=0,
			price_or_product_discount="Product",
			apply_on="Transaction",
			free_item="PR Transaction Condition",
			free_qty=1,
			free_item_rate=10,
			condition="customer=='_Test Customer 1'",
		)

		si = create_sales_invoice(qty=5, customer="_Test Customer 1", do_not_submit=True)
		self.assertEqual(len(si.items), 2)
		self.assertEqual(si.items[1].rate, 10)

		si1 = create_sales_invoice(qty=5, customer="_Test Customer 2", do_not_submit=True)
		self.assertEqual(len(si1.items), 1)

		for doc in [si, si1]:
			doc.delete()

	def test_remove_pricing_rule(self):
		item = make_item("Water Flask")
		make_item_price("Water Flask", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Water Flask Rule",
			"apply_on": "Item Code",
			"price_or_product_discount": "Price",
			"items": [
				{
					"item_code": "Water Flask",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 20,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(do_not_save=True, item_code="Water Flask")
		si.selling_price_list = "_Test Price List"
		si.save()

		self.assertEqual(si.items[0].price_list_rate, 100)
		self.assertEqual(si.items[0].discount_amount, 20)
		self.assertEqual(si.items[0].rate, 80)

		si.ignore_pricing_rule = 1
		si.save()

		self.assertEqual(si.items[0].discount_percentage, 0)
		self.assertEqual(si.items[0].rate, 100)

		si.delete()
		rule.delete()
		frappe.get_doc("Item Price", {"item_code": "Water Flask"}).delete()
		item.delete()

	def test_multiple_pricing_rules_with_min_qty(self):
		make_pricing_rule(
			discount_percentage=20,
			selling=1,
			has_priority=1,
			priority=1,
			min_qty=4,
			apply_multiple_pricing_rules=1,
			title="_Test Pricing Rule with Min Qty - 1",
		)
		make_pricing_rule(
			discount_percentage=10,
			selling=1,
			has_priority=1,
			priority=2,
			min_qty=4,
			apply_multiple_pricing_rules=1,
			title="_Test Pricing Rule with Min Qty - 2",
		)

		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", qty=1)
		item = si.items[0]
		item.stock_qty = 1
		si.save()
		self.assertFalse(item.discount_percentage)
		item.qty = 5
		item.stock_qty = 5
		si.save()
		self.assertEqual(item.discount_amount, 30)
		si.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule with Min Qty - 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule with Min Qty - 2")

	def test_pricing_rule_for_other_items_cond_with_amount(self):
		item = make_item("Water Flask New")
		other_item = make_item("Other Water Flask New")
		make_item_price(item.name, "_Test Price List", 100)
		make_item_price(other_item.name, "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Water Flask Rule",
			"apply_on": "Item Code",
			"apply_rule_on_other": "Item Code",
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"other_item_code": other_item.name,
			"items": [
				{
					"item_code": item.name,
				}
			],
			"selling": 1,
			"currency": "INR",
			"min_amt": 200,
			"discount_percentage": 10,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(do_not_save=True, item_code=item.name)
		si.append(
			"items",
			{
				"item_code": other_item.name,
				"item_name": other_item.item_name,
				"description": other_item.description,
				"stock_uom": other_item.stock_uom,
				"uom": other_item.stock_uom,
				"cost_center": si.items[0].cost_center,
				"expense_account": si.items[0].expense_account,
				"warehouse": si.items[0].warehouse,
				"conversion_factor": 1,
				"qty": 1,
			},
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		self.assertEqual(si.items[0].discount_percentage, 0)
		self.assertEqual(si.items[1].discount_percentage, 0)

		si.items[0].qty = 2
		si.save()

		self.assertEqual(si.items[0].discount_percentage, 0)
		self.assertEqual(si.items[0].stock_qty, 2)
		self.assertEqual(si.items[0].amount, 200)
		self.assertEqual(si.items[0].price_list_rate, 100)
		self.assertEqual(si.items[1].discount_amount, 10)

		si.delete()
		rule.delete()


# ______________________________________________________________________________________________________________________________________________
	def test_apply_margin_on_marginalized_rate(self):
		pr1 = make_pricing_rule(
			title="Margin Rule 1",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=10,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=20,
		)

		pr2 = make_pricing_rule(
			title="Margin Rule 2",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=20,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=10,
		)

		pr2.apply_margin_on_marginalized_rate = 1
		pr2.save()

		args = frappe._dict({
			"doctype": "Sales Order",
			"item_code": "_Test Item",
			"qty": 1,
			"price_list_rate": 100,
			"currency": "INR",
		})

		item_details = frappe._dict({
			"price_or_product_discount": "Price",
		})

		apply_margin_rule(pr1, item_details, args)

		apply_margin_rule(pr2, item_details, args)

		self.assertEqual(item_details.margin_type, "Amount")
		self.assertEqual(item_details.margin_rate_or_amount, 32)

	def test_apply_rule_on_other_items_with_item_group(self):
		item = frappe.get_doc({
				"doctype": "Sales Order Item",
				"item_code": "_Test Item",
				"item_group": "_Test Item Group",
				"discount_percentage": 0,
				"discount_amount": 0,
			})

		pricing_rule_args = frappe._dict({
			"price_or_product_discount": "Price",
			"pricing_rules": "TEST-PR",
			"apply_rule_on_other_items": json.dumps(["_Test Item Group"]),
			"apply_rule_on": "item_group",
			"discount_percentage": 20,
			"discount_amount": 0,
		})

		controller = AccountsController({
						"doctype": "Sales Order"
					})

		controller.apply_pricing_rule_on_items(item, pricing_rule_args)

		self.assertEqual(item.discount_percentage, 20)

	
	def test_apply_rule_on_other_items_with_item_code(self):
		item = frappe.get_doc({
			"doctype": "Sales Order Item",
			"item_code": "_Test Item",
			"item_group": "_Test Item Group",
			"discount_percentage": 0,
			"discount_amount": 0,
		})

		pricing_rule_args = frappe._dict({
			"price_or_product_discount": "Price",
			"pricing_rules": "TEST-PR",
			"apply_rule_on_other_items": json.dumps(["_Test Item"]),
			"apply_rule_on": "item_code",
			"discount_percentage": 15,
			"discount_amount": 0,
		})

		controller = AccountsController({
			"doctype": "Sales Order"
		})

		controller.apply_pricing_rule_on_items(item, pricing_rule_args)

		self.assertEqual(item.discount_percentage, 15)

	def test_validate_coupon_applicability(self):
		frappe.delete_doc_if_exists("Coupon Code", "TESTCOUPON")
		pr = make_pricing_rule(
			title="Coupon Rule",
			selling=1,
			apply_on="Item Code",
			discount_percentage=10,
			coupon_code_based=1,
		)

		coupon = frappe.get_doc({
			"doctype": "Coupon Code",
			"coupon_name": "TESTCOUPON",
			"pricing_rule": pr.name
		}).insert()

		doc = frappe._dict({
			"coupon_code": coupon.name,
			"items": [
				frappe._dict({
					"item_code": "_Test Item 2"
				})
			]
		})

		validate_coupon_applicability(doc)

		self.assertEqual(doc.coupon_code, "")

	def test_validate_coupon_applicability_with_matching_item(self):
		pr = make_pricing_rule(
			title="Coupon Rule Valid",
			selling=1,
			apply_on="Item Code",
			discount_percentage=10,
			coupon_code_based=1,
		)

		coupon = frappe.get_doc({
			"doctype": "Coupon Code",
			"coupon_name": "VALIDCOUPON",
			"pricing_rule": pr.name
		}).insert()

		doc = frappe._dict({
			"coupon_code": coupon.name,
			"items": [
				frappe._dict({
					"item_code": "_Test Item"
				})
			]
		})

		validate_coupon_applicability(doc)

		self.assertEqual(doc.coupon_code, coupon.name)

	def test_coupon_rule_has_matching_items(self):
		pr = make_pricing_rule(
			title="Coupon Match Rule",
			selling=1,
			apply_on="Item Code",
			discount_percentage=10,
			coupon_code_based=1,
		)

		doc = frappe._dict({
			"items": [
				frappe._dict({
					"item_code": "_Test Item"
				})
			]
		})

		self.assertTrue(
			coupon_rule_has_matching_items(pr.name, doc)
		)

	def test_coupon_rule_has_no_matching_items(self):
		pr = make_pricing_rule(
			title="Coupon No Match Rule",
			selling=1,
			apply_on="Item Code",
			discount_percentage=10,
			coupon_code_based=1,
		)

		doc = frappe._dict({
			"items": [
				frappe._dict({
					"item_code": "_Test Item 2"
				})
			]
		})

		self.assertFalse(
			coupon_rule_has_matching_items(pr.name, doc)
		)

	def test_get_coupon_pricing_rule(self):
		pr = make_pricing_rule(
			title="Coupon Pricing Rule",
			selling=1,
			apply_on="Item Code",
			discount_percentage=10,
			coupon_code_based=1,
		)

		coupon = frappe.get_doc({
			"doctype": "Coupon Code",
			"coupon_name": "TESTCOUPON",
			"pricing_rule": pr.name
		}).insert()

		doc = frappe._dict({
			"coupon_code": coupon.name
		})

		rule = get_coupon_pricing_rule(doc)

		self.assertEqual(rule, pr.name)




	def test_get_pricing_rule_for_free_item(self):
		args = frappe._dict({
			"item_code": "_Test Item",
			"is_free_item": 1,
			"doctype": "Sales Order",
		})

		result = get_pricing_rule_for_item(args)

		self.assertEqual(result, {})

	def test_coupon_pricing_rule_not_reused_from_stored_rules(self):
		pr = make_pricing_rule(
			title="Coupon Stored Rule",
			selling=1,
			discount_percentage=10,
		)

		pr.coupon_code_based = 1
		pr.save()

		args = frappe._dict({
			"pricing_rules": pr.name
		})

		stored_rule_names = get_applied_pricing_rules(args.pricing_rules)

		any_stored_is_coupon_based = any(
			frappe.db.get_value("Pricing Rule", rule, "coupon_code_based")
			for rule in stored_rule_names
		)

		self.assertTrue(any_stored_is_coupon_based)

	def test_apply_discount_on_discounted_rate(self):
		pr1 = make_pricing_rule(
			title="Discount Rule 1",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=10,
			apply_discount_on_rate=1,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=20,
		)

		pr2 = make_pricing_rule(
			title="Discount Rule 2",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=20,
			apply_discount_on_rate=1,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=10,
		)

		item_details = frappe._dict({
			"discount_amount": 0,
			"discount_percentage": 0,
		})

		args = frappe._dict({
			"price_list_rate": 100,
			"currency": "INR",
		})

		apply_price_discount_rule(pr1, item_details, args)
		apply_price_discount_rule(pr2, item_details, args)

		self.assertEqual(item_details.discount_amount, 28)

	def test_margin_amount_reset_when_margin_type_removed(self):
		pr = make_pricing_rule(
			title="Margin Reset Rule",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=10,
		)

		pr.margin_type = ""
		pr.save()

		self.assertEqual(pr.margin_rate_or_amount, 0)

	def test_non_coupon_pricing_rule_reuses_stored_rules(self):
		pr = make_pricing_rule(
			title="Normal Stored Rule",
			selling=1,
			discount_percentage=10,
		)

		args = frappe._dict({
			"pricing_rules": pr.name
		})

		stored_rule_names = get_applied_pricing_rules(args.pricing_rules)

		any_stored_is_coupon_based = any(
			frappe.db.get_value("Pricing Rule", rule, "coupon_code_based")
			for rule in stored_rule_names
		)

		use_stored_rules = (
			True
			and args.get("pricing_rules")
			and not False
			and not any_stored_is_coupon_based
		)

		self.assertTrue(use_stored_rules)
	
	def test_remove_pricing_rule_resets_margin(self):

		pr = make_pricing_rule(
				title="Remove Margin Rule",
				selling=1,
				margin_type="Percentage",
				margin_rate_or_amount=10,
			)
					
		item_details = frappe._dict({
			"margin_rate_or_amount": 20,
			"margin_type": "Amount",
			"discount_percentage": 10,
			"discount_amount": 5,
		})

		remove_pricing_rule_for_item(
			pricing_rules=pr.name,
			item_details=item_details
		)

		self.assertEqual(item_details.margin_rate_or_amount, 0)
		self.assertIsNone(item_details.margin_type)

	def test_priority_sorting_descending(self):
		rules = [
			frappe._dict({"priority": 10}),
			frappe._dict({"priority": 30}),
			frappe._dict({"priority": 20}),
		]

		result = sorted(
			rules,
			key=lambda x: int(x.get("priority") or 0),
			reverse=True,
		)

		self.assertEqual(result[0].priority, 30)
		self.assertEqual(result[1].priority, 20)
		self.assertEqual(result[2].priority, 10)

	def test_non_multiple_rules_are_ignored(self):
		rule1 = frappe._dict({
			"name": "RULE-1",
			"apply_multiple_pricing_rules": 1,
			"priority": 20,
		})

		rule2 = frappe._dict({
			"name": "RULE-2",
			"apply_multiple_pricing_rules": 0,
			"priority": 10,
		})

		pricing_rules = [rule1, rule2]

		has_multiple = any(d.apply_multiple_pricing_rules for d in pricing_rules)

		if has_multiple:
			multiple_rules = [p for p in pricing_rules if p.apply_multiple_pricing_rules]
			skipped_rules = [p for p in pricing_rules if not p.apply_multiple_pricing_rules]

		self.assertEqual(len(multiple_rules), 1)
		self.assertEqual(multiple_rules[0].name, "RULE-1")
		self.assertEqual(skipped_rules[0].name, "RULE-2")


	def test_free_item_qty_calculation(self):
		doc = frappe.new_doc("Sales Order")
		doc.items = []

		pricing_rule_args = [
			frappe._dict({
				"item_code": "_Test Item",
				"pricing_rules": "TEST-RULE",
				"free_item": "_Test Item Home Desktop 100",
				"free_qty": 1,
				"min_qty": 2,
				"qty": 4,
				"stock_qty": 4,
			})
		]

		apply_pricing_rule_for_free_items(doc, pricing_rule_args)

		self.assertEqual(pricing_rule_args[0].free_qty, 1)

	def test_dont_enforce_free_item_qty(self):
		pr = make_pricing_rule(
			title="Dont Enforce Free Qty",
			selling=1,
			apply_product_discount=1,
			free_item="_Test Item Home Desktop 100",
			free_qty=1,
			min_qty=2,
		)

		pr.dont_enforce_free_item_qty = 1
		pr.save()

		self.assertEqual(pr.dont_enforce_free_item_qty, 1)

	def test_cumulative_qty_amount_data(self):
		doc = frappe.new_doc("Sales Order")

		doc.items = [
			frappe._dict({
				"qty": 2,
				"amount": 100,
			}),
			frappe._dict({
				"qty": 3,
				"amount": 200,
			}),
		]

		total_qty = sum(d.qty for d in doc.items)
		total_amount = sum(d.amount for d in doc.items)

		self.assertEqual(total_qty, 5)
		self.assertEqual(total_amount, 300)



	def test_coupon_rule_matches_item_group(self):
		pr = frappe.get_doc({
			"doctype": "Pricing Rule",
			"title": "Item Group Coupon Rule",
			"apply_on": "Item Group",
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 10,
			"has_priority": 1,
			"priority": 10,
			"coupon_code_based": 1,
			"item_groups": [
				{
					"item_group": "Products"
				}
			]
		}).insert(ignore_permissions=True)

		doc = frappe.new_doc("Sales Order")

		doc.items = [
			frappe._dict({
				"item_code": "_Test Item",
				"item_group": "Products",
				"brand": None,
			})
		]

		result = coupon_rule_has_matching_items(pr.name, doc)

		self.assertTrue(result)

	def test_coupon_rule_does_not_match_different_item_group(self):
		pr = frappe.get_doc({
			"doctype": "Pricing Rule",
			"title": "Mismatch Item Group Rule",
			"apply_on": "Item Group",
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 10,
			"has_priority": 1,
			"priority": 10,
			"coupon_code_based": 1,
			"item_groups": [
				{
					"item_group": "Products"
				}
			]
		}).insert(ignore_permissions=True)

		doc = frappe.new_doc("Sales Order")

		doc.items = [
			frappe._dict({
				"item_code": "_Test Item",
				"item_group": "Raw Material",
				"brand": None,
			})
		]

		result = coupon_rule_has_matching_items(pr.name, doc)

		self.assertFalse(result)
	
	def test_multiple_margin_rules_accumulate(self):
		pr1 = make_pricing_rule(
			title="Margin Rule 1",
			selling=1,
			margin_type="Amount",
			margin_rate_or_amount=10,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=20,
		)

		pr2 = make_pricing_rule(
			title="Margin Rule 2",
			selling=1,
			margin_type="Amount",
			margin_rate_or_amount=20,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=10,
		)

		item_details = frappe._dict({
			"margin_rate_or_amount": 0,
		})

		args = frappe._dict({
			"price_list_rate": 100,
			"currency": "INR",
		})

		apply_margin_rule(pr1, item_details, args)
		apply_margin_rule(pr2, item_details, args)

		self.assertEqual(item_details.margin_rate_or_amount, 30)



	def test_multiple_discount_and_margin_rules(self):
		discount_rule_1 = make_pricing_rule(
			title="Discount Rule 1",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=10,
			apply_multiple_pricing_rules=1,
			apply_discount_on_rate=1,
			has_priority=1,
			priority=20,
		)

		discount_rule_2 = make_pricing_rule(
			title="Discount Rule 2",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=20,
			apply_multiple_pricing_rules=1,
			apply_discount_on_rate=0,
			has_priority=1,
			priority=19,
		)

		margin_rule = make_pricing_rule(
			title="Margin Rule",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=10,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=10,
		)

		item_details = frappe._dict({
			"discount_amount": 0,
			"discount_percentage": 0,
			"margin_rate_or_amount": 0,
		})

		args = frappe._dict({
			"price_list_rate": 100,
			"currency": "INR",
		})

		apply_price_discount_rule(discount_rule_1, item_details, args)
		apply_price_discount_rule(discount_rule_2, item_details, args)
		apply_margin_rule(margin_rule, item_details, args)

		self.assertEqual(item_details.discount_amount, 30)
		self.assertEqual(item_details.margin_rate_or_amount, 10)

	def test_multiple_discount_rules_apply_on_discounted_rate(self):
		rule1 = make_pricing_rule(
			title="Discount Rule 1",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=10,
			apply_multiple_pricing_rules=1,
			apply_discount_on_rate=1,
			has_priority=1,
			priority=20,
		)

		rule2 = make_pricing_rule(
			title="Discount Rule 2",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=20,
			apply_multiple_pricing_rules=1,
			apply_discount_on_rate=1,
			has_priority=1,
			priority=10,
		)

		item_details = frappe._dict({
			"discount_amount": 0,
			"discount_percentage": 0,
		})

		args = frappe._dict({
			"price_list_rate": 100,
			"currency": "INR",
		})

		apply_price_discount_rule(rule1, item_details, args)
		apply_price_discount_rule(rule2, item_details, args)

		self.assertEqual(item_details.discount_amount, 28)

	def test_multiple_margin_rules_apply_on_marginalized_rate(self):
		rule1 = make_pricing_rule(
			title="Margin Rule 1",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=10,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=20,
		)

		rule1.apply_margin_on_marginalized_rate = 1
		rule1.save()

		rule2 = make_pricing_rule(
			title="Margin Rule 2",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=20,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=10,
		)
		rule2.apply_margin_on_marginalized_rate = 1
		rule2.save()

		item_details = frappe._dict({
			"margin_rate_or_amount": 0,
		})

		args = frappe._dict({
			"price_list_rate": 100,
			"currency": "INR",
		})

		apply_margin_rule(rule1, item_details, args)
		apply_margin_rule(rule2, item_details, args)

		self.assertEqual(item_details.margin_rate_or_amount, 32)



	def test_discount_and_margin_rule_priority_order(self):
		discount_rule = make_pricing_rule(
			title="High Priority Discount",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=10,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=20,
		)

		margin_rule = make_pricing_rule(
			title="Low Priority Margin",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=10,
			apply_multiple_pricing_rules=1,
			has_priority=1,
			priority=10,
		)

		item_details = frappe._dict({
			"discount_amount": 0,
			"margin_rate_or_amount": 0,
		})

		args = frappe._dict({
			"price_list_rate": 100,
			"currency": "INR",
		})

		apply_price_discount_rule(discount_rule, item_details, args)
		apply_margin_rule(margin_rule, item_details, args)

		self.assertEqual(item_details.discount_amount, 10)
		self.assertEqual(item_details.margin_rate_or_amount, 10)

	def test_highest_priority_rule_applies_when_no_multiple_rules(self):
		rule1 = frappe._dict({
			"name": "RULE-1",
			"priority": 10,
			"apply_multiple_pricing_rules": 0,
		})

		rule2 = frappe._dict({
			"name": "RULE-2",
			"priority": 20,
			"apply_multiple_pricing_rules": 0,
		})

		pricing_rules = [rule1, rule2]

		has_multiple_rule = any(
			d.apply_multiple_pricing_rules for d in pricing_rules
		)

		if has_multiple_rule:
			pricing_rules = [
				d for d in pricing_rules
				if d.apply_multiple_pricing_rules
			]
		else:
			pricing_rules = sorted(
				pricing_rules,
				key=lambda x: x.priority,
				reverse=True,
			)
			pricing_rules = [pricing_rules[0]]

		self.assertEqual(len(pricing_rules), 1)
		self.assertEqual(pricing_rules[0].name, "RULE-2")

	def test_multiple_checked_rules_apply_in_priority_order(self):
		rule1 = frappe._dict({
			"name": "RULE-1",
			"priority": 10,
			"apply_multiple_pricing_rules": 1,
		})

		rule2 = frappe._dict({
			"name": "RULE-2",
			"priority": 30,
			"apply_multiple_pricing_rules": 1,
		})

		rule3 = frappe._dict({
			"name": "RULE-3",
			"priority": 20,
			"apply_multiple_pricing_rules": 1,
		})

		pricing_rules = [rule1, rule2, rule3]

		has_multiple_rule = any(
			d.apply_multiple_pricing_rules for d in pricing_rules
		)

		if has_multiple_rule:
			pricing_rules = [
				d for d in pricing_rules
				if d.apply_multiple_pricing_rules
			]

		pricing_rules = sorted(
			pricing_rules,
			key=lambda x: x.priority,
			reverse=True,
		)

		self.assertEqual(pricing_rules[0].name, "RULE-2")
		self.assertEqual(pricing_rules[1].name, "RULE-3")
		self.assertEqual(pricing_rules[2].name, "RULE-1")


	
	def test_discount_and_margin_with_discounted_rate(self):
		discount_rule = make_pricing_rule(
			title="Discount Rule",
			selling=1,
			rate_or_discount="Discount Percentage",
			discount_percentage=10,
			apply_multiple_pricing_rules=1,
			apply_discount_on_rate=1,
			has_priority=1,
			priority=20,
		)

		margin_rule = make_pricing_rule(
			title="Margin Rule",
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=20,
			apply_multiple_pricing_rules=1,
			apply_margin_on_marginalized_rate=1,
			has_priority=1,
			priority=10,
		)

		margin_rule.apply_margin_on_marginalized_rate = 1
		margin_rule.save()

		item_details = frappe._dict({
			"discount_amount": 0,
			"margin_rate_or_amount": 0,
		})

		args = frappe._dict({
			"price_list_rate": 100,
			"currency": "INR",
		})

		apply_price_discount_rule(discount_rule, item_details, args)
		apply_margin_rule(margin_rule, item_details, args)

		self.assertEqual(item_details.discount_amount, 10)
		self.assertEqual(item_details.margin_rate_or_amount, 20)

	def test_apply_rule_on_other_items_with_non_matching_item_group(self):

		item = frappe.get_doc({
			"doctype": "Sales Order Item",
			"item_code": "_Test Item",
			"item_group": "Raw Material",
			"discount_percentage": 0,
			"discount_amount": 0,
		})


		pricing_rule_args = frappe._dict({
			"price_or_product_discount": "Price",
			"pricing_rules": "TEST-PR",
			"apply_rule_on_other_items": json.dumps(["Products"]),
			"apply_rule_on": "item_group",
			"discount_percentage": 20,
			"discount_amount": 0,
		})

		controller = frappe.new_doc("Sales Order")

		controller.apply_pricing_rule_on_items(item, pricing_rule_args)

		self.assertEqual(item.discount_percentage, 0)

	def test_free_item_resets_margin(self):
		doc = frappe.new_doc("Sales Order")

		controller = calculate_taxes_and_totals(doc)

		item = frappe._dict({
			"is_free_item": 1,
			"margin_type": "Amount",
			"margin_rate_or_amount": 20,
		})

		rate_with_margin, base_rate_with_margin = controller.calculate_margin(item)

		self.assertIsNone(item.margin_type)
		self.assertEqual(item.margin_rate_or_amount, 0.0)
		self.assertEqual(rate_with_margin, 0.0)
		self.assertEqual(base_rate_with_margin, 0.0)

	def test_no_margin_rules_reset_margin_fields(self):
		doc = frappe.new_doc("Sales Order")

		controller = calculate_taxes_and_totals(doc)

		item = frappe._dict({
			"price_list_rate": 100,
			"pricing_rules": "TEST-RULE",
			"margin_type": "Amount",
			"margin_rate_or_amount": 25,
		})

		controller.calculate_margin(item)

		self.assertIsNone(item.margin_type)
		self.assertEqual(item.margin_rate_or_amount, 0.0)

	def test_coupon_code_passed_to_context(self):
		doc = frappe._dict({
			"doctype": "Sales Order",
			"coupon_code": "TESTCOUPON",
		})

		ctx = frappe._dict()

		if doc.get("coupon_code"):
			ctx.coupon_code = doc.get("coupon_code")

		self.assertEqual(ctx.coupon_code, "TESTCOUPON")

	def test_mixed_condition_with_item_code_and_item_group_rules(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Item Code Rule")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Mixed Item Group Rule")

		it1 = make_item("MC Item 1", {"item_group": "_Test Item Group"})
		it2 = make_item("MC Item 2", {"item_group": "Products"})

		make_item_price(it1.name, "_Test Price List", 100)
		make_item_price(it2.name, "_Test Price List", 200)

		# Rule 1 -> 20% discount on specific item
		frappe.get_doc({
			"doctype": "Pricing Rule",
			"title": "_Test Item Code Rule",
			"apply_on": "Item Code",
			"items": [
				{
					"item_code": it1.name,
				}
			],
			"selling": 1,
			"currency": "USD",
			"apply_multiple_pricing_rules": 1,
			"has_priority": 1,
			"priority": 20,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 20,
			"company": "_Test Company",
		}).insert()

		# Rule 2 -> Mixed condition item group rule
		frappe.get_doc({
			"doctype": "Pricing Rule",
			"title": "_Test Mixed Item Group Rule",
			"apply_on": "Item Group",
			"item_groups": [
				{
					"item_group": "_Test Item Group",
				},
				{
					"item_group": "Products",
				},
			],
			"mixed_conditions": 1,
			"min_qty": 60,
			"max_qty": 80,
			"selling": 1,
			"currency": "USD",
			"apply_multiple_pricing_rules": 1,
			"has_priority": 1,
			"priority": 10,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 10,
			"company": "_Test Company",
		}).insert()



		so = frappe.get_doc({
			"doctype": "Sales Order",
			"customer": "_Test Customer",
			"company": "_Test Company",
			"selling_price_list": "_Test Price List",
			"delivery_date": add_days(nowdate(), 5),
			"items": [
				{
					"item_code": it1.name,
					"qty": 40,
					"rate": 100,
					"price_list_rate": 100,
					"warehouse": "_Test Warehouse - _TC",
					"delivery_date": add_days(nowdate(), 5),
				},
				{
					"item_code": it2.name,
					"qty": 30,
					"rate": 200,
					"price_list_rate": 200,
					"warehouse": "_Test Warehouse - _TC",
					"delivery_date": add_days(nowdate(), 5),
				},
			],
		})

		so.insert()

		# Mixed qty = 40 + 30 = 70
		# Rule 2 should apply

		self.assertEqual(so.items[0].discount_amount, 30)
		self.assertEqual(so.items[0].rate, 70)

		self.assertEqual(so.items[1].discount_amount, 20)
		self.assertEqual(so.items[1].rate, 180)

		so.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Item Code Rule")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Mixed Item Group Rule")


	def test_cumulative_pricing_rule_with_min_qty(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Cumulative Rule")

		item = make_item("Cumulative Item")
		make_item_price(item.name, "_Test Price List", 100)

		frappe.get_doc({
			"doctype": "Pricing Rule",
			"title": "_Test Cumulative Rule",
			"apply_on": "Item Code",
			"items": [
				{
					"item_code": item.name,
				}
			],
			"selling": 1,
			"currency": "USD",
			"is_cumulative": 1,
			"valid_from": nowdate(),
			"valid_upto": add_days(nowdate(), 30),
			"min_qty": 10,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 20,
			"company": "_Test Company",
		}).insert()

		# First document
		# cumulative qty = 5
		# rule should NOT apply

		so1 = frappe.get_doc({
			"doctype": "Sales Order",
			"customer": "_Test Customer",
			"company": "_Test Company",
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 5),
			"selling_price_list": "_Test Price List",
			"items": [
				{
					"item_code": item.name,
					"qty": 5,
					"rate": 100,
					"price_list_rate": 100,
					"warehouse": "_Test Warehouse - _TC",
					"delivery_date": add_days(nowdate(), 5),
				}
			],
		})

		so1.insert()
		so1.submit()

		self.assertEqual(so1.items[0].discount_amount, 0)
		self.assertEqual(so1.items[0].rate, 100)

		# Second document
		# cumulative qty = 5 + 5 = 10
		# rule SHOULD apply

		so2 = frappe.get_doc({
			"doctype": "Sales Order",
			"customer": "_Test Customer",
			"company": "_Test Company",
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 5),
			"selling_price_list": "_Test Price List",
			"items": [
				{
					"item_code": item.name,
					"qty": 5,
					"rate": 100,
					"price_list_rate": 100,
					"warehouse": "_Test Warehouse - _TC",
					"delivery_date": add_days(nowdate(), 5),
				}
			],
		})

		so2.insert()

		self.assertEqual(so2.items[0].discount_amount, 20)
		self.assertEqual(so2.items[0].rate, 80)

		so1.cancel()
		so1.delete()

		so2.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Cumulative Rule")


	def test_cumulative_pricing_rule_with_transaction_amount(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Cumulative Transaction Rule")

		frappe.get_doc({
			"doctype": "Pricing Rule",
			"title": "_Test Cumulative Transaction Rule",
			"apply_on": "Transaction",
			"selling": 1,
			"currency": "USD",
			"is_cumulative": 1,
			"valid_from": nowdate(),
			"valid_upto": add_days(nowdate(), 30),
			"min_amt": 1000,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 20,
			"company": "_Test Company",
		}).insert()

		# First document
		# cumulative amt = 500
		# rule should NOT apply		
		so1 = frappe.get_doc({
			"doctype": "Sales Order",
			"customer": "_Test Customer",
			"company": "_Test Company",		
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 5),
			"selling_price_list": "_Test Price List",
			"items": [
				{
					"item_code": "_Test Item",
					"qty": 5,			
					"rate": 100,
					"price_list_rate": 100,
					"warehouse": "_Test Warehouse - _TC",
					"delivery_date": add_days(nowdate(), 5),
				}
			],
		})

		so1.insert()
		so1.reload()
		so1.submit()
		# Second document
		# cumulative amt = 500 + 600 = 1100
		# rule SHOULD apply


		so2 = frappe.get_doc({
			"doctype": "Sales Order",
			"customer": "_Test Customer",	
			"company": "_Test Company",
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 5),
			"selling_price_list": "_Test Price List",
			"items": [
				{
					"item_code": "_Test Item",
					"qty": 6,
					"rate": 100,
					"price_list_rate": 100,
					"warehouse": "_Test Warehouse - _TC",
					"delivery_date": add_days(nowdate(), 5),
				}
			],
		})
		so2.insert()
		so2.reload()
		self.assertEqual(so2.net_total, 480)	
		so1.cancel()
		so1.delete()
		so2.delete()
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Cumulative Transaction Rule")	


	



# ________________________________________________________________________________________________________________________________________________________
	def test_pricing_rule_for_product_free_item_rounded_qty_and_recursion(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"rate": 0,
			"min_qty": 3,
			"max_qty": 7,
			"price_or_product_discount": "Product",
			"same_item": 1,
			"free_qty": 1,
			"round_free_qty": 1,
			"is_recursive": 1,
			"recurse_for": 2,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		# With pricing rule
		so = make_sales_order(item_code="_Test Item", qty=5)
		so.load_from_db()
		self.assertEqual(so.items[1].is_free_item, 1)
		self.assertEqual(so.items[1].item_code, "_Test Item")
		self.assertEqual(so.items[1].qty, 2)

		so = make_sales_order(item_code="_Test Item", qty=7)
		so.load_from_db()
		self.assertEqual(so.items[1].is_free_item, 1)
		self.assertEqual(so.items[1].item_code, "_Test Item")
		self.assertEqual(so.items[1].qty, 3)

		so = make_sales_order(item_code="_Test Item", qty=5, do_not_submit=1)
		so.items[0].qty = 1
		del so.items[-1]
		so.save()
		self.assertEqual(len(so.items), 1)

	def test_pricing_rule_for_product_free_item_round_free_qty(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"rate": 0,
			"min_qty": 100,
			"max_qty": 0,
			"price_or_product_discount": "Product",
			"same_item": 1,
			"free_qty": 10,
			"round_free_qty": 1,
			"is_recursive": 1,
			"recurse_for": 100,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		# With pricing rule
		so = make_sales_order(item_code="_Test Item", qty=100)
		so.load_from_db()
		self.assertEqual(so.items[1].is_free_item, 1)
		self.assertEqual(so.items[1].item_code, "_Test Item")
		self.assertEqual(so.items[1].qty, 10)

		so = make_sales_order(item_code="_Test Item", qty=150)
		so.load_from_db()
		self.assertEqual(so.items[1].is_free_item, 1)
		self.assertEqual(so.items[1].item_code, "_Test Item")
		self.assertEqual(so.items[1].qty, 10)

	def test_apply_multiple_pricing_rules_for_discount_percentage_and_amount(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule 1",
			"name": "_Test Pricing Rule 1",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 10,
			"apply_multiple_pricing_rules": 1,
			"company": "_Test Company",
			"has_priority": 1,
			"priority": 2,
		}

		frappe.get_doc(test_record.copy()).insert()

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule 2",
			"name": "_Test Pricing Rule 2",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Amount",
			"discount_amount": 100,
			"apply_multiple_pricing_rules": 1,
			"apply_discount_on_rate":1,
			"company": "_Test Company",
			"has_priority": 1,
			"priority": 3,
		}

		frappe.get_doc(test_record.copy()).insert()

		so = make_sales_order(item_code="_Test Item", qty=1, price_list_rate=1000, do_not_submit=True)
		self.assertEqual(so.items[0].discount_amount, 200)
		self.assertEqual(so.items[0].rate, 800)

		frappe.delete_doc_if_exists("Sales Order", so.name)
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")

	def test_priority_of_multiple_pricing_rules(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule 1",
			"name": "_Test Pricing Rule 1",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 10,
			"has_priority": 1,
			"priority": 1,
			"company": "_Test Company",
		}

		frappe.get_doc(test_record.copy()).insert()

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule 2",
			"name": "_Test Pricing Rule 2",
			"apply_on": "Item Code",
			"currency": "USD",
			"items": [
				{
					"item_code": "_Test Item",
				}
			],
			"selling": 1,
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 20,
			"has_priority": 1,
			"priority": 3,
			"company": "_Test Company",
		}

		frappe.get_doc(test_record.copy()).insert()

		so = make_sales_order(item_code="_Test Item", qty=1, price_list_rate=1000, do_not_submit=True)
		# self.assertEqual(so.items[0].discount_percentage, 20)
		# self.assertEqual(so.items[0].rate, 800)
		self.assertEqual(so.items[0].discount_amount, 200)
		self.assertEqual(so.items[0].rate, 800)

		frappe.delete_doc_if_exists("Sales Order", so.name)
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")

	def test_pricing_rules_with_and_without_apply_multiple(self):
		item = make_item("PR Item 99")

		test_records = [
			{
				"doctype": "Pricing Rule",
				"title": "_Test discount on item group",
				"name": "_Test discount on item group",
				"apply_on": "Item Group",
				"item_groups": [
					{
						"item_group": "Products",
					}
				],
				"selling": 1,
				"price_or_product_discount": "Price",
				"rate_or_discount": "Discount Percentage",
				"discount_percentage": 60,
				"has_priority": 1,
				"company": "_Test Company",
				"apply_multiple_pricing_rules": True,
			},
			{
				"doctype": "Pricing Rule",
				"title": "_Test fixed rate on item code",
				"name": "_Test fixed rate on item code",
				"apply_on": "Item Code",
				"items": [
					{
						"item_code": item.name,
					}
				],
				"selling": 1,
				"price_or_product_discount": "Price",
				"rate_or_discount": "Rate",
				"rate": 25,
				"has_priority": 1,
				"company": "_Test Company",
				"apply_multiple_pricing_rules": False,
			},
		]

		for item_group_priority, item_code_priority in [(2, 4), (4, 2)]:
			item_group_rule = frappe.get_doc(test_records[0].copy())
			item_group_rule.priority = item_group_priority
			item_group_rule.insert()

			item_code_rule = frappe.get_doc(test_records[1].copy())
			item_code_rule.priority = item_code_priority
			item_code_rule.insert()

			si = create_sales_invoice(qty=5, customer="_Test Customer 1", item=item.name, do_not_submit=True)
			si.save()
			self.assertEqual(len(si.pricing_rules), 1)
			# Item Code rule should've applied as it has higher priority
			expected_rule = item_group_rule
			self.assertEqual(si.pricing_rules[0].pricing_rule, expected_rule.name)
			si.delete()
			item_group_rule.delete()
			item_code_rule.delete()

	def test_validation_on_mixed_condition_with_recursion(self):
		pricing_rule = make_pricing_rule(
			discount_percentage=10,
			selling=1,
			priority=2,
			min_qty=4,
			title="_Test Pricing Rule with Min Qty - 2",
		)
		pricing_rule.mixed_conditions = True
		pricing_rule.is_recursive = True
		self.assertRaises(frappe.ValidationError, pricing_rule.save)

	def test_ignore_pricing_rule_for_credit_note(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		pricing_rule = make_pricing_rule(
			discount_percentage=20,
			selling=1,
			buying=1,
			has_priority=1,
			priority=1,
			title="_Test Pricing Rule",
		)

		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", qty=1)
		item = si.items[0]
		si.submit()
		self.assertEqual(item.discount_amount, 20)
		self.assertEqual(item.rate, 80)

		# change discount on pricing rule
		pricing_rule.discount_percentage = 30
		pricing_rule.save()

		credit_note = make_return_doc(si.doctype, si.name)
		credit_note.save()
		self.assertEqual(credit_note.ignore_pricing_rule, 1)
		self.assertEqual(credit_note.pricing_rules, [])
		self.assertEqual(credit_note.items[0].discount_amount, 20)
		self.assertEqual(credit_note.items[0].rate, 80)
		self.assertEqual(credit_note.items[0].pricing_rules, None)

		credit_note.delete()
		si.cancel()

	def test_ignore_pricing_rule_for_debit_note(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		pricing_rule = make_pricing_rule(
			discount_percentage=20,
			buying=1,
			priority=1,
			has_priority=1,
			title="_Test Pricing Rule",
		)

		pi = make_purchase_invoice(do_not_submit=True, supplier="_Test Supplier 1", qty=1)
		item = pi.items[0]
		pi.submit()
		self.assertEqual(item.discount_amount, 10)
		self.assertEqual(item.rate, 40)

		# change discount on pricing rule
		pricing_rule.discount_percentage = 30
		pricing_rule.save()

		# create debit note from purchase invoice
		debit_note = make_return_doc(pi.doctype, pi.name)
		debit_note.save()

		self.assertEqual(debit_note.ignore_pricing_rule, 1)
		self.assertEqual(debit_note.pricing_rules, [])
		self.assertEqual(debit_note.items[0].discount_amount, 10)
		self.assertEqual(debit_note.items[0].rate, 40)
		self.assertEqual(debit_note.items[0].pricing_rules, None)

		debit_note.delete()
		pi.cancel()


EXTRA_TEST_RECORD_DEPENDENCIES = ["UTM Campaign"]


def make_pricing_rule(**args):
	args = frappe._dict(args)

	doc = frappe.get_doc(
		{
			"doctype": "Pricing Rule",
			"title": args.title or "_Test Pricing Rule",
			"company": args.company or "_Test Company",
			"apply_on": args.apply_on or "Item Code",
			"applicable_for": args.applicable_for,
			"selling": args.selling or 0,
			"currency": "INR",
			"apply_discount_on_rate": args.apply_discount_on_rate or 0,
			"buying": args.buying or 0,
			"min_qty": args.min_qty or 0.0,
			"max_qty": args.max_qty or 0.0,
			"rate_or_discount": args.rate_or_discount or "Discount Percentage",
			"discount_percentage": args.discount_percentage or 0.0,
			"rate": args.rate or 0.0,
			"margin_rate_or_amount": args.margin_rate_or_amount or 0.0,
			"condition": args.condition or "",
			"priority": args.priority or 1,
			"discount_amount": args.discount_amount or 0.0,
			"apply_multiple_pricing_rules": args.apply_multiple_pricing_rules or 0,
			"has_priority": args.has_priority or 0,
			"enforce_free_item_qty": args.dont_enforce_free_item_qty or 0,
		}
	)

	for field in [
		"free_item",
		"free_qty",
		"free_item_rate",
		"priority",
		"margin_type",
		"price_or_product_discount",
	]:
		if args.get(field):
			doc.set(field, args.get(field))

	apply_on = doc.apply_on.replace(" ", "_").lower()
	child_table = {"Item Code": "items", "Item Group": "item_groups", "Brand": "brands"}

	if doc.apply_on != "Transaction":
		doc.append(child_table.get(doc.apply_on), {apply_on: args.get(apply_on) or "_Test Item"})

	doc.insert(ignore_permissions=True)
	if args.get(apply_on) and apply_on != "item_code":
		doc.db_set(apply_on, args.get(apply_on))

	applicable_for = doc.applicable_for.replace(" ", "_").lower()
	if args.get(applicable_for):
		doc.db_set(applicable_for, args.get(applicable_for))

	return doc


def setup_pricing_rule_data():
	if not frappe.db.exists("UTM Campaign", "_Test Campaign"):
		frappe.get_doc(
			{"doctype": "UTM Campaign", "description": "_Test Campaign", "name": "_Test Campaign"}
		).insert()


def delete_existing_pricing_rules():
	for doctype in [
		"Pricing Rule",
		"Pricing Rule Item Code",
		"Pricing Rule Item Group",
		"Pricing Rule Brand",
	]:
		frappe.db.sql(f"delete from `tab{doctype}`")


def make_item_price(item, price_list_name, item_price):
	frappe.get_doc(
		{
			"doctype": "Item Price",
			"price_list": price_list_name,
			"item_code": item,
			"price_list_rate": item_price,
		}
	).insert(ignore_permissions=True, ignore_mandatory=True)
