#!/bin/sh
# Enable cpuset controller (runtime-only, cgroup2 fs) and clamp the mics
# cgroup (mics + rtapi_app + milltask) to housekeeping cpus 0-1.
# Must run after mics.service started (its cgroup must exist).
CG=/sys/fs/cgroup
echo +cpuset > $CG/cgroup.subtree_control 2>/dev/null
echo +cpuset > $CG/system.slice/cgroup.subtree_control 2>/dev/null
f=$CG/system.slice/mics.service/cpuset.cpus
[ -w "$f" ] && echo 0-1 > "$f"
