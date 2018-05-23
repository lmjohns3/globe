#!/bin/bash

(
    cd /home/pi/globe
    sudo ./globe.py 1>/dev/null 2>/dev/null &
)
