# T153 native stmmac — 构建 / 部署 / 使用

目标板:Luckfox Lume (T153M3-QCX, hostname `9i60`),双千兆 RGMII
(gmac0 = end0, gmac1 = end1),内核 `5.10.198-rt97-9i60a1` (sunxi BSP,
preempt_rt, MODVERSIONS)。原理见 [t153-native.md](t153-native.md)。

台架验收 (2026-10-30/31) 已 PASS:HPM5E31 ecat_uart 从站 1 kHz 周期
60 s,`wc_bad=0`,OP 稳态,end1 SSH 全程存活。

## 1. 构建 (构建机 .125, ARM 交叉)

### 前置

- SDK:`~/T153/T153_Linux_V1.0`,内核树 `$SDK/kernel/linux-5.10-rt`
  (下称 KDIR),out-of-tree 构建目录 `$BOUT=$SDK/out/t153/kernel/build`。
- **SDK 内核必须已用 stmmac fragment 编过** (一次性,~20-40 min):
  fragment 使 stmmac 栈 `=m` (CONFIG_STMMAC_ETH / STMMAC_PLATFORM /
  DWMAC_SUNXI / DWMAC_SUN8I / DWMAC_GENERIC / AW_STMMAC_ETH /
  AW_MACADDR_MGT / PHYLINK / MDIO_BUS_MUX)。
  `$BOUT` 由此得到:autoconf (CONFIG_STMMAC_*)、Module.symvers、
  stock .ko (sunxi-stmmac / sunxi_addr / phylink / mdio-mux)、boot.img。
- 工具链:`$SDK/out/toolchain/gcc-linaro-11.3.1-2022.06-x86_64_arm-linux-gnueabihf`。
- 本仓库 (ethercat.git) dev-1.6 / dev-1.7 分支 checkout。
  **构建必须在该 checkout 内进行** (configure 把绝对路径代进 Kbuild,
  不要拷到别处再编)。

### 构建流程

```bash
cd <checkout>
./bootstrap      # 改 configure.ac 后重新生成 configure
./configure --host=arm-linux-gnueabihf --prefix=$OUT/userspace \
    --with-linux-dir=$BOUT \
    --enable-generic \
    --enable-stmmac --with-stmmac-kernel=5.10
export ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- PATH=$TC/bin:$PATH
make -j$(nproc) && make install
make clean
make -C $KDIR O=$BOUT M=$PWD modules -j$(nproc)
```

1.7 分支另需 `--disable-examples` (examples/mini.c 死代码,过不了 SDK
内核 `-Werror=declaration-after-statement`);1.6 分支的顶层 Kbuild 已移除
examples/。

### 关键参数说明

| 参数 | 含义 |
|---|---|
| `--with-linux-dir=$BOUT` | 用 **out-of-tree 构建目录** (不是 KDIR):configure 从 `$BOUT/.config` 与 `include/config/kernel.release` 读内核版本 (→ 5.10),kbuild 用它拿 autoconf/symvers |
| `--enable-stmmac` | 启用 stmmac 平台驱动分支 (T153 5.10);不启用则 6.x PCI 模块 (ec_stmmac-pci / ec_dwmac-intel) 也都不编 |
| `--with-stmmac-kernel=5.10` | 选文件对版本 (configure 按 `devices/stmmac/stmmac-5.10-orig.h` 存在性校验;缺省取 `--with-linux-dir` 内核的大版本) |
| `--enable-generic` | 同时编 `ec_generic` (任意 netdev 后端,冗余部署) |
| `M=$PWD` (kbuild) | 顶层 Kbuild = `master/ + devices/` 一次 kbuild 出全部模块;`KBUILD_EXTRA_SYMBOLS` 指向本树 `master/Module.symvers` (ecdev_offer CRC) |

**坑 (已固化进树,勿删)**:

- 顶层 `Kbuild.in` (1.6) 已移除 `examples/` (mini.c 死代码,过不了 SDK
  内核的 `-Werror=declaration-after-statement`);1.7 用 `--disable-examples`。
- `devices/stmmac/Kbuild.in` 的 5.10 分支带
  `ccflags-y += -Wno-error=declaration-after-statement`
  (SDK BSP 内核把 declaration-after-statement 当 error;子目录 Kbuild
  不继承父目录 ccflags)。
