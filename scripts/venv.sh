#!/bin/bash

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "No venv, creating..."
    python3 -m venv venv
    source venv/bin/activate
fi

exec $SHELL