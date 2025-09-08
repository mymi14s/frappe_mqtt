frappe.listview_settings['MQTT Broker'] = {
    onload(listview) {
        listview.page.add_inner_button(__('Reload Brokers'), function() {

            frappe.call({
                method: "frappe_mqtt.utility.reload_all_clients",
                callback: function(r) {
                    if (!r.exc) {
                        frappe.msgprint(__('Action executed successfully'));
                        listview.refresh();
                    }
                }
            });
        });
    }
};
