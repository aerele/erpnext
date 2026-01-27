frappe.treeview_settings["Department"] = {
	ignore_fields: ["parent_department"],
	get_tree_nodes: "erpnext.setup.doctype.department.department.get_children",
	add_tree_node: "erpnext.setup.doctype.department.department.add_node",
	get_tree_root: false,
	root_label: __("Department"),
	filters: [
		{
			fieldname: "company",
			fieldtype: "Select",
			label: __("Company"),
			options: erpnext.utils.get_tree_options("company"),
			default: erpnext.utils.get_tree_default("company"),
			on_change: function () {
				cur_tree && cur_tree.make_tree();
			},
		},
	],
	fields: [
		{
			fieldtype: "Data",
			fieldname: "department_name",
			label: __("New Department Name"),
			reqd: true,
		},
		{
			fieldtype: "Check",
			fieldname: "is_group",
			label: __("Is Group"),
		},
	],
	onload: function (treeview) {
		treeview.make_tree();
	},
};
