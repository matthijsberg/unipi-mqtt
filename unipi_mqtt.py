#!/usr/bin/python3

# Script to turn on / set level of UNIPI s based on MQTT messages that come in. 
# No fancy coding to see here, please move on (Build by a complete amateur ;-) )
# Matthijs van den Berg / https://github.com/matthijsberg/unipi-mqtt
# MIT License
# version 0.3

# resources used besides google;
# - http://jasonbrazeal.com/blog/how-to-build-a-simple-iot-system-with-python/
# - http://www.diegoacuna.me/how-to-run-a-script-as-a-service-in-raspberry-pi-raspbian-jessie/ TO run this as service
# - https://gist.github.com/beugley/e37dd3e36fd8654553df for stopable thread part, ### Class and functions to create threads that can be stopped, so that when a lights is still dimming (in a thread since it blocking) but motion is detected and lights need to turn on during dimming we kill the thread and start the new action. WARNING. If you dont need threading, dont use it. Its not fun ;-).

import paho.mqtt.client as mqtt
import sys
import json
import logging
import datetime
from unipipython import unipython
import os
import time
import threading
import websocket
import traceback
from collections import OrderedDict
from statistics import mean, median
import math
# from hanging_threads import start_monitoring

########################################################################################################################
# 											Variables used in the system					 					     ###
########################################################################################################################

# MQTT Connection Variables
mqtt_address = "192.168.1.126"
mqtt_subscr_topic = "homeassistant/#" #to what channel to listen for MQTT updates to switch stuff. I use a "send by" topic here as example.
mqtt_client_name = "UNIPI-MQTT"
mqtt_user = "none" #not implemented auth for mqtt yet
mqtt_pass = "none"
# Websocket Connection Variables
ws_server = "192.168.1.125"
ws_user = "none" #not implemented auth for ws yet
ws_pass = "none"
# Generic Variables
logging_path = "/var/log/unipi_mqtt.log"
interval = 29 #realtime data sampling interval (for ais that report every second) to reduce updates to bus and rest API, TODO to fix to more elegant solution.
dThreads = {} #keeping track of all threads running

########################################################################################################################
###                     Some housekeeping functions to handle threads, logging, etc.                                 ###
########################################################################################################################

class StoppableThread(threading.Thread): # Implements a thread that can be stopped.
	def __init__(self,  name, target, args=()):
		super(StoppableThread, self).__init__(name=name, target=target, args=args)
		self._status = 'running'
	def stop_me(self):
		if (self._status == 'running'):
			#logging.debug('{}: Changing thread "{}" status to "stopping".'.format(get_function_name(),dThreads[thread_id]))
			self._status = 'stopping'
	def running(self):
		self._status = 'running'
		#logging.debug('{}: Changing thread "{}" status to "running".'.format(get_function_name(),dThreads[thread_id]))
	def stopped(self):
		self._status = 'stopped'
		#logging.debug('{}: Changing thread "{}" status to "stopped".'.format(get_function_name(),dThreads[thread_id]))
	def is_running(self):
		return (self._status == 'running')
	def is_stopping(self):
		return (self._status == 'stopping')
	def is_stopped(self):
		return (self._status == 'stopped')

def StopThread(thread_id):
	# Stops a thread and removes its entry from the global dThreads dictionary.
	logging.warning('{}: STOPthread ID {} .'.format(get_function_name(),thread_id))
	global dThreads
	if thread_id in str(dThreads):
		logging.warning('{}: Thread {} found in thread list: {} , checking if running or started...'.format(get_function_name(),dThreads[thread_id],dThreads))			
		thread = dThreads[thread_id]
		if (thread.is_stopping()):
			logging.warning('{}: Thread {} IS found STOPPING in running threads: {} , waiting till stop complete for thread {}.'.format(get_function_name(),thread_id,dThreads,dThreads[thread_id]))
		if (thread.is_running()):
			logging.warning('{}: Thread {} IS found active in running threads: {} , proceeding to stop {}.'.format(get_function_name(),thread_id,dThreads,dThreads[thread_id]))
			logging.warning('{}: Stopping thread "{}"'.format(get_function_name(),thread_id))
			thread.stop_me()
			logging.warning('{}: thread.stop_me finished. Now running threads and status: {} .'.format(get_function_name(),dThreads))
			thread.join(10) #implemented a timeout of 10 since the join here is blocking and halts the complete script. this allows the main function to continue, but is an ERROR since a thread is not joining. Most likely an function that hangs (function needs to end before a join is succesfull)! 
			thread.stopped()
			logging.warning('{}: Stopped thread "{}"'.format(get_function_name(),thread_id))
			del dThreads[thread_id]
			logging.warning('{}: Remaining running threads are "{}".'.format(get_function_name(),dThreads))
		else:
			logging.warning('{}: Thread {} not running or started.'.format(get_function_name(),dThreads[thread_id]))
	else:
		logging.warning('{}: Thread {} not found in global thread var: {}.'.format(get_function_name(),thread_id,dThreads))


def get_function_name():
    return traceback.extract_stack(None, 2)[0][2]

########################################################################################################################
###   Functions to handle the incomming MQTT messages, filter, sort, and kick off the action functions to switch.    ###
########################################################################################################################

def on_mqtt_message(mqttc, userdata, msg):
	#print(msg.topic+" "+str(msg.payload))
	if "set" in msg.topic:
		mqtt_msg=str(msg.payload.decode("utf-8","ignore"))
		logging.debug('{}: Message "{}" on input.'.format(get_function_name(),mqtt_msg))
		mqtt_msg_history = mqtt_msg
		if mqtt_msg.startswith("{"):
			try:
				mqtt_msg_json = json.loads(mqtt_msg, object_pairs_hook=OrderedDict) #need the orderedDict here otherwise the order of the mQTT message is changed, that will bnreak the return message and than the device won't turn on in HASSIO
			except ValueError as e:
				logging.error('{}: Message "{}" not a valid JSON - message not processed, error is "{}".'.format(get_function_name(),mqtt_msg,e))
			else:
				logging.debug('{}: Message "{}" is a valid JSON, processing json in handle_json.'.format(get_function_name(),mqtt_msg_json))
				handle_json(msg.topic,mqtt_msg_json)
		else:
			logging.debug("{}: Message \"{}\" not JSON format, processing other format.".format(get_function_name(),mqtt_msg))
			handle_other(msg.topic,mqtt_msg)

