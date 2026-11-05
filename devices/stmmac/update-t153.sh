#!/bin/bash
# Regenerate the 5.10 (T153 SDK linux-5.10-rt, sunxi BSP) stmmac
# -orig/-ethercat file pairs from the SDK kernel tree.
#
# T153 counterpart of update.sh (which tracks mainline kernel trees).
# The T153 SDK tree is not mainline: it carries sunxi patches and a
# different module split (platform stack, stock module names), so the
# file set and the EC hook (patches/0001-t153-ecdev.patch) differ.
#
# Usage:
#   update-t153.sh <SDK kernel source dir>
#   e.g. update-t153.sh ~/T153/T153_Linux_V1.0/kernel/linux-5.10-rt
#
# Result:
#   <file>-5.10-orig.c/h     pristine SDK copy
#   <file>-5.10-ethercat.c/h pristine + patches/0001-t153-ecdev.patch
#                            (stmmac_main.c, stmmac.h only) + local
#                            #include "X.h" rewritten to "X-5.10-ethercat.h"
#
# The SDK tree is left untouched. If a previous build left the EC patch
# applied in the tree (build scripts patch in place), this script
# normalizes the scratch copy back to pristine before generating -orig.

set -e

[ $# -ne 1 ] && { echo "usage: $0 <SDK kernel source dir (linux-5.10-rt)>"; exit 1; }
KDIR=$1
IGBDIR=drivers/net/ethernet/stmicro/stmmac
VER=5.10
PATCH=patches/0001-t153-ecdev.patch

cd "$(dirname "$0")"   # devices/stmmac
[ -f $PATCH ] || { echo "missing $PATCH"; exit 1; }

FILES="stmmac_main.c stmmac_ethtool.c stmmac_mdio.c ring_mode.c chain_mode.c \
dwmac_lib.c dwmac1000_core.c dwmac1000_dma.c dwmac100_core.c dwmac100_dma.c \
enh_desc.c norm_desc.c mmc_core.c stmmac_hwtstamp.c stmmac_ptp.c \
dwmac4_descs.c dwmac4_dma.c dwmac4_lib.c dwmac4_core.c dwmac5.c hwif.c \
stmmac_tc.c dwxgmac2_core.c dwxgmac2_dma.c dwxgmac2_descs.c \
stmmac_platform.c \
dwmac-sunxi.c dwmac-sun8i.c dwmac-generic.c \
common.h descs.h descs_com.h dwmac1000.h dwmac100.h dwmac4_descs.h \
dwmac4_dma.h dwmac4.h dwmac5.h dwmac_dma.h dwxgmac2.h dwxlgmac2.h \
hwif.h mmc.h stmmac.h stmmac_ptp.h stmmac_platform.h stmmac_pcs.h"

# scratch copy of the tree section (patch paths are a/drivers/... -> -p6)
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
mkdir -p "$SCRATCH/pt"
for f in $FILES; do
	[ -f "$KDIR/$IGBDIR/$f" ] || { echo "missing $KDIR/$IGBDIR/$f"; exit 1; }
	cp "$KDIR/$IGBDIR/$f" "$SCRATCH/pt/$f"
done

# normalize: SDK tree may be left patched by a build run
if grep -q ecdev "$SCRATCH/pt/stmmac.h"; then
	echo "SDK tree is patched; normalizing scratch copy to pristine"
	patch -R -p6 -d "$SCRATCH/pt" < $PATCH
fi
# patched variant (for the -ethercat files that carry the EC hook)
mkdir -p "$SCRATCH/pp"
cp "$SCRATCH/pt/"stmmac.h "$SCRATCH/pt/"stmmac_main.c "$SCRATCH/pp/"
patch -p6 -d "$SCRATCH/pp" < $PATCH

# local header names that must be rewritten in -ethercat includes
# (longest first; the pattern is anchored on ".h" so overlaps are safe)
HDRS="stmmac_platform|stmmac_ptp|stmmac_pcs|stmmac_ethtool|stmmac_mdio|stmmac_tc|stmmac_hwtstamp|stmmac_xdp|stmmac_main|stmmac|dwmac4_descs|dwmac4_dma|dwmac4_lib|dwmac1000|dwmac100|dwmac5|dwmac4|dwmac_dma|dwmac_lib|dwxgmac2|dwxlgmac2|chain_mode|ring_mode|enh_desc|norm_desc|dwmac-generic|dwmac-sun8i|dwmac-sunxi|hwif|mmc|common|descs_com|descs"

for f in $FILES; do
	base=${f%.*}; ext=${f#*.}
	o="$base-$VER-orig.$ext"
	e="$base-$VER-ethercat.$ext"
	case $f in
	stmmac.h|stmmac_main.c) SRCF="$SCRATCH/pp/$f" ;;
	*)                        SRCF="$SCRATCH/pt/$f" ;;
	esac
	cp "$SCRATCH/pt/$f" "$o"
	cp "$SRCF" "$e"
	sed -i -E "s/^([ \t]*)#include \"($HDRS)\.h\"/\1#include \"\2-$VER-ethercat.h\"/" "$e"
	chmod 644 "$o" "$e"
done

echo "generated $(( $(ls *-$VER-* | wc -l) )) files (expected $(( $(echo $FILES | wc -w) * 2 )))"
