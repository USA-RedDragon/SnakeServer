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
    time.sleep(0.3)
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
        time.sleep(0.3)

thread.start_new_thread(initThread,())

from flask import Flask
from json import dumps
import threading

app = Flask(__name__)

screenLock = threading.Lock()
databaseLock = threading.Lock()

config = {
    'alertHumidityThreshold': 60,
    'turnOnHumidityThreshold': 70,
    'alertTemperatureThreshold': 85,
    'alertTemperatureAboveThreshold': 96,
    'turnOnTemperatureThreshold': 89,
    'turnOffTemperatureThreshold': 92,
    'canAnonViewWebUI': True,
    'canAnonUsePublicAPI': False
}

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

    sensor.trigger()
    time.sleep(0.2)
    lastHumidity = int(sensor.humidity())
    lastTemperature = int(sensor.temperature()*9.0/5.0+32.0)
    addDataToDB(lastHumidity, lastTemperature)
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

def addUserToDB(username, password, admin=False):
    with databaseLock:
        conn.execute("INSERT INTO users (username, password, admin) VALUES (?, ?, ?)", (username, sha256(password), int(admin)))
        db.commit()

def updateUser(username, password, admin=False):
    with databaseLock:
        conn.execute("UPDATE users SET password=?, admin=? WHERE username = ?", (sha256(password), int(admin), username))
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

def getUserFromDB(username):
    with databaseLock:
        conn.execute("SELECT * FROM users WHERE username = ?", (username,))
        dbData = conn.fetchall()
        if len(dbData) == 0:
            return None, None, None
        return dbData[0][0], dbData[0][1], dbData[0][2]
    return None, None, None

def authUser(request):
    username = (request.authorization and request.authorization.username) or (request.headers.get('Username'))
    password = (request.authorization and sha256(request.authorization.password)) or (request.headers.get('Password'))
    expectedUsername, expectedPassword, isAdmin = getUserFromDB(username)
    if (request.authorization is not None or request.headers.get('Username') is not None) and expectedUsername is not None and expectedPassword.lower() == password.lower():
        return True;
    return False

def authHttpUserAsAdmin(auth):
    username = auth.username
    password = sha256(auth.password)
    expectedUsername, expectedPassword, isAdmin = getUserFromDB(username)
    if expectedPassword.lower() == password.lower():
        return True and isAdmin;
    return False

def authUserAsAdmin(headers):
    username = headers.get('Username')
    password = headers.get('Password')
    expectedUsername, expectedPassword, isAdmin = getUserFromDB(username)
    if expectedPassword.lower() == password.lower():
        return True and isAdmin;
    return False

def httpAuth():
    from flask import Response
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

## API Calls
@app.route("/")
def ui():
    from datetime import datetime, timedelta
    from flask import render_template
    from flask import abort
    from flask import request

    if config['canAnonViewWebUI'] or authUser(request):
        dt = datetime.now()
        twelveHrsAgo = dt - timedelta(hours=12)
        temperaturearray, humidityarray, timearray = getDataFromDB(int(time.mktime(twelveHrsAgo.timetuple())))
        return render_template('index.html', temperature=lastTemperature, humidity=lastHumidity, temperaturearray=temperaturearray, humidityarray=humidityarray, timearray=timearray)
    else:
        return httpAuth()

@app.route("/api/v1/temperature")
def temperature():
    from flask import request
    if config['canAnonUsePublicAPI'] or authUser(request):
        return str(lastTemperature)
    else:
        return httpAuth()

@app.route("/api/v1/humidity")
def humidity():
    from flask import request
    if config['canAnonUsePublicAPI'] or authUser(request):
        return str(lastHumidity)
    else:
        return httpAuth()