# Main function to handle incomming MQTT messages, check content en start the correct function to handle the request. All time consuming, and thus blocking actions, are threaded. 
def handle_json(ms_topic,message):
	global dThreads
	try:
		# We NEED a dev in the message as this targets a circuit type (analog / digital inputs, etc.) on the UniPi
		dev_presence = 'dev' in message
		if dev_presence == True: dev_value = message['dev']
		# We also NEED a circuit in the message to be able to target a circuit on the UniPi
		circuit_presence = 'circuit' in message
		if circuit_presence == True: circuit_value = message['circuit']
		# state, what do we need to do
		state_presence = 'state' in message
		if state_presence == True: state_value = message['state']
		# Transition, optional. You can fade anolog outputs slowly. Transition is the amount of seconds you want to fade to take (seconds always applied to 0-100%, so 0-25% = 25% of seconds)
		transition_presence = 'transition' in message
		if transition_presence == True: transition_value = message['transition']
		# Brightness, if you switch lights with 0-10 volt we translate the input value (0-255) to 0-10 and consider this brightness
		brightness_presence = 'brightness' in message
		if brightness_presence == True: brightness_value = message['brightness']
		# Repeat, if present this will trigger an on - off action x amout of times. I use this to trigger a relay multiple times to let a bel ring x amount of times.
		repeat_presence = 'repeat' in message
		if repeat_presence == True: repeat_value = message['repeat']
		# Duration is used to switch a output on for x seconds. IN my case used to open electrical windows.
		duration_presence = 'duration' in message
		if duration_presence == True: duration_value = message['duration']
		# Effect, not activly used yet, for future reference.
		effect_presence = 'effect' in message
		if effect_presence == True: effect_value = message['effect']
		logging.debug('Device: {} - {}, Circuit: {} - {}, State: {} - {}, Transition: {} , Brightness: {} , Repeat: {} , Duration: {} , Effect: {} .'.format(dev_presence,dev_value,circuit_presence,circuit_value,state_presence,state_value,transition_presence,brightness_presence,repeat_presence,duration_presence,effect_presence))
	except:
		logging.error('{}: Unhandled exception. Looks like input is not valid dict / json. Message data is: "{}".'.format(get_function_name(),message))
	#id = circuit_value
	thread_id = dev_value + circuit_value
	if dev_presence and circuit_presence and state_presence: # these are the minimal required arguments for this function to work
		logging.debug('{}: Valid WebSocket input received, processing message "{}"'.format(get_function_name(),message))
		if transition_presence:
			if brightness_presence:
				logging.warning('{}: starting "transition" message handling for dev "{}" circuit "{}" to value "{}" in {} s. time.'.format(get_function_name(),circuit_value,state_value,brightness_value,transition_value))
				if(brightness_value > 255):	logging.error('{}: Brightness input is greater than 255, 255 is max value! Setting Brightness to 255.'.format(get_function_name())); brightness_value = 255
				StopThread(thread_id)
				dThreads[thread_id] = StoppableThread(name=thread_id, target=transition_brightness, args=(brightness_value,transition_value,dev_value,circuit_value,ms_topic,message))
				dThreads[thread_id].start()
				logging.warning('TEMP threads {}:'.format(dThreads))
				logging.warning('{}: started thread "{}" for "transition" of dev "{}" circuit "{}".'.format(get_function_name(),dThreads[thread_id],circuit_value,state_value))
			else:
				logging.error('{}: Processing "transition", but missing argument "brightness", aborting. Message data is "{}".'.format(get_function_name(),message))
		elif brightness_presence:
			logging.debug('{}: starting "brightness" message handling for dev "{}" circuit "{}" to value "{}" (not in thread).'.format(get_function_name(),circuit_value,state_value,brightness_value))
			if(brightness_value > 255):	logging.error('{}: Brightness input is greater than 255, 255 is max value! Setting Brightness to 255.'.format(get_function_name())); brightness_value = 255
			StopThread(thread_id)
			set_brightness(brightness_value,circuit_value,ms_topic,message) # not in thread as this is not blocking
		elif effect_presence:
			logging.error('{}: Processing "effect", but not yet implemented, aborting. Message data is "{}"'.format(get_function_name(),message))
		elif duration_presence:
			logging.debug('{}: starting "duration" message handling for dev "{}" circuit "{}" to value "{}" for {} sec.'.format(get_function_name(),circuit_value,state_value,state_value,duration_value))
			StopThread(thread_id)
			dThreads[thread_id] = StoppableThread(name=thread_id, target=set_duration, args=(dev_value,circuit_value,state_value,duration_value,ms_topic,message))
			dThreads[thread_id].start()
			logging.debug('{}: started thread "{}" for "duration" of dev "{}" circuit "{}".'.format(get_function_name(),dThreads[thread_id],circuit_value,state_value))
		elif repeat_presence:
			logging.debug('{}: starting "repeat" message handling for dev "{}" circuit "{}" for {} time'.format(get_function_name(),circuit_value,state_value,int(repeat_value)))
			StopThread(thread_id)
			dThreads[thread_id] = StoppableThread(name=thread_id, target=set_repeat, args=(dev_value,circuit_value,int(repeat_value),ms_topic,message))
			dThreads[thread_id].start()
			logging.debug('{}: started thread "{}" for "repeat" of dev "{}" circuit "{}".'.format(get_function_name(),dThreads[thread_id],circuit_value,state_value))
		elif (state_value == "on" or state_value == "off"):
			logging.debug('{}: starting "state value" message handling for dev "{}" circuit "{}" to value "{}" (not in thread).'.format(get_function_name(),circuit_value,state_value,state_value))
			StopThread(thread_id)
			set_state(dev_value,circuit_value,state_value,ms_topic,message) #not in thread, not blocking
		else:
			logging.error('{}: No valid actionable item found!')
	else:
		logging.error('{}: Not all required arguments found in received MQTT message "{}". Need "dev", "circuit" and "state" minimal.'.format(get_function_name(),message))

def handle_other(ms_topic,message): #TODO, initialy started to handle ON and OFF messages, but since we require dev and circuit this doesn't work. Maybe for future ref. and use config file?
	logging.warning('"{}": function not yet implemented! Received message "{}" here.'.format(get_function_name(),message))

########################################################################################################################
#       Functions to handle WebSockets (UniPi) inputs to filter, sort, and kick off the actions via MQTT Publish.      #
########################################################################################################################

