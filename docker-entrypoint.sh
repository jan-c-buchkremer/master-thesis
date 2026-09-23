#!/bin/sh
# Downloads the SPECTER2 models into the models volume on first start, then runs the command.
set -e
if [ ! -f models/specter2_base_model/config.json ] || [ ! -f models/specter2_adapter/adapter_config.json ]; then
    echo "SPECTER2 models not found in /app/models, downloading..."
    python SaveModel.py
fi
exec "$@"
