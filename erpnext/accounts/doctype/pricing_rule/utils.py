# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

# For license information, please see license.txt


import copy
import json

import frappe
from frappe import _, bold
from frappe.utils import cint, flt, fmt_money, get_link_to_form, getdate, today

from erpnext.setup.doctype.item_group.item_group import get_child_item_groups
from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses
from erpnext.stock.get_item_details import get_conversion_factor


class MultiplePricingRuleConflict(frappe.ValidationError):
	pass


apply_on_table = {"Item Code": "items", "Item Group": "item_groups", "Brand": "brands"}

def get_coupon_pricing_rule(doc):
	"""
	Returns the Pricing Rule name linked to the coupon on the doc.
	Returns None if no coupon is entered or the coupon has no linked rule.
	"""
	if not doc:
		return None

	# doc could be a Document object or a dict — handle both
	coupon_code = doc.get("coupon_code") if hasattr(doc, "get") else None
	if not coupon_code:
		return None

	return frappe.db.get_value("Coupon Code", coupon_code, "pricing_rule")

def coupon_rule_has_matching_items(coupon_rule_name, doc):
	"""
	Returns True if the coupon's pricing rule actually applies to items in the doc.

	- Transaction-level rules always return True (no item filter).
	- Item/Group/Brand rules return True only if at least one matching item is in doc.items.
	- Returns False if the rule has no targets, or none of them match.
	"""
	if not coupon_rule_name or not doc:
		return False

	pr = frappe.get_cached_doc("Pricing Rule", coupon_rule_name)

	if pr.apply_on == "Transaction":
		return True

	rule_items = set()
	rule_item_groups = set()
	rule_brands = set()

	if pr.apply_on == "Item Code":
		rule_items = {row.item_code for row in pr.items if row.item_code}
	elif pr.apply_on == "Item Group":
		rule_item_groups = {row.item_group for row in pr.item_groups if row.item_group}
	elif pr.apply_on == "Brand":
		rule_brands = {row.brand for row in pr.brands if row.brand}

	if not (rule_items or rule_item_groups or rule_brands):
		return False

	for item in doc.get("items") or []:
		if item.get("is_free_item"):
			continue
		if item.item_code and item.item_code in rule_items:
			return True
		if item.item_group and item.item_group in rule_item_groups:
			return True
		if item.brand and item.brand in rule_brands:
			return True

	return False


def get_pricing_rules(args, doc=None):
	pricing_rules = []
	values = {}

	if not frappe.db.count("Pricing Rule", cache=True):
		return

	for apply_on in ["Item Code", "Item Group", "Brand"]:
		pricing_rules.extend(_get_pricing_rules(apply_on, args, values))
		if pricing_rules and pricing_rules[0].has_priority:
			continue

		if pricing_rules and not apply_multiple_pricing_rules(pricing_rules):
			break

	rules = []
	pricing_rules = filter_pricing_rule_based_on_condition(pricing_rules, doc)

	if not pricing_rules:
		return []

	# if both apply multiple pricing rules and not checked one is there , separate it out 
	has_multiple = any(d.apply_multiple_pricing_rules for d in pricing_rules)

	if has_multiple:
		# separate multiple and non multiple ones
		multiple_rules = [p for p in pricing_rules if p.apply_multiple_pricing_rules]
		skipped_rules = [p for p in pricing_rules if not p.apply_multiple_pricing_rules]

		# notifying the user
		for skipped in skipped_rules:
			frappe.msgprint(
				_("Pricing Rule {0} is being ignored because 'Apply Multiple Pricing Rules' is not checked, "
				  "while other applicable rules have it checked.").format(
					frappe.bold(skipped.name)
				),
				indicator="orange",
				alert=True
			)

		pricing_rules = sorted_by_priority(multiple_rules, args, doc)
		for pricing_rule in pricing_rules:
			if isinstance(pricing_rule, list):
				rules.extend(pricing_rule)
			else:
				rules.append(pricing_rule)
	else:
		pricing_rule = filter_pricing_rules(args, pricing_rules, doc)
		if pricing_rule:
			rules.append(pricing_rule)

	return rules


def sorted_by_priority(pricing_rules, args, doc=None):
	# If more than one pricing rules, then sort by priority
	pricing_rules_list = []
	pricing_rule_dict = {}

	for pricing_rule in pricing_rules:
		pricing_rule = filter_pricing_rules(args, pricing_rule, doc)
		if pricing_rule:
			if not pricing_rule.get("priority"):
				pricing_rule["priority"] = 1

			if pricing_rule.get("apply_multiple_pricing_rules"):
				pricing_rule_dict.setdefault(cint(pricing_rule.get("priority")), []).append(pricing_rule)

	for key in sorted(pricing_rule_dict,reverse = True):
		pricing_rules_list.extend(pricing_rule_dict.get(key))

	return pricing_rules_list


