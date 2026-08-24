// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Purchase Analytics"] = {
	filters: [
		{
			fieldname: "tree_type",
			label: __("Tree Type"),
			fieldtype: "Select",
			options: ["Supplier Group", "Supplier", "Item Group", "Item"],
			default: "Supplier",
			reqd: 1,
		},
		{
			fieldname: "doc_type",
			label: __("based_on"),
			fieldtype: "Select",
			options: ["Purchase Order", "Purchase Receipt", "Purchase Invoice"],
			default: "Purchase Invoice",
			reqd: 1,
		},
		{
			fieldname: "value_quantity",
			label: __("Value Or Qty"),
			fieldtype: "Select",
			options: [
				{ value: "Value", label: __("Value") },
				{ value: "Quantity", label: __("Quantity") },
			],
			default: "Value",
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[1],
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[2],
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "range",
			label: __("Range"),
			fieldtype: "Select",
			options: [
				{ value: "Weekly", label: __("Weekly") },
				{ value: "Monthly", label: __("Monthly") },
				{ value: "Quarterly", label: __("Quarterly") },
				{ value: "Yearly", label: __("Yearly") },
			],
			default: "Monthly",
			reqd: 1,
		},
		{
			fieldname: "show_aggregate_value_from_subsidiary_companies",
			label: __("Show Aggregate Value from Subsidiary Companies"),
			fieldtype: "Check",
		},
	],
	after_datatable_render(datatable) {
		const chart_data = frappe.query_report.chart_options?.data;
		if (!chart_data) return;

		// Keep an untouched copy because checkbox interactions mutate the rendered chart data.
		datatable.purchase_analytics_chart_data = {
			labels: [...chart_data.labels],
			datasets: chart_data.datasets.map((dataset) => ({
				...dataset,
				values: [...dataset.values],
			})),
		};

		if (datatable.purchase_analytics_filter_bound) return;

		const filter_rows = datatable.datamanager.options.filterRows;
		datatable.datamanager.options.filterRows = (...args) =>
			Promise.resolve(filter_rows(...args)).then((row_indices) => {
				const indices = row_indices || datatable.datamanager.getAllRowIndices();
				const source_data = datatable.purchase_analytics_chart_data;
				const data = {
					labels: source_data.labels,
					datasets: indices.map((index) => source_data.datasets[index]).filter(Boolean),
				};

				const options = Object.assign({}, frappe.query_report.chart_options, { data });
				frappe.query_report.render_chart(options);
				frappe.query_report.raw_chart_data = data;

				return row_indices;
			});

		datatable.purchase_analytics_filter_bound = true;
	},
	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
			events: {
				onCheckRow: function (data) {
					if (!data) return;

					const data_doctype = $(data[2].html)[0].attributes.getNamedItem("data-doctype").value;
					const tree_type = frappe.query_report.filters[0].value;
					if (data_doctype != tree_type) return;

					let row_name = data[2].content;
					let length = data.length;
					let row_values = "";

					if (tree_type == "Supplier") {
						row_values = data.slice(4, length - 1).map(function (column) {
							return column.content;
						});
					} else if (tree_type == "Item") {
						row_values = data.slice(5, length - 1).map(function (column) {
							return column.content;
						});
					} else {
						row_values = data.slice(3, length - 1).map(function (column) {
							return column.content;
						});
					}

					let entry = {
						name: row_name,
						values: row_values,
					};

					let raw_data = frappe.query_report.chart.data;
					let new_datasets = raw_data.datasets;

					let element_found = new_datasets.some((element, index, array) => {
						if (element.name == row_name) {
							array.splice(index, 1);
							return true;
						}
						return false;
					});

					if (!element_found) {
						new_datasets.push(entry);
					}
					let new_data = {
						labels: raw_data.labels,
						datasets: new_datasets,
					};
					const new_options = Object.assign({}, frappe.query_report.chart_options, {
						data: new_data,
					});
					frappe.query_report.render_chart(new_options);

					frappe.query_report.raw_chart_data = new_data;
				},
			},
		});
	},
};