@app.route("/api/v1/settings/temperaturethreshold", methods=['GET', 'POST'])
def tempthreshold():
    from flask import jsonify, request

    if request.method == 'POST':
        if authUserAsAdmin(request.headers):
            if request.values.get('alertTemperatureThreshold') is not None:
                config['alertTemperatureThreshold'] = int(request.values.get('alertTemperatureThreshold'))
            if request.values.get('alertTemperatureAboveThreshold') is not None:
                config['alertTemperatureAboveThreshold'] = int(request.values.get('alertTemperatureAboveThreshold'))
            if request.values.get('turnOnTemperatureThreshold') is not None:
                config['turnOnTemperatureThreshold'] = int(request.values.get('turnOnTemperatureThreshold'))
            if request.values.get('turnOffTemperatureThreshold') is not None:
                config['turnOffTemperatureThreshold'] = int(request.values.get('turnOffTemperatureThreshold'))
            saveConfig()
            return "Success"
        else:
            return "Authentication Failed"
    else:
        if config['canAnonUsePublicAPI'] or authUser(request):
            return jsonify(alertTemperatureThreshold=config['alertTemperatureThreshold'], alertTemperatureAboveThreshold=config['alertTemperatureAboveThreshold'], turnOnTemperatureThreshold=config['turnOnTemperatureThreshold'], turnOffTemperatureThreshold=config['turnOffTemperatureThreshold'])
        else:
            return httpAuth()

@app.route("/api/v1/settings/humiditythreshold", methods=['GET', 'POST'])
def humiditythreshold():
    from flask import jsonify, request

    if request.method == 'POST':
        if authUserAsAdmin(request.headers):
            if request.values.get('alertHumidityThreshold') is not None:
                config['alertHumidityThreshold'] = int(request.values.get('alertHumidityThreshold'))
            if request.values.get('turnOnHumidityThreshold') is not None:
                config['turnOnHumidityThreshold'] = int(request.values.get('turnOnHumidityThreshold'))
            saveConfig()
            return "Success"
        else:
            return "Authentication failed, or user is not an administrator"
    else:
        if config['canAnonUsePublicAPI'] or authUser(request):
            return jsonify(alertHumidityThreshold=config['alertHumidityThreshold'], turnOnHumidityThreshold=config['turnOnHumidityThreshold'])
        else:
            return httpAuth()

@app.route('/api/v1/database/<since>')
def sendDatabase(since):
    from flask import request

    if config['canAnonUsePublicAPI'] or authUser(request):
        from flask import jsonify

        temperaturearray, humidityarray, timearray = getDataFromDB(int(since), False)
        return jsonify(temperature=temperaturearray, humidity=humidityarray, time=timearray)
    else:
        return httpAuth()

@app.route('/api/v1/adduser', methods=['GET', 'POST'])
def addUsr():
    from flask import request

    if (request.headers.get("Headless") and authUserAsAdmin(request.headers)) or (request.authorization and authHttpUserAsAdmin(request.authorization)):
        username = request.headers.get("NewUsername")
        if username is not None:
            password = request.headers.get("NewPassword")
            adminStr = request.headers.get("IsAdmin")
            admin = adminStr == "true"
            usr, psw, ta = getUserFromDB(username)
            if username == usr:
                return "Username already exists"
            else:
                addUserToDB(username, password, admin)
                return "Success"
        elif request.form:
            usr, psw, ta = getUserFromDB(username)
            if username == usr:
                return "Username already exists"
            else:
                admin = request.form.get("isAdmin") is not None
                username = request.form.get("newUsername")
                password = request.form.get("newPassword")
                if username is None or password is None:
                    return "Error Occured"
                else:
                    addUserToDB(username, password, admin)
                    return "User {} added".format(username)
        else:
            from flask import render_template, url_for
            return render_template('addUser.html', submit=url_for('addUsr'))
    else:
        return httpAuth()

