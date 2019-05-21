# unipi-mqtt

This is a script that creates MQTT messages based on the events that happen on the UNIPI device. Worked with a Unipi 513 here. Main goal is to get MQTT messages to Home Assistant. I am building a client script too that listens to MQTT and switches UNIPI outputs, stay tuned, 

Script creates a websocket connection to EVOK and based on those websocket messages and a config file MQTT messages are published. 

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
 - Create a service based on this script (example to do so here; http://www.diegoacuna.me/how-to-run-a-script-as-a-service-in-raspberry-pi-raspbian-jessie/)
 - Start the service and see what happens. 
 - Logging goes to /var/log/unipi_mqtt.log
 
## HASSIO Config

Example for sensor (from UniPi input to HASSIO)
```
- platform: mqtt
  state_topic: "unipi/bgg/woonkamer/lux"
  name: 'Woonkamer Lux'
  unit_of_measurement: 'lux'
  value_template: '{{ value_json.lux }}'
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

## ToDO
  - Something with authenticaton
  - Use config file for client part too
  - clean up code more
  - many other yet to discover things.
