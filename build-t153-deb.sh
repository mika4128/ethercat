#!/bin/bash
# build-t153-deb.sh — 全志 (sunxi) 预编译 armhf 安装包 (无 DKMS: 板无 kernel-headers,
# 模块交叉编好直接装 /lib/modules/<KVER>/extra/). KVER 从 .ko 的 vermagic 自动推导,
# 所以同一脚本能对**不同内核**的 OUT 打出对应 deb (见下方 KVER 段 + t153-guide.md §4).
# 普通平台 (Intel NIC 等) 用仓库自带 debian/ 的 DKMS 流程, 本脚本仅服务 sunxi 全志板.
#
# 前置: 已跑 build_branch.sh (交叉 configure + make install + kbuild), OUT 含
#       userspace/ + modules/ (7 个 .ko) + ec_wkc_test.
# usage: build-t153-deb.sh <checkout> <OUT>
# 可选: KVER=<ver> 环境变量覆盖自动推导 (默认从 ec_master.ko vermagic 取).
#
# 产物: deb_armhf/{ethercat-t153-modules,libethercat,libethercat-dev,ethercat-t153}_*.deb
# 新板安装: scp deb_armhf/*.deb mika@<board>:/tmp/ && sudo dpkg -i /tmp/*.deb && reboot

set -e

WT=$1
OUT=$2
ARCH=armhf
ECVER=$(grep -m1 AC_INIT $WT/configure.ac | sed -n 's/.*AC_INIT(\[ethercat\], \[\([^]]*\)\].*/\1/p')
[ -n "$ECVER" ] || { echo "cannot determine version from configure.ac"; exit 1; }
VERSION="${ECVER}-t153"

SRC_DIR=$WT
OUT_DIR=$SRC_DIR/deb_armhf
PKG_DIR=$SRC_DIR/deb-pkg-t153

for ko in stmmac stmmac-platform dwmac-sunxi dwmac-sun8i dwmac-generic ec_master ec_generic; do
    [ -f $OUT/modules/$ko.ko ] || { echo "missing $OUT/modules/$ko.ko (run build first)"; exit 1; }
done
[ -d $OUT/userspace/lib ] || { echo "missing $OUT/userspace (run build first)"; exit 1; }

# ---------- KVER: 内核版本 (deb 装到 /lib/modules/<KVER>/extra, postinst depmod <KVER>) ----------
# 从模块 vermagic 自动推导 (= .ko 交叉编译时针对的内核版本). 构建机无 modinfo,
# 用 strings 读 vermagic 串, 取第一个 token (形如 5.10.198-rt97-9i60a1).
# 换内核 = 换 OUT (指向该内核的 build_branch 产物), 脚本自动跟. 可用 KVER=<v> 覆盖.
if [ -z "$KVER" ]; then
    KVER=$(strings -n 8 "$OUT/modules/ec_master.ko" | grep -m1 vermagic \
        | sed -n 's/.*vermagic[=: ]*\([0-9][^ ]*\).*/\1/p')
fi
[ -n "$KVER" ] || { echo "cannot derive KVER from $OUT/modules/ec_master.ko (set KVER=<ver>)"; exit 1; }
echo "KVER (from module vermagic): $KVER"

rm -rf "$OUT_DIR" "$PKG_DIR"
mkdir -p "$OUT_DIR" "$PKG_DIR"

mkdeb() {
    local name=$1
    local pkgdir=$PKG_DIR/${name}_${VERSION}_${ARCH}
    mkdir -p "$pkgdir/DEBIAN"
    echo "$pkgdir"
}

# ---------- modprobe 选口配置 (包独占, conffile) ----------
MODPROBE_CONF='# IgH native EtherCAT: 选口 (哪个 MAC 给 master claim).
# 编辑本文件换口; 勿在 /etc/modprobe.d/ 另建第二个写 options ec_master 的文件
# (modprobe 按字母序合并, 后者覆盖前者).
# T153: end0 = 02:00:15:fc:91:e1 (gmac0), end1 = 12:00:15:fc:91:e1 (gmac1).
# 两层一致: 用户态 MASTER0_DEVICE (/etc/sysconfig/ethercat.conf) 与这里的
# main_devices 必须是同一个 MAC.
# 回退: 删除 options 行 (或 main_devices 为空) → stmmac 全部保持普通网卡.
options ec_master main_devices=02:00:15:fc:91:e1'