@app.route('/api/v1/updateuser', methods=['GET', 'POST'])
def updateUsr():
    from flask import request

    if (request.headers.get("Headless") and authUserAsAdmin(request.headers)) or (request.authorization and authHttpUserAsAdmin(request.authorization)):
        username = request.headers.get("NewUsername")
        if username is not None:
            password = request.headers.get("NewPassword")
            adminStr = request.headers.get("IsAdmin")
            admin = adminStr == "true"
            usr, psw, ta = getUserFromDB(username)
            if usr == None:
                return "Username doesn't exist"
            else:
                updateUser(username, password, admin)
                print getUserFromDB('admin')
                return "Success"
        elif request.form:
            username = request.form.get("newUsername")
            usr, psw, ta = getUserFromDB(username)
            if usr == None:
                return "Username doesn't exist"
            else:
                admin = request.form.get("isAdmin") is not None
                password = request.form.get("newPassword")
                if username is None or password is None:
                    return "Error Occured"
                else:
                    updateUser(username, password, admin)
                    print getUserFromDB('admin')
                    return "User {} updated".format(username)
        else:
            from flask import render_template, url_for
            return render_template('updateUser.html', submit=url_for('updateUsr'))
    else:
        return httpAuth()

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
    while lastTemperature >= config['alertTemperatureAboveThreshold'] or lastTemperature <= config['alertTemperatureThreshold'] or lastHumidity <= config['alertHumidityThreshold']:
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
    time.sleep(2)
    lastHumidity, lastTemperature = getDhtData()

    alert = lastTemperature >= config['alertTemperatureAboveThreshold'] or lastTemperature <= config['alertTemperatureThreshold'] or lastHumidity <= config['alertHumidityThreshold']
    updateScreen(lastHumidity, lastTemperature, alert)
    if alert:
        thread.start_new_thread(alertThread,())

    if lastTemperature >= config['alertTemperatureAboveThreshold']:
        if lastTemperature is not 0:
            sendAlert("Temperature is too high at {}".format(lastTemperature), "temperature")
        turnOffHeat()
    elif lastTemperature <= config['alertTemperatureThreshold']:
        if lastTemperature is not 0:
            sendAlert("Temperature is too low at {}".format(lastTemperature), "temperature")
        turnOnHeat()
    elif lastTemperature <= config['turnOnTemperatureThreshold']:
        turnOnHeat()
    elif lastTemperature >= config['turnOffTemperatureThreshold']:
        turnOffHeat()

    if lastHumidity <= config['alertHumidityThreshold']:
        if lastHumidity is not 0:
            sendAlert("Humidity too low at {}".format(lastHumidity), "humidity")
        runPump(6)
    elif lastHumidity <= config['turnOnHumidityThreshold']:
        runPump(3)
    time.sleep(5)

def flaskThread():
    with screenLock:
        lcd.clear()
        lcd.message("Server Loaded")
    app.run(port=80, host='0.0.0.0')

def sendAlert(message, type):
    from pyfcm import FCMNotification

    global lastHumidityTime
    global lastTemperatureTime

    push_service = FCMNotification(api_key=process.env.FCM_KEY)
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
        dump(config, f)

def readConfig():
    from json import load
    import os.path

    global config

    with open('{}/config.json'.format(os.path.dirname(os.path.realpath(__file__))), 'r') as f:
        config = load(f)

def sha256(string):
    import hashlib
    return hashlib.sha256(string.encode('utf-8')).hexdigest()

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
    global finishedInit
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
    conn.execute("CREATE TABLE IF NOT EXISTS users (username text, password text, admin int);")
    conn.execute("CREATE TABLE IF NOT EXISTS data (timestamp int, temperature int, humidity int);")

    alertRunning = False
    finishedInit = True
    gpio.set_mode(10, pigpio.OUTPUT)
    gpio.set_mode(11, pigpio.OUTPUT)
    lastHumidity, lastTemperature = getDhtData()
    atexit.register(exit_handler)
    thread.start_new_thread(flaskThread,())
    time.sleep(2)
    while True:
        loop()
