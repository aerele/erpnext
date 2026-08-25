# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe

from erpnext.setup.doctype.company.company import get_default_company_address
from erpnext.tests.utils import ERPNextTestSuite


class TestERPNextAddress(ERPNextTestSuite):
	def make_address(self, title, links, **kwargs):
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": title,
				"address_type": "Shipping",
				"address_line1": "Plot 42, MIDC",
				"city": "Pune",
				"country": "India",
				"links": links,
				**kwargs,
			}
		).insert()
		self.addCleanup(frappe.delete_doc, "Address", address.name, force=True)

		return address

	def test_company_only_address_is_marked_as_company_address(self):
		address = self.make_address(
			"_Test Company Warehouse", [{"link_doctype": "Company", "link_name": "_Test Company"}]
		)

		self.assertEqual(address.is_your_company_address, 1)

	def test_address_shared_with_a_party_is_not_a_company_address(self):
		"""A job worker's premises has to be linked to the company to be selectable as a
		shipping address, but that must not turn it into one of the company's own locations."""
		address = self.make_address(
			"_Test Job Worker Premises",
			[
				{"link_doctype": "Company", "link_name": "_Test Company"},
				{"link_doctype": "Supplier", "link_name": "_Test Supplier"},
			],
		)

		self.assertEqual(address.is_your_company_address, 0)

	def test_company_address_flag_is_not_cleared_by_a_party_link(self):
		address = self.make_address(
			"_Test Company Branch", [{"link_doctype": "Company", "link_name": "_Test Company"}]
		)
		address.append("links", {"link_doctype": "Supplier", "link_name": "_Test Supplier"})
		address.save()

		self.assertEqual(address.is_your_company_address, 1)

	def test_third_party_address_is_not_picked_as_the_company_default(self):
		address = self.make_address(
			"_Test Job Worker Default",
			[
				{"link_doctype": "Company", "link_name": "_Test Company"},
				{"link_doctype": "Supplier", "link_name": "_Test Supplier"},
			],
			is_primary_address=1,
			is_shipping_address=1,
		)

		self.assertNotEqual(get_default_company_address("_Test Company"), address.name)
		self.assertNotEqual(get_default_company_address("_Test Company", "is_shipping_address"), address.name)
