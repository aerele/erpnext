# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Floor, IfNull, Max, Min, Sum
from frappe.utils import flt
from frappe.utils.data import comma_and
from pypika.terms import ExistsCriterion

from erpnext.stock.doctype.inventory_dimension.inventory_dimension import get_inventory_dimensions
from erpnext.stock.doctype.warehouse.warehouse import apply_warehouse_filter


def execute(filters=None):
	filters = filters or {}
	if filters.get("qty_to_make"):
		columns = get_columns_with_qty_to_make()
		data = get_data_with_qty_to_make(filters)
	else:
		columns = get_columns_without_qty_to_make()
		data = get_data_without_qty_to_make(filters)

	return add_dimension_columns(columns, filters), data


def fmt_qty(value):
	"""Format a float quantity for display as a string, so blank rows stay blank."""
	return frappe.utils.fmt_money(value, precision=2, currency=None)


def fmt_rate(value):
	"""Format a currency rate for display as a string."""
	currency = frappe.defaults.get_global_default("currency")
	return frappe.utils.fmt_money(value, precision=2, currency=currency)


def get_dimensions():
	"""Inventory Dimensions whose field is actually on Stock Ledger Entry."""
	return [
		dimension
		for dimension in get_inventory_dimensions()
		if frappe.db.has_column("Stock Ledger Entry", dimension.fieldname)
	]


def get_applied_dimensions(filters):
	"""Only while the breakdown is on: a depends_on filter keeps its value once hidden."""
	if not filters.get("show_dimension_wise_stock"):
		return []

	return [dimension for dimension in get_dimensions() if filters.get(dimension.fieldname)]


def add_dimension_columns(columns, filters):
	"""Show one column per Inventory Dimension, just before the qty it breaks up."""
	if not filters.get("show_dimension_wise_stock"):
		return columns

	dimension_columns = [
		{
			"fieldname": dimension.fieldname,
			"label": _(dimension.doctype),
			"fieldtype": "Link",
			"options": dimension.doctype,
			"width": 140,
		}
		for dimension in get_dimensions()
	]
	if not dimension_columns:
		return columns

	index = next(
		(i for i, column in enumerate(columns) if column["fieldname"] == "available_qty"), len(columns)
	)

	return columns[:index] + dimension_columns + columns[index:]


def apply_dimension_filters(query, sle, filters):
	for dimension in get_applied_dimensions(filters):
		values = filters.get(dimension.fieldname)
		if isinstance(values, str):
			values = [values]
		query = query.where(sle[dimension.fieldname].isin(values))

	return query


def get_dimension_wise_stock(filters, item_codes):
	"""item_code -> ledger rows carrying its qty per Inventory Dimension value."""
	dimensions = get_dimensions()
	if not dimensions or not item_codes:
		return {}

	sle = frappe.qb.DocType("Stock Ledger Entry")
	query = (
		frappe.qb.from_(sle)
		.select(sle.item_code, Sum(sle.actual_qty).as_("actual_qty"))
		.where((sle.docstatus < 2) & (sle.is_cancelled == 0) & sle.item_code.isin(item_codes))
		.groupby(sle.item_code)
	)

	for dimension in dimensions:
		query = query.select(sle[dimension.fieldname]).groupby(sle[dimension.fieldname])

	query = apply_warehouse_filter(query, sle, filters)
	query = apply_dimension_filters(query, sle, filters)

	breakup = {}
	for row in query.run(as_dict=True):
		# a dimension value the item has moved fully out of is not stock on hand
		if flt(row.actual_qty):
			breakup.setdefault(row.item_code, []).append(row)

	return breakup


def get_dimension_row(row, dimensions):
	"""A sub-row under its item: only the dimension values and their qty, every other cell blank."""
	dimension_row = {dimension.fieldname: row.get(dimension.fieldname) for dimension in dimensions}
	dimension_row["available_qty"] = fmt_qty(row.actual_qty)

	return dimension_row


