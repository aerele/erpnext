# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
import plaid
from frappe import _
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_sync_request_options import TransactionsSyncRequestOptions

PLAID_ENVIRONMENT_HOSTS = {
	"sandbox": plaid.Environment.Sandbox,
	"production": plaid.Environment.Production,
	# Development was removed from the SDK but is still a Plaid Settings option.
	"development": "https://development.plaid.com",
}

PLAID_SUPPORTED_COUNTRY_CODES = [
	"US",
	"GB",
	"ES",
	"NL",
	"FR",
	"IE",
	"CA",
	"DE",
	"IT",
	"PL",
	"DK",
	"NO",
	"SE",
	"EE",
	"LT",
	"LV",
	"PT",
	"BE",
	"AT",
	"FI",
]

PLAID_SUPPORTED_LANGUAGE_CODES = {
	"da",
	"nl",
	"en",
	"et",
	"fr",
	"de",
	"hi",
	"it",
	"lv",
	"lt",
	"no",
	"pl",
	"pt",
	"ro",
	"es",
	"sv",
	"vi",
}


class PlaidConnector:
	def __init__(self, access_token=None):
		self.access_token = access_token
		self.settings = frappe.get_single("Plaid Settings")
		self.products = ["transactions"]
		self.client_name = frappe.local.site
		self.client = self._get_client()

	def _get_client(self):
		configuration = plaid.Configuration(
			host=PLAID_ENVIRONMENT_HOSTS.get(self.settings.plaid_env),
			api_key={
				"clientId": self.settings.plaid_client_id,
				"secret": self.settings.get_password("plaid_secret"),
				"plaidVersion": "2020-09-14",
			},
		)
		api_client = plaid.ApiClient(configuration)
		return plaid_api.PlaidApi(api_client)

	@staticmethod
	def _to_dict(obj):
		if hasattr(obj, "to_dict"):
			return obj.to_dict()
		return obj

	@staticmethod
	def _get_link_language():
		language = (frappe.local.lang or "en").split("-")[0].lower()
		return language if language in PLAID_SUPPORTED_LANGUAGE_CODES else "en"

	def get_transactions_days_requested(self):
		days_requested = self.settings.transactions_days_requested or 90
		return min(max(days_requested, 90), 730)

	def get_access_token(self, public_token):
		if public_token is None:
			frappe.log_error("Plaid: Public token is missing")

		request = ItemPublicTokenExchangeRequest(public_token=public_token)
		response = self.client.item_public_token_exchange(request)
		return response.access_token

	def get_token_request(self, update_mode=False):
		country_codes = (
			PLAID_SUPPORTED_COUNTRY_CODES if self.settings.enable_european_access else ["US", "CA"]
		)
		request_kwargs = {
			"client_name": self.client_name,
			"language": self._get_link_language(),
			"country_codes": [CountryCode(code) for code in country_codes],
			"user": LinkTokenCreateRequestUser(
				client_user_id=frappe.generate_hash(frappe.session.user, length=32)
			),
		}

		if update_mode:
			request_kwargs["access_token"] = self.access_token
		else:
			request_kwargs["products"] = [Products(product) for product in self.products]
			request_kwargs["transactions"] = LinkTokenTransactions(
				days_requested=self.get_transactions_days_requested()
			)

		return request_kwargs

	def get_link_token(self, update_mode=False):
		token_request = self.get_token_request(update_mode)

		try:
			response = self.client.link_token_create(LinkTokenCreateRequest(**token_request))
		except plaid.ApiException as e:
			frappe.log_error(message=e.body, title=_("Plaid: Link token create error"))
			frappe.throw(_("Cannot retrieve link token. Check Error Log for more information"))
		else:
			return response.link_token

	def get_transactions(self, cursor=None, account_id=None):
		options = TransactionsSyncRequestOptions()
		if account_id:
			options.account_id = account_id

		updates = {"added": [], "modified": [], "removed": [], "next_cursor": None}
		request_cursor = cursor

		try:
			while True:
				request = TransactionsSyncRequest(
					access_token=self.access_token,
					cursor=request_cursor,
					count=500,
					options=options,
				)
				response = self.client.transactions_sync(request)
				for key in ("added", "modified", "removed"):
					updates[key].extend(self._to_dict(transaction) for transaction in response.get(key, []))
				updates["next_cursor"] = response.get("next_cursor")

				if not response.has_more:
					break

				request_cursor = response.next_cursor

			return updates
		except plaid.ApiException as e:
			frappe.log_error(message=e.body, title=_("Plaid: Transactions sync error"))
			frappe.throw(_("Cannot sync transactions. Check Error Log for more information"))
		except Exception:
			frappe.log_error("Plaid: Transactions sync error")
