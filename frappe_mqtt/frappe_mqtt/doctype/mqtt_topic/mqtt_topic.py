# Copyright (c) 2025, Emmanuel Anthony and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe_mqtt.utility import refresh_subscriptions_from_doctype

class MQTTTopic(Document):


	def after_insert(self):
		refresh_subscriptions_from_doctype()

	def on_update(self):
		refresh_subscriptions_from_doctype()

	def on_trash(self):
		refresh_subscriptions_from_doctype()

