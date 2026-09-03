# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions

STANDARD_DIMENSIONS = {
	"Cost Center": {"fieldname": "cost_center", "document_type": "Cost Center"},
	"Project": {"fieldname": "project", "document_type": "Project"},
}


def execute(filters: dict | None = None):
	validate_filters(filters)
	return get_columns(), get_data(filters)


def validate_filters(filters):
	if not filters:
		frappe.throw(_("Filters missing"))

	if not filters.company:
		frappe.throw(_("Company is mandatory"))

	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are mandatory"))

	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be greater than To Date"))

	if not filters.dimension:
		frappe.throw(_("Accounting Dimension is mandatory"))

	resolve_dimension(filters.dimension)


def resolve_dimension(dimension):
	dimensions = STANDARD_DIMENSIONS.copy()

	for row in get_accounting_dimensions(as_list=False):
		if row.disabled:
			continue

		dimensions[row.document_type] = {
			"fieldname": row.fieldname,
			"document_type": row.document_type,
		}
		dimensions[row.fieldname] = {
			"fieldname": row.fieldname,
			"document_type": row.document_type,
		}

	if dimension not in dimensions:
		frappe.throw(_("Invalid Accounting Dimension: {0}").format(frappe.bold(dimension)))

	return frappe._dict(dimensions[dimension])


def get_columns():
	return [
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
		{
			"label": _("Voucher Type"),
			"fieldname": "voucher_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 150,
		},
		{
			"label": _("Voucher No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 180,
		},
		{"label": _("Issue Type"), "fieldname": "issue_type", "fieldtype": "Data", "width": 190},
		{"label": _("Dimension"), "fieldname": "dimension", "fieldtype": "Data", "width": 140},
		{
			"label": _("Dimension Values"),
			"fieldname": "dimension_values",
			"fieldtype": "Small Text",
			"width": 240,
		},
		{"label": _("Missing Rows"), "fieldname": "missing_rows", "fieldtype": "Int", "width": 110},
		{"label": _("Total Rows"), "fieldname": "total_rows", "fieldtype": "Int", "width": 100},
		{
			"label": _("Debit"),
			"fieldname": "debit",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Credit"),
			"fieldname": "credit",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Difference"),
			"fieldname": "difference",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{"label": _("Accounts"), "fieldname": "accounts", "fieldtype": "Small Text", "width": 260},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
		},
	]


def get_data(filters):
	dimension = resolve_dimension(filters.dimension)
	gl_entries = get_gl_entries(filters, dimension.fieldname)
	company_currency = frappe.get_cached_value("Company", filters.company, "default_currency")

	include_missing = filters.get("include_missing_dimensions", 1)
	include_different = filters.get("include_different_dimensions", 1)

	vouchers = {}
	for gle in gl_entries:
		key = (gle.voucher_type, gle.voucher_no)
		voucher = vouchers.setdefault(
			key,
			frappe._dict(
				{
					"posting_date": gle.posting_date,
					"voucher_type": gle.voucher_type,
					"voucher_no": gle.voucher_no,
					"debit": 0,
					"credit": 0,
					"accounts": set(),
					"dimension_values": set(),
					"missing_rows": 0,
					"total_rows": 0,
				}
			),
		)

		if gle.posting_date and gle.posting_date < voucher.posting_date:
			voucher.posting_date = gle.posting_date

		voucher.debit += flt(gle.debit)
		voucher.credit += flt(gle.credit)
		voucher.accounts.add(gle.account)
		voucher.total_rows += 1

		if value := gle.get(dimension.fieldname):
			voucher.dimension_values.add(value)
		else:
			voucher.missing_rows += 1

	return [
		get_report_row(voucher, dimension, company_currency)
		for voucher in vouchers.values()
		if is_issue(voucher, include_missing, include_different)
	]


def get_gl_entries(filters, dimension_field):
	fields = [
		"posting_date",
		"voucher_type",
		"voucher_no",
		"account",
		"debit",
		"credit",
		dimension_field,
	]

	gl_filters = {
		"company": filters.company,
		"posting_date": ["between", [filters.from_date, filters.to_date]],
		"is_cancelled": 0,
	}

	if filters.get("voucher_no"):
		gl_filters["voucher_no"] = filters.voucher_no

	if filters.get("account"):
		accounts = filters.account if isinstance(filters.account, list | tuple) else [filters.account]
		gl_filters["account"] = ["in", accounts]

	return frappe.get_all(
		"GL Entry",
		filters=gl_filters,
		fields=fields,
		order_by="posting_date, voucher_type, voucher_no",
	)


def is_issue(voucher, include_missing, include_different):
	has_missing = voucher.missing_rows > 0
	has_different = len(voucher.dimension_values) > 1
	return (include_missing and has_missing) or (include_different and has_different)


def get_report_row(voucher, dimension, company_currency):
	issue_types = []
	if voucher.missing_rows:
		issue_types.append(_("Missing Dimension"))
	if len(voucher.dimension_values) > 1:
		issue_types.append(_("Different Dimensions"))

	return {
		"posting_date": voucher.posting_date,
		"voucher_type": voucher.voucher_type,
		"voucher_no": voucher.voucher_no,
		"issue_type": ", ".join(issue_types),
		"dimension": dimension.document_type,
		"dimension_values": ", ".join(sorted(voucher.dimension_values)) or _("Not Set"),
		"missing_rows": voucher.missing_rows,
		"total_rows": voucher.total_rows,
		"debit": voucher.debit,
		"credit": voucher.credit,
		"difference": voucher.debit - voucher.credit,
		"accounts": ", ".join(sorted(voucher.accounts)),
		"currency": company_currency,
	}