def ws_sanity_check(message):
# Function to handle all messaging from Websocket Connection and do input validation
	# MEMO TO SELF - print("{}. {} appears {} times.".format(i, key, wordBank[key]))
	tijd = time.time()
	# Check if message is list or dict (Unipi sends most as list in dics, but modbus sensors as dict
	mesdata = json.loads(message)
	if type(mesdata) is dict:
		message_sort(mesdata)
		logging.debug('DICT message without converting (will be processed): {}'.format(message))
	else:
		for message_dev in mesdata:	# Check if there are updates over websocket and run functions to see if we need to update anything 
			if type(message_dev) is dict:
				message_sort(message_dev)
			else:
				logging.debug('Ignoring received data, it is not a dict: {}'.format(device))
	# Check if we need to switch off something. This is handled here since this function triggers every second (analoge input update freq.) NEEDS SEP FUNCTION		
	off_commands()

def message_sort(message_dev):
# Function to sort to different websocket messages for processing based on device type (dev)
	if message_dev['dev'] == "input":
		dev_di(message_dev)
	elif message_dev['dev'] == "ai":
		dev_ai(message_dev)
	elif message_dev['dev'] == "temp": #temp is being used for the modbus temp only sensors, multi sensors in modbus use dev: 1wdevice since latest evok version 
		dev_modbus(message_dev)
	elif message_dev['dev'] == "1wdevice": # modules I tested so far as indicator (U1WTVS, U1WTD) that also report humidity and light intensity.
		dev_modbus(message_dev)
	elif message_dev['dev'] == "relay": # not sure what this does yet, not worked with it much.
		dev_relay(message_dev)
	elif message_dev['dev'] == "wd": #Watchdog notices, ignoring and only show in debug logging level (std off)
		logging.debug('{}: UNIPI WatchDog Notice: {}'.format(get_function_name(),message_dev))
	elif message_dev['dev'] == "ao":
		logging.debug('{}: Received and AO message in web-socket input, most likely a result from a switch action that also triggers this. ignoring'.format(get_function_name(),message_dev))
	else:
		logging.warning('{}: Message has no "dev" type of "input", "ai", "relay" or string "DS". Received input is : {} .'.format(get_function_name(),message_dev))

def dev_di(message_dev):
# Function to handle Digital Inputs from WebSocket (UniPi)
	logging.debug('{}: SOF'.format(get_function_name()))
	tijd = time.time()
	in_list_cntr = 0
	for config_dev in devdes:
		if (config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == 'input'):						# To check if device switch is in config file and is an input
			handle_local_presence = 'handle_local' in config_dev 													# becomes True is "handle local" is found in cofig
			device_delay_presence = 'device_delay' in config_dev 													# becomes True is "device_delay" is found in cofig
			if (device_delay_presence == True):
				device_delay_local = config_dev['device_delay']
				if config_dev['device_delay'] == 0: device_delay_presence = False									# Disable device delay if delay is 0 (should not be like that but be deleted from config, but it happens)
			if (device_delay_presence == True):
			# Running devices with delay to reswitch (like pulse bsed motion sensors that pulse on presence ever 10 sec to on) Using no / nc and delay to switch
			# We should only see "ON" here! Off messages are handled in function off_commands 
				logging.debug('{}: Loop with delay with message: {}'.format(get_function_name(),message_dev))
				tijd = time.time()
				if tijd >= (config_dev['unipi_prev_value_timstamp'] + config_dev['device_delay']):
					if (message_dev['value'] == 1):
						if (config_dev['unipi_value'] == 1):
							logging.debug('{}: received status 1 is actual status: {}'.format(get_function_name(),message_dev)) #nothing to do, since there is not status change. First in condition to easy load ;-)
						elif(config_dev['device_normal'] == 'no'): 
							dev_switch_on(config_dev['state_topic']) 														# check if device is normal status is OPEN or CLOSED loop to turn ON / OFF
							if handle_local_presence == True: handle_local_switch_on_or_toggle(message_dev,config_dev)
							config_dev['unipi_value'] = message_dev['value']
							config_dev['unipi_prev_value_timstamp'] = tijd
						elif(config_dev['device_normal'] == 'nc' and device_delay_presence == False): #should never run!
							dev_switch_off(config_dev['state_topic']) 														# Turn off devices that switch to their normal mode and have no delay configured! Delayed devices will be turned off somewhere else
							if handle_local_presence == True: handle_local_switch_toggle(message_dev,config_dev)
							config_dev['unipi_value'] = message_dev['value']
							config_dev['unipi_prev_value_timstamp'] = tijd
						else:
							logging.error('{}: Unhandled Exception 1, config: {}, status: {}, normal_config: {}, {}, {}'.format(get_function_name(),config_dev['unipi_value'],message_dev['value'],config_dev['device_normal'],message_dev['circuit'],config_dev['state_topic']))
					elif (message_dev['value'] == 0):
						if (config_dev['unipi_value'] == 0):
							logging.debug('{}: received status 0 is actual status: {}'.format(get_function_name(),message_dev)) #nothing to do, since there is not status change. First in condition to easy load ;-)
						elif(config_dev['device_normal'] == 'no' and device_delay_presence == False): #should never run!
							dev_switch_off(config_dev['state_topic']) 														# Turn off devices that switch to their normal mode and have no delay configured! Delayed devices will be turned off somewhere else
							if handle_local_presence == True: handle_local_switch_toggle(message_dev,config_dev)
							config_dev['unipi_value'] = message_dev['value']
							config_dev['unipi_prev_value_timstamp'] = tijd
						elif(config_dev['device_normal'] == 'nc'): 
							dev_switch_on(config_dev['state_topic'])
							if handle_local_presence == True: handle_local_switch_on_or_toggle(message_dev,config_dev)
							config_dev['unipi_value'] = message_dev['value']
							config_dev['unipi_prev_value_timstamp'] = tijd
						else:
							logging.error('{}: Unhandled Exception 2, config: {}, status: {}, normal_config: {}, {}, {}'.format(get_function_name(),config_dev['unipi_value'],message_dev['value'],config_dev['device_normal'],message_dev['circuit'],config_dev['state_topic']))
					else:
						logging.error('{}: Device value not 0 or 1 as expected for Digital Input. Message is: {}'.format(get_function_name(),message_dev))	
				else:
					config_dev['unipi_prev_value_timstamp'] = tijd
					logging.info('{}: new input on DI circuit {} but within configured switch delay, only adjusting timestamp. Message is: {}'.format(get_function_name(),message_dev['circuit'],message_dev))
			else:
			# Running devices without delay, always switching on / of based on UniPi Digital Input
				logging.debug('{}: Loop without delay with message: {}'.format(get_function_name(),message_dev))
				if (message_dev['value'] == 1):
					if(config_dev['device_normal'] == 'no'): 															# check if device is normal status is OPEN or CLOSED loop to turn ON / OFF
						if handle_local_presence == True: handle_local_switch_on_or_toggle(message_dev,config_dev)
						else: dev_switch_on(config_dev['state_topic']) 													# sends MQTT command, removed as test since this is done in handle_local_switch_toggle too
					elif(config_dev['device_normal'] == 'nc' and device_delay_presence == False): 						# Turn off devices that switch to their normal mode and have no delay configured! Delayed devices will be turned off somewhere else
						if handle_local_presence == True: pass # OLD: handle_local_switch_toggle(message_dev,config_dev) # we do a pass since a pulse based switch sends a ON and OFF in 1 action, we only need 1 action to happen! 
						else: dev_switch_off(config_dev['state_topic']) 												# sends MQTT command, removed as test since this is done in handle_local_switch_toggle too
					else:
						logging.debug('{}: ERROR 1, config: {}, normal_config: {}, {}, {}'.format(get_function_name(),message_dev['value'],config_dev['device_normal'],message_dev['circuit'],config_dev['state_topic']))
				elif (message_dev['value'] == 0):
					if(config_dev['device_normal'] == 'no' and device_delay_presence == False): 
						if handle_local_presence == True: pass #- OLD:handle_local_switch_toggle(message_dev,config_dev)
						else: dev_switch_off(config_dev['state_topic']) 												# Turn off devices that switch to their normal mode and have no delay configured! Delayed devices will be turned off somewhere else
					elif(config_dev['device_normal'] == 'nc'): 
						if handle_local_presence == True: handle_local_switch_on_or_toggle(message_dev,config_dev)
						else: dev_switch_on(config_dev['state_topic'])
					else:
						logging.debug('{}: ERROR 2, config: {}, normal_config: {}, {}, {}'.format(get_function_name(),message_dev['value'],config_dev['device_normal'],message_dev['circuit'],config_dev['state_topic']))
				else:
					logging.error('{}: Device value not 0 or 1 as expected for Digital Input. Message is: {}'.format(get_function_name(),message_dev))

