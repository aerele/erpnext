# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from unittest.mock import patch

import frappe

from erpnext.accounts.doctype.process_payment_reconciliation import (
	process_payment_reconciliation as process_reconciliation,
)
from erpnext.accounts.doctype.process_payment_reconciliation.process_payment_reconciliation import (
	get_pr_instance,
)
from erpnext.tests.permission_test_utils import as_user, make_fenced_user
from erpnext.tests.utils import ERPNextTestSuite

COMPANY = "_Test Company"


class TestProcessPaymentReconciliation(ERPNextTestSuite):
	"""Process Payment Reconciliation validates its accounts against the company,
	moves to Queued on submit, and hands its filters to a Payment Reconciliation run."""

	def setUp(self):
		frappe.set_user("Administrator")

	def make_ppr(self, **args):
		args = frappe._dict(args)
		doc = frappe.new_doc("Process Payment Reconciliation")
		doc.company = COMPANY
		doc.party_type = "Customer"
		doc.party = "_Test Customer"
		doc.receivable_payable_account = args.get("receivable_payable_account", "Debtors - _TC")
		doc.bank_cash_account = args.get("bank_cash_account")
		doc.from_invoice_date = args.get("from_invoice_date")
		doc.to_invoice_date = args.get("to_invoice_date")
		return doc

	def other_company_account(self, **extra):
		filters = {"company": "_Test Company 1", "is_group": 0, **extra}
		account = frappe.db.get_value("Account", filters, "name")
		self.assertTrue(account, "need a matching account in _Test Company 1")
		return account

	def test_receivable_account_must_belong_to_company(self):
		doc = self.make_ppr(receivable_payable_account=self.other_company_account(account_type="Receivable"))
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_bank_cash_account_must_belong_to_company(self):
		doc = self.make_ppr(bank_cash_account=self.other_company_account())
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_submit_sets_status_to_queued(self):
		doc = self.make_ppr()
		doc.insert()
		doc.submit()
		self.assertEqual(doc.status, "Queued")

	def test_get_pr_instance_copies_filters_and_caps_limits(self):
		doc = self.make_ppr(from_invoice_date="2026-01-01", to_invoice_date="2026-06-30")
		doc.insert()

		pr = get_pr_instance(doc.name)
		self.assertEqual(pr.company, COMPANY)
		self.assertEqual(pr.party, "_Test Customer")
		self.assertEqual(pr.receivable_payable_account, "Debtors - _TC")
		self.assertEqual(str(pr.from_invoice_date), "2026-01-01")
		# the tool run is capped so a single process can't fetch unbounded rows
		self.assertEqual(pr.invoice_limit, 1000)
		self.assertEqual(pr.payment_limit, 1000)

	def test_get_pr_instance_copies_bank_cash_and_cost_center(self):
		doc = self.make_ppr(bank_cash_account="Cash - _TC")
		doc.cost_center = "_Test Cost Center - _TC"
		doc.insert()

		pr = get_pr_instance(doc.name)
		self.assertEqual(pr.bank_cash_account, "Cash - _TC")
		self.assertEqual(pr.cost_center, "_Test Cost Center - _TC")

	def make_allocated_log(self):
		from erpnext.accounts.doctype.payment_entry.test_payment_entry import create_payment_entry
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		invoice = create_sales_invoice(qty=1, rate=600)
		payment = create_payment_entry(
			company=COMPANY,
			payment_type="Receive",
			party_type="Customer",
			party="_Test Customer",
			paid_from="Debtors - _TC",
			paid_to="Cash - _TC",
			paid_amount=1000,
		)
		payment.insert().submit()
		process = self.make_ppr().insert().submit()
		with (
			patch.object(process_reconciliation.frappe, "enqueue"),
			patch.object(process_reconciliation, "is_job_running", return_value=False),
		):
			process_reconciliation.reconcile_based_on_filters(process.name)
			process_reconciliation.fetch_and_allocate(process.name)
		log = frappe.get_doc("Process Payment Reconciliation Log", {"process_pr": process.name})
		self.assertEqual(len(log.allocations), 1)
		self.assertEqual(log.allocations[0].allocated_amount, 600)
		return process, log, invoice, payment

	def test_log_is_read_only_for_accounts_roles(self):
		process, log = self.make_allocated_log()[:2]
		for role in ("Accounts User", "Accounts Manager"):
			with self.subTest(role=role):
				user = make_fenced_user(
					f"reconciliation-{role.replace(' ', '-').lower()}@example.com", [role]
				)
				with as_user(user):
					log = frappe.get_doc(log.doctype, log.name)
					log.check_permission("read")
					log.allocations[0].allocated_amount = 1000
					with self.assertRaises(frappe.PermissionError):
						log.save()
					with self.assertRaises(frappe.PermissionError):
						frappe.get_doc(doctype=log.doctype, process_pr=process.name).insert()
					with self.assertRaises(frappe.PermissionError):
						frappe.delete_doc(log.doctype, log.name)

	def test_worker_rejects_edited_allocation(self):
		process, log, invoice, payment = self.make_allocated_log()
		log.allocations[0].allocated_amount = 1000
		log.save()
		frappe.db.savepoint("before_reconciliation")
		rollback = frappe.db.rollback
		with (
			patch.object(process_reconciliation.frappe, "enqueue"),
			patch.object(process_reconciliation, "is_job_running", return_value=False),
			patch.object(
				frappe.db, "rollback", side_effect=lambda: rollback(save_point="before_reconciliation")
			),
		):
			process_reconciliation.reconcile(process.name)
		self.assertEqual(invoice.reload().outstanding_amount, 600)
		self.assertEqual(payment.reload().unallocated_amount, 1000)
		self.assertEqual(process.reload().status, "Failed")
		self.assertFalse(log.reload().allocations[0].reconciled)

	def test_reconciliation_validates_total_against_current_ledger(self):
		process, log, invoice, payment = self.make_allocated_log()
		pr = get_pr_instance(process.name)
		# A stored or client-provided outstanding must not override the ledger.
		pr.append(
			"invoices",
			{"invoice_type": invoice.doctype, "invoice_number": invoice.name, "outstanding_amount": 1000},
		)
		for amount in (400, 300):
			row = log.allocations[0].as_dict()
			row.update(name=None, allocated_amount=amount)
			pr.append("allocation", row)
		with self.assertRaises(frappe.ValidationError):
			pr.reconcile_allocations()
		self.assertEqual(invoice.reload().outstanding_amount, 600)
		self.assertEqual(payment.reload().unallocated_amount, 1000)

	def test_accounts_user_can_run_reconciliation_with_read_only_log(self):
		user = make_fenced_user("reconciliation-worker@example.com", ["Accounts User"])
		with as_user(user):
			process, log, invoice, payment = self.make_allocated_log()
			with (
				patch.object(process_reconciliation.frappe, "enqueue"),
				patch.object(process_reconciliation, "is_job_running", return_value=False),
			):
				process_reconciliation.reconcile(process.name)
			self.assertEqual(invoice.reload().outstanding_amount, 0)
			self.assertEqual(payment.reload().unallocated_amount, 400)
			self.assertTrue(log.reload().allocations[0].reconciled)
