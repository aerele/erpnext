# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe

from erpnext.accounts.doctype.chart_of_accounts_importer.chart_of_accounts_importer import (
	build_forest,
	unset_existing_data,
	validate_columns,
	validate_missing_roots,
)
from erpnext.tests.assertions import assert_raises_with_savepoint
from erpnext.tests.permission_test_utils import make_bank_account, make_fenced_user
from erpnext.tests.utils import ERPNextTestSuite

# columns: account_name, parent_account, account_number, parent_account_number,
#          is_group, account_type, root_type, account_currency
ROOT = ["Assets", "Assets", "", "", 1, "", "Asset", "INR"]
CHILD = ["Cash", "Assets", "", "", 0, "Cash", "Asset", "INR"]


class TestChartofAccountsImporter(ERPNextTestSuite):
	"""The importer parses an uploaded CoA into a nested tree and validates its
	shape. These cover the parsing/validation helpers without a file upload."""

	def test_validate_columns_rejects_blank_file(self):
		self.assertRaises(frappe.ValidationError, validate_columns, [])

	def test_validate_columns_requires_eight_columns(self):
		self.assertRaises(frappe.ValidationError, validate_columns, [["a", "b", "c"]])
		# the standard template width passes
		validate_columns([ROOT])

	def test_build_forest_nests_child_under_parent(self):
		forest = build_forest([ROOT, CHILD])
		self.assertIn("Assets", forest)
		self.assertIn("Cash", forest["Assets"])

	def test_build_forest_rejects_unknown_parent(self):
		orphan = ["Cash", "Missing Parent", "", "", 0, "Cash", "Asset", "INR"]
		self.assertRaises(frappe.ValidationError, build_forest, [orphan])

	def test_build_forest_requires_account_name(self):
		nameless = ["", "Assets", "", "", 0, "Cash", "Asset", "INR"]
		self.assertRaises(frappe.ValidationError, build_forest, [ROOT, nameless])

	def test_validate_missing_roots_requires_all_root_types(self):
		present = ("Asset", "Liability", "Expense", "Income")  # Equity missing
		self.assertRaises(
			frappe.ValidationError,
			validate_missing_roots,
			[{"root_type": rt} for rt in present],
		)
		# all five root types present -> no error
		validate_missing_roots(
			[{"root_type": rt} for rt in ("Asset", "Liability", "Expense", "Income", "Equity")]
		)

	def test_unset_existing_data_preserves_warehouse_account(self):
		company = self.make_company()
		warehouse = frappe.get_doc("Warehouse", company.default_warehouse)
		warehouse.account = company.default_inventory_account
		warehouse.save()

		with assert_raises_with_savepoint(self, frappe.LinkExistsError):
			unset_existing_data(company.name)

		self.assertEqual(warehouse.reload().account, company.default_inventory_account)
		self.assertTrue(frappe.db.exists("Account", warehouse.account))

	def test_unset_existing_data_preserves_bank_account(self):
		company = self.make_company()
		account = frappe.get_doc(
			doctype="Account",
			account_name="_Test Import Bank",
			account_type="Bank",
			company=company.name,
			parent_account=frappe.db.get_value(
				"Account", {"company": company.name, "account_type": "Bank", "is_group": 1}
			),
		).insert()
		bank_account = make_bank_account("_Test Import Bank", company.name, account.name)

		with assert_raises_with_savepoint(self, frappe.LinkExistsError):
			unset_existing_data(company.name)

		self.assertEqual(frappe.db.get_value("Bank Account", bank_account, "account"), account.name)
		self.assertTrue(frappe.db.exists("Account", account.name))

	def test_unset_existing_data_preserves_item_default(self):
		company = self.make_company()
		item = frappe.get_doc(
			doctype="Item",
			item_code="_Test COA Import Item",
			item_group="All Item Groups",
			stock_uom="Nos",
			is_stock_item=0,
			item_defaults=[{"company": company.name, "expense_account": company.default_expense_account}],
		).insert()

		with assert_raises_with_savepoint(self, frappe.LinkExistsError):
			unset_existing_data(company.name)

		self.assertEqual(item.reload().item_defaults[0].expense_account, company.default_expense_account)
		self.assertTrue(frappe.db.exists("Account", company.default_expense_account))

	def test_unset_existing_data_deletes_tax_children_and_account_tree(self):
		company = self.make_company()
		other_company = self.make_company("O")
		templates = []
		for template_company in (company, other_company):
			for doctype in ("Sales Taxes and Charges Template", "Purchase Taxes and Charges Template"):
				templates.append(
					frappe.get_doc(
						doctype=doctype,
						title="_Test Import Tax",
						company=template_company.name,
						taxes=[
							{
								"charge_type": "On Net Total",
								"account_head": template_company.default_expense_account,
								"description": "Import tax",
								"rate": 5,
							}
						],
					).insert()
				)

		user = make_fenced_user("coa-import-manager@example.com", ["Accounts Manager"])
		with self.set_user(user):
			unset_existing_data(company.name)

		self.assertFalse(frappe.db.exists("Account", {"company": company.name}))
		self.assertTrue(frappe.db.exists("Account", {"company": other_company.name}))
		for template in templates:
			should_exist = template.company == other_company.name
			self.assertEqual(bool(frappe.db.exists(template.doctype, template.name)), should_exist)
			self.assertEqual(
				bool(frappe.db.exists(template.taxes[0].doctype, template.taxes[0].name)), should_exist
			)

	def test_unset_existing_data_requires_account_delete_permission(self):
		company = self.make_company()
		default_expense_account = company.default_expense_account
		user = make_fenced_user("coa-import-reader@example.com", ["Accounts User"])
		with self.set_user(user):
			with assert_raises_with_savepoint(self, frappe.PermissionError):
				unset_existing_data(company.name)

		self.assertTrue(frappe.db.exists("Account", {"company": company.name}))
		self.assertEqual(company.reload().default_expense_account, default_expense_account)

	def make_company(self, suffix=""):
		return frappe.get_doc(
			doctype="Company",
			company_name=f"_Test COA Import{suffix}",
			abbr=f"TCI{suffix}",
			country="United States",
			default_currency="USD",
			chart_of_accounts="Standard",
		).insert()
