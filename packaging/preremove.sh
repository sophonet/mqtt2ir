#!/bin/bash
set -e

# Only run if systemctl command is available
if command -v systemctl >/dev/null 2>&1; then
    # Stop and disable the service if it is running
    if systemctl is-enabled --quiet mqtt2ir.service; then
        systemctl disable mqtt2ir.service || true
    fi

    if systemctl is-active --quiet mqtt2ir.service; then
        systemctl stop mqtt2ir.service || true
    fi

    echo "mqtt2ir services have been stopped and disabled."
fi