def filter_pricing_rule_based_on_condition(pricing_rules, doc=None):
	filtered_pricing_rules = []
	if doc:
		for pricing_rule in pricing_rules:
			if pricing_rule.condition:
				try:
					if frappe.safe_eval(pricing_rule.condition, None, doc.as_dict()):
						filtered_pricing_rules.append(pricing_rule)
				except Exception:
					pass
			else:
				filtered_pricing_rules.append(pricing_rule)
	else:
		filtered_pricing_rules = pricing_rules

	return filtered_pricing_rules


def _get_pricing_rules(apply_on, args, values):
	apply_on_field = frappe.scrub(apply_on)

	if not args.get(apply_on_field):
		return []

	child_doc = f"`tabPricing Rule {apply_on}`"

	conditions = item_variant_condition = item_conditions = ""
	values[apply_on_field] = args.get(apply_on_field)
	if apply_on_field in ["item_code", "brand"]:
		item_conditions = f"{child_doc}.{apply_on_field}= %({apply_on_field})s"

		if apply_on_field == "item_code":
			if args.get("uom", None):
				item_conditions += (
					" and ({child_doc}.uom={item_uom} or IFNULL({child_doc}.uom, '')='')".format(
						child_doc=child_doc, item_uom=frappe.db.escape(args.get("uom"))
					)
				)
			if "variant_of" not in args:
				args.variant_of = frappe.get_cached_value("Item", args.item_code, "variant_of")

			if args.variant_of:
				item_variant_condition = f" or {child_doc}.item_code=%(variant_of)s "
				values["variant_of"] = args.variant_of
	elif apply_on_field == "item_group":
		item_conditions = _get_tree_conditions(args, "Item Group", child_doc, False)
		if args.get("uom", None):
			item_conditions += " and ({child_doc}.uom={item_uom} or IFNULL({child_doc}.uom, '')='')".format(
				child_doc=child_doc, item_uom=frappe.db.escape(args.get("uom"))
			)

	conditions += get_other_conditions(conditions, values, args)
	warehouse_conditions = _get_tree_conditions(args, "Warehouse", "`tabPricing Rule`")
	if warehouse_conditions:
		warehouse_conditions = f" and {warehouse_conditions}"

	if not args.price_list:
		args.price_list = None

	conditions += " and ifnull(`tabPricing Rule`.for_price_list, '') in (%(price_list)s, '')"
	values["price_list"] = args.get("price_list")

	pricing_rules = (
		frappe.db.sql(
			"""select `tabPricing Rule`.*,
			{child_doc}.{apply_on_field}, {child_doc}.uom
		from `tabPricing Rule`, {child_doc}
		where ({item_conditions} or (`tabPricing Rule`.apply_rule_on_other is not null
			and `tabPricing Rule`.{apply_on_other_field}=%({apply_on_field})s) {item_variant_condition})
			and {child_doc}.parent = `tabPricing Rule`.name
			and `tabPricing Rule`.disable = 0 and
			`tabPricing Rule`.{transaction_type} = 1 {warehouse_cond} {conditions}
		order by `tabPricing Rule`.priority desc,
			`tabPricing Rule`.name desc""".format(
				child_doc=child_doc,
				apply_on_field=apply_on_field,
				item_conditions=item_conditions,
				item_variant_condition=item_variant_condition,
				transaction_type=args.transaction_type,
				warehouse_cond=warehouse_conditions,
				apply_on_other_field=f"other_{apply_on_field}",
				conditions=conditions,
			),
			values,
			as_dict=1,
		)
		or []
	)

	return pricing_rules


def apply_multiple_pricing_rules(pricing_rules):
	for d in pricing_rules:
		if not d.apply_multiple_pricing_rules:
			return False

	return True


def _get_tree_conditions(args, parenttype, table, allow_blank=True):
	field = frappe.scrub(parenttype)
	condition = ""
	if args.get(field):
		if not frappe.flags.tree_conditions:
			frappe.flags.tree_conditions = {}
		key = (parenttype, args.get(field))
		if key in frappe.flags.tree_conditions:
			return frappe.flags.tree_conditions[key]

		try:
			lft, rgt = frappe.db.get_value(parenttype, args.get(field), ["lft", "rgt"])
		except TypeError:
			frappe.throw(_("Invalid {0}").format(args.get(field)))

		parent_groups = frappe.db.sql_list(
			"""select name from `tab{}`
			where lft<={} and rgt>={}""".format(parenttype, "%s", "%s"),
			(lft, rgt),
		)

		if parenttype in ["Customer Group", "Item Group", "Territory"]:
			parent_field = f"parent_{frappe.scrub(parenttype)}"
			root_name = frappe.db.get_list(
				parenttype,
				{"is_group": 1, parent_field: ("is", "not set")},
				"name",
				as_list=1,
				ignore_permissions=True,
			)

			if root_name and root_name[0][0]:
				parent_groups.append(root_name[0][0])

		if parent_groups:
			if allow_blank:
				parent_groups.append("")
			condition = "ifnull({table}.{field}, '') in ({parent_groups})".format(
				table=table, field=field, parent_groups=", ".join(frappe.db.escape(d) for d in parent_groups)
			)

			frappe.flags.tree_conditions[key] = condition

	elif allow_blank:
		condition = f"ifnull({table}.{field}, '') = ''"

	return condition


