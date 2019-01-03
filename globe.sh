#!/bin/bash

(
    cd /home/pi/globe
    sudo ./main.py 1>/dev/null 2>/dev/null &
)
