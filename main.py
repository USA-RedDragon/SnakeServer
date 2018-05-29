#!/usr/bin/python

finishedInit = False

from lcd1602 import LCD
import thread
import time

lcd = LCD()
def initThread():
    lcd.clear()
    lcd.message("Initializing Boa\nHome")
    lcd.begin(0, 2)
    lcd.setCursor(4, 1)
    currentCharacter = ".   "
    lcd.message(currentCharacter)
    time.sleep(0.5)
    while not finishedInit:
        if currentCharacter is ".   ":
            currentCharacter = "..  "
        elif currentCharacter is "..  ":
            currentCharacter = "... "
        elif currentCharacter is "... ":
            currentCharacter = "...."
        elif currentCharacter is "....":
            currentCharacter = ".   "
        lcd.setCursor(4, 1)
        lcd.message(currentCharacter)
        time.sleep(0.5)

thread.start_new_thread(initThread,())

import atexit
from datetime import datetime, timedelta
import DHT22
from flask import Flask
from flask import render_template
from flask import request
from flask import jsonify
import json
import numpy as np
import os.path
import pigpio
from pyfcm import FCMNotification
import sqlite3
import threading

app = Flask(__name__)
gpio = pigpio.pi()
if not gpio.connected:
    exit()
sensor = DHT22.sensor(gpio, 15)
db = sqlite3.connect("data.db", check_same_thread = False)
conn = db.cursor()
push_service = FCMNotification(api_key="DUMMY")
lastTemperature = 0
lastHumidity = 0
lock = threading.Lock()
screenLock = threading.Lock()
alertHumidityThreshold = 60
turnOnHumidityThreshold = 70
alertTemperatureThreshold = 85
alertTemperatureAboveThreshold = 96
turnOnTemperatureThreshold = 89
turnOffTemperatureThreshold = 92
lastHumidityTime = 0
lastTemperatureTime = 0

## Gracefully stop gpio
def exit_handler():
    with screenLock:
        lcd.clear()
        lcd.destroy()
    sensor.cancel()
    gpio.stop()

def getDhtData():
    global lastTemperature
    global lastHumidity
    with lock:
        sensor.trigger()
        time.sleep(0.2)
        if sensor.humidity() < 0 or sensor.temperature() < 0:
            return lastHumidity, lastTemperature
        lastHumidity = int(sensor.humidity())
        lastTemperature = int(sensor.temperature()*9.0/5.0+32.0)
        addDataToDB(lastHumidity, lastTemperature)
        return lastHumidity, lastTemperature
    return lastHumidity, lastTemperature

def runPump(seconds):
    print("Turning on pump for {} seconds".format(seconds))
    gpio.write(10, 1)
    time.sleep(seconds)
    gpio.write(10, 0)

def turnOnHeat():
    print("Turning on heat")
    gpio.write(11, 1)

def turnOffHeat():
    print("Turning off heat")
    gpio.write(11, 0)

## DB Utils
def addDataToDB(temperature, humidity):
    conn.execute("INSERT INTO data (timestamp, temperature, humidity) VALUES (?, ?, ?)", (int(time.mktime(datetime.now().timetuple())), temperature, humidity))
    db.commit()

def getDataFromDB(since, useJson=True):
    conn.execute("SELECT * FROM data WHERE timestamp > ?", (since,))
    dbData = np.array(conn.fetchall())
    if useJson:
        if dbData.size == 0:
            return json.dumps([]), json.dumps([]), json.dumps([])
        temperaturearray=json.dumps(dbData[:,2].tolist())
        humidityarray=json.dumps(dbData[:,1].tolist())
        timearray=json.dumps(dbData[:,0].tolist())
        return temperaturearray, humidityarray, timearray
    else:
        if dbData.size == 0:
            return [], [], []
        temperaturearray=dbData[:,2].tolist()
        humidityarray=dbData[:,1].tolist()
        timearray=dbData[:,0].tolist()
        return temperaturearray, humidityarray, timearray

