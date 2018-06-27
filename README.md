# unipi-mqtt

This is a script that creates MQTT messages based on the events that happen on the UNIPI device. Worked with a Unipi 513 here. Main goal is to get MQTT messages to Home Assistant. I am building a client script too that listens to MQTT and switches UNIPI outputs, stay tuned, 

Script creates a websocket connection to EVOK and based on those websocket messages and a config file MQTT messages are published. 

WARNING: I am not a programmer, so this code kinda works, but it ain't pretty ;-) (I think...). 

Be sure to use the 2 python scripts (python3) and the json config file. 
