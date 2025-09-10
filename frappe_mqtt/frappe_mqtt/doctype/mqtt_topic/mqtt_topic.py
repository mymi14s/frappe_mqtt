# Copyright (c) 2025, Emmanuel Anthony and contributors
# For license information, please see license.txt

from urllib.parse import urlparse
import frappe, re
from frappe.model.document import Document
from frappe_mqtt.mqtt_utility import refresh_subscriptions_from_doctype

class MQTTTopic(Document):

	def validate(self):
		self.is_valid_url()

	def after_insert(self):
		refresh_subscriptions_from_doctype()

	def on_update(self):
		refresh_subscriptions_from_doctype()

	def on_trash(self):
		refresh_subscriptions_from_doctype()


	def is_valid_url(self):
		if self.webhook_url:
			result = urlparse(self.webhook_url)
			if all([result.scheme, result.netloc]):
				regex = re.compile(
					r'^(?:http|ftp)s?://' 
					r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+'
					r'(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' 
					r'localhost|' 
					r'\d{1,3}(?:\.\d{1,3}){3})' 
					r'(?::\d+)?' 
					r'(?:/?|[/?]\S+)$', re.IGNORECASE
				)
				if not re.match(regex, self.webhook_url):
					frappe.throw("Invalid URL")
			else:
				frappe.throw("Invalid URL")