def get_other_conditions(conditions, values, args):
	for field in ["company", "customer", "supplier", "campaign", "sales_partner"]:
		if args.get(field):
			conditions += f" and ifnull(`tabPricing Rule`.{field}, '') in (%({field})s, '')"
			values[field] = args.get(field)
		else:
			conditions += f" and ifnull(`tabPricing Rule`.{field}, '') = ''"

	for parenttype in ["Customer Group", "Territory", "Supplier Group"]:
		group_condition = _get_tree_conditions(args, parenttype, "`tabPricing Rule`")
		if group_condition:
			conditions += " and " + group_condition

	date = args.get("transaction_date") or frappe.get_value(
		args.get("doctype"), args.get("name"), "posting_date", ignore=True
	)
	if date:
		conditions += """ and %(transaction_date)s between ifnull(`tabPricing Rule`.valid_from, '2000-01-01')
			and ifnull(`tabPricing Rule`.valid_upto, '2500-12-31')"""
		values["transaction_date"] = date

	if args.get("doctype") in [
		"Quotation",
		"Quotation Item",
		"Sales Order",
		"Sales Order Item",
		"Delivery Note",
		"Delivery Note Item",
		"Sales Invoice",
		"Sales Invoice Item",
		"POS Invoice",
		"POS Invoice Item",
	]:
		conditions += """ and ifnull(`tabPricing Rule`.selling, 0) = 1"""
	else:
		conditions += """ and ifnull(`tabPricing Rule`.buying, 0) = 1"""

	return conditions


def filter_pricing_rules(args, pricing_rules, doc=None):
	if not isinstance(pricing_rules, list):
		pricing_rules = [pricing_rules]

	original_pricing_rule = copy.copy(pricing_rules)

	if doc:
		coupon_rule_name = get_coupon_pricing_rule(doc)
		if coupon_rule_name and coupon_rule_has_matching_items(coupon_rule_name, doc):
			coupon_matched = [p for p in pricing_rules if p.name == coupon_rule_name]
			skipped_names = {p.name for p in pricing_rules if p.name != coupon_rule_name}

			for skipped_name in skipped_names:
				frappe.msgprint(
					_("Pricing Rule {0} is being ignored on item {1} because coupon {2} is applied.").format(
						frappe.bold(skipped_name),
						frappe.bold(args.get("item_code") or ""),
						frappe.bold(doc.get("coupon_code")),
					),
					indicator="orange",
					alert=True,
				)

			pricing_rules = coupon_matched
			# If the coupon's rule isn't a candidate for this item,
			# this item gets no rule at all.
			if not pricing_rules:
				return None

	# filter for qty
	if pricing_rules:
		stock_qty = flt(args.get("stock_qty"))
		amount = flt(args.get("price_list_rate")) * flt(args.get("qty"))

		pr_doc = frappe.get_cached_doc("Pricing Rule", pricing_rules[0].name)

		if pricing_rules[0].mixed_conditions and doc:
			stock_qty, amount, items = get_qty_and_rate_for_mixed_conditions(doc, pr_doc, args)
			for pricing_rule_args in pricing_rules:
				pricing_rule_args.apply_rule_on_other_items = items

		elif pricing_rules[0].is_cumulative:
			items = [args.get(frappe.scrub(pr_doc.get("apply_on")))]
			data = get_qty_amount_data_for_cumulative(pr_doc, args, items)

			if data:
				stock_qty += data[0]
				amount += data[1]

		if pricing_rules[0].apply_rule_on_other and not pricing_rules[0].mixed_conditions and doc:
			pricing_rules = get_qty_and_rate_for_other_item(doc, pr_doc, pricing_rules, args) or []
		else:
			pricing_rules = filter_pricing_rules_for_qty_amount(stock_qty, amount, pricing_rules, args)

		if not pricing_rules:
			for d in original_pricing_rule:
				if not d.threshold_percentage:
					continue

				msg = validate_quantity_and_amount_for_suggestion(
					d, stock_qty, amount, args.get("item_code"), args.get("transaction_type")
				)

				if msg:
					return {"suggestion": msg, "item_code": args.get("item_code")}

		# add variant_of property in pricing rule
		for p in pricing_rules:
			if p.item_code and args.variant_of:
				p.variant_of = args.variant_of
			else:
				p.variant_of = None

	if len(pricing_rules) > 1:
		filtered_rules = list(filter(lambda x: x.currency == args.get("currency"), pricing_rules))
		if filtered_rules:
			pricing_rules = filtered_rules

	# find pricing rule with highest priority
	if pricing_rules:
		max_priority = max(cint(p.priority) for p in pricing_rules)
		if max_priority:
			pricing_rules = list(filter(lambda x: cint(x.priority) == max_priority, pricing_rules))

	if pricing_rules and not isinstance(pricing_rules, list):
		pricing_rules = list(pricing_rules)

	if len(pricing_rules) > 1:
		rate_or_discount = list(set(d.rate_or_discount for d in pricing_rules))
		if len(rate_or_discount) == 1 and rate_or_discount[0] == "Discount Percentage":
			pricing_rules = (
				list(filter(lambda x: x.for_price_list == args.price_list, pricing_rules)) or pricing_rules
			)

	if len(pricing_rules) > 1 and not args.for_shopping_cart:
		frappe.throw(
			_(
				"Multiple Price Rules exists with same criteria, please resolve conflict by assigning priority. Price Rules: {0}"
			).format("\n".join(d.name for d in pricing_rules)),
			MultiplePricingRuleConflict,
		)
	elif pricing_rules:
		return pricing_rules[0]


