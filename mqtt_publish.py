#!/usr/bin/python3

# Script to send MQTT messages to broker based on state changes from a UNIPI devices
# Requires a JSON config file with device config
# Also has the ability to handle certains actions locally (independent of the 
# Matthijs van den Berg, 2018 (not a real programmer, so code is crappy, ok?!)

# Documentation Resources
# - EVOK / UNIPI : https://evok.api-docs.io/1.0/jKcTKe5aRBCNjt8Az/introduction

import websocket
import _thread
import time
import datetime
import json
import logging
from domoticzpython import pythonz
from unipipython import unipython
from statistics import mean, median
import paho.mqtt.client as mqtt
import os
import traceback

# Your MQTT server information
broker_aconfig_devress="192.168.1.124" 
# Your UniPI server information
unipi_aconfig_devress = str ("127.0.0.1")

# Some variables and other config items that we might need
interval = 29 #realtime data sampling interval (for ais that report every second) to reduce updates to bus and rest API 

# logging information to log file on system
logging.basicConfig(format='%(asctime)s:%(levelname)s:%(message)s',filename='/var/log/mqtt_publish2.log',level=logging.DEBUG,datefmt='%Y-%m-%d %H:%M:%S')

def get_function_name():
    return traceback.extract_stack(None, 2)[0][2]

client = mqtt.Client()
client.connect(broker_aconfig_devress)

#set relative path for loading files
dirname = os.path.dirname(__file__)

####### Message Broker - Main function ########

def on_message(ws, message):
# Function to handle all messaging from Websocket Connection and do input validation
	# to print all json info to screen in realtime.
	#print(message)
	# To set the state of a device we check if the timeout has expired in a seperate function
	#dev_switch()
	#errdatetime = ('Timestamp: {:%Y-%m-%d %H:%M:%S}'.format(datetime.datetime.now()))
	#logging.debug('Received WebSocket data: %s ', message)
	# MEMO TO SELF - print("{}. {} appears {} times.".format(i, key, wordBank[key]))
	tijd = time.time()
	# Check if message is list or dict (Unipi sends most as list in dics, but UTP sensors as dict
	mesdata = json.loads(message)
	if type(mesdata) is dict:
		message_sort(mesdata)
		#print(message)
		logging.debug('DICT message without converting (will be processed): %s', message)
	else:
		for message_dev in mesdata:	# Check if there are updates over websocket and run functions to see if we need to update anything 
			if type(message_dev) is dict:
				message_sort(message_dev)
			else:
				logging.debug('Ignoring received data, it is not a dict: %s', device)
	# Check if we need to switch off something. This is handled here since this function triggers every second (analoge input update freq.) NEEDS SEP FUNCTION		
	for config_dev in devices_config:	
		try:
			dev_delay_check = 'device_delay' in message
		except:
			logging.debug('No device delay. Function "{}". Looks like input is not valid dict / json. Message data was "{}".'.format(get_function_name(),message))
		if dev_delay_check == True and (config_dev['device_delay'] > 0): #Only switch devices off that have a delay > 0. Devices with no delay or delay '0' do not need to turned off or are turned off bij a new status (like door sensor)
			if (config_dev['unipi_value'] == 1 and tijd >= (config_dev['unipi_prev_value_timstamp'] + config_dev['device_delay'])):
				config_dev['unipi_value'] = 0
				dev_switch_off(config_dev['state_topic']) #device uit zetten


def message_sort(message_dev):
# Function to sort to different functions for processing based on device type (dev)
	#if message_dev['dev'] == "input":
	#	dev_di_local(message_dev)
	if message_dev['dev'] == "input":
		dev_di(message_dev)
	elif message_dev['dev'] == "ai":
		dev_ai(message_dev)
	elif message_dev['dev'] == "temp": 
		dev_temp(message_dev)
	elif message_dev['dev'] == "wd": #Watchdog notices, ignoring and only show in debug logging level (std off)
		logging.debug('UNIPI WatchDog Notice: %s', message_dev)
	else:
		#logging.warning('Message not an input, temp or AI device but a %s', device)
		logging.warning("Message not an input, temp or AI device but a {} .".format(message_dev))

###### Device input handlers ######

