#!/usr/bin/env bash
ssh bebebeka@172.25.255.123 '
  latest=$(find /var/lib/homellm/forgemind/run-artifacts \
    -maxdepth 1 \
    -type f \
    -name "artifact_*" \
    -printf "%T@ %p\n" \
    | sort -nr \
    | head -n1 \
    | cut -d" " -f2-)

  echo "latest: $latest" >&2
  cat "$latest"
' | xclip -i -selection clipboard
