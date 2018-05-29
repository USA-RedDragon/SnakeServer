#!/bin/bash

! pgrep -x "pigpiod" > /dev/null && pigpiod
cd /home/pi/snake
python main.py
