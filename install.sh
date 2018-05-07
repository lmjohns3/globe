#!/bin/sh

set -x

cd /home/pi/globe

virtualenv -p python3 venv

. venv/bin/activate

pip install -r requirements.txt

sudo cp globe.sh /etc/init.d/globe.sh
