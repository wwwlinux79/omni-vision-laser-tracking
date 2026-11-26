#!/bin/bash
# 连续前进 1.5 秒，停车后用相机定位 ArUco 标记。

set -e
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$script_dir/control.py" forward-locate "$@"