def dev_ai(message_dev):
# Function to handle Analoge Inputs from WebSocket (UniPi), mainly focussed on LUX from analoge input now. using a sample rate to reduce rest calls to domotics
	for config_dev in devdes:
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
				logging.debug('PING Received WebSocket data and collected 30 samples of lux data : {}'.format(message_dev)) #we're loosing websocket connection, debug

def dev_relay(message_dev):
	pass #still need to figure out what to do with this. 

def dev_modbus(message_dev):
# Function to handle Analoge Inputs from WebSocket (UniPi), mainly focussed on LUX from analoge input now. using a sample rate to reduce MQTT massages. TODO needs to be improved!
	for config_dev in devdes:
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
			elif config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == "light" and message_dev['typ'] == "DS2438":
				light = float(message_dev['vis'])
				if light < 0:
					light = 0 # sometimes I see negative values that would make no sense, make that a 0
				if 0 <= light <= 0.25:
					# try to match this with LUX from other sensors, 0 to 2000 LUX so need to calculate from 0 to 0.25 volt to match that. TODO is 2000 LUX = 0.25 or more?
					light = light*8000
					light = round(light,0)
					mqtt_set_lux(config_dev['state_topic'],light)
				else:
					logging.error('Message "{}" is out of range, light ("vis") larger than 0.25 (Volts).'.format(message_dev))
		except ValueError as e:
			logging.error('Message "{}" not a valid JSON - message not processed, error is "{}".'.format(message_dev,e))


### Functions to switch outputs on the UniPi
### Used for incomming messages from MQTT and switches UniPi outputs conform the message received

def set_repeat(dev,circuit,repeat,topic,message):
	logging.debug('   {}: SOF with message "{}".'.format(get_function_name(),message))
	global dThreads
	thread_id = dev + circuit
	thread = dThreads[thread_id]
	ctr = 0
	while repeat > ctr and thread.is_running(): 
		stat_code_on = (unipy.set_on(dev,circuit))
		time.sleep(0.001) # time for output on
		stat_code_off = (unipy.set_off(dev,circuit))
		if ctr == 0: #set MQTT responce on so icon turn ON while loop runs
			mqtt_ack(topic,message)
		ctr += 1
		time.sleep(0.25) #sleep between output, maybe put this in var one day.
	else:			
		if thread.is_stopping():
			logging.warning('   {}: Thread {} was given stop signal and stop before finish. Leaving the cleaning of thread information to "def StopThread". NOT sending final MQTT messages'.format(get_function_name(),thread_id))
			unipy.set_off(dev,circuit) # extra off since we need to make sure my bel is off, or it will burn out. :-(
		else:
			if (int(stat_code_off) == 200 or int(stat_code_on) == 200):
				# Need to disable switch in HASS with message like {"circuit": "2_01", "dev": "relay", "state": "off"} where org message is {"circuit": "2_01", "dev": "relay", "repeat": "2", "state": "pulse"}. 
				message.pop("repeat") #remove repeat from final mqtt ack with orderd dict action
				message.update({"state":"off"}) #replace state "pulse" with "off" with orderd dict action
				mqtt_ack(topic,message)
				logging.info('    {}: Successful ran function on dev {} circuit {} for {} times.'.format(get_function_name(),dev,circuit,repeat))
			else:
				logging.error('   {}: Error setting device {} circuit {} on UniPi, got error "{}" back when posting via rest.'.format(get_function_name(),dev,circuit,stat_code_off))
			logging.info('   {}: Successful finished thread {}, now deleting thread information from global thread var'.format(get_function_name(),thread_id))
			del dThreads[thread_id]
	logging.debug('   {}: EOF.'.format(get_function_name()))

# SET A DEVICE STATE, NOTE: json keys are put in order somewhere, and for the ack message to hassio to work it needs to be in the same order (for switches as template is not available, only on / off)
def set_state(dev,circuit,state,topic,message):
	logging.debug('   {}: SOF with message "{}".'.format(get_function_name(),message))
	if (dev == "analogoutput" and state == "on"):
		logging.error('   {}: We can not switch an analog output on since we don not maintain last value, not sure to witch value to set output. Send brightness along to fix this'.format(get_function_name()))
	elif (dev == "relay" or dev == "output" or (dev == "analogoutput" and state == "off")):
		if state == 'on':
			stat_code = (unipy.set_on(dev,circuit))
		elif state == 'off':
			stat_code = (unipy.set_off(dev,circuit))
		else:
			stat_code = '999'
		if int(stat_code) == 200:
			mqtt_ack(topic,message)
			logging.info('    {}: Successful ran function on device {} circuit {} to state {}.'.format(get_function_name(),dev,circuit,state))
		else:
			logging.error('   {}: Error setting device {} circuit {} on UniPi, got error "{}" back when posting via rest.'.format(get_function_name(),dev,circuit,stat_code.status_code))
	else:
		logging.error('   {}: Unhandled exception in function.'.format(get_function_name()))
	del dThreads[thread_id]
	logging.debug('   {}: EOF.'.format(get_function_name()))

