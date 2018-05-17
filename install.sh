#!/bin/sh

set -x

cd /home/pi

virtualenv -p python3 venv

. venv/bin/activate

pip install -r globe/requirements.txt

sudo cp globe/globe.sh /etc/init.d