def dev_di(message_dev):
# Function to handle Digital Inputs 
	logging.debug('Running function : "{}"'.format(get_function_name()))
	tijd = time.time()
	in_list_cntr = 0
	for config_dev in devices_config:
		if config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == 'input':
			if ('device_delay' in config_dev):
				if config_dev['device_delay'] > 0: no_delay = 0
				else: no_delay = 1
			in_list_cntr = 1
			if (message_dev['value'] == 1 and config_dev['unipi_value'] == 0):
				config_dev['unipi_value'] = message_dev['value']
				config_dev['unipi_prev_value_timstamp'] = tijd
				if(config_dev['device_normal'] == 'no'): dev_switch_on(config_dev['state_topic']) # check if device is normal status is OPEN or CLOSED loop to turn ON
				if(config_dev['device_normal'] == 'nc' and no_delay == 1): dev_switch_off(config_dev['state_topic']) # Turn off devices that switch to their normal mode and have no delay configured! Delayed devices will be turned off somewhere else
			elif (message_dev['value'] == 0 and config_dev['unipi_value'] == 1):
				config_dev['unipi_value'] = message_dev['value']
				config_dev['unipi_prev_value_timstamp'] = tijd
				if(config_dev['device_normal'] == 'no' and no_delay == 1): dev_switch_off(config_dev['state_topic']) # Turn off devices that switch to their normal mode and have no delay configured! Delayed devices will be turned off somewhere else
				if(config_dev['device_normal'] == 'nc'): dev_switch_on(config_dev['state_topic']) # check if device is normal status is OPEN or CLOSED loop to turn on
			elif (message_dev['value'] == 1 and config_dev['unipi_value'] == 1 and config_dev['device_normal'] == 'no'):
				config_dev['unipi_prev_value_timstamp'] = tijd # Re-apply timestamp voor NO devices (not normal state)
			elif (message_dev['value'] == 0 and config_dev['unipi_value'] == 0 and config_dev['device_normal'] == 'nc'):
				config_dev['unipi_prev_value_timstamp'] = tijd # Re-apply timestamp voor NO devices (not normal state)
			else:
				logging.warning('Error in NO for INPUT, non updated status received.')
	if in_list_cntr == 0:
		logging.error('Device not found in devices_config file : %s ', message_dev) 
			
def dev_ai(message_dev):
# Function to handle Analoge Inputs, mainly focussed on LUX from analoge input now. using a sample rate to reduce rest calls to domotics
	for config_dev in devices_config:
		if config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == "ai":
			#print(round(message_dev['value'],3))
			config_dev['unipi_avg_cntr']
			if config_dev['unipi_avg_cntr'] <= interval:
				cntr=config_dev['unipi_avg_cntr']
				config_dev['unipi_prev_value'][cntr] = round(message_dev['value'],2)
				config_dev['unipi_avg_cntr'] += 1
			else:
				# write LUX to MQTT here.
				lux = str(round((mean(config_dev['unipi_prev_value'])*200),0))
				mqtt_set_lux(config_dev['state_topic'],lux)
				config_dev['unipi_avg_cntr'] = 0
				logging.debug('PING Received WebSocket data and collected 30 samples of lux data : %s', message_dev) #we're loosing websocket connection, debug

def dev_temp(message_dev):
# Function to handle Analoge Inputs, mainly focussed on LUX from analoge input now. using a sample rate to reduce rest calls to domotics
	for config_dev in devices_config:
		try:
			if config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == "temp" and message_dev['typ'] == "DS18B20":
				temperature = float(message_dev['value'])
				temperature = round(temperature,1)
				mqtt_set_temp(config_dev['state_topic'],temperature)
			elif config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == "temp" and message_dev['typ'] == "DS2438":
				temperature = float(message_dev['temp'])
				if 0 <= temperature <= 50:
					temperature = round(temperature,1)
					mqtt_set_temp(config_dev['state_topic'],temperature)
			elif config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == "humidity" and message_dev['typ'] == "DS2438":
				humidity = float(message_dev['humidity'])
				if 0 <= humidity <= 100:
					humidity = round(humidity,1)
					mqtt_set_humi(config_dev['state_topic'],humidity)
				else:
					logging.error('Message "{}" is out of range, humidity larger than 100.'.format(message_dev))
		except ValueError as e:
			logging.error('Message "{}" not a valid JSON - message not processed, error is "{}".'.format(message_dev,e))