# ---------- modules-load 开机链触发 (包独占, conffile) ----------
# sunxi-stmmac = BSP 顶层驱动; depmod 符号依赖拉出 stmmac (IgH ecdev 钩子版)
# → stmmac_platform/phylink → ec_master, 开机即 claim (与 IT-3 验证的启动路径一致).
MODLOAD_CONF='# IgH native EtherCAT T153: 开机模块链触发.
# 回退普通网卡: 删除本文件 (stmmac 链不再开机加载).
sunxi-stmmac'

# =============== ethercat-t153-modules (7 个预编译 .ko) ===============
PKG=$(mkdeb ethercat-t153-modules)
mkdir -p "$PKG/lib/modules/$KVER/extra" "$PKG/etc/modprobe.d" "$PKG/etc/modules-load.d"
for ko in stmmac stmmac-platform dwmac-sunxi dwmac-sun8i dwmac-generic ec_master ec_generic; do
    cp $OUT/modules/$ko.ko "$PKG/lib/modules/$KVER/extra/"
done
printf '%s\n' "$MODPROBE_CONF" > "$PKG/etc/modprobe.d/90-igh-ec.conf"
printf '%s\n' "$MODLOAD_CONF" > "$PKG/etc/modules-load.d/90-igh-ec.conf"
cat > "$PKG/DEBIAN/control" <<EOF
Package: ethercat-t153-modules
Version: $VERSION
Architecture: $ARCH
Maintainer: T153 <mika-net@outlook.com>
Section: kernel
Priority: optional
Description: IgH EtherCAT master T153 native stmmac modules ($ECVER)
 Pre-compiled for kernel ${KVER} (sunxi BSP, preempt_rt, MODVERSIONS).
 stmmac core (ecdev hook) + stmmac-platform + dwmac-sunxi/sun8i/generic glue
 (other Allwinner 5.10 boards) + ec_master + ec_generic.
 NIC selection: /etc/modprobe.d/90-igh-ec.conf (conffile, main_devices).
 Boot chain trigger: /etc/modules-load.d/90-igh-ec.conf (conffile).
EOF
cat > "$PKG/DEBIAN/conffiles" <<EOF
/etc/modprobe.d/90-igh-ec.conf
/etc/modules-load.d/90-igh-ec.conf
EOF
cat > "$PKG/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
depmod -a "$KVER" 2>/dev/null || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"
cat > "$PKG/DEBIAN/postrm" <<EOF
#!/bin/sh
set -e
depmod -a "$KVER" 2>/dev/null || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postrm"

# =============== libethercat (运行时库) ===============
PKG=$(mkdeb libethercat)
mkdir -p "$PKG/usr/lib"
SOREAL=$(ls $OUT/userspace/lib/libethercat.so.1.* | head -1 | xargs basename)
cp "$OUT/userspace/lib/$SOREAL" "$PKG/usr/lib/"
ln -sf "$SOREAL" "$PKG/usr/lib/libethercat.so.1"
cat > "$PKG/DEBIAN/control" <<EOF
Package: libethercat
Version: $VERSION
Architecture: $ARCH
Maintainer: T153 <mika-net@outlook.com>
Section: libs
Priority: optional
Depends: libc6 (>= 2.28)
Description: IgH EtherCAT master userspace library ($ECVER, T153 build)
 libethercat.so.1 (ecrt.h API). ABI generation pinned to this build.
EOF
cat > "$PKG/DEBIAN/shlibs" <<EOF
libethercat 1 libethercat (>= $VERSION)
EOF