def validate_quantity_and_amount_for_suggestion(args, qty, amount, item_code, transaction_type):
	fieldname, msg = "", ""
	type_of_transaction = "purchase" if transaction_type == "buying" else "sale"

	for field, value in {"min_qty": qty, "min_amt": amount}.items():
		if (
			args.get(field)
			and value < args.get(field)
			and (args.get(field) - cint(args.get(field) * args.threshold_percentage * 0.01)) <= value
		):
			fieldname = field

	for field, value in {"max_qty": qty, "max_amt": amount}.items():
		if (
			args.get(field)
			and value > args.get(field)
			and (args.get(field) + cint(args.get(field) * args.threshold_percentage * 0.01)) >= value
		):
			fieldname = field

	if fieldname:
		msg = _(
			"If you {0} {1} quantities of the item {2}, the scheme {3} will be applied on the item."
		).format(type_of_transaction, args.get(fieldname), bold(item_code), bold(args.title))

		if fieldname in ["min_amt", "max_amt"]:
			msg = _("If you {0} {1} worth item {2}, the scheme {3} will be applied on the item.").format(
				type_of_transaction,
				fmt_money(args.get(fieldname), currency=args.get("currency")),
				bold(item_code),
				bold(args.title),
			)

		frappe.msgprint(msg)

	return msg


def filter_pricing_rules_for_qty_amount(qty, rate, pricing_rules, args=None):
	rules = []

	for rule in pricing_rules:
		status = False
		conversion_factor = 1

		if rule.get("uom"):
			conversion_factor = get_conversion_factor(rule.item_code, rule.uom).get("conversion_factor", 1)

		if flt(qty) >= (flt(rule.min_qty) * conversion_factor) and (
			flt(qty) <= (rule.max_qty * conversion_factor) if rule.max_qty else True
		):
			status = True

		# if user has created item price against the transaction UOM
		if args and rule.get("uom") == args.get("uom"):
			conversion_factor = 1.0

		if status and (
			flt(rate) >= (flt(rule.min_amt) * conversion_factor)
			and (flt(rate) <= (rule.max_amt * conversion_factor) if rule.max_amt else True)
		):
			status = True
		else:
			status = False

		if status:
			rules.append(rule)

	return rules


def if_all_rules_same(pricing_rules, fields):
	all_rules_same = True
	val = [pricing_rules[0].get(k) for k in fields]
	for p in pricing_rules[1:]:
		if val != [p.get(k) for k in fields]:
			all_rules_same = False
			break

	return all_rules_same


def apply_internal_priority(pricing_rules, field_set, args):
	filtered_rules = []
	for field in field_set:
		if args.get(field):
			# filter function always returns a filter object even if empty
			# list conversion is necessary to check for an empty result
			filtered_rules = list(filter(lambda x: x.get(field) == args.get(field), pricing_rules))
			if filtered_rules:
				break

	return filtered_rules or pricing_rules


