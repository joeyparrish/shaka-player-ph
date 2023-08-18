#!/bin/bash

# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

set -x
set -e

cd $(dirname "$0")

time ./main.py -j -d 90 > ../ph-90.json
time ./main.py -j -d 30 > ../ph-30.json
time ./main.py -j -d 7 > ../ph-7.json
