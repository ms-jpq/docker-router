#!/usr/bin/env bash

set -eu
set -o pipefail


export PATH="/usr/local/bin:$PATH"
exec /srv/run/nftables/nft.sh
