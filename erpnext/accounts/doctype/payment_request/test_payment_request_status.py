# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.payment_request import payment_request


class TestPaymentRequestStatus(unittest.TestCase):
	def setUp(self):
		self.request = frappe._dict(
			name="TEST-PR",
			grand_total=100,
			outstanding_amount=0,
			currency="USD",
			party_account_currency="INR",
			company="Test Company",
			payment_request_type="Outward",
			reference_doctype="Purchase Order",
			reference_name="TEST-ORDER",
		)
		self.reference = frappe._dict(
			grand_total=100, base_grand_total=8000, rounded_total=0, base_rounded_total=0
		)
		self.enterContext(patch.object(frappe, "get_all", side_effect=lambda *a, **k: [self.request.copy()]))
		self.enterContext(patch.object(frappe, "get_value", return_value=self.reference))
		self.enterContext(patch.object(frappe, "get_system_settings", return_value="Banker's Rounding"))
		self.enterContext(
			patch.object(
				payment_request,
				"get_company_currency",
				side_effect=lambda company: self.request.party_account_currency,
			)
		)
		self.enterContext(
			patch.object(
				frappe,
				"get_precision",
				side_effect=lambda doctype, fieldname, currency=None: 3 if currency == "KWD" else 2,
			)
		)
		self.db = self.enterContext(patch.object(frappe, "db", Mock()))

	def assert_payment_update(self, allocations, *, cancel, outstanding, status):
		self.db.reset_mock()
		references = [
			frappe._dict(payment_request=self.request.name, allocated_amount=amount) for amount in allocations
		]
		payment_request.update_payment_requests_as_per_pe_references(references, cancel=cancel)
		self.db.bulk_update.assert_called_once_with(
			"Payment Request",
			{self.request.name: {"outstanding_amount": outstanding, "status": status}},
		)

	def test_full_cancellation_restores_unpaid_status(self):
		for direction, status in (("Inward", "Requested"), ("Outward", "Initiated")):
			for currency, allocations, outstanding in (
				("USD", [3000, 5000], 8000),
				("INR", [40, 60], 100),
			):
				with self.subTest(direction=direction, currency=currency):
					self.request.payment_request_type = direction
					self.request.currency = currency
					self.assert_payment_update(
						allocations, cancel=True, outstanding=outstanding, status=status
					)

	def test_partial_cancellation_keeps_partially_paid_status(self):
		for direction in ("Inward", "Outward"):
			with self.subTest(direction=direction):
				self.request.payment_request_type = direction
				self.assert_payment_update([3000], cancel=True, outstanding=3000, status="Partially Paid")

	def test_partial_payment_does_not_compare_amounts_in_different_currencies(self):
		self.request.outstanding_amount = 8000
		# INR 100 remains outstanding; it is not the full USD 100 requested amount.
		self.assert_payment_update([7900], cancel=False, outstanding=100, status="Partially Paid")

	def test_partial_request_uses_rounded_reference_totals(self):
		self.request.grand_total = 25
		self.reference.update(
			grand_total=99.99, base_grand_total=7999.2, rounded_total=100, base_rounded_total=8000
		)
		self.assert_payment_update([2000], cancel=True, outstanding=2000, status="Initiated")

	def test_cancellation_preserves_party_currency_precision(self):
		self.request.update(grand_total=10, party_account_currency="KWD")
		self.reference.update(grand_total=10, base_grand_total=3.071)
		self.assert_payment_update([3.071], cancel=True, outstanding=3.071, status="Initiated")
