# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe

from erpnext.accounts.report.accounting_dimension_comparison.accounting_dimension_comparison import (
	execute,
)
from erpnext.accounts.utils import get_fiscal_year
from erpnext.tests.utils import ERPNextTestSuite


class TestAccountingDimensionComparison(ERPNextTestSuite):
	def setUp(self):
		self.company = "_Test Company"
		self.debit_account = "_Test Bank - _TC"
		self.credit_account = "_Test Cash - _TC"
		self.from_date = "2026-01-01"
		self.to_date = "2026-12-31"
		self.posting_date = "2026-06-01"
		self.cost_center = "_Test Cost Center - _TC"
		self.other_cost_center = "_Test Cost Center 2 - _TC"
		self.fiscal_year = get_fiscal_year(self.posting_date, company=self.company)[0]
		self.currency = frappe.get_cached_value("Company", self.company, "default_currency")

	def run_report(self, **extra):
		filters = frappe._dict(
			{
				"company": self.company,
				"from_date": self.from_date,
				"to_date": self.to_date,
				"dimension": "Cost Center",
				"include_missing_dimensions": 1,
				"include_different_dimensions": 1,
			}
		)
		filters.update(extra)
		return execute(filters)[1]

	def make_gl_voucher(self, cost_center=None):
		voucher_no = f"TEST-DIM-ISSUE-{frappe.generate_hash(length=8)}"
		self.insert_gl_entry(
			voucher_no=voucher_no,
			account=self.debit_account,
			debit=500,
			credit=0,
			cost_center=cost_center,
		)
		self.insert_gl_entry(
			voucher_no=voucher_no,
			account=self.credit_account,
			debit=0,
			credit=500,
			cost_center=cost_center,
		)
		return frappe._dict({"name": voucher_no})

	def insert_gl_entry(self, voucher_no, account, debit, credit, cost_center=None):
		gl_entry = frappe.get_doc(
			{
				"doctype": "GL Entry",
				"posting_date": self.posting_date,
				"company": self.company,
				"account": account,
				"account_currency": self.currency,
				"debit": debit,
				"credit": credit,
				"debit_in_account_currency": debit,
				"credit_in_account_currency": credit,
				"voucher_type": "Journal Entry",
				"voucher_no": voucher_no,
				"against": self.credit_account if debit else self.debit_account,
				"is_opening": "No",
				"is_cancelled": 0,
				"fiscal_year": self.fiscal_year,
				"cost_center": cost_center,
			}
		)
		gl_entry.name = frappe.generate_hash(length=10)
		gl_entry.docstatus = 1
		gl_entry.db_insert()
		return gl_entry

	def test_missing_dimension_is_flagged(self):
		jv = self.make_gl_voucher()

		matching = [row for row in self.run_report() if row.get("voucher_no") == jv.name]

		self.assertEqual(len(matching), 1)
		self.assertEqual(matching[0].get("issue_type"), "Missing Dimension")
		self.assertEqual(matching[0].get("missing_rows"), 2)

	def test_different_dimensions_are_flagged(self):
		jv = self.make_gl_voucher(cost_center=self.cost_center)
		credit_gle = frappe.db.get_value(
			"GL Entry",
			{"voucher_no": jv.name, "account": self.credit_account, "is_cancelled": 0},
			"name",
		)
		frappe.db.set_value("GL Entry", credit_gle, "cost_center", self.other_cost_center)

		matching = [row for row in self.run_report() if row.get("voucher_no") == jv.name]

		self.assertEqual(len(matching), 1)
		self.assertEqual(matching[0].get("issue_type"), "Different Dimensions")
		self.assertEqual(matching[0].get("missing_rows"), 0)
		self.assertIn(self.cost_center, matching[0].get("dimension_values"))
		self.assertIn(self.other_cost_center, matching[0].get("dimension_values"))

	def test_consistent_dimension_is_not_flagged(self):
		jv = self.make_gl_voucher(cost_center=self.cost_center)

		flagged = {row.get("voucher_no") for row in self.run_report()}

		self.assertNotIn(jv.name, flagged)

	def test_issue_type_filters(self):
		missing = self.make_gl_voucher()
		different = self.make_gl_voucher(cost_center=self.cost_center)
		credit_gle = frappe.db.get_value(
			"GL Entry",
			{"voucher_no": different.name, "account": self.credit_account, "is_cancelled": 0},
			"name",
		)
		frappe.db.set_value("GL Entry", credit_gle, "cost_center", self.other_cost_center)

		flagged = {
			row.get("voucher_no")
			for row in self.run_report(include_missing_dimensions=0, include_different_dimensions=1)
		}

		self.assertNotIn(missing.name, flagged)
		self.assertIn(different.name, flagged)

	def test_voucher_no_filter_scopes_scan(self):
		matching_voucher = self.make_gl_voucher()
		other_voucher = self.make_gl_voucher()

		flagged = {row.get("voucher_no") for row in self.run_report(voucher_no=matching_voucher.name)}

		self.assertIn(matching_voucher.name, flagged)
		self.assertNotIn(other_voucher.name, flagged)

	def test_invalid_filters_raise(self):
		self.assertRaises(frappe.ValidationError, execute, None)
		self.assertRaises(
			frappe.ValidationError,
			execute,
			frappe._dict(
				{
					"company": self.company,
					"from_date": self.to_date,
					"to_date": self.from_date,
					"dimension": "Cost Center",
				}
			),
		)
		self.assertRaises(
			frappe.ValidationError,
			execute,
			frappe._dict(
				{
					"company": self.company,
					"from_date": self.from_date,
					"to_date": self.to_date,
					"dimension": "Sales Invoice",
				}
			),
		)
