#!/bin/bash

cd "$(dirname "$0")"

/usr/bin/env python3 run_pipeline.py

echo ""
echo "Pipeline finished."
echo "Press any key to close."
read -n 1
