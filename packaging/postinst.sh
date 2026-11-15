#!/bin/bash
set -e

if command -v systemctl >/dev/null 2>&1; then
    # Reload systemd to pick up the new service
    systemctl daemon-reload

    # Optional: enable service so it starts on boot
    systemctl enable mqtt2ir.service

    echo "mqtt2ir has been installed."
    echo "Edit /etc/mqtt2ir.toml before starting the service:"
    echo "   sudo nano /etc/mqtt2ir.toml"
    echo "Provide a file compatible to ir-keytable, e.g. /etc/ir/keytable.toml", otherwise the service will not work.
    echo "Then start the service with:"
    echo "   sudo systemctl start mqtt2ir"
else
    echo "systemctl not found. Please configure and start mqtt2ir manually."
fi
