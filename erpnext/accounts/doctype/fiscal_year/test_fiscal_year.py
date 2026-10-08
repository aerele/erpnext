# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import getdate, now_datetime
from freezegun import freeze_time

from erpnext.accounts.doctype.fiscal_year.fiscal_year import auto_create_fiscal_year
from erpnext.tests.utils import ERPNextTestSuite


class TestFiscalYear(ERPNextTestSuite):
	def test_extra_year(self):
		if frappe.db.exists("Fiscal Year", "_Test Fiscal Year 2000"):
			frappe.delete_doc("Fiscal Year", "_Test Fiscal Year 2000")

		fy = frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": "_Test Fiscal Year 2000",
				"year_end_date": "2002-12-31",
				"year_start_date": "2000-04-01",
			}
		)

		self.assertRaises(frappe.exceptions.InvalidDates, fy.insert)

	def test_company_fiscal_year_overlap(self):
		for name in ["_Test Global FY 2001", "_Test Company FY 2001"]:
			if frappe.db.exists("Fiscal Year", name):
				frappe.delete_doc("Fiscal Year", name)

		global_fy = frappe.new_doc("Fiscal Year")
		global_fy.year = "_Test Global FY 2001"
		global_fy.year_start_date = "2001-04-01"
		global_fy.year_end_date = "2002-03-31"
		global_fy.insert()

		company_fy = frappe.new_doc("Fiscal Year")
		company_fy.year = "_Test Company FY 2001"
		company_fy.year_start_date = "2001-01-01"
		company_fy.year_end_date = "2001-12-31"
		company_fy.append("companies", {"company": "_Test Company"})

		company_fy.insert()
		self.assertTrue(frappe.db.exists("Fiscal Year", global_fy.name))
		self.assertTrue(frappe.db.exists("Fiscal Year", company_fy.name))

	@freeze_time("1903-02-25")
	def test_auto_create_fiscal_year_before_leap_year(self):
		frappe.get_doc(
			doctype="Fiscal Year",
			year="_Test Fiscal Year Before Leap Year",
			year_start_date="1902-03-01",
			year_end_date="1903-02-28",
		).insert()

		auto_create_fiscal_year()
		fiscal_year = frappe.get_doc("Fiscal Year", "1903-1904")
		self.assertEqual(fiscal_year.year_start_date, getdate("1903-03-01"))
		self.assertEqual(fiscal_year.year_end_date, getdate("1904-02-29"))
		self.assertTrue(fiscal_year.auto_created)

	def test_auto_create_fiscal_year_after_missed_run(self):
		frappe.get_doc(
			doctype="Fiscal Year",
			year="_Test Fiscal Year Missed Run",
			year_start_date="1905-01-01",
			year_end_date="1905-12-31",
		).insert()

		with freeze_time("1905-12-27"):
			auto_create_fiscal_year()
			self.assertFalse(frappe.db.exists("Fiscal Year", "1906"))

		for run_date in ("1905-12-29", "1906-01-02"):
			with self.subTest(run_date=run_date), freeze_time(run_date):
				auto_create_fiscal_year()
				fiscal_year = frappe.get_doc("Fiscal Year", "1906")
				self.assertEqual(fiscal_year.year_start_date, getdate("1906-01-01"))
				self.assertEqual(fiscal_year.year_end_date, getdate("1906-12-31"))
				auto_create_fiscal_year()
				self.assertEqual(frappe.db.count("Fiscal Year", {"name": "1906"}), 1)
				frappe.delete_doc("Fiscal Year", "1906")


def test_record_generator():
	test_records = [
		{
			"doctype": "Fiscal Year",
			"year": "_Test Short Fiscal Year 2011",
			"is_short_year": 1,
			"year_start_date": "2011-04-01",
			"year_end_date": "2011-12-31",
		}
	]

	start = 2012
	this_year = now_datetime().year
	end = now_datetime().year + 25
	# The current year fails to load with the following error:
	# Year start date or end date is overlapping with 2024. To avoid please set company
	# This is a quick-fix: if current FY is needed, please refactor test data properly
	for year in range(start, this_year):
		test_records.append(
			{
				"doctype": "Fiscal Year",
				"year": f"_Test Fiscal Year {year}",
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			}
		)
	for year in range(this_year + 1, end):
		test_records.append(
			{
				"doctype": "Fiscal Year",
				"year": f"_Test Fiscal Year {year}",
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			}
		)

	return test_records


test_records = test_record_generator()