def get_qty_and_rate_for_mixed_conditions(doc, pr_doc, args):
	sum_qty, sum_amt = [0, 0]
	items = get_pricing_rule_items(pr_doc) or []
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	if items and doc.get("items"):
		for row in doc.get("items"):
			if (row.get(apply_on) or args.get(apply_on)) not in items:
				continue

			if pr_doc.mixed_conditions:
				amt = args.get("qty") * args.get("price_list_rate")
				if args.get("item_code") != row.get("item_code"):
					amt = flt(row.get("qty")) * flt(row.get("price_list_rate") or args.get("rate"))

				sum_qty += flt(row.get("stock_qty")) or flt(args.get("stock_qty")) or flt(args.get("qty"))
				sum_amt += amt

		if pr_doc.is_cumulative:
			data = get_qty_amount_data_for_cumulative(pr_doc, doc, items)

			if data and data[0]:
				sum_qty += data[0]
				sum_amt += data[1]

	return sum_qty, sum_amt, items


def get_qty_and_rate_for_other_item(doc, pr_doc, pricing_rules, row_item):
	other_items = get_pricing_rule_items(pr_doc, other_items=True)
	pricing_rule_apply_on = apply_on_table.get(pr_doc.get("apply_on"))
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	items = []
	for d in pr_doc.get(pricing_rule_apply_on):
		if apply_on == "item_group":
			items.extend(get_child_item_groups(d.get(apply_on)))
		else:
			items.append(d.get(apply_on))

	for row in doc.items:
		if row.get(apply_on) in items:
			if not row.get("qty"):
				continue

			stock_qty = row.get("qty") * (row.get("conversion_factor") or 1.0)
			amount = stock_qty * (flt(row.get("price_list_rate")) or flt(row.get("rate")))
			pricing_rules = filter_pricing_rules_for_qty_amount(stock_qty, amount, pricing_rules, row)

			if pricing_rules and pricing_rules[0]:
				pricing_rules[0].apply_rule_on_other_items = other_items
				return pricing_rules


def get_qty_amount_data_for_cumulative(pr_doc, doc, items=None):
	if items is None:
		items = []
	sum_qty, sum_amt = [0, 0]
	doctype = doc.get("parenttype") or doc.doctype

	date_field = (
		"transaction_date" if frappe.get_meta(doctype).has_field("transaction_date") else "posting_date"
	)

	child_doctype = f"{doctype} Item"
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	# Transaction- in is-cumulative
	if pr_doc.get("apply_on") == "Transaction":
		parent_meta = frappe.get_meta(doctype)
		Parent = frappe.qb.DocType(doctype)

		amount_field = (
			"base_net_total" if parent_meta.has_field("base_net_total") else "base_grand_total"
		)
		qty_field = "total_qty" if parent_meta.has_field("total_qty") else None

		query = (
			frappe.qb.from_(Parent)
			.select(Parent[amount_field].as_("amount"))
			.where(Parent[date_field][pr_doc.valid_from : pr_doc.valid_upto])
			.where(Parent.docstatus == 1)
		)
		if qty_field:
			query = query.select(Parent[qty_field].as_("stock_qty"))

		for field in ("company", "currency"):
			if pr_doc.get(field) and parent_meta.has_field(field):
				query = query.where(Parent[field] == pr_doc.get(field))

		party_field = "customer" if pr_doc.get("selling") else "supplier"
		if pr_doc.get(party_field) and parent_meta.has_field(party_field):
			query = query.where(Parent[party_field] == pr_doc.get(party_field))

		if pr_doc.get("modified"):
			query = query.where(Parent.creation >= pr_doc.get("modified"))

		if doc.get("name"):
			query = query.where(Parent.name != doc.get("name"))

		for data in query.run(as_dict=True):
			sum_qty += flt(data.get("stock_qty") or 0)
			sum_amt += flt(data.get("amount") or 0)

		return [sum_qty, sum_amt]

	values = [pr_doc.valid_from, pr_doc.valid_upto]
	condition = ""

	if pr_doc.warehouse:
		warehouses = get_child_warehouses(pr_doc.warehouse)

		condition += """ and `tab{child_doc}`.warehouse in ({warehouses})
			""".format(child_doc=child_doctype, warehouses=",".join(["%s"] * len(warehouses)))

		values.extend(warehouses)

	if items:
		condition += " and `tab{child_doc}`.{apply_on} in ({items})".format(
			child_doc=child_doctype, apply_on=apply_on, items=",".join(["%s"] * len(items))
		)

		values.extend(items)
	
	if pr_doc.get("modified"):
		condition += f" and `tab{doctype}`.creation >= %s"
		values.append(pr_doc.get("modified"))

	data_set = frappe.db.sql(
		f""" SELECT `tab{child_doctype}`.stock_qty,
			`tab{child_doctype}`.amount
		FROM `tab{child_doctype}`, `tab{doctype}`
		WHERE
			`tab{child_doctype}`.parent = `tab{doctype}`.name and `tab{doctype}`.{date_field}
			between %s and %s and `tab{doctype}`.docstatus = 1
			{condition} group by `tab{child_doctype}`.name
	""",
		tuple(values),
		as_dict=1,
	)

	for data in data_set:
		sum_qty += data.get("stock_qty")
		sum_amt += data.get("amount")

	return [sum_qty, sum_amt]


