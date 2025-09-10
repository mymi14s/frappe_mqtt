# Copyright (c) 2025, Emmanuel Anthony and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe_mqtt.utility import reload_all_clients

class MQTTBroker(Document):
	
	
	def after_insert(self):
		reload_all_clients()

	def on_update(self):
		reload_all_clients()

	def on_trash(self):
		reload_all_clients()
