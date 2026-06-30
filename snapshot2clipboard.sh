ssh bebebeka@172.25.255.123 '
  cd /var/lib/homellm/forgemind/run-artifacts || exit 1

  latest=$(ls -ltr artifact_* 2>/dev/null | tail -n 1 | awk "{print \$NF}")

  if [ -z "$latest" ]; then
    echo "No artifact_* files found" >&2
    exit 1
  fi

  echo "latest: $latest" >&2
  cat "$latest"
' | xclip -i -selection clipboard