def set_duration(dev,circuit,state,duration,topic,message): #Set to switch on for a certain amount of time, I use this to open a rooftop window so for example 30 = 30 seconds
	logging.debug('   {}: SOF with message "{}".'.format(get_function_name(),message))
	global dThreads
	thread_id = dev + circuit
	thread = dThreads[thread_id]
	counter = int(duration)
	if (dev == "analogoutput" and state == "on"):
		logging.error('   {}: We can not switch an analog output on since we don not maintain last value, not sure to witch value to set output. Send brightness along to fix this'.format(get_function_name()))
	elif (dev == "relay" or dev == "output" or (dev == "analogoutput" and state == "off")):
		logging.info('   {}: Setting {} device {} to state {} for {} seconds.'.format(get_function_name(),dev,circuit,state,time))
		if state == 'on': 
			rev_state = "off"
			stat_code = (unipy.set_on(dev,circuit))
		elif state == 'off': 
			rev_state = "on"
			stat_code = (unipy.set_off(dev,circuit))
		if int(stat_code) == 200: # sending return message straight away otherwise the swithc will only turn on after delay time
			mqtt_ack(topic,message)
			logging.info('    {}: Set {} for circuit "{}".'.format(get_function_name(),state,circuit))
		else:
			logging.error('   {}: error switching device {} on UniPi {}.'.format(get_function_name(),circuit,stat_code))
		while counter > 0 and thread.is_running():
			time.sleep(1)
			counter -= 1
		else: #handled when thread finishes by completion or external stop signal (StopThread function) #time.sleep(int(duration)) #old depriciated for stoppable thread
			if state == 'on': 
				stat_code = (unipy.set_off(dev,circuit))
				message.update({"state":"off"}) #need to change on to off in mqtt message
			elif state == 'off': 
				stat_code = (unipy.set_on(dev,circuit))
				message.update({"state":"on"}) #need to change on to off in mqtt message
			if int(stat_code) == 200: # sending return message straight away otherwise the swithc will only turn on after delay time
				mqtt_ack(topic,message)
				logging.info('    {}: Set {} for circuit "{}".'.format(get_function_name(),rev_state,circuit))
			else:
				logging.error('   {}: error switching device {} to {} on UniPi {}.'.format(get_function_name(),circuit,rev_state,stat_code))
			if thread.is_stopping():
				logging.warning('   {}: Thread {} was given stop signal and stop before finish. Leaving the cleaning of thread information to "def StopThread". NOT sending final MQTT messages'.format(get_function_name(),thread_id))
			else:
				logging.info('   {}: Successful Finished thread {}, now deleting thread information from global thread var'.format(get_function_name(),thread_id))
				del dThreads[thread_id]
	logging.debug('    {}: EOF.'.format(get_function_name()))

def set_brightness(desired_brightness,circuit,topic,message):
	logging.debug('   {}: Starting with message "{}".'.format(get_function_name(), message))
	brightness_volt=round(int(desired_brightness)/25.5,2)
	stat_code = (unipy.set_level(circuit, brightness_volt))
	if stat_code == 200:
		mqtt_ack(topic,message)
		logging.info('    {}: Set {} for circuit "{}".'.format(get_function_name(),state,circuit))
	else:
		logging.error("Error switching on device on UniPi: %s ", stat_code.status_code)
	logging.debug('   {}: EOF.'.format(get_function_name()))

def transition_brightness(desired_brightness,trans_time,dev,circuit,topic,message):
	logging.debug('   {}: Starting function with message "{}".'.format(get_function_name(), message))
	global dThreads
	thread_id = dev + circuit
	thread = dThreads[thread_id]
	logging.info('   {}:thread information from global thread var {}'.format(get_function_name(),dThreads))
	trans_step = round(float(trans_time)/100,3)								# determine time per step for 100 steps. Fix for 100 so dimming is always the same speed, independent of from and to levels
	current_level = unipy.get_circuit(dev,circuit)							# get current circuit level from unipi REST
	desired_level = round(float(desired_brightness) / 25.5,1)				# calc desired level to 1/100 in stead of 256 steps for 0-10 volts
	delta_level = (desired_level - current_level['value'])					# determine delta based on from and to levels
	number_steps = abs(round(delta_level*10,0))								# determine number of steps based on from and to level
	new_level = current_level['value']
	execution_error = 2														# start with debugging to based return message on
	id = circuit
	logging.debug('   {}: Running with Current Level: {} and Desired Level: {} resulting in a delta of {} and {} number of steps to get there'.format(get_function_name(),current_level['value'],desired_level,delta_level,number_steps))
	if (number_steps != 0):
		if (delta_level != number_steps):
			# we need to set a start level via MQTT here as otherwise the device won't show as on when stating transition. Do not include in loop, too slow. 
			step_increase = float(delta_level / number_steps)
			#logging.debug('TRANSITION DEBUG 2; number of steps: {} and tread.is_running: {}'.format(number_steps,thread_status))
			### Threaded part from here, using the stop_thread function to interrupt when needed. Thread.is_running makes sure we listen to external stop signals ###
			while int(number_steps) > 0 and thread.is_running(): 
				new_level = round(new_level + step_increase,1)
				stat_code = 1 #(unipy.set_level(circuit, new_level))
				ws.send('{"cmd":"set","dev":"' + dev + '","circuit":"' + circuit + '","value":' + str(new_level) + '}')
				#Test, send mqtt message to switch device on on every change (maybe throttle in future/). If we don't HA will still thinks it's off while the loop turned it on. With long times this can mess up automations
				temp_level = math.ceil(new_level * 25.5)
				message.update({"brightness":temp_level}) #replace requested level with actual level in orderd dict action
				mqtt_ack(topic,message)
				number_steps -= 1
				if number_steps > 0:
					time.sleep(trans_step)
				elif number_steps == 0:
					logging.info('   {}: Done setting brightness via WebSocket.'.format(get_function_name()))
					#NEXT CODE IS TO CHECK IS COMMAND WAS SUCCESFULL 
					time.sleep(1.5) # need a sleep here since getting actual value back is slow sometimes, it takes about a second to get the final value.
					actual_level = unipy.get_circuit(dev,circuit)
					logging.info('   {}: Got actual level of "{}" back from function unipy.get_circuit.'.format(get_function_name(),actual_level))
					if (round(actual_level['value'],1) != desired_level):
						execution_error == 1 # TOT Need to changed this to 0 so i always send back actual status of lamp via MQTT (had issue that mqtt was not updating while lamp was on)
						logging.error("   {}: Return value \"{}\" not matching requested value \"{}\". Unipi might not be responding or in error. Retuning mqtt message with actual level, not requested".format(get_function_name(),round(actual_level['value'],1),desired_level))
						temp_level = math.ceil(actual_level['value'] * 25.5)
						message.update({"brightness":temp_level}) #replace requested level with actual level in orderd dict action
						mqtt_ack(topic,message)
					else:
						execution_error == 0
						logging.info('   {}: Return value "{}" IS matching requested value "{}". Proceeding in compiling the MQTT message to ack that.'.format(get_function_name(),round(actual_level['value'],1),desired_level))
					if execution_error != 1:
						# COMPILE THE MQTT ACK MESSAGE TO HASSIO
						mqtt_ack(topic,message)					
						logging.info('    {}: Finished Set brightness for dev "{}" circuit "{}" to "{}" in "{}" seconds.'.format(get_function_name(),dev,circuit,desired_brightness,trans_time))	
				else:
					logging.error('   {}: Unhandled Condition'.format(get_function_name()))
			else: #handled when thread finishes by completion or external stop signal (StopThread function)
				if thread.is_stopping():
					logging.info('   {}: Thread {} was given stop signal and stop before finish. Leaving the cleaning of thread information to "def StopThread". NOT sending final MQTT messages'.format(get_function_name(),thread_id))
				else:
					logging.warning('   {}: Successful Finished thread {}, now deleting thread information from global thread var'.format(get_function_name(),thread_id))
					del dThreads[thread_id]
				logging.debug('   {}: EOF.'.format(get_function_name()))
		else:
			logging.error('    {}: delta_level != number_steps.'.format(get_function_name(),dev,circuit))
	else:
		logging.info('    {}: Actual UniPi status for device {} circuit {} is matching desired state, not changing anything.'.format(get_function_name(),dev,circuit))


