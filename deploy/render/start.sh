#!/bin/sh
# Starts the three parts in one container. Render gives the public port in $PORT.
set -e
APP_DIR="${APP_DIR:-/app}"
export MQTT_HOST=127.0.0.1 MQTT_PORT=1883
export DATABASE_URL="${DATABASE_URL:-sqlite:////tmp/hvac.db}"
export CHECKPOINT_DB="${CHECKPOINT_DB:-/tmp/checkpoints.sqlite}"

mosquitto -c "$APP_DIR/mosquitto.conf" -d
sleep 1

# simulator: restarted automatically if it ever stops
(cd "$APP_DIR/simulator" && while true; do
  python main.py --host 127.0.0.1 --start "${SIM_START:-09:00}" < /dev/null || true
  echo "simulator stopped, restarting in 3 s"; sleep 3
done) &

cd "$APP_DIR/backend"
exec uvicorn app.serve:app --host 0.0.0.0 --port "${PORT:-10000}" --proxy-headers --forwarded-allow-ips '*'