###### Device Switch Commands #######

def dev_switch_on(mqtt_topic):
	# Set via MQTT
	mqtt_topic_online = (mqtt_topic + "/available")
	client.publish(mqtt_topic_online, payload='online', qos=0, retain=True)
	client.publish(mqtt_topic, payload='ON', qos=0, retain=True)
	logging.debug("Set ON for MQTT topic: {} .".format(mqtt_topic))
	
def dev_switch_off(mqtt_topic):
	# Set via MQTT
	mqtt_topic_online = (mqtt_topic + "/available")
	client.publish(mqtt_topic_online, payload='online', qos=0, retain=True)
	client.publish(mqtt_topic, payload='OFF', qos=0, retain=True)
	logging.debug("Set OFF for MQTT topic: {} .".format(mqtt_topic))
	
def mqtt_set_lux(mqtt_topic, lux):
	# MQTT only
	send_msg = {
        "lux": lux
	}
	client.publish(mqtt_topic, payload=json.dumps(send_msg), qos=0, retain=False)
	logging.debug("Set LUX: {} for MQTT topic: {} .".format(lux,mqtt_topic))

def mqtt_set_temp(mqtt_topic, temp):
	send_msg = {
        "temperature": temp
	}
	client.publish(mqtt_topic, payload=json.dumps(send_msg), qos=0, retain=False)
	logging.debug("Set temperature: {} for MQTT topic: {} .".format(temp,mqtt_topic))

def mqtt_set_humi(mqtt_topic, humi):
	send_msg = {
        "humidity": humi
	}
	client.publish(mqtt_topic, payload=json.dumps(send_msg), qos=0, retain=False)
	logging.debug("Set humidity: {} for MQTT topic: {} .".format(humi,mqtt_topic))

######## Infra and Script Functions #########

def on_error(ws, error):
	print(error)
	logging.error(error)

def on_close(ws):
	#errdatetime = ('Timestamp: {:%Y-%m-%d %H:%M:%S}'.format(datetime.datetime.now()))
	# MEMO TO SELF - print("{}. {} appears {} times.".format(i, key, wordBank[key]))
	# print("### Oh Shit, Session closed on {}  ###".format(errdatetime))
	logging.info('Websocket connection now closed')
	mqtt_offline()
	
def on_open(ws):
	def run(*args):
		pass
	#	 for i in range(10):
	#		 time.sleep(3)
	#		 ws.send("Hello %d" % i)
	#	 time.sleep(3)
	#	 ws.close()
	#	 print("thread terminating...")
	_thread.start_new_thread(run, ())
	logging.info('Websocket connection now open')
	mqtt_online()

def mqtt_online(): #function to bring MQTT devices online to broker
	client.connect(broker_aconfig_devress)
	for config_dev in devices_config:
		mqtt_topic_online = (config_dev['state_topic'] + "/available")
		client.publish(mqtt_topic_online, payload='online', qos=0, retain=True)
		logging.info('MQTT ONline command for %s ', mqtt_topic_online)
		
def mqtt_offline(): #function to bring MQTT devices offline to broker
	client.disconnect()
	for config_dev in devices_config:
		mqtt_topic_online = (config_dev['state_topic'] + "/available")
		client.publish(mqtt_topic_online, payload='offline', qos=0, retain=True)
		logging.info('MQTT OFFline command for %s ', mqtt_topic_online)		

if __name__ == "__main__":
	#pz = pythonz(domoticz_ip, userName, passWord)
	unipy = unipython('127.0.0.1', 'none', 'none')
	dirname = os.path.dirname(__file__)
	dev_des_file = os.path.join(dirname, 'device_config2.json')
	#devices_config = json.load(open('/home/matthijs/scripts/filename = os.path.join(dirname,'))
	devices_config = json.load(open(dev_des_file))
	# open websocket connection 
	websocket.enableTrace(True)
	ws = websocket.WebSocketApp("ws://" + unipi_aconfig_devress + "/ws",
							  on_message = on_message,
							  on_error = on_error,
							  on_close = on_close)
	ws.on_open = on_open
	while True:
		ws.run_forever()	
	
	


