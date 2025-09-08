app_name = "frappe_mqtt"
app_title = "Frappe MQTT"
app_publisher = "Emmanuel Anthony"
app_description = "MQTT Client for Frappe"
app_email = "mymi14s@hotmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "frappe_mqtt",
		"logo": "/assets/frappe_mqtt/images/frappe_mqtt.png",
		"title": "Frappe MQTT",
		"route": "app/frappe-mqtt",
		# "has_permission": "frappe_mqtt.api.permission.has_app_permission"
	}
]





fixtures = [
    {
        "dt": "Role", 
        "filters": [["name", "=", "MQTT"]],
    }
]