## API Calls
@app.route("/")
def ui():
    lastHumidity, lastTemperature = getDhtData()
    dt = datetime.now()
    twelveHrsAgo = dt - timedelta(hours=12)
    temperaturearray, humidityarray, timearray = getDataFromDB(int(time.mktime(twelveHrsAgo.timetuple())))
    return render_template('index.html', temperature=lastTemperature, humidity=lastHumidity, temperaturearray=temperaturearray, humidityarray=humidityarray, timearray=timearray)

@app.route("/api/v1/temperature")
def temperature():
    throwaway, temperature = getDhtData()
    return str(temperature)

@app.route("/api/v1/humidity")
def humidity():
    humidity, throwaway = getDhtData()
    return str(humidity)

@app.route("/api/v1/settings/temperaturethreshold", methods=['GET', 'POST'])
def tempthreshold():
    global alertTemperatureThreshold
    global alertTemperatureAboveThreshold
    global turnOnTemperatureThreshold
    global turnOffTemperatureThreshold
    if request.method == 'POST':
        if request.values.get('alertTemperatureThreshold') is not None:
            alertTemperatureThreshold = int(request.values.get('alertTemperatureThreshold'))
        if request.values.get('alertTemperatureAboveThreshold') is not None:
            alertTemperatureAboveThreshold = int(request.values.get('alertTemperatureAboveThreshold'))
        if request.values.get('turnOnTemperatureThreshold') is not None:
            turnOnTemperatureThreshold = int(request.values.get('turnOnTemperatureThreshold'))
        if request.values.get('turnOffTemperatureThreshold') is not None:
            turnOffTemperatureThreshold = int(request.values.get('turnOffTemperatureThreshold'))
        saveConfig()
        return "Success"
    else:
        return jsonify(alertTemperatureThreshold=alertTemperatureThreshold, alertTemperatureAboveThreshold=alertTemperatureAboveThreshold, turnOnTemperatureThreshold=turnOnTemperatureThreshold, turnOffTemperatureThreshold=turnOffTemperatureThreshold)

@app.route("/api/v1/settings/humiditythreshold", methods=['GET', 'POST'])
def humiditythreshold():
    global alertHumidityThreshold
    global turnOnHumidityThreshold
    if request.method == 'POST':
        if request.values.get('alertHumidityThreshold') is not None:
            alertHumidityThreshold = int(request.values.get('alertHumidityThreshold'))
        if request.values.get('turnOnHumidityThreshold') is not None:
            turnOnHumidityThreshold = int(request.values.get('turnOnHumidityThreshold'))
        saveConfig()
        return "Success"
    else:
        return jsonify(alertHumidityThreshold=alertHumidityThreshold, turnOnHumidityThreshold=turnOnHumidityThreshold)

@app.route('/api/v1/database/<since>')
def sendDatabase(since):
    temperaturearray, humidityarray, timearray = getDataFromDB(int(since), False)
    return jsonify(temperature=temperaturearray, humidity=humidityarray, time=timearray)

def updateScreen(humidity, temperature, alert):
    with screenLock:
        lcd.clear()
        lcd.message("Temperature: {}\337F\nHumidity: {}%".format(temperature, humidity))

def alertThread():
    global alertRunning
    if alertRunning:
        return None
    else:
        alertRunning = True
    while lastTemperature >= alertTemperatureAboveThreshold or lastTemperature <= alertTemperatureThreshold or lastHumidity <= alertHumidityThreshold:
        with screenLock:
            lcd.setCursor(14, 1)
            lcd.message("!!")
        time.sleep(1)
        with screenLock:
            lcd.setCursor(14, 1)
            lcd.message("  ")
        time.sleep(1)
    alertRunning = False

