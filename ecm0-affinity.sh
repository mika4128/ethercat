#!/bin/sh
# Pin the ecm0 (EtherCAT master) IRQ to isolated cpu2.
# cpu2 smp_affinity bit = 0x4. IRQ number resolved by name (stable across boots).
irq=$(awk '/ecm0/ {split($1, a, ":"); print a[1]}' /proc/interrupts)
[ -n "$irq" ] && echo 4 > /proc/irq/$irq/smp_affinity
