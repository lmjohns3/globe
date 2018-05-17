#!/bin/bash

### BEGIN INIT INFO
# Provides:          globe.sh
# Required-Start:    $syslog
# Required-Stop:     $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Start globe at boot time
# Description:       Enable service for RBG LED globe.
### END INIT INFO

set -x

PATH=/sbin:/bin:/usr/sbin:/usr/bin
                                                                                     
. /lib/lsb/init-functions
                  
DAEMON=/home/pi/globe/globe.py

test -x $DAEMON || exit 5

if [ -r /etc/default/globe ]; then
  . /etc/default/globe
fi

case $1 in
  start)
    log_daemon_msg "Starting globe server" "globe"
    cd /home/pi/globe
    . venv/bin/activate
    echo $PATH
    start-stop-daemon --start --quiet --oknodo --exec $DAEMON
    log_end_msg $?
    ;;
  stop)
    log_daemon_msg "Stopping globe server" "globe"
    start-stop-daemon --stop --quiet --oknodo --retry=TERM/30/KILL/5 --exec $DAEMON
    log_end_msg $?
    rm -f $PIDFILE
    ;;
  restart|force-reload)
    $0 stop && sleep 2 && $0 start
    ;;
  try-restart)
    if $0 status >/dev/null; then
      $0 restart
    else
      exit 0
    fi
    ;;
  reload)
    exit 3
    ;;
  status)
    status_of_proc $DAEMON "globe server"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|try-restart|force-reload|status}"
    exit 2
    ;;
esac