def apply_pricing_rule_on_transaction(doc):
    conditions = "apply_on = 'Transaction'"
    values = {}
    conditions = get_other_conditions(conditions, values, doc)

    pricing_rules = frappe.db.sql(
        f""" Select `tabPricing Rule`.* from `tabPricing Rule`
        where  {conditions} and `tabPricing Rule`.disable = 0
    """, values, as_dict=1)

    if not pricing_rules:
        return

    coupon_rule_name = get_coupon_pricing_rule(doc)

    if coupon_rule_name and coupon_rule_has_matching_items(coupon_rule_name, doc):
        # Coupon entered AND its rule applies to items in this doc:
        # only the coupon's rule applies, all others are skipped.
        coupon_matched = [p for p in pricing_rules if p.name == coupon_rule_name]
        skipped = [p for p in pricing_rules if p.name != coupon_rule_name]

        for s in skipped:
            frappe.msgprint(
                _("Pricing Rule {0} is being ignored because coupon {1} is applied. "
                  "Only the coupon's pricing rule will be used.").format(
                    frappe.bold(s.name),
                    frappe.bold(doc.get("coupon_code")),
                ),
                indicator="orange",
                alert=True,
            )

        pricing_rules = coupon_matched

        if not pricing_rules:
            doc.set("additional_discount_percentage", 0)
            doc.set("discount_amount", 0)
            return

    elif coupon_rule_name:
        # Coupon entered but its rule doesn't match anything in this doc.
        frappe.msgprint(
            _("Coupon {0} does not apply to any items in this transaction. "
              "Normal pricing rules will be applied instead.").format(
                frappe.bold(doc.get("coupon_code")),
            ),
            indicator="yellow",
            alert=True,
        )
        pricing_rules = [p for p in pricing_rules if not p.get("coupon_code_based")]
        if not pricing_rules:
            return

    else:
        # No coupon entered: drop all coupon-based rules so they don't compete.
        pricing_rules = [p for p in pricing_rules if not p.get("coupon_code_based")]
        if not pricing_rules:
            return

    base_qty = flt(doc.total_qty)
    base_amount = flt(doc.total)

    filtered = []
    for pr in pricing_rules:
        qty = base_qty
        amount = base_amount
        if pr.get("is_cumulative"):
            pr_doc = frappe.get_cached_doc("Pricing Rule", pr.name)
            data = get_qty_amount_data_for_cumulative(pr_doc, doc)
            if data:
                qty += flt(data[0])
                amount += flt(data[1])
        if filter_pricing_rules_for_qty_amount(qty, amount, [pr]):
            filtered.append(pr)

    pricing_rules = filtered
    pricing_rules = filter_pricing_rule_based_on_condition(pricing_rules, doc)

    if pricing_rules:
        has_multiple = any(d.get("apply_multiple_pricing_rules") for d in pricing_rules)
        if has_multiple:
            multiple_rules = [p for p in pricing_rules if p.get("apply_multiple_pricing_rules")]
            skipped_rules = [p for p in pricing_rules if not p.get("apply_multiple_pricing_rules")]
            for skipped in skipped_rules:
                frappe.msgprint(
                    _("Pricing Rule {0} is being ignored because 'Apply Multiple Pricing Rules' is not checked, "
                      "while other applicable rules have it checked.").format(frappe.bold(skipped.name)),
                    indicator="orange", alert=True
                )
            pricing_rules = sorted(multiple_rules, key=lambda x: cint(x.get("priority") or 0), reverse=True)
        else:
            pricing_rules = [max(pricing_rules, key=lambda x: cint(x.get("priority") or 0))]

    if not pricing_rules:
        remove_free_item(doc)
        return

    if doc.get("pricing_rules"):
        doc.set(
            "pricing_rules",
            [r for r in doc.get("pricing_rules") if r.get("child_docname")]
        )

    accumulated_discount_amount = 0.0
    base_total = flt(doc.total)
    applied_transaction_rules = []
    coupon_short_circuited = False

    for d in pricing_rules:
        if d.price_or_product_discount == "Price":
            if d.apply_discount_on:
                doc.set("apply_discount_on", d.apply_discount_on)

            rule_value = flt(d.discount_percentage) or flt(d.discount_amount)
            if d.validate_applied_rule and rule_value:
                current_doc_value = flt(doc.get("additional_discount_percentage")) or flt(doc.get("discount_amount"))
                if current_doc_value is not None and current_doc_value < rule_value:
                    frappe.msgprint(_("User has not applied rule on the invoice {0}").format(doc.name))
                    continue

            if d.coupon_code_based:
                if doc.get("coupon_code"):
                    coupon_code_pricing_rule = frappe.db.get_value(
                        "Coupon Code", doc.get("coupon_code"), "pricing_rule"
                    )
                    if coupon_code_pricing_rule == d.name:
                        if d.discount_percentage:
                            accumulated_discount_amount = base_total * flt(d.discount_percentage) / 100.0
                        else:
                            accumulated_discount_amount = flt(d.discount_amount)
                        applied_transaction_rules = [d.name]   
                        coupon_short_circuited = True
                        break
                continue

            if d.apply_discount_on_rate:
                current_effective = base_total - accumulated_discount_amount
                if d.discount_percentage:
                    new_effective = current_effective * (1.0 - flt(d.discount_percentage) / 100.0)
                else:
                    new_effective = current_effective - flt(d.discount_amount)
                accumulated_discount_amount = base_total - new_effective
            else:
                if d.discount_percentage:
                    accumulated_discount_amount += base_total * flt(d.discount_percentage) / 100.0
                else:
                    accumulated_discount_amount += flt(d.discount_amount)

            applied_transaction_rules.append(d.name)

        elif d.price_or_product_discount == "Product":
            item_details = frappe._dict({"parenttype": doc.doctype, "free_item_data": []})
            get_product_discount_rule(d, item_details, doc=doc)
            apply_pricing_rule_for_free_items(doc, item_details.free_item_data)
            doc.set_missing_values()
            applied_transaction_rules.append(d.name)

    if accumulated_discount_amount:
        doc.set("additional_discount_percentage", 0)
        doc.set("discount_amount", flt(accumulated_discount_amount))

    for rule_name in applied_transaction_rules:
        doc.append("pricing_rules", {
            "pricing_rule": rule_name,
            "rule_applied": 1,
        })


    doc.calculate_taxes_and_totals()


