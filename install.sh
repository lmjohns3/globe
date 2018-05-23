#!/bin/sh

set -x

cd /home/pi

virtualenv -p python3 venv

. venv/bin/activate

pip install -r globe/requirements.txt

echo 'Now put the following in /etc/rc.local:'
echo 'sudo /home/pi/globe/globe.sh'
