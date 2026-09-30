import frappe

from erpnext.crm.report.opportunity_summary_by_sales_stage.opportunity_summary_by_sales_stage import (
	execute,
)
from erpnext.crm.report.sales_pipeline_analytics.test_sales_pipeline_analytics import (
	create_opportunity,
)
from erpnext.tests.utils import ERPNextTestSuite


class TestOpportunitySummaryBySalesStage(ERPNextTestSuite):
	def setUp(self):
		create_opportunity()

	def test_opportunity_summary_by_sales_stage(self):
		self.check_for_opportunity_owner()
		self.check_for_source()
		self.check_for_opportunity_type()
		self.check_all_filters()

	def test_opportunity_owner_ignores_assignments(self):
		opportunity = frappe.get_doc("Opportunity", {"party_name": "_Test NC"})
		filters = {"based_on": "Opportunity Owner", "company": "Best Test"}

		for owner in ("Administrator", None, ""):
			for assignments in (
				None,
				"[]",
				'["test@example.com"]',
				'["test@example.com", "test2@example.com"]',
			):
				for data_based_on, expected in (("Number", 1), ("Amount", 150000)):
					with self.subTest(owner=owner, assignments=assignments, data_based_on=data_based_on):
						opportunity.db_set({"opportunity_owner": owner, "_assign": assignments})
						filters["data_based_on"] = data_based_on
						report = execute(filters)

						self.assertEqual(
							report[1],
							[{"opportunity_owner": owner or "Not Assigned", "Prospecting": expected}],
						)
						self.assertEqual(sum(report[3]["data"]["datasets"][0]["values"]), expected)

	def test_opportunity_owner_totals(self):
		opportunity = frappe.get_doc("Opportunity", {"party_name": "_Test NC"})
		opportunity.db_set("opportunity_owner", "Administrator")
		second_opportunity = frappe.copy_doc(opportunity)
		second_opportunity.opportunity_amount = 50000
		second_opportunity.insert()
		second_opportunity.db_set("_assign", '["test@example.com", "test2@example.com"]')
		unowned_opportunity = frappe.copy_doc(opportunity)
		unowned_opportunity.opportunity_owner = None
		unowned_opportunity.opportunity_amount = 25000
		unowned_opportunity.insert()

		for data_based_on, owner_total, unowned_total in (("Number", 2, 1), ("Amount", 200000, 25000)):
			with self.subTest(data_based_on=data_based_on):
				report = execute(
					{"based_on": "Opportunity Owner", "data_based_on": data_based_on, "company": "Best Test"}
				)
				self.assertCountEqual(
					report[1],
					[
						{"opportunity_owner": "Administrator", "Prospecting": owner_total},
						{"opportunity_owner": "Not Assigned", "Prospecting": unowned_total},
					],
				)
				self.assertEqual(sum(report[3]["data"]["datasets"][0]["values"]), owner_total + unowned_total)

	def check_for_opportunity_owner(self):
		filters = {"based_on": "Opportunity Owner", "data_based_on": "Number", "company": "Best Test"}

		report = execute(filters)

		expected_data = [{"opportunity_owner": "Not Assigned", "Prospecting": 1}]

		self.assertEqual(expected_data, report[1])

	def check_for_source(self):
		filters = {"based_on": "Source", "data_based_on": "Number", "company": "Best Test"}

		report = execute(filters)

		expected_data = [{"utm_source": "Cold Calling", "Prospecting": 1}]

		self.assertEqual(expected_data, report[1])

	def check_for_opportunity_type(self):
		filters = {"based_on": "Opportunity Type", "data_based_on": "Number", "company": "Best Test"}

		report = execute(filters)

		expected_data = [{"opportunity_type": "Sales", "Prospecting": 1}]

		self.assertEqual(expected_data, report[1])

	def check_all_filters(self):
		filters = {
			"based_on": "Opportunity Type",
			"data_based_on": "Number",
			"company": "Best Test",
			"opportunity_source": "Cold Calling",
			"opportunity_type": "Sales",
			"status": ["Open"],
		}

		report = execute(filters)

		expected_data = [{"opportunity_type": "Sales", "Prospecting": 1}]

		self.assertEqual(expected_data, report[1])
