# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe

from erpnext.accounts.doctype.pos_profile.test_pos_profile import make_pos_profile
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.accounts.doctype.tax_rule.tax_rule import get_tax_template
from erpnext.accounts.doctype.tax_rule.test_tax_rule import make_tax_rule
from erpnext.accounts.party import get_address_tax_category
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from erpnext.tests.utils import ERPNextTestSuite


class TestTaxCategory(ERPNextTestSuite):
	def test_disabled_category_fallback_and_tax_rules(self):
		category = frappe.get_doc({"doctype": "Tax Category", "title": "_Test Disabled Category"}).insert()
		fallback = frappe.get_doc({"doctype": "Tax Category", "title": "_Test Fallback Category"}).insert()
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": "_Test Tax Category Address",
				"address_line1": "1 Main Road",
				"city": "Chennai",
				"country": "India",
				"tax_category": category.name,
			}
		).insert()
		shipping = frappe.copy_doc(address)
		shipping.address_title = "_Test Shipping Tax Category Address"
		shipping.tax_category = fallback.name
		shipping.insert()
		template = "_Test Sales Taxes and Charges Template - _TC"
		make_tax_rule(tax_category=category.name, sales_tax_template=template, save=True)
		self.assertEqual(get_tax_template(None, {"tax_category": category.name}), template)
		category.disabled = 1
		category.save()

		for source, expected in (("Billing Address", ""), ("Shipping Address", fallback.name)):
			with self.change_settings("Accounts Settings", determine_address_tax_category_from=source):
				self.assertEqual(
					get_address_tax_category(fallback.name, address.name, shipping.name), fallback.name
				)
				self.assertEqual(
					get_address_tax_category(category.name, address.name, shipping.name), expected
				)
				self.assertEqual(get_address_tax_category(fallback.name), fallback.name)
		self.assertIsNone(get_tax_template(None, {"tax_category": category.name}))
		with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
			address.save()
		category.disabled = 0
		category.save()
		self.assertEqual(get_tax_template(None, {"tax_category": category.name}), template)
		with self.change_settings("Accounts Settings", determine_address_tax_category_from="Billing Address"):
			self.assertEqual(
				get_address_tax_category(fallback.name, address.name, shipping.name), category.name
			)
		address.save()

	def test_disabled_category_cannot_be_saved(self):
		category = frappe.get_doc(
			{
				"doctype": "Tax Category",
				"title": "_Test Disabled Category",
			}
		).insert()
		profile = make_pos_profile()
		profile.tax_category = category.name
		profile.save()
		category.disabled = 1
		category.save()
		fallback = frappe.get_doc(
			{"doctype": "Tax Category", "title": "_Test POS Fallback Category"}
		).insert()
		for doctype in ("Sales Invoice", "POS Invoice"):
			pos_invoice = frappe.new_doc(doctype)
			pos_invoice.update(
				{
					"company": profile.company,
					"pos_profile": profile.name,
					"is_pos": 1,
					"customer": "_Test Customer",
					"tax_category": fallback.name,
				}
			)
			pos_invoice.set_pos_fields()
			self.assertEqual(pos_invoice.tax_category, fallback.name)
			pos_invoice.tax_category = ""
			pos_invoice.set_pos_fields()
			self.assertFalse(pos_invoice.tax_category)

		for doctype, name in (("Customer", "_Test Customer"), ("Supplier", "_Test Supplier")):
			doc = frappe.get_doc(doctype, name)
			doc.tax_category = category.name
			with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
				doc.save()

		invoice = create_sales_invoice(do_not_save=True)
		invoice.tax_category = category.name
		with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
			invoice.insert()

	def test_linked_return_preserves_disabled_category(self):
		category = frappe.get_doc({"doctype": "Tax Category", "title": "_Test Return Category"}).insert()
		invoice = create_sales_invoice(do_not_save=True)
		invoice.tax_category = category.name
		invoice.insert()
		invoice.submit()
		category.disabled = 1
		category.save()

		credit_note = make_return_doc("Sales Invoice", invoice.name)
		self.assertEqual(credit_note.tax_category, category.name)
		credit_note.insert()

		credit_note.return_against = ""
		with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
			credit_note.save()
		credit_note.return_against = invoice.name
		other = frappe.get_doc(
			{"doctype": "Tax Category", "title": "_Test Other Return Category", "disabled": 1}
		).insert()
		credit_note.tax_category = other.name
		with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
			credit_note.save()