### UniPi outputs Switch Commands
### Used to switch outputs on the UniPi device based on the websocket message received

def off_commands():
	# Function to handle delayed off for devices based on config file. use to switch motion sensors off (get a pulse update every 10 sec)
	#logging.debug('{}: Starting function.'.format(get_function_name()))
	tijd = time.time()
	for config_dev in devdes:
		if 'device_delay' in config_dev: #Only switch devices off that have a delay > 0. Devices with no delay or delay '0' do not need to turned off or are turned off bij a new status (like door sensor)
			if config_dev['device_delay'] > 0 and tijd >= (config_dev['unipi_prev_value_timstamp'] + config_dev['device_delay']):
				#dev_switch_off(config_dev['state_topic']) #device uit zetten
				#if config_dev['unipi_value'] == 1 and config_dev['device_normal'] == 'no':
				if config_dev['unipi_value'] == 1 and config_dev['device_normal'] == 'no':
					dev_switch_off(config_dev['state_topic']) #device uit zetten
					config_dev['unipi_value'] = 0 # Set var in config file to off
					logging.info('{}: Triggered delayed OFF after {} sec for "no" device "{}" for MQTT topic: "{}" .'.format(get_function_name(),config_dev['device_delay'],config_dev['description'],config_dev['state_topic']))
				elif config_dev['unipi_value'] == 0 and config_dev['device_normal'] == 'nc':
					dev_switch_off(config_dev['state_topic']) #device uit zetten
					config_dev['unipi_value'] = 1 # Set var in config file to on
					logging.info('{}: Triggered delayed OFF after {} sec for "nc" device "{}" for MQTT topic: "{}" .'.format(get_function_name(),config_dev['device_delay'],config_dev['description'],config_dev['state_topic']))
				#else:
				#	logging.debug('{}: unhandled exception in device switch off'.format(get_function_name()))
	logging.debug('   {}: EOF.'.format(get_function_name()))

def dev_switch_on(mqtt_topic):
	# Set via MQTT
	#mqtt_topic_online = (mqtt_topic + "/available")
	#mqttc.publish(mqtt_topic_online, payload='online', qos=1, retain=True)
	mqttc.publish(mqtt_topic, payload='ON', qos=1, retain=True)
	logging.info('{}: Set ON for MQTT topic: "{}".'.format(get_function_name(),mqtt_topic))
	
def dev_switch_off(mqtt_topic):
	# Set via MQTT
	mqttc.publish(mqtt_topic, payload='OFF', qos=1, retain=True)
	logging.info('{}: Set OFF for MQTT topic: "{}".'.format(get_function_name(),mqtt_topic))
	
def mqtt_set_lux(mqtt_topic, lux):
	# MQTT only
	send_msg = {
        "lux": lux
	}
	mqttc.publish(mqtt_topic, payload=json.dumps(send_msg), qos=1, retain=False)
	logging.info('{}: Set LUX: {} for MQTT topic: "{}" .'.format(get_function_name(),lux,mqtt_topic))

def mqtt_set_temp(mqtt_topic, temp):
	send_msg = {
        "temperature": temp
	}
	mqttc.publish(mqtt_topic, payload=json.dumps(send_msg), qos=1, retain=False)
	logging.info('{}: Set temperature: {} C for MQTT topic: "{}" .'.format(get_function_name(),temp,mqtt_topic))

def mqtt_set_humi(mqtt_topic, humi):
	send_msg = {
        "humidity": humi
	}
	mqttc.publish(mqtt_topic, payload=json.dumps(send_msg), qos=1, retain=False)
	logging.info('{}: Set humidity: {} for MQTT topic: "{}" .'.format(get_function_name(),humi,mqtt_topic))

def mqtt_topic_ack(mqtt_topic, mqtt_message):
	mqttc.publish(mqtt_topic, payload=mqtt_message, qos=1, retain=False)
	logging.info('{}: Send MQTT message: "{}" for MQTT topic: "{}" .'.format(get_function_name(),mqtt_message,mqtt_topic))

