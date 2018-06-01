#!/usr/bin/python

import faulthandler
faulthandler.enable()

from lcd1602 import LCD
import thread
import time

def initThread():
    global finishedInit
    global lcd

    finishedInit = False
    lcd = LCD()
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

from flask import Flask
from json import dumps
import threading

app = Flask(__name__)

sensorLock = threading.Lock()
screenLock = threading.Lock()
databaseLock = threading.Lock()

alertHumidityThreshold = 60
turnOnHumidityThreshold = 70
alertTemperatureThreshold = 85
alertTemperatureAboveThreshold = 96
turnOnTemperatureThreshold = 89
turnOffTemperatureThreshold = 92

lastHumidityTime = 0
lastTemperatureTime = 0
lastPumpTime = 0

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

    with sensorLock:
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
    global lastPumpTime
    if lastPumpTime + 12000000 < int(round(time.time() * 1000)):
        lastPumpTime = int(round(time.time() * 1000))
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
    from datetime import datetime
    with databaseLock:
        conn.execute("INSERT INTO data (timestamp, temperature, humidity) VALUES (?, ?, ?)", (int(time.mktime(datetime.now().timetuple())), temperature, humidity))
        db.commit()

def getDataFromDB(since, useJson=True):
    with databaseLock:
        conn.execute("SELECT * FROM data WHERE timestamp > ?", (since,))
        dbData = conn.fetchall()
        if useJson:
            if len(dbData) == 0:
                return dumps([]), dumps([]), dumps([])
            temperaturearray=dumps([i[2] for i in dbData])
            humidityarray=dumps([i[1] for i in dbData])
            timearray=dumps([i[0] for i in dbData])
            return temperaturearray, humidityarray, timearray
        else:
            if len(dbData) == 0:
                return [], [], []
            temperaturearray=[i[2] for i in dbData]
            humidityarray=[i[1] for i in dbData]
            timearray=[i[0] for i in dbData]
            return temperaturearray, humidityarray, timearray

    if useJson:
        return dumps([]), dumps([]), dumps([])
    else:
        return [], [], []

## API Calls
@app.route("/")
def ui():
    from datetime import datetime, timedelta
    from flask import render_template

    humidity, temperature = getDhtData()
    dt = datetime.now()
    twelveHrsAgo = dt - timedelta(hours=12)
    temperaturearray, humidityarray, timearray = getDataFromDB(int(time.mktime(twelveHrsAgo.timetuple())))
    return render_template('index.html', temperature=temperature, humidity=humidity, temperaturearray=temperaturearray, humidityarray=humidityarray, timearray=timearray)

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
    from flask import jsonify, request

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
    from flask import jsonify, request

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
    from flask import jsonify

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
        time.sleep(3)
        with screenLock:
            lcd.setCursor(14, 1)
            lcd.message("  ")
        time.sleep(3)
    alertRunning = False

## Use main to init and this as a work loop
def loop():
    time.sleep(5)
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
        runPump(3)
    time.sleep(5)

def flaskThread():
    global finishedInit
    app.run(port=80, host='0.0.0.0')
    finishedInit = True
    with screenLock:
        lcd.clear()
        lcd.message("Server Loaded")

def sendAlert(message, type):
    from pyfcm import FCMNotification

    global lastHumidityTime
    global lastTemperatureTime

    push_service = FCMNotification(api_key="DUMMY")
    if type is "humidity" and lastHumidityTime + 300000 < int(round(time.time() * 1000)):
        print("Sending humidity Alert")
        lastHumidityTime = int(round(time.time() * 1000))
        push_service.notify_topic_subscribers(topic_name="alerts", message_body=message)
    if type is "temperature" and lastTemperatureTime + 300000 < int(round(time.time() * 1000)):
        print("Sending temperature Alert")
        lastTemperatureTime = int(round(time.time() * 1000))
        push_service.notify_topic_subscribers(topic_name="alerts", message_body=message)

def saveConfig():
    from json import dump
    import os.path

    with open('{}/config.json'.format(os.path.dirname(os.path.realpath(__file__))), 'w') as f:
        dump({'alertHumidityThreshold': alertHumidityThreshold, 'turnOnHumidityThreshold': turnOnHumidityThreshold, 'alertTemperatureThreshold': alertTemperatureThreshold, 'alertTemperatureAboveThreshold': alertTemperatureAboveThreshold, 'turnOnTemperatureThreshold': turnOnTemperatureThreshold, 'turnOffTemperatureThreshold': turnOffTemperatureThreshold}, f);

def readConfig():
    from json import load
    import os.path

    global alertHumidityThreshold
    global turnOnHumidityThreshold
    global alertTemperatureThreshold
    global alertTemperatureAboveThreshold
    global turnOnTemperatureThreshold
    global turnOffTemperatureThreshold

    with open('{}/config.json'.format(os.path.dirname(os.path.realpath(__file__))), 'r') as f:
        jsonConfig = load(f)
        alertHumidityThreshold = jsonConfig["alertHumidityThreshold"]
        turnOnHumidityThreshold = jsonConfig["turnOnHumidityThreshold"]
        alertTemperatureThreshold = jsonConfig["alertTemperatureThreshold"]
        alertTemperatureAboveThreshold = jsonConfig["alertTemperatureAboveThreshold"]
        turnOnTemperatureThreshold = jsonConfig["turnOnTemperatureThreshold"]
        turnOffTemperatureThreshold = jsonConfig["turnOffTemperatureThreshold"]

## Main
if __name__ == "__main__":
    import atexit
    import DHT22
    import os.path
    import pigpio
    import sqlite3

    global alertRunning
    global db
    global conn
    global gpio
    global sensor
    global lastHumidity
    global lastTemperature

    lastHumidity = 0
    lastTemperature = 0

    gpio = pigpio.pi()
    if not gpio.connected:
        exit()
    sensor = DHT22.sensor(gpio, 15)
    if not os.path.exists('{}/config.json'.format(os.path.dirname(os.path.realpath(__file__)))):
        saveConfig()
    else:
        readConfig()

    db = sqlite3.connect('{}/data.db'.format(os.path.dirname(os.path.realpath(__file__))), check_same_thread = False)
    conn = db.cursor()
    conn.execute("CREATE TABLE IF NOT EXISTS data (timestamp int, temperature int, humidity int);")

    alertRunning = False
    gpio.set_mode(15, pigpio.OUTPUT)
    gpio.set_mode(10, pigpio.OUTPUT)
    gpio.set_mode(11, pigpio.OUTPUT)
    lastHumidity, lastTemperature = getDhtData()
    atexit.register(exit_handler)
    thread.start_new_thread(flaskThread,())
    time.sleep(2)
    while True:
        loop()
