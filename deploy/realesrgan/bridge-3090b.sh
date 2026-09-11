#!/bin/sh
set -eu

exec /usr/bin/ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=8 \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -p 2222 \
  gpucontrol@10.3.34.14 \
  /usr/bin/nc 127.0.0.1 9301