## Use main to init and this as a work loop
def loop():
    # Reset sequence on the DHT22 pins
    gpio.write(15, 0)
    gpio.write(15, 1)
    gpio.write(15, 0)
    gpio.write(15, 0)
    gpio.write(15, 0)
    lastHumidity, lastTemperature = getDhtData()

    alert = lastTemperature >= alertTemperatureAboveThreshold or lastTemperature <= alertTemperatureThreshold or lastHumidity <= alertHumidityThreshold
    updateScreen(lastHumidity, lastTemperature, alert)
    if alert:
        thread.start_new_thread(alertThread,())

    if lastTemperature >= alertTemperatureAboveThreshold:
        if lastTemperature is not 0:
            sendAlert("Temperature is too high at {}".format(lastTemperature), "temperature")
        turnOffHeat()
    elif lastTemperature <= alertTemperatureThreshold:
        if lastTemperature is not 0:
            sendAlert("Temperature is too low at {}".format(lastTemperature), "temperature")
        turnOnHeat()
    elif lastTemperature <= turnOnTemperatureThreshold:
        turnOnHeat()
    elif lastTemperature >= turnOffTemperatureThreshold:
        turnOffHeat()

    if lastHumidity <= alertHumidityThreshold:
        if lastHumidity is not 0:
            sendAlert("Humidity too low at {}".format(lastHumidity), "humidity")
        runPump(6)
    elif lastHumidity <= turnOnHumidityThreshold:
        runPump(6)

def flaskThread():
    with screenLock:
        lcd.clear()
        lcd.message("Server Loaded")
    app.run(port=80, host='0.0.0.0')

def sendAlert(message, type):
    global lastHumidityTime
    global lastTemperatureTime
    if type is "humidity" and lastHumidityTime + 300000 < int(round(time.time() * 1000)):
        print("Sending humidity Alert")
        lastHumidityTime = int(round(time.time() * 1000))
        push_service.notify_topic_subscribers(topic_name="alerts", message_body=message)
    if type is "temperature" and lastTemperatureTime + 300000 < int(round(time.time() * 1000)):
        print("Sending temperature Alert")
        lastTemperatureTime = int(round(time.time() * 1000))
        push_service.notify_topic_subscribers(topic_name="alerts", message_body=message)

def saveConfig():
    with open('{}/config.json'.format(os.path.dirname(os.path.realpath(__file__))), 'w') as f:
        json.dump({'alertHumidityThreshold': alertHumidityThreshold, 'turnOnHumidityThreshold': turnOnHumidityThreshold, 'alertTemperatureThreshold': alertTemperatureThreshold, 'alertTemperatureAboveThreshold': alertTemperatureAboveThreshold, 'turnOnTemperatureThreshold': turnOnTemperatureThreshold, 'turnOffTemperatureThreshold': turnOffTemperatureThreshold}, f);

def readConfig():
    global alertHumidityThreshold
    global turnOnHumidityThreshold
    global alertTemperatureThreshold
    global alertTemperatureAboveThreshold
    global turnOnTemperatureThreshold
    global turnOffTemperatureThreshold
    with open('{}/config.json'.format(os.path.dirname(os.path.realpath(__file__))), 'r') as f:
        jsonConfig = json.load(f)
        alertHumidityThreshold = jsonConfig["alertHumidityThreshold"]
        turnOnHumidityThreshold = jsonConfig["turnOnHumidityThreshold"]
        alertTemperatureThreshold = jsonConfig["alertTemperatureThreshold"]
        alertTemperatureAboveThreshold = jsonConfig["alertTemperatureAboveThreshold"]
        turnOnTemperatureThreshold = jsonConfig["turnOnTemperatureThreshold"]
        turnOffTemperatureThreshold = jsonConfig["turnOffTemperatureThreshold"]

## Main
if __name__ == "__main__":
    global alertRunning
    if not os.path.exists('{}/config.json'.format(os.path.dirname(os.path.realpath(__file__)))):
        saveConfig()
    else:
        readConfig()
    alertRunning = False
    gpio.set_mode(15, pigpio.OUTPUT)
    gpio.set_mode(10, pigpio.OUTPUT)
    gpio.set_mode(11, pigpio.OUTPUT)
    conn.execute("CREATE TABLE IF NOT EXISTS data (timestamp int, temperature int, humidity int);")
    atexit.register(exit_handler)
    thread.start_new_thread(flaskThread,())
    finishedInit = True
    time.sleep(2)
    while True:
        loop()
