#!/bin/sh
# Test-only commands; the caller creates and owns the temporary cwd.
set -eu
item=$1
printf '%s\n' "$$" > "$item.start"
count=0
while ! test -f "release-$item"; do
    count=$((count + 1))
    if test "$count" -gt 1000; then
        echo "gate timed out: $item" >&2
        exit 2
    fi
    sleep 0.02
done
if test "$item" = B && test -f fail-B; then
    echo failure-B >&2
    exit 1
fi
touch "$item.end"
echo "result-$item"
