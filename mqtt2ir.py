import fnmatch
import json
import logging
import subprocess
from pathlib import Path

import paho.mqtt.client as mqtt

import tomllib

# --- Configuration ---------------------------------------------------------

CONFIG_FILE = Path("/etc/mqtt2ir.toml")

with CONFIG_FILE.open("rb") as config_file:
    CONFIG = tomllib.load(config_file)

MQTT_CONFIG = CONFIG["mqttserver"]
IR_CONFIG = CONFIG["ir"]

MQTT_HOST = MQTT_CONFIG["host"]
MQTT_PORT = MQTT_CONFIG["port"]
MQTT_USER = MQTT_CONFIG.get("user")
MQTT_PASSWORD = MQTT_CONFIG.get("password")
IR_DEVICE = IR_CONFIG["device"]
IR_KEYMAP = Path(IR_CONFIG["keymap"])
IR_GAP = IR_CONFIG["gap"]
IR_CARRIERS = IR_CONFIG["carriers"]

if not isinstance(IR_CARRIERS, dict) or "default" not in IR_CARRIERS:
    raise ValueError(
        "IR configuration must contain a [ir.carriers] table with a default"
    )
for pattern, carrier in IR_CARRIERS.items():
    if not isinstance(pattern, str) or type(carrier) is not int or carrier < 1:
        raise ValueError(
            "IR carrier patterns must map to positive integer frequencies"
        )

DISCOVERY_PREFIX = "homeassistant"   # HA default
BASE_TOPIC = "multimediaremote/ir"
if MQTT_CONFIG.get("base_topic"):
    BASE_TOPIC = MQTT_CONFIG["base_topic"]

COMMAND_TOPIC = f"{BASE_TOPIC}/set"
AVAILABILITY_TOPIC = f"{BASE_TOPIC}/availability"
HA_STATUS_TOPIC = f"{DISCOVERY_PREFIX}/status"
# Home Assistant publishes "online" here on start.

DEVICE_INFO = {
    "identifiers": [BASE_TOPIC],
    "name": "MQTT IR Bridge",
}

# --- Helpers ---------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ir_mqtt")


def parse_keymap(path):
    """Return button names from all protocols in an ir-keytable keymap."""
    with Path(path).open("rb") as keymap_file:
        keymap = tomllib.load(keymap_file)

    protocols = keymap.get("protocols")
    if not isinstance(protocols, list):
        raise ValueError(
            "Keymap must contain one or more [[protocols]] entries"
        )

    button_to_protocol = {}
    for protocol in protocols:
        scancodes = (
            protocol.get("scancodes") if isinstance(protocol, dict) else None
        )
        if not isinstance(scancodes, dict):
            raise ValueError(
                "Each keymap protocol must contain a "
                "[protocols.scancodes] table"
            )
        protocol_name = protocol.get("protocol")
        if not isinstance(protocol_name, str) or not protocol_name:
            raise ValueError(
                "Each keymap protocol must contain a non-empty protocol name"
            )
        for button in scancodes.values():
            if not isinstance(button, str) or not button:
                raise ValueError(
                    "Keymap scancode values must be non-empty button names"
                )
            invalid_chars = {"+", "#", "/", "\0"}
            if any(char in button for char in invalid_chars):
                raise ValueError(
                    f"Button name {button!r} contains characters invalid in "
                    "a discovery topic"
                )
            if button in button_to_protocol:
                raise ValueError(f"Duplicate button name in keymap: {button}")
            button_to_protocol[button] = protocol_name

    if not button_to_protocol:
        raise ValueError("Keymap does not contain any scancode button names")
    return button_to_protocol


BUTTON_TO_PROTOCOL = parse_keymap(IR_KEYMAP)
BUTTONS = list(BUTTON_TO_PROTOCOL)


def state_topic():
    return f"{BASE_TOPIC}/state"


def carrier_for_protocol(protocol):
    """Return the first matching protocol carrier, or the default."""
    for pattern, carrier in IR_CARRIERS.items():
        if pattern != "default" and fnmatch.fnmatchcase(protocol, pattern):
            return carrier
    return IR_CARRIERS["default"]


