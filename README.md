# UniPi MQTT

This is a script that creates MQTT messages based on the events that happen on the UNIPI device ans switches UniPi outputs based on received MQTT messages. Worked with a Unipi 513 here. Main goal is to get MQTT messages to/from Home Assistant.

Script creates a websocket connection to EVOK and based on those websocket messages and a config file MQTT messages are published. 

Also creates a MQTT listener to listen to incomming MQTT topics and switch UniPi outputs based on those messages. I created the system in such a way that info to switch an output must be in the MQTT message. See the Hass examples below. 

WARNING: I am not a programmer, so this code kinda works, but it ain't pretty ;-) (I think...). So there is a big chance you need to tinker a bit in the scripts. It's also a version 1 that I build specifically for my home assistant setup, so it's quite tailored to my personal need and way of working. 

Be sure to use the 2 python scripts (python3) and the json config file. 

## Setup

I put the files in a directory and create a service to automatically start and stop the service on system start. 

Prereq:
 - A UniPi system with EVOK (opensource software from EVOK)
 - MQTT Broker somewhere
 
Setup:
 - Copy the 3 scripts into a dir
 - Adjust the vars in the script to your needs, like IP, etc.
 - Adjust the unipi_mqtt_config.json file to refelxt your unipi and the connected devices to it (see below for more details)
 - optional; Create a service based on this script (example to do so here; http://www.diegoacuna.me/how-to-run-a-script-as-a-service-in-raspberry-pi-raspbian-jessie/)
 - Start the service or script and see what happens. 
 - Logging goes to /var/log/unipi_mqtt.log

## UniPi unipi_mqtt_config.json

A config file is used to describe to inputs on the UniPi so the script knows what to send out when a change on a input is detected. An example config file is in the repo, here an example entry. It's JSON, so make sure it's valid. 

Example PIR sensor for motion detection:
```json
   {
      "circuit":"1_04",
      "description":"Kantoor PIR",
      "dev":"input",
      "device_delay":120,
      "device_normal":"no",
      "unipi_value":0,
      "unipi_prev_value":0,
      "unipi_prev_value_timstamp":0,
      "state_topic": "unipi/bgg/kantoor/motion"
   },
```

Example with "handle local" function to handle a local "critical" function within the script.

```json
   {
      "circuit":"2_05",
      "description":"Voordeur Beldrukker",
      "dev":"input",
      "handle_local":
            {
	            "type": "bel",
				"trigger":"on",
				"rings": 3,
				"output_dev": "output",
				"output_circuit": "2_01"
			},
      "device_delay":1,
      "device_normal":"no",
      "unipi_value":0,
      "unipi_prev_value":0,
      "unipi_prev_value_timstamp":0,
      "state_topic": "unipi/bgg/voordeur/beldrukker"
   },
```

Description of the fields:
 - dev: The input device type on the UniPi
 - circuit: The input circuit on the UniPi
 - description: Description of what you do with this input
 - device_delay: delay to turn device off automatically (used for PIR sensors that work pulse based)
 - device_normal: is device normal open or normal closed
 - unipi_value: what is the current value, used as a "global var"
 - unipi_prev_value: what is the previous value, used as a "global var" to calculate average of multiple values ver time
 - unipi_prev_value_timstamp: when was the last status change. Used for delay based off messages, for exmpl. for PIR pulse
 - state_topic: MQTT state topic to send message on
 - handle_local: Use to switch outputs based on a input directly. So no dependency on MQTT broker or HASSIO. Use this for bel and light switches. Does send a MQTT update message to status can change in Home Assistant.
 

## HASSIO Config

Example for sensor (from UniPi input to HASSIO)
```
- platform: mqtt
  name: "Kantoor Motion"
  state_topic: "unipi/bgg/kantoor/motion"
  payload_on: "ON"
  payload_off: "OFF"
  availability_topic: "unipi/bgg/kantoor/motion/available"
  payload_available: "online"
  payload_not_available: "offline"
  qos: 0
  device_class: presence
  #retain: true
```  
Example for light (publish from HASS to UniPi to turn on an output)
```
- platform: mqtt
  schema: template
  name: "Voordeur light"
  state_topic: "homeassistant/buiten/voordeur/licht"
  command_topic: "homeassistant/buiten/voordeur/licht/set"
  availability_topic: "homeassistant/buiten/voordeur/licht/available"
  payload_available: "online"
  payload_not_available: "offline"
  command_on_template: >
    {"state": "on"
    , "circuit": "2_02"
    , "dev": "analogoutput"
    {%- if brightness is defined -%}
    , "brightness": {{ brightness }}
    {%- elif brightness is undefined -%}
    , "brightness": 100
    {%- endif -%}
    {%- if effect is defined -%}
    , "effect": "{{ effect }}"
    {%- endif -%}
    {%- if transition is defined -%}
    , "transition": {{ transition }}
    {%- endif -%}
    }
  command_off_template: '{"state": "off", "circuit": "2_02", "dev": "analogoutput"}'
  state_template: '{{ value_json.state }}'
  brightness_template: '{{ value_json.brightness }}'
  qos: 0
```

# Change log

### Version 0.1
Initial release and documentation in this readme file

### Version 0.2
Changes:
 - Changed handling if DI devices with delay to no longer use previous state for rest of devices, cleaned up json config file. Should fix a bug that crashed the script on certain ON / OFF actions.
 - Implemented a "frist run" part to set MQTT messages at script start to reflect actual status of inputs, not last known status maintained in MQTT broker or no status at al. 
 - tested UART (extension module) and that works. Changed config file with example

## ToDo
  - Something with authenticaton
  - Use config file for client part too
  - clean up code more
  - many other yet to discover things.

# Test info

Tested on a UniPi 513 with Extensio xS30 running Evok 2.x and Home Assistant 0.93
Used:
 - 0-10v inputs and outputs
 - relay outputs
 - Digital inputs and outputs
 - 1 wire for temp, humidity and light
 - UART Extention module 30