def mqtt_topic_set(mqtt_topic, mqtt_message):
	mqtt_topic = mqtt_topic+"/set"
	mqttc.publish(mqtt_topic, payload=mqtt_message, qos=1, retain=True) #changed retain to true as HASS does a retain true for most messages. Meaning actual state is not maintained to last resort.
	logging.info('{}: Send MQTT message: "{}" for MQTT topic: "{}" .'.format(get_function_name(),mqtt_message,mqtt_topic))

### Handle Local Switch Commands
### Used to switch local outputs based on the websock input with some basic logic so some stuff still works when we do not have a working MQTT / Home Assistant

def handle_local_switch_on_or_toggle(message_dev,config_dev):
	logging.debug('{}: Handle Local ON for message: {} and handle_local_config {}.'.format(get_function_name(),message_dev,config_dev["handle_local"]))
	if config_dev["handle_local"]["type"] == 'bel':
		unipy.ring_bel(config_dev["handle_local"]["rings"],"relay",config_dev["handle_local"]["output_circuit"])
		logging.info('{}: Handle Local is ringing the bel {} times'.format(get_function_name(),config_dev["handle_local"]["rings"]))
	else:
		handle_local_switch_toggle(message_dev,config_dev)

def handle_local_switch_toggle(message_dev,config_dev):
	logging.debug('{}: Starting function with message "{}"'.format(get_function_name(),message_dev))
	if config_dev["handle_local"]["type"] == 'dimmer':
		logging.debug('{}: Dimmer Toggle Running.'.format(get_function_name()))
		status,success=(unipy.toggle_dimmer("analogoutput",config_dev["handle_local"]["output_circuit"],10))
		# unipy.toggle_dimmer('analogoutput', '2_03', 7)
		if success == 200: # I know, mixing up status and succes here from the unipython class... some day ill fix it. 
			if status == 0:
				mqtt_message = '{"state": "off", "circuit": "' + config_dev["handle_local"]["output_circuit"] + '", "dev": "analogoutput"}'
				#mqtt_topic_ack(config_dev["state_topic"], mqtt_message)
				mqtt_topic_set(config_dev["state_topic"], mqtt_message) #(we send a set too, to maks sure we stop threads in mqtt_client)
				logging.info('{}: Handle Local toggled analogoutput {} to OFF'.format(get_function_name(),config_dev["handle_local"]["output_circuit"]))
			elif status == 1:
				mqtt_message = '{"state": "on", "circuit": "' + config_dev["handle_local"]["output_circuit"] + '", "dev": "analogoutput", "brightness": 255}'
				#mqtt_topic_ack(config_dev["state_topic"], mqtt_message)
				mqtt_topic_set(config_dev["state_topic"], mqtt_message) #(we send a set too, to maks sure we stop threads in mqtt_client)
				logging.info('{}: Handle Local toggled analogoutput {} to ON'.format(get_function_name(),config_dev["handle_local"]["output_circuit"]))
			elif (status == 666 or status == 667):
				logging.error('{}: Received error from rest call with code "{}" on analogoutput {}.'.format(get_function_name(),status,config_dev["handle_local"]["output_circuit"]))
			else:
				logging.error('{}: "status" not 0,1,666 or 667 while running "dimmer loop"."'.format(get_function_name()))
		else:
			logging.error('{}: Tried to toggle analogoutput {} but failed with http return code "{}" .'.format(get_function_name(),config_dev["handle_local"]["output_circuit"],success))
	elif config_dev["handle_local"]["type"] == 'switch':
		logging.debug('Switch Toggle Running function : "{}"'.format(get_function_name()))
		status,success=(unipy.toggle_switch("output",config_dev["handle_local"]["output_circuit"]))
		if success == 200:
			if status == 0:
				mqtt_message = 'OFF'
				mqtt_topic_set(config_dev["state_topic"], mqtt_message) #(we send a set too, to maks sure we stop threads in mqtt_client)
				logging.info('{}: Handle Local toggled output {} to OFF'.format(get_function_name(),config_dev["handle_local"]["output_circuit"]))
			elif status == 1:
				mqtt_message = 'ON'
				mqtt_topic_set(config_dev["state_topic"], mqtt_message) #(we send a set too, to maks sure we stop threads in mqtt_client)
				logging.info('{}: Handle Local toggled output {} to ON'.format(get_function_name(),config_dev["handle_local"]["output_circuit"]))
			elif (status == 666 or status == 667):
				logging.error('{}: Received error from rest call with code "{}" on output {}.'.format(get_function_name(),status,config_dev["handle_local"]["output_circuit"]))
			else:
				logging.error('{}: "status" not found while running "switch loop"'.format(get_function_name()))
		else:
			logging.error("{}: Tried to toggle device  {} but failed with http return code '{}' .".format(get_function_name(),config_dev["handle_local"]["output_circuit"],success))
	else:
		logging.error('{}: Unhandled exception in function config type: {}'.format(get_function_name(),config_dev["handle_local"]["type"]))
	logging.debug('{}: EOF.'.format(get_function_name()))


### MQTT FUNCTIONS ###

def mqtt_ack(topic,message):
	#Function to adjust MQTT message / topic to return to sender.
	logging.debug('         {}: Starting function on topic "{}" with message "{}".'.format(get_function_name(),topic,message))
	if topic.endswith('/set'):
		topic = topic[:-4]
		logging.debug('         {}: Removed "set" from state topic, is now "{}" .'.format(get_function_name(),topic))
	if topic.endswith('/brightness'):
		topic = topic[:-11]
		logging.debug('         {}: Removed "/brightness" from state topic, is now "{}" .'.format(get_function_name(),topic))
	# Adjusting Message to be returned
	if 'mqtt_reply_message' in message:
		#this is currently unused, not a clue why i build it once... 
		logging.debug('         {}:Found "mqtt_reply_message" key in message "{}", changing reply message.'.format(get_function_name(),message))
		for key,value in message.items():
			if key=='mqtt_reply_message':
				message = value
				logging.debug('         {}:Message set to: "{}".'.format(get_function_name(),message))
	else:
		logging.debug('         {}:UNchanged return message, remains "{}" .'.format(get_function_name(),message))
	#returnmessage = message
	return_message = json.dumps(message) # we need this due to the fact that some MQTT message need a retun value of ON or OFF instead of original message
	mqttc.publish(topic, return_message, qos=0, retain=True) # You need to confirm light status to leave it on in HASSIO
	logging.debug('         {}: Returned topic is "{}" and message is "{}".'.format(get_function_name(),return_message, topic))
	logging.debug('         {}: EOF.'.format(get_function_name()))

