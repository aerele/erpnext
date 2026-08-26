frappe.ready(() => {
	document.querySelectorAll(".quotation-response").forEach((button) => {
		button.addEventListener("click", () => {
			const response = button.dataset.response;
			const note = document.querySelector("#customer-response-note").value;
			const message = response === "Accepted" ? __("Accept this quotation?") : __("Reject this quotation?");

			frappe.confirm(message, () => {
				frappe.call({
					type: "POST",
					method: "erpnext.selling.doctype.quotation.quotation.respond_from_customer_portal",
					args: {
						quotation_name: {{ doc.name | tojson }},
						response,
						note,
					},
					btn: button,
					callback: () => window.location.reload(),
				});
			});
		});
	});
});
