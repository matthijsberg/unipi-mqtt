#!/usr/bin/python
#	Title: unipipython.py
#	Author: Matthijs van den Berg
#	Date: 2018 somewhere
#	Version 0.1 alfa NEEDS A LOT OF POLISHING
#	Information from https://evok.api-docs.io/1.0/rest

# VAN VOORBEELD SCRIPT BOVENSTAANDE SITE
# payload = "{}"
# conn.request("POST", "/rest/analogoutput/%7Bcircuit%7D?mode=Voltage", payload)


import urllib.request
import requests
import json
import datetime
import time
import datetime

# Remove in production - just for test and dev in https URI's
requests.packages.urllib3.disable_warnings()

def ErrorHandling(e):
	errdatetime = ('Timestamp: {:%Y-%m-%d %H:%M:%S}'.format(datetime.datetime.now()))
	print("### Oops, some weird error occurred on that we still need to report properly ###")
	# MEMO TO SELF - print("{}. {} appears {} times.".format(i, key, wordBank[key]))
	print("### Timestamp: {}  ###".format(errdatetime))
	print("### Error dump:")
	print(e)
	print("### --------------END-------------- ###")

class unipython(object):

	def __init__(self, host, username, password):
			self.base_url = 'http://%s:8080/rest/' % (host) 
			#self.api = requests.Session()
			#self.api.auth = (username, password)
			#self.api.headers.update({'Content-Type': 'application/json; charset=utf-8'})

	# Turn a device OFF
	def set_off(self, dev, circuit):
		url=(self.base_url + dev + "/" + circuit + "")
		payload = {"value" : 0} # voltage toe te passen 0-10 volt NOG LEVEL VAR MAKEN
		headers = {"source-system": "matthijs-unipi"}
		try:
			r = requests.post(url, data=payload, headers=headers)
		except Exception as e:
			return (e)
		else:
			return(r.status_code)

	# Turn a device ON
	# only works for DO / Relay devices?
	def set_on(self, dev, circuit):
		url=(self.base_url + dev + "/" + circuit + "/")
		payload = {"value" : 1} # voltage toe te passen  NOG LEVEL VAR MAKEN
		headers = {"source-system": "matthijs-unipi"}
		try:
			r = requests.post(url, data=payload, headers=headers)
		except Exception as e:
			return (e)
		else:
			return(r.status_code)

	# Set device level (http://your-ip-goes-here:8080/rest/analogoutput/{circuit}?mode=Voltage)
	def set_level(self, circuit, level):
		url=(self.base_url + "analogoutput/" + circuit + "?mode=Voltage")
		payload = {"value" : level} # voltage toe te passen 0-10 volt NOG LEVEL VAR MAKEN
		headers = {"source-system": "matthijs-unipi"}
		try:
			r = requests.post(url, data=payload, headers=headers)
		except Exception as e:
			ErrorHandling(e)
		else:
			r = requests.post(url, data=payload, headers=headers)
			return(r.status_code)
	
	# Get device information from Unipi and return to calling function. Json format.
	def get_circuit(self, dev, circuit):
		url=(self.base_url + dev + "/" + circuit + "/")
		headers = {"source-system": "matthijs-unipi"}
		r = requests.get(url, headers=headers)
		if(r.status_code == 200):
			return(r.json())
		else:
			return('ERROR: statuscode: ' + r.status_code)
		
	#moet ik dit niet in client mqtt script oplossen?
	def ring_bel(self, times, dev, circuit):
		ctr = 0
		while (ctr < times):
			url=(self.base_url + dev + "/" + circuit + "/")
			print(url)
			payload = {"value" : 1} # voltage toe te passen 0-10 volt NOG LEVEL VAR MAKEN
			headers = {"source-system": "matthijs-unipi"}
			try:
				result = requests.post(url, data=payload, headers=headers)
			except Exception as e:
				ErrorHandling(e)
			else:
				response = requests.post(url, data=payload, headers=headers)
			payload = {"value" : 0} 
			try:
				result = requests.post(url, data=payload, headers=headers)
			except Exception as e:
				ErrorHandling(e)
			else:
				response = requests.post(url, data=payload, headers=headers)
			time.sleep(0.2)
			ctr += 1
		print(response)
		return (response) #assuming that last responce is representative for all s-:
