from collections import defaultdict

import frappe
from frappe import _, bold
from frappe.utils import cint, flt

from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos


class WorkOrderTransferPool:
	def __init__(self, doc, work_order):
		self.doc = doc
		self.work_order = work_order

	def is_enabled(self):
		return bool(
			self.doc.work_order
			and self.work_order
			and not self.doc.get("is_return")
			and not self.work_order.skip_transfer
			and cint(frappe.db.get_single_value("Stock Settings", "enforce_work_order_transfer_pool"))
		)

	def validate(self):
		if not self.is_enabled():
			return

		from erpnext.stock.doctype.stock_entry.services.disassemble import get_available_materials

		available_materials = get_available_materials(self.doc.work_order)
		for key, selection in self._get_selected_materials().items():
			available = available_materials.get(key)
			self._validate_qty(selection, available)
			self._validate_batches(selection, available)
			self._validate_serial_nos(selection, available)

	def _get_selected_materials(self):
		selected_materials = {}
		for row in self.doc.items:
			if not row.s_warehouse:
				continue

			has_serial_no, has_batch_no = frappe.get_cached_value(
				"Item", row.item_code, ["has_serial_no", "has_batch_no"]
			)
			if not (has_serial_no or has_batch_no):
				continue

			key = (row.item_code, row.s_warehouse)
			selection = selected_materials.setdefault(
				key,
				frappe._dict(
					qty=0,
					rows=[],
					has_serial_no=has_serial_no,
					has_batch_no=has_batch_no,
					serial_nos=set(),
					batches=defaultdict(float),
				),
			)
			selection.qty += abs(flt(row.transfer_qty or row.qty))
			selection.rows.append(row.idx)
			self._add_row_serial_batches(row, selection)

		return selected_materials

	def _add_row_serial_batches(self, row, selection):
		if row.serial_and_batch_bundle:
			entries = frappe.get_all(
				"Serial and Batch Entry",
				filters={
					"parent": row.serial_and_batch_bundle,
					"docstatus": ("<", 2),
					"is_cancelled": 0,
				},
				fields=["serial_no", "batch_no", "qty"],
			)
			for entry in entries:
				if entry.serial_no:
					selection.serial_nos.add(entry.serial_no)
				if entry.batch_no:
					selection.batches[entry.batch_no] += abs(flt(entry.qty))
			return

		selection.serial_nos.update(get_serial_nos(row.serial_no))
		if row.batch_no:
			selection.batches[row.batch_no] += abs(flt(row.transfer_qty or row.qty))

	def _validate_qty(self, selection, available):
		available_qty = max(flt(available.qty), 0) if available else 0
		precision = frappe.get_precision("Stock Entry Detail", "qty")
		if flt(selection.qty, precision) <= flt(available_qty, precision):
			return

		self._throw_unavailable(selection, available)

	def _validate_batches(self, selection, available):
		if not selection.has_batch_no:
			return
		if not selection.batches:
			self._throw_unavailable(selection, available)

		available_batches = available.batch_details if available else {}
		precision = frappe.get_precision("Stock Entry Detail", "qty")
		for batch_no, qty in selection.batches.items():
			available_qty = max(flt(available_batches.get(batch_no)), 0)
			if flt(qty, precision) > flt(available_qty, precision):
				self._throw_unavailable(selection, available, batch_no=batch_no)

	def _validate_serial_nos(self, selection, available):
		if not selection.has_serial_no:
			return
		if not selection.serial_nos:
			self._throw_unavailable(selection, available)

		available_serial_nos = set(available.serial_nos) if available else set()
		if invalid_serial_nos := selection.serial_nos - available_serial_nos:
			self._throw_unavailable(selection, available, serial_no=sorted(invalid_serial_nos)[0])

	def _throw_unavailable(self, selection, available, batch_no=None, serial_no=None):
		if batch_no:
			detail = _("Batch {0} is not available").format(bold(batch_no))
		elif serial_no:
			detail = _("Serial No {0} is not available").format(bold(serial_no))
		else:
			detail = _("The selected quantity or serial/batch numbers are not available")

		frappe.throw(
			_("Rows {0}: {1} in the transfer pool for Work Order {2}. Available: {3}").format(
				", ".join(str(row) for row in selection.rows),
				detail,
				bold(self.doc.work_order),
				self._format_available(available),
			),
			title=_("Work Order Transfer Pool"),
		)

	@staticmethod
	def _format_available(available):
		if not available:
			return _("None")

		details = [
			_("Batch {0} ({1})").format(bold(batch_no), flt(qty))
			for batch_no, qty in available.batch_details.items()
			if flt(qty) > 0
		]
		if available.serial_nos:
			details.append(_("Serial Nos {0}").format(", ".join(available.serial_nos)))
		return ", ".join(details) or str(max(flt(available.qty), 0))