# =============== libethercat-dev (头 + 静态库) ===============
PKG=$(mkdeb libethercat-dev)
mkdir -p "$PKG/usr/include" "$PKG/usr/lib"
cp $OUT/userspace/include/*.h "$PKG/usr/include/"
ln -sf "$SOREAL" "$PKG/usr/lib/libethercat.so"
cp $OUT/userspace/lib/libethercat.a "$PKG/usr/lib/" 2>/dev/null || true
cat > "$PKG/DEBIAN/control" <<EOF
Package: libethercat-dev
Version: $VERSION
Architecture: $ARCH
Maintainer: T153 <mika-net@outlook.com>
Section: libdevel
Priority: optional
Depends: libethercat (= $VERSION), libc6-dev
Description: IgH EtherCAT master development files ($ECVER, T153 build)
 ecrt.h headers + libethercat.a + .so symlink for compiling against the
 RT EtherCAT API (must match the installed libethercat generation).
EOF

# =============== ethercat-t153 (CLI + 服务 + 用户态配置) ===============
# 上游 install 树: bin/ethercat, sbin/ethercatctl, etc/init.d/ethercat,
# etc/sysconfig/ethercat, etc/ethercat.conf (systemd unit 视分支而定, 有则收).
PKG=$(mkdeb ethercat-t153)
mkdir -p "$PKG/usr/bin" "$PKG/usr/sbin" "$PKG/etc/sysconfig"
cp $OUT/userspace/bin/ethercat "$PKG/usr/bin/"
[ -f $OUT/userspace/bin/ethercat_mbg ] && cp $OUT/userspace/bin/ethercat_mbg "$PKG/usr/bin/" || true
[ -f $OUT/userspace/sbin/ethercatctl ] && cp $OUT/userspace/sbin/ethercatctl "$PKG/usr/sbin/" || true
[ -f $OUT/userspace/lib/systemd/system/ethercat.service ] && \
    { mkdir -p "$PKG/lib/systemd/system"; cp $OUT/userspace/lib/systemd/system/ethercat.service "$PKG/lib/systemd/system/"; } || true
[ -f $OUT/userspace/etc/init.d/ethercat ] && \
    { mkdir -p "$PKG/etc/init.d"; cp $OUT/userspace/etc/init.d/ethercat "$PKG/etc/init.d/"; chmod 755 "$PKG/etc/init.d/ethercat"; } || true
for c in etc/sysconfig/ethercat etc/ethercat.conf; do
    [ -f $OUT/userspace/$c ] || { echo "missing $OUT/userspace/$c"; exit 1; }
    mkdir -p "$PKG/$(dirname $c)"
    cp "$OUT/userspace/$c" "$PKG/$c"
done
cat > "$PKG/DEBIAN/control" <<EOF
Package: ethercat-t153
Version: $VERSION
Architecture: $ARCH
Maintainer: T153 <mika-net@outlook.com>
Section: net
Priority: optional
Depends: libethercat (= $VERSION), ethercat-t153-modules (= $VERSION), iproute2, kmod
Description: IgH EtherCAT master tools and service ($ECVER, T153 build)
 ethercat CLI, ethercatctl, init/systemd unit. Configuration (conffiles):
 /etc/sysconfig/ethercat + /etc/ethercat.conf (MASTER0_DEVICE — must match
 main_devices in /etc/modprobe.d/90-igh-ec.conf).
EOF
cat > "$PKG/DEBIAN/conffiles" <<EOF
/etc/sysconfig/ethercat
/etc/ethercat.conf
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ -x /bin/systemctl ]; then
    systemctl daemon-reload || true
fi
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"

# =============== 打全部 deb ===============
echo "=== Building debs (version $VERSION) ==="
for d in "$PKG_DIR"/*; do
    pkg=$(basename "$d")
    fakeroot dpkg-deb --root-owner-group --build "$d" "$OUT_DIR/${pkg}.deb" 2>&1 | tail -1
done
ls -la "$OUT_DIR/"
rm -rf "$PKG_DIR"
echo ""
echo "新板安装:"
echo "  scp deb_armhf/*.deb mika@<board>:/tmp/"
echo "  ssh mika@<board> 'sudo dpkg -i /tmp/*.deb && sudo reboot'"
