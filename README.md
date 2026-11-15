# mqtt2ir

`mqtt2ir` bridges MQTT commands to infrared keys sent by `ir-ctl` from
v4l-utils. It publishes Home Assistant button entities for the button names in
the configured ir-keytable keymap. This script requires python 3.11 and above (for tomllib).

## Configuration

The service reads `/etc/mqtt2ir.toml`:

```toml
[mqttserver]
host = "mqtt-server"
port = 1883
# user = "mqttuser"
# password = "mqttpassword"

[ir]
keymap = "/etc/ir/keymap.toml"
device = "/dev/lirc0"
gap = 200000

[ir.carriers]
default = 38000
"*sony*" = 40000
```

`keymap` points to an ir-keytable-compatible TOML file. Every
`[[protocols]]` entry must have a `[protocols.scancodes]` table whose values
are the button names to expose and send, for example:

```toml
[[protocols]]
name = "example"
protocol = "nec"

[protocols.scancodes]
"0x1234" = "KEY_POWER"
```

Button names from all protocol entries are combined. Therefore,
button names in the keymap and MQTT payload must be unique across protocols.
If devices use the same button names, prefix them
with the device name (for example, `TV_KEY_POWER`) to avoid collisions.
Button names must not contain `+`, `#`, `/`, or NUL characters because they
are used as a segment in Home Assistant discovery topics.
The discovered buttons and status sensor are grouped under one MQTT device.
The carrier frequency is selected by matching each keymap protocol name
against the patterns in `[ir.carriers]`. Patterns use shell-style wildcards;
the first matching pattern is used, and `default` is used when none match.
Carrier frequencies are positive integer values in hertz.

## MQTT command payload

Publish JSON to `multimediaremote/ir/set` with a keymap button name and a
string for a positive integer `repeat`.

```json
{"button": "KEY_POWER", "repeat": "3"}
```

The bridge invokes `ir-ctl` with the configured keymap, one `-K KEY_POWER`
argument per repeat, and the configured `gap` value for `--gap`.