def publish_discovery(client):
    """Publish one button entity per mapped key and a last-command sensor."""
    availability = {
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
    }

    for key in BUTTONS:
        config = {
            "name": key,
            "unique_id": key,
            "object_id": key,
            "command_topic": COMMAND_TOPIC,
            "payload_press": json.dumps(
                {"repeat": 1, "button": key}
            ),
            "device": DEVICE_INFO,
            **availability,
        }
        topic = f"{DISCOVERY_PREFIX}/button/{key}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)

    sensor = {
        "name": "Last command",
        "unique_id": "last_command",
        "object_id": "last_command",
        "state_topic": state_topic(),
        "entity_category": "diagnostic",
        "icon": "mdi:remote",
        "device": DEVICE_INFO,
        **availability,
    }
    topic = f"{DISCOVERY_PREFIX}/sensor/last_command/config"
    client.publish(topic, json.dumps(sensor), qos=1, retain=True)

    log.info("Published discovery for %d button(s)", len(BUTTONS))


def set_online(client):
    client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)


# --- MQTT callbacks --------------------------------------------------------

def on_connect(client, userdata, flags, reason_code, properties=None):
    # paho 2.x passes a ReasonCode object, paho 1.x passes an int (0 = success)
    failed = (
        reason_code.is_failure
        if hasattr(reason_code, "is_failure")
        else reason_code != 0
    )
    if failed:
        log.error("MQTT connect failed: %s", reason_code)
        return
    log.info("Connected to broker")
    # Subscribing here means we resubscribe automatically after a reconnect
    client.subscribe([(COMMAND_TOPIC, 1), (HA_STATUS_TOPIC, 1)])
    set_online(client)
    publish_discovery(client)


def handle_command(client, payload):
    try:
        data = json.loads(payload)
        repeat = int(data["repeat"])
        button = data["button"]
    except (ValueError, KeyError, TypeError):
        log.warning("Ignoring malformed payload: %r", payload)
        return

    # Only whitelisted values ever reach the command line
    if (
        type(repeat) is not int
        or repeat < 1
        or not isinstance(button, str)
        or button not in BUTTONS
    ):
        log.warning("Rejected: repeat=%r button=%r", repeat, button)
        client.publish(state_topic(), "rejected")
        return

    args = [
        "ir-ctl",
        "-d",
        IR_DEVICE,
        "--keymap",
        str(IR_KEYMAP),
        "--gap",
        str(IR_GAP),
    ]
    for _ in range(repeat):
        args.extend(["-K", button])

    args.extend(["-c", str(carrier_for_protocol(BUTTON_TO_PROTOCOL[button]))])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        log.error("ir-ctl timed out: %s", args)
        client.publish(state_topic(), f"timeout: {repeat} {button}")
        return

    if result.returncode == 0:
        log.info("OK: %s x%d %s", IR_DEVICE, repeat, button)
        client.publish(state_topic(), f"ok: {repeat} {button}")
    else:
        log.error(
            "ir-ctl failed (%s): %s",
            result.returncode,
            result.stderr.strip(),
        )
        client.publish(state_topic(), f"error: {repeat} {button}")


def on_message(client, userdata, msg):
    if msg.topic == HA_STATUS_TOPIC:
        # HA restarted: re-announce so entities reappear
        if msg.payload.decode(errors="ignore") == "online":
            publish_discovery(client)
            set_online(client)
        return
    if msg.topic == COMMAND_TOPIC:
        handle_command(client, msg.payload)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD or None)
    else:
        log.info("MQTT_USER not set, connecting without authentication")
    # Broker marks us offline if we disconnect unexpectedly
    client.will_set(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    try:
        client.loop_forever()  # handles reconnects
    except KeyboardInterrupt:
        client.publish(
            AVAILABILITY_TOPIC,
            "offline",
            qos=1,
            retain=True,
        ).wait_for_publish()
        client.disconnect()


if __name__ == "__main__":
    main()
