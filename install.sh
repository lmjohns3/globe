#!/bin/sh

sudo apt-get install libopenjp2-7 libtiff5 ntp

virtualenv -p python3 /home/pi/venv

. /home/pi/venv/bin/activate

pip install -r /home/pi/globe/requirements.txt

echo 'Now put the following in /etc/rc.local:'
echo '/home/pi/globe/globe.sh'