- 交叉编译用户态用 `--host=arm-linux-gnueabihf` + TC 在 PATH;
  kbuild 用 `ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf-`。
  **不要**给 gcc 加 `-rt` 链接旗标 (linaro 拒绝;`-lrt` 放命令尾部即可)。
- stale userspace 对象可致链接期浮点 ABI 不匹配
  (`ethercat uses VFP register arguments ... does not`):换分支/工具链
  后先 `find master tool devices -type f \( -name "*.o" -o -name "*.lo" \) -delete`。

### 产物

`$OUT/` (建议 `~/T153/T153_Linux_V1.0/out/igh-native-stmmac-<branch>`):

```
userspace/      (configure --prefix, make install)
modules: 本次 kbuild 出的 7 个 .ko
  stmmac.ko stmmac-platform.ko dwmac-sunxi.ko dwmac-sun8i.ko dwmac-generic.ko
  ec_master.ko ec_generic.ko
```

vermagic 必须 =
`5.10.198-rt97-9i60a1 SMP preempt_rt mod_unload modversions ARMv7 p2v8`。

另外 4 件 stock (sunxi-stmmac / sunxi_addr / phylink / mdio-mux)
**不由本仓库构建** — 部署时的代际纪律见 §2。旧 sncn-13 流程
(t153-native-stmmac repo 的 tools/build_unified_125.sh,11 件整包)
留档参考。
## 2. 部署

### 全量部署 (首次 / 换内核)

`tools/deploy_board.sh <board_ip>` (需要 `LUME_PASS` env):
boot.img dd 进 p4 + 11 模块 + userspace + modprobe conf + reboot。
**注意**:SDK 的 boot.img 尾部 dtb 是通用 board.dts 编译前的版本。
当前板上 p4 的 dtb 已做 rfkill 修复 (见 §5 故障),**在 SDK
board.dts 重新进 boot.img 之前,不要 dd boot.img** — 用模块级部署。

### 模块级部署 (日常迭代)

`tools/deploy_modules_only.sh <board_ip>` (在 .125 跑,
需要 `~/lume-creds.env` 含 LUME_HOST/USER/PASS, **LF 行尾**):

备份板上 extra/ → 推 11 件 → depmod → stop mics → reboot。

**MODVERSIONS 纪律** (台架实锤事故):11 件必须**同代**。
IgH 7 件 (stmmac×5 + ec_master + ec_generic) 来自一次 kbuild;
stock 4 件来自**同一个 $BOUT 构建状态**。若 SDK 内核在两次部署之间
重编过,stock 件 CRC 代际变化 → 混装 → `sunxi_stmmac: disagrees
about version of symbol stmmac_dvr_probe` → 顶层驱动加载失败 →
双口无 netdev (板活着,只能串口进)。
修复:从板上备份恢复 stock 4 件 (deploy 前自动备份到
`/home/mika/ko-backup-*`),或等 SDK 不重编后 11 件同代整体换。

### 板端布局

模块全部在 `/lib/modules/5.10.198-rt97-9i60a1/extra/`。
`/etc/modules-load.d/` 加载 `sunxi-stmmac` (BSP 顶层驱动,拉整条链)。
`/etc/modprobe.d/90-igh-ec.conf`:

```
options ec_master main_devices=02:00:15:fc:91:e1
```

## 3. 多网卡选择 (哪个口给 EtherCAT)

三层各管一件事:

| 层 | 机制 | 文件 |
|---|---|---|
| 选口 (master claim) | `main_devices` MAC 匹配 | `/etc/modprobe.d/90-igh-ec.conf` |
| 选驱动 / 启用 | DT `compatible` / `status` | board.dts (gmac0/gmac1) |
| 命名 | DT `label` / udev | end0/end1 |

- T153 两口的 MAC 只差首字节:end0 = `02:00:15:fc:91:e1`,
  end1 = `12:00:15:fc:91:e1`。`main_devices` 写 end0 → stmmac probe
  时 end0 被 claim (改名 ecm0,不建 netdev),end1 保持普通 NIC。
- 换口 = 改 MAC 重启;两口都要普通网卡 = 删掉 options 行 (透明回退)。
- claim 证据:`dmesg | grep "claimed by IgH"`;`ip link` 里 end0 消失。