# The callback for when the client receives a CONNACK response from the server.
def on_mqtt_connect(mqttc, userdata, flags, rc):
	logging.info('{}: MQTT Connected with result code {}.'.format(get_function_name(),str(rc)))
	mqttc.subscribe(mqtt_subscr_topic) # Subscribing in on_connect() means that if we lose the connection and reconnect then subscriptions will be renewed.
	mqtt_online()

def mqtt_online(): #function to bring MQTT devices online to broker
	for dd in devdes:
		mqtt_topic_online = (dd['state_topic'] + "/available")
		mqttc.publish(mqtt_topic_online, payload='online', qos=2, retain=True)
		logging.info('{}: MQTT "online" command to topic "{}" send.'.format(get_function_name(),mqtt_topic_online))

def on_mqtt_subscribe(mqttc, userdata, mid, granted_qos):
	logging.info('{}: Subscribed with details: mqttc: {}, userdata: {}, mid: {}, granted_qos: {}.'.format(get_function_name(),mqttc,userdata,mid,granted_qos))


def on_mqtt_disconnect(mqttc, userdata, rc):
	logging.warning('{}: MQTT DISConnected from MQTT broker with reason: {}.'.format(get_function_name(),str(rc))) # Return Code (rc)- Indication of disconnect reason. 0 is normal all other values indicate abnormal disconnection
	if str(rc) == 0:
		mqttc.unsubscribe(mqtt_subscr_topic)
		mqtt_offline()
		
def mqtt_offline(): #function to bring MQTT devices offline to broker
	for dd in devdes:
		#print("debug2")
		mqtt_topic_offline = (dd['state_topic'] + "/available")
		mqttc.publish(mqtt_topic_offline, payload='offline', qos=0, retain=True)
		logging.warning('{}: MQTT "offline" command to topic "{}" send.'.format(get_function_name(),mqtt_topic_offline))
	mqttc.disconnect()

def on_mqtt_unsubscribe(mqttc, userdata, mid, granted_qos):
	logging.info('{}: Unsubscribed with details: mqttc: {}, userdata: {}, mid: {}, granted_qos: {}.'.format(get_function_name(),mqttc,userdata,mid,granted_qos))

def on_mqtt_close(ws):
    logging.warning('{}: MQTT on_close function called.'.format(get_function_name()))

def on_mqtt_log(client, userdata, level, buf):
    logging.debug('{}: {}'.format(get_function_name(),buf))

### WEBSOCKET FUNCTIONS ###

def on_ws_open(ws):
	logging.info('{}: WebSockets connection is open in a separate thread!'.format(get_function_name()))
	firstrun()
	#TODO, Build a first run function to set ACTUAL states of UniPi inputs as MQTT message and in config file!
	
def on_ws_message(ws, message):
	ws_sanity_check(message) #This is starting the main message handling for UniPi originating messages
	#print(ws)

def on_ws_close(ws):
	logging.warning('{}: WebSockets connection is now Closed!'.format(get_function_name()))
	t_ws.join();
	
def on_ws_error(ws, errors):
	logging.error('{}: WebSocket Error; "{}"'.format(get_function_name(),errors))

### First Run Function to set initial state of Inputs
def firstrun():
	for config_dev in devdes:
		message = unipy.get_circuit(config_dev['dev'],config_dev['circuit'])
		try:
			message = json.dumps(message)
			logging.info('{}: Set status for dev: {}, circuit: {} to message and values: {}'.format(get_function_name(),config_dev['dev'],config_dev['circuit'],message))
			ws_sanity_check(message)
		except:
			logging.error('{}: Input error in first run, message received is ERROR {} on dev: {} and circuit: {}. Please ignore if dev humidity or light'.format(get_function_name(),message,config_dev['dev'],config_dev['circuit']))
			#Note first run will also find dev = humidity, etc. but cannot match that to a get to unipi and the creates arror 500, however the humidity is already handled on topic "temp" as humidity is not a deice class. Maybe oneday clean this up by changing dev types and something like sub_dev, but works like a charm this way too. 
		
### MAIN FUNCTION

if __name__ == "__main__":
	### setting some housekeeping functions and globel vars
	# DEVmonitoring_thread = start_monitoring(seconds_frozen=31, test_interval=15000)
	logging.basicConfig(format='%(asctime)s:%(levelname)s:%(message)s',filename=logging_path,level=logging.ERROR,datefmt='%Y-%m-%d %H:%M:%S') #DEBUG,INFO,WARNING,ERROR,CRITICAL
	urllib3_log = logging.getLogger("urllib3") #ignoring informational logging from called modules (rest calls in this case) https://stackoverflow.com/questions/24344045/how-can-i-completely-remove-any-logging-from-requests-module-in-python
	urllib3_log.setLevel(logging.CRITICAL) 
	unipy = unipython(ws_server, ws_user, ws_pass)

	### Loading the JSON settingsfile
	dirname = os.path.dirname(__file__)									#set relative path for loading files
	dev_des_file = os.path.join(dirname, 'unipi_mqtt_config.json')
	devdes = json.load(open(dev_des_file))
	
	### MQTT Connection.
	mqttc = mqtt.Client(mqtt_client_name) 								# If you want to use a specific client id, use this, otherwise a randon is autogenerated.
	mqttc.on_connect = on_mqtt_connect
	mqttc.on_log = on_mqtt_log # set client logging
	mqttc.on_disconnect = on_mqtt_disconnect
	mqttc.on_subscribe = on_mqtt_subscribe
	mqttc.on_unsubscribe = on_mqtt_unsubscribe
	mqttc.on_message = on_mqtt_message
	mqttc.connect(mqtt_address, 1883, 600,) #define MQTT server settings
	t_mqtt = threading.Thread(target=mqttc.loop_forever) #define a thread to run MQTT connection
	t_mqtt.start() #Start connection to MQTT in thread so non-blocking 
	
	### WebSocket Connection. Must be in main to be referenced from other functions like ws.send .
	ws_header = {'Authorization': 'Basic {0}','ClientID': 'UniPI'}
	ws_protocol = 'ws' #wss if secure
	ws_url = (ws_protocol + "://" + ws_server + "/ws")
	ws = websocket.WebSocketApp("ws://" + ws_server + "/ws",# header=ws_header,
							on_open = on_ws_open,
							on_message = on_ws_message,
							on_error = on_ws_error,
							on_close = on_ws_close)
							### WebSocket to connect to the broker, subscribe to messages (optional) and loop this forever in a thread so non-blocking				
	t_ws = threading.Thread(target=ws.run_forever)
	t_ws.start() #Start connection to WebSocket in thread so non-blocking	