def validate_coupon_applicability(doc,method = None):
    if not doc.get("coupon_code"):
        return

    coupon_rule_name = get_coupon_pricing_rule(doc)

    if not coupon_rule_name:
        frappe.msgprint(
            _("Coupon {0} has no linked pricing rule. It has been removed from the document.").format(
                frappe.bold(doc.get("coupon_code")),
            ),
            indicator="orange",
            alert=True,
        )
        doc.coupon_code = ""
        return

    if not coupon_rule_has_matching_items(coupon_rule_name, doc):
        frappe.msgprint(
            _("Coupon {0} does not apply to any items in this transaction. "
              "It has been removed and normal pricing rules will be used.").format(
                frappe.bold(doc.get("coupon_code")),
            ),
            indicator="orange",
            alert=True,
        )
        doc.coupon_code = ""


def remove_free_item(doc):
	for d in doc.items:
		if d.is_free_item:
			doc.remove(d)


def get_applied_pricing_rules(pricing_rules):
	if pricing_rules:
		if pricing_rules.startswith("["):
			return json.loads(pricing_rules)
		else:
			return pricing_rules.split(",")

	return []


def get_product_discount_rule(pricing_rule, item_details, args=None, doc=None):


	free_item = pricing_rule.free_item
	if pricing_rule.same_item and pricing_rule.get("apply_on") != "Transaction":
		free_item = item_details.item_code or args.item_code

	qty = pricing_rule.free_qty or 1

	if pricing_rule.is_recursive:
		qty = 0

		transaction_qty = 0

		for row in doc.items:
			if row.is_free_item:
				continue
			if pricing_rule.name in (row.pricing_rules or ""):
				transaction_qty += row.qty

		if args and args.get("item_code") and not args.get("is_free_item"):
			# Match row.name against args.child_docname (the row identifier)
			# or args.name (older callers that pass the row name as 'name').
			row_id = args.get("child_docname") or args.get("name")
			already_counted = any(
				not row.is_free_item
				and pricing_rule.name in (row.pricing_rules or "")
				and row.get("name") == row_id
				for row in doc.items
			)
			if not already_counted:
				transaction_qty += flt(args.get("qty") or 0)
		

		transaction_qty = transaction_qty - pricing_rule.apply_recursion_over
		if transaction_qty and transaction_qty > 0:
			qty = flt(transaction_qty) * qty / pricing_rule.recurse_for

			if pricing_rule.round_free_qty:
				qty = (flt(transaction_qty) // pricing_rule.recurse_for) * (pricing_rule.free_qty or 1)
			
	if not qty:
		return

	free_item_data_args = {
		"item_code": free_item,
		"qty": qty,
		"pricing_rules": pricing_rule.name,
		"rate": pricing_rule.free_item_rate or 0,
		"price_list_rate": pricing_rule.free_item_rate or 0,
		"is_free_item": 1,
	}

	item_data = frappe.get_cached_value(
		"Item", free_item, ["item_name", "description", "stock_uom"], as_dict=1
	)

	free_item_data_args.update(item_data)
	free_item_data_args["uom"] = pricing_rule.free_item_uom or item_data.stock_uom
	free_item_data_args["conversion_factor"] = get_conversion_factor(
		free_item, free_item_data_args["uom"]
	).get("conversion_factor", 1)

	if item_details.get("parenttype") == "Purchase Order":
		free_item_data_args["schedule_date"] = doc.schedule_date if doc else today()

	if item_details.get("parenttype") == "Sales Order":
		free_item_data_args["delivery_date"] = doc.delivery_date if doc else today()

	item_details.free_item_data.append(free_item_data_args)

def apply_pricing_rule_for_free_items(doc, pricing_rule_args):
	if pricing_rule_args:
		args = {(d["item_code"], d["pricing_rules"]): d for d in pricing_rule_args}

		for item in doc.items:
			if not item.is_free_item:
				continue

			free_item_data = args.get((item.item_code, item.pricing_rules))
			if free_item_data:
				dont_enforce = frappe.db.get_value(
					"Pricing Rule", free_item_data["pricing_rules"], "dont_enforce_free_item_qty"
				)

				if not dont_enforce:
					# Rule enforces qty/rate — overwrite whatever the user typed
					free_item_data.pop("item_name", None)
					free_item_data.pop("description", None)
					item.update(free_item_data)

				else:
					rule_qty = flt(free_item_data.get("qty"))
					if flt(item.qty) > rule_qty:
						frappe.msgprint(
							_("Free item {0} qty capped at {1} (max allowed by Pricing Rule {2}).").format(
								frappe.bold(item.item_code), frappe.bold(rule_qty), frappe.bold(free_item_data["pricing_rules"]),
							),
							indicator="orange", alert=True,
						)
						item.qty = rule_qty
					# rate is always rule-controlled
					item.rate = flt(free_item_data.get("rate"))
					item.price_list_rate = flt(free_item_data.get("price_list_rate"))

				# else: user is allowed to edit — keep qty/rate as-is, just acknowledge the row

				args.pop((item.item_code, item.pricing_rules))

		for free_item in args.values():
			if doc.is_new() or not frappe.db.get_value(
				"Pricing Rule", free_item["pricing_rules"], "dont_enforce_free_item_qty"
			):
				doc.append("items", free_item)


def get_pricing_rule_items(pr_doc, other_items=False) -> list:
	apply_on_data = []
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	pricing_rule_apply_on = apply_on_table.get(pr_doc.get("apply_on"))

	if pr_doc.apply_rule_on_other and other_items:
		apply_on = frappe.scrub(pr_doc.apply_rule_on_other)
		apply_on_data.append(pr_doc.get("other_" + apply_on))
	else:
		for d in pr_doc.get(pricing_rule_apply_on):
			if apply_on == "item_group":
				apply_on_data.extend(get_child_item_groups(d.get(apply_on)))
			else:
				apply_on_data.append(d.get(apply_on))

	return list(set(apply_on_data))


def validate_coupon_code(coupon_name):
	coupon = frappe.get_doc("Coupon Code", coupon_name)
	if coupon.valid_from and coupon.valid_from > getdate(today()):
		frappe.throw(_("Sorry, this coupon code's validity has not started"))
	elif coupon.valid_upto and coupon.valid_upto < getdate(today()):
		frappe.throw(_("Sorry, this coupon code's validity has expired"))
	elif coupon.maximum_use and coupon.used >= coupon.maximum_use:
		frappe.throw(_("Sorry, this coupon code is no longer valid"))


def update_coupon_code_count(coupon_name, transaction_type):
	coupon = frappe.get_doc("Coupon Code", coupon_name)
	if coupon:
		if transaction_type == "used":
			if not coupon.maximum_use:
				coupon.used = coupon.used + 1
				coupon.save(ignore_permissions=True)
			elif coupon.used < coupon.maximum_use:
				coupon.used = coupon.used + 1
				coupon.save(ignore_permissions=True)
			else:
				frappe.throw(
					_("{0} Coupon used are {1}. Allowed quantity is exhausted").format(
						coupon.coupon_code, coupon.used
					)
				)
		elif transaction_type == "cancelled":
			if coupon.used > 0:
				coupon.used = coupon.used - 1
				coupon.save(ignore_permissions=True)
