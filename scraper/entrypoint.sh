#!/bin/bash
# Dump current env vars so cron jobs can read them.
# Cron does not inherit the container's environment by default.
env >> /etc/environment
cron -f