## 4. 验收 (台架)

从站:HPM5E31 板载 ecat_uart (SII: vendor 0x0A0C, product 0x0001)。
测试程序 `ec_wkc_test` (t153-native-stmmac/tools/,板上
`/home/mika/ec_wkc_test`,**板 /tmp 是 tmpfs 重启全清,持久件放
/home/mika**):

```bash
# 前置: stop mics (end0 被 claim 后 mics 的 eth 口没了,且避免争总线)
sudo systemctl stop mics; sudo pkill -f noneui
sudo /home/mika/ec_wkc_test 60
```

判据 (两段式,见原理文档 WKC 节):
- grace 10 s 内从站进 OP (al=8);
- 其后 60 s × 1 kHz:每周期 `wc_state==COMPLETE` 且 `wc==3`;
- `cycles=60000 wc_bad=0` + `final op=1` = **PASS**;
- 全程 end1 (192.168.4.x) SSH 存活。

常用 AL 码:0x001B = Sync manager watchdog (无 FMMU/SM 区没人写);
0x0013/0x0014 = 配置/映射错误;0x0015 = 无效邮箱。
诊断口:`ethercat slaves` (或 ecrt_slave_config_state().online) —
online=0 即 attach 失败 (vendor/product 不匹配,静默 DBG 日志)。

## 5. 故障记录 (已修,防再犯)

### end1 网口不启 (pg11/12/13 被 rfkill 抢)

- 症状:end1 (4500000.ethernet) probe -22,无 netdev;
  `dmesg: pin PG11 already requested by 3604000.pinctrl:203`。
- 根因:board.dts 的 `&rfkill` 节点 wlan/bt GPIO
  (wlan_hostwake=PG11, bt_rst_n=PG12, wlan_regon=PG13) 全落在
  gmac1 rgmii1 的 PG0-13 内;Lume 无 WiFi/BT 硬件,sunxi-rfkill
  (built-in, suppress_bind_attrs, 无 sysfs unbind) 抢先 claim。
- 修复:board.dts `&rfkill { status="disabled"; }` → 重编 dtb →
  **补零到旧 dtb 同尺寸 → 外科 dd 进 p4 的 dtb blob**
  (boot.img 内 dtb 偏移 15495168, 256 对齐, 自描述 d00dfeed 头)。
- 教训:同一节点里写了两条 `status` (一 disabled 一 okay),dtc
  last-wins → "修过"没生效。验证修复要反编译**运行中的 dtb** diff,
  别信源文件注释。

### 板挂死 (IT-2 事故, 已恢复)

全量部署时 dd 进 p4 的 boot.img 打的是通用 dtb (无 board 串) →
控制台 UART 未 mux + gmac 引脚不配 → 无串口无网络。
恢复:FEL 模式 (FEL 脚拉低上电) + xfel 写回旧 p4。
此后 dtb 修复用外科 dd,不动 kernel blob。

### 选口失败的静默性

`ecrt_master_slave_config()` vendor/product 不匹配 → attach 返
-EINVAL 但仅 DBG 日志,调用者不检查 → 表现为"从站在但永不 OP、
WKC=0"。排查第一步永远看 `online` flag,不要直接怀疑总线。

## 6. 回滚

- 模块级:板上 `/home/mika/ko-backup-<ts>/` (每次 deploy 自动备份
  11 件) → 拷回 extra/ + depmod + reboot。
- 板级 (内核/boot):FEL 写回 `$SDK/out/images/latest-lume.img`
  0x4500000 处的原始 p4 (备份件
  `~/t153-native-stmmac/recovery/old-p4-20260930.img`,
  md5=4ee7dfe303a27fa3e9712f574e62e770)。

## 7. 相关脚本位置

| 脚本 | 位置 | 作用 |
|---|---|---|
| build_unified_125.sh | t153-native-stmmac/tools/ | 统一构建 (§1) |
| build_on_125.sh | 同上 (**deprecated**, 留档) | 旧三步链 |
| deploy_modules_only.sh | 同上 | 模块级部署 (§2) |
| deploy_board.sh | 同上 | 全量部署 (含 boot.img) |
| ec_wkc_test.c | 同上 | 验收 (§4) |
| update-t153.sh | **本目录** | 再生 5.10 文件对 |
| patches/0001-t153-ecdev.patch | **本目录** | EC hook 全量 diff |
| build-t153-deb.sh | **仓库根目录** | T153 预编译 deb 打包 (§8) |