def get_data_with_qty_to_make(filters):
	bom_data = get_bom_data(filters)
	manufacture_details = get_manufacturer_records()
	purchase_rates = batch_fetch_purchase_rates(bom_data)
	qty_to_make = flt(filters.get("qty_to_make"))
	dimensions = get_dimensions() if filters.get("show_dimension_wise_stock") else []
	dimension_wise_stock = (
		get_dimension_wise_stock(filters, [row.item_code for row in bom_data]) if dimensions else {}
	)

	data = []
	for row in bom_data:
		qty_per_unit = flt(row.qty_per_unit) if row.qty_per_unit > 0 else 0
		required_qty = qty_to_make * qty_per_unit
		difference_qty = flt(row.actual_qty) - required_qty
		rate = purchase_rates.get(row.item_code, 0)

		data.append(
			{
				"item": row.item_code,
				"description": row.description,
				"from_bom_no": row.from_bom_no,
				"manufacturer": comma_and(
					manufacture_details.get(row.item_code, {}).get("manufacturer", []), add_quotes=False
				),
				"manufacturer_part_number": comma_and(
					manufacture_details.get(row.item_code, {}).get("manufacturer_part", []), add_quotes=False
				),
				"qty_per_unit": fmt_qty(qty_per_unit),
				"available_qty": fmt_qty(row.actual_qty),
				"required_qty": fmt_qty(required_qty),
				"difference_qty": fmt_qty(difference_qty),
				"last_purchase_rate": fmt_rate(rate),
				"_available_qty": flt(row.actual_qty),
				"_qty_per_unit": qty_per_unit,
			}
		)

		for split in dimension_wise_stock.get(row.item_code, []):
			data.append(get_dimension_row(split, dimensions))

	# sub-rows carry no qty_per_unit, so only the item rows below decide what can be built
	producible = [int(r["_available_qty"] // r["_qty_per_unit"]) for r in data if r.get("_qty_per_unit")]
	min_producible = min(producible) if producible else 0

	for row in data:
		row.pop("_available_qty", None)
		row.pop("_qty_per_unit", None)

	# blank spacer row
	data.append({})

	data.append(
		{
			"item": _("Maximum Producible Items"),
			"description": min_producible,
			"from_bom_no": "",
			"manufacturer": "",
			"manufacturer_part_number": "",
			"qty_per_unit": "",
			"available_qty": "",
			"required_qty": "",
			"difference_qty": "",
			"last_purchase_rate": "",
			"bold": 1,
		}
	)

	return data


def get_columns_with_qty_to_make():
	return [
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "from_bom_no",
			"label": _("From BOM No"),
			"fieldtype": "Link",
			"options": "BOM",
			"width": 150,
		},
		{"fieldname": "manufacturer", "label": _("Manufacturer"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "manufacturer_part_number",
			"label": _("Manufacturer Part Number"),
			"fieldtype": "Data",
			"width": 170,
		},
		{"fieldname": "qty_per_unit", "label": _("Qty Per Unit"), "fieldtype": "Data", "width": 110},
		{"fieldname": "available_qty", "label": _("Available Qty"), "fieldtype": "Data", "width": 120},
		{"fieldname": "required_qty", "label": _("Required Qty"), "fieldtype": "Data", "width": 120},
		{"fieldname": "difference_qty", "label": _("Difference Qty"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "last_purchase_rate",
			"label": _("Last Purchase Rate"),
			"fieldtype": "Data",
			"width": 160,
		},
	]


def get_data_without_qty_to_make(filters):
	raw_rows = get_producible_fg_items(filters)
	dimensions = get_dimensions() if filters.get("show_dimension_wise_stock") else []
	dimension_wise_stock = (
		get_dimension_wise_stock(filters, [row.item_code for row in raw_rows]) if dimensions else {}
	)

	data = []
	for row in raw_rows:
		data.append(
			{
				"item": row.item_code,
				"description": row.description,
				"from_bom_no": row.from_bom_no,
				"qty_per_unit": fmt_qty(row.qty_per_unit),
				"available_qty": fmt_qty(row.available_qty),
			}
		)

		for split in dimension_wise_stock.get(row.item_code, []):
			data.append(get_dimension_row(split, dimensions))

	min_producible = min((row.producible_qty or 0) for row in raw_rows) if raw_rows else 0
	# blank spacer row
	data.append({})

	data.append(
		{
			"item": _("Maximum Producible Items"),
			"description": min_producible,
			"from_bom_no": "",
			"qty_per_unit": "",
			"available_qty": "",
			"bold": 1,
		}
	)

	return data


def get_columns_without_qty_to_make():
	return [
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 200},
		{
			"fieldname": "from_bom_no",
			"label": _("From BOM No"),
			"fieldtype": "Link",
			"options": "BOM",
			"width": 160,
		},
		{"fieldname": "qty_per_unit", "label": _("Qty Per Unit"), "fieldtype": "Data", "width": 120},
		{"fieldname": "available_qty", "label": _("Available Qty"), "fieldtype": "Data", "width": 120},
	]


def batch_fetch_purchase_rates(bom_data):
	if not bom_data:
		return {}
	item_codes = [row.item_code for row in bom_data]
	return {
		r.name: r.last_purchase_rate
		for r in frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=["name", "last_purchase_rate"],
		)
	}


def get_sle_stock_qty_by_item(filters):
	"""Bin holds no Inventory Dimension columns, so dimension-filtered stock has to come off the ledger."""
	sle = frappe.qb.DocType("Stock Ledger Entry")

	query = (
		frappe.qb.from_(sle)
		.select(sle.item_code, Sum(sle.actual_qty).as_("actual_qty"))
		.where((sle.docstatus < 2) & (sle.is_cancelled == 0))
		.groupby(sle.item_code)
	)

	query = apply_warehouse_filter(query, sle, filters)

	return apply_dimension_filters(query, sle, filters)


def get_stock_qty_by_item(filters):
	"""One row per item_code, so joining it to BOM Item cannot multiply either side's sum."""
	if get_applied_dimensions(filters):
		return get_sle_stock_qty_by_item(filters)

	bin = frappe.qb.DocType("Bin")

	query = (
		frappe.qb.from_(bin)
		.select(bin.item_code, Sum(bin.actual_qty).as_("actual_qty"))
		.groupby(bin.item_code)
	)

	if filters.get("warehouse"):
		warehouse_details = frappe.db.get_value(
			"Warehouse", filters.get("warehouse"), ["lft", "rgt"], as_dict=1
		)
		if warehouse_details:
			wh = frappe.qb.DocType("Warehouse")
			query = query.where(
				ExistsCriterion(
					frappe.qb.from_(wh)
					.select(wh.name)
					.where(
						(wh.lft >= warehouse_details.lft)
						& (wh.rgt <= warehouse_details.rgt)
						& (bin.warehouse == wh.name)
					)
				)
			)
		else:
			query = query.where(bin.warehouse == filters.get("warehouse"))

	return query


def get_bom_data(filters):
	bom_item_table = "BOM Explosion Item" if filters.get("show_exploded_view") else "BOM Item"

	bom_item = frappe.qb.DocType(bom_item_table)
	stock_qty = get_stock_qty_by_item(filters).as_("stock_qty")

	base = frappe.qb.from_(bom_item)
	base = base.join(stock_qty) if filters.get("warehouse") else base.left_join(stock_qty)

	query = (
		base.on(bom_item.item_code == stock_qty.item_code)
		.select(
			bom_item.item_code,
			# non-grouped columns are constant per grouped item_code -> Max() keeps the GROUP BY valid
			Max(bom_item.parent).as_("from_bom_no"),
			Sum(bom_item.qty_consumed_per_unit).as_("qty_per_unit"),
			IfNull(Max(stock_qty.actual_qty), 0).as_("actual_qty"),
		)
		.where((bom_item.parent == filters.get("bom")) & (bom_item.parenttype == "BOM"))
		.groupby(bom_item.item_code)
		.orderby(Min(bom_item.idx))
	)

	data = query.run(as_dict=True)

	# description belongs to a BOM line, not to the item, so a component listed more than once holds
	# several values per group. Max() over text is a sort and the engines sort text differently
	# (MariaDB folds case, PostgreSQL orders by byte value), so read it off one real line instead.
	# For BOM Item that same line also supplies bom_no + is_phantom_item, which drive whether and
	# which sub-BOM explode_phantom_boms recurses into and so must stay coherent with each other:
	# the first line, upgraded to the first phantom line if any exists, so a phantom sub-BOM is never
	# dropped just because a non-phantom line happens to be listed first.
	fields = ["item_code", "description"]
	if bom_item_table == "BOM Item":
		fields += ["bom_no", "is_phantom_item"]

	representative = {}
	for line in frappe.get_all(
		bom_item_table,
		filters={"parent": filters.get("bom"), "parenttype": "BOM"},
		fields=fields,
		order_by="idx",
	):
		existing = representative.get(line.item_code)
		if existing is None or (line.get("is_phantom_item") and not existing.get("is_phantom_item")):
			representative[line.item_code] = line

	for row in data:
		line = representative.get(row.item_code)
		row.description = line.description if line else None
		if bom_item_table == "BOM Item":
			row.bom_no = line.bom_no if line else None
			row.is_phantom_item = line.is_phantom_item if line else None

	if bom_item_table == "BOM Item":
		return explode_phantom_boms(data, filters)

	return data


def explode_phantom_boms(data, filters):
	original_bom = filters.get("bom")
	replacements = []

	for idx, item in enumerate(data):
		if not item.is_phantom_item:
			continue

		filters["bom"] = item.bom_no
		children = get_bom_data(filters)
		filters["bom"] = original_bom

		for child in children:
			child.qty_per_unit = (child.qty_per_unit or 0) * (item.qty_per_unit or 0)

		replacements.append((idx, children))

	for idx, children in reversed(replacements):
		data.pop(idx)
		data[idx:idx] = children

	return data


def get_manufacturer_records():
	details = frappe.get_all(
		"Item Manufacturer", fields=["manufacturer", "manufacturer_part_no", "item_code"]
	)
	manufacture_details = frappe._dict()
	for detail in details:
		dic = manufacture_details.setdefault(detail.get("item_code"), {})
		dic.setdefault("manufacturer", []).append(detail.get("manufacturer"))
		dic.setdefault("manufacturer_part", []).append(detail.get("manufacturer_part_no"))
	return manufacture_details


def get_producible_fg_items(filters):
	BOM_ITEM = frappe.qb.DocType("BOM Item")
	BOM = frappe.qb.DocType("BOM")

	if not filters.get("warehouse"):
		frappe.throw(_("Warehouse is required to get producible FG Items"))

	item_stock = get_stock_qty_by_item(filters).as_("item_stock")

	query = (
		frappe.qb.from_(BOM_ITEM)
		.join(BOM)
		.on(BOM_ITEM.parent == BOM.name)
		.left_join(item_stock)
		.on(BOM_ITEM.item_code == item_stock.item_code)
		.select(
			BOM_ITEM.item_code,
			# Sum() below makes this an aggregate query; the other columns are constant per grouped
			# item_code -> Max() keeps them valid on postgres with the same value MySQL picked.
			# description is not: it belongs to the line, so it comes from a representative one below.
			Max(BOM_ITEM.parent).as_("from_bom_no"),
			Max(BOM_ITEM.stock_qty / BOM.quantity).as_("qty_per_unit"),
			Max(IfNull(item_stock.actual_qty, 0)).as_("available_qty"),
			Floor(Max(item_stock.actual_qty) / ((Sum(BOM_ITEM.stock_qty)) / Max(BOM.quantity))).as_(
				"producible_qty"
			),
		)
		.where((BOM_ITEM.parent == filters.get("bom")) & (BOM_ITEM.parenttype == "BOM"))
		.groupby(BOM_ITEM.item_code)
		.orderby(Min(BOM_ITEM.idx))
	)

	rows = query.run(as_dict=True)
	descriptions = get_representative_descriptions("BOM Item", filters.get("bom"))
	for row in rows:
		row.description = descriptions.get(row.item_code)

	return rows


def get_representative_descriptions(doctype, bom):
	"""First line by idx per item_code. description belongs to a line, not an item, so aggregating it
	sorts text -- and MariaDB folds case while PostgreSQL orders by byte value."""
	descriptions = {}
	for line in frappe.get_all(
		doctype,
		filters={"parent": bom, "parenttype": "BOM"},
		fields=["item_code", "description"],
		order_by="idx",
	):
		descriptions.setdefault(line.item_code, line.description)

	return descriptions
