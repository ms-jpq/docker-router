#!/usr/bin/env bash

set -eu
set -o pipefail


exec s6-setuidgid router python3 -m router.cake --wan-if "$WAN_IF"