## 8. 打包 / 新板安装 (T153 预编译 deb)

T153 板无 kernel headers 且内核版本固定 (5.10.198-rt97-9i60a1) → DKMS 流程不适用
(装时重编需要 headers)。T153 用 **预编译 armhf deb** (`build-t153-deb.sh`, 仓库根目录),
4 个包:

| 包 | 内容 |
|---|---|
| `ethercat-t153-modules` | 7 .ko → `/lib/modules/$KVER/extra/` + conffiles: `/etc/modprobe.d/90-igh-ec.conf` (选口 main_devices) + `/etc/modules-load.d/90-igh-ec.conf` (开机链触发 sunxi-stmmac) |
| `libethercat` | libethercat.so.1 运行时库 |
| `libethercat-dev` | ecrt.h 头 + libethercat.a 静态库 |
| `ethercat-t153` | ethercat CLI + ethercatctl + init.d + conffiles: `/etc/sysconfig/ethercat` + `/etc/ethercat.conf` (MASTER0_DEVICE) |

### 构建

```
# 前置: build_branch.sh 已产出 OUT (userspace/ + modules/ 7 件 .ko + ec_wkc_test)
bash build-t153-deb.sh ~/T153/ethercat-1.7 $OUT
# 产物: <checkout>/deb_armhf/*.deb
```

### 新板安装 (三步)

```
scp deb_armhf/*.deb mika@<board>:/tmp/
ssh mika@<board> 'sudo dpkg -i /tmp/*.deb'
sudo reboot   # 或同一条 ssh 里 && sudo reboot
```

重启后: end0 被 claim, `ethercat slaves` 应列出从站。
注: 板 `/tmp` 是 tmpfs, reboot 全清 — dpkg -i 必须在 reboot 前完成。

### 换 EtherCAT 口

两个 conffile 同步改 (必须同一个 MAC):
1. `/etc/modprobe.d/90-igh-ec.conf` — `options ec_master main_devices=<MAC>` (内核选口)
2. `/etc/sysconfig/ethercat` — `MASTER0_DEVICE=<MAC>` (用户态)

T153: end0 = 02:00:15:fc:91:e1, end1 = 12:00:15:fc:91:e1。
勿在 `/etc/modprobe.d/` 另建第二个写 options ec_master 的文件 (modprobe 按字母序合并, 后者覆盖)。
回退普通网卡: 删两个 conffile (或 main_devices 留空) → stmmac 全部保持普通网卡。

### 旧 sncn13 代包 (ethercat-master) 迁移

板上若装有旧 `ethercat-master` / `ethercat-master-modules` (1.5.2+sncn13-t153), 其
`/etc/ethercat.conf`、`/usr/bin/ethercat` 等文件属主会与新主包冲突 — 先除旧再装新:

```
sudo dpkg -r ethercat-master ethercat-master-modules
sudo dpkg -i --force-confnew /tmp/*.deb
```

(`--force-confnew` = conffile 一律取包内新版; 旧 CLI 与新内核主站 ioctl 版本不兼容, 必须换新。)

### 内核代际纪律 (MODVERSIONS)

包内 .ko 绑定 5.10.198-rt97-9i60a1, 与 BSP stock 4 件 (sunxi-stmmac / stmmac-platform /
phylink / mdio-mux, **不在包内**, 用板原代) 的兼容闸门 = 新旧 .ko `__versions` 逐符号 CRC
比对 (见 §2)。SDK 重编内核后必须重建 + 重打包, 不得混装不同代。

### 其他全志芯片

5.10 文件对含 dwmac-sunxi / dwmac-sun8i / dwmac-generic 三套 glue, 按 DT compatible 绑模块。
同内核版本的其他全志 5.10 板 (T113 / sun8i 系) 直接装同一 deb, 只改 conffile 里的 MAC。

### 普通平台 (Intel 网卡等)

不用这些 deb。普通平台走仓库自带 `debian/` 的 **DKMS 流程** (装时对本机内核重编,
generic/igc 驱动, 与本 T153 native 预编译包无关)。
