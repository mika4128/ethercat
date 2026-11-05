# T153 EtherCAT 网口配置与部署指南

> 本文是「怎么用」层: 网口怎么设、和 Intel 网卡比哪里一样哪里不同、支持哪些全志芯片、怎么给不同内核出 deb。
> 原理 (ecdev hook 如何 claim 网卡) → `t153-native.md`。构建 / 部署 / 故障 / 回滚细节 → `t153-config.md`。

## 1. 网口怎么设 (哪个口做 EtherCAT)

### 核心旋钮只有一个: `main_devices=<MAC>`

内核 `ec_master` 的模块参数。指定哪个 MAC, 哪个口就被 master **claim** 成 EtherCAT 口
— 被 claim 的口不再是普通网卡 (从 `ip link` 里消失)。下面两条配置路径都汇到这同一个参数, 语义一致。

### T153 的两个口

| 口 | MAC | 角色 |
|---|---|---|
| `end0` | `02:00:15:fc:91:e1` (gmac0) | **EtherCAT 口** (默认) |
| `end1` | `12:00:15:fc:91:e1` (gmac1) | **SSH / 管理口** |

### 路径 A — modprobe (本板 + 我们 deb 用这条, 现役)

```
/etc/modprobe.d/90-igh-ec.conf
options ec_master main_devices=02:00:15:fc:91:e1     # = end0
```
改 MAC → `reboot`。deb 里 conffile 预填的就是这个值。

### 路径 B — 服务 (标准 IgH, 本板 `MASTER0_DEVICE=""` 空着没用)

```
/etc/sysconfig/ethercat
MASTER0_DEVICE="end0"        # 接口名 或 MAC 都行
```
`ethercatctl start` 会读它、转成 MAC、自动 `modprobe ec_master main_devices=<MAC>`
(见 `script/ethercatctl.in`)。

### 验证

```
dmesg | grep claimed          # → "dwmac-sunxi 4500000.ethernet: claimed by IgH native EtherCAT master"
ip -br link                    # 被 claim 的口消失 (只剩 end1)
ethercat slaves                # 列出从站 (如 HPM5E31 ecat_uart)
```

### ⚠️ 换口警告

`end1` 是 SSH 口。若把 `main_devices` 改成 end1 的 MAC (`12:00:15:fc:91:e1`), end1 被
claim 成 EC 口 → **SSH 直接断**, 只能靠串口 / OTG 救回。T153 保持 **EC=end0, 管理=end1**,
别换 — 除非手上有串口。

## 2. 和 Intel 网卡配置对比

**选口方法完全一致。** `main_devices` / `MASTER0_DEVICE` 是 `ec_master` 内核核心能力,
与具体网卡驱动无关 — Intel 和 T153 用同一套配置文件、同一套语义。这就是「一致」的部分。

差异全在**构建 / 打包层**, 是平台约束逼出来的, 不是设计不一致:

| 维度 | Intel 网卡 (igc) | T153 (native stmmac) |
|---|---|---|
| 驱动模块形态 | 单 `ec_igc` 模块 (IgH 自带 igc 源码) | 7 件 stmmac 族 (stmmac 核心 + stmmac-platform + dwmac-sunxi/sun8i/generic 3 glue + ec_master + ec_generic) |
| 为什么形态不同 | igc 是自包含单驱动 | stmmac 是核心 + glue 拆分, 且 BSP 自带顶层驱动 `sunxi-stmmac` 必须保留 |
| 构建 / 打包 | DKMS (`debian/`): 装时对本机内核重编 | 预编译 armhf deb (`build-t153-deb.sh`) |
| 为什么打包不同 | x86 开发机有 kernel-headers | 板子无 kernel-headers, 内核固定 |
| 内核绑定 | DKMS 天然内核无关 (每次装都重编) | 预编译 .ko 绑特定内核 (vermagic/MODVERSIONS), 但可对任意内核重编 |
| 用户态配置文件 | 同 (`/etc/modprobe.d` + `/etc/sysconfig/ethercat`) | 同 |

一句话: **选口跟 Intel 一模一样 (同一 ec_master 机制、同一套配置文件); 只是「模块怎么构建
交付」不同 (Intel=DKMS 现场编, T153=预编译 deb), 这个差异由「板子有没有 headers」决定。**

## 3. 支持哪些全志芯片

三层说清 (不夸大):

**1. 实机验证: 只有 T153 (sun55i)。** 台架 WKC 60s PASS 的就这块。T153 走 BSP 自带的
`sunxi-stmmac.ko` 顶层驱动 (调 `stmmac_dvr_probe`), **不是**我们的 dwmac-* glue。

**2. 源码已带 (按 DT compatible 会绑, 未实测):** 包里 3 个 dwmac glue 列了这些 SoC:

| glue | DT compatible | 芯片 |
|---|---|---|
| dwmac-sunxi | `sun7i-a20-gmac` | A20 |
| dwmac-sun8i | `sun8i-h3-emac` / `sun8i-v3s-emac` / `sun8i-a83t-emac` / `sun8i-r40-gmac` / `sun50i-a64-emac` / `sun50i-h6-emac` | H3 / V3s / A83T / R40 / A64 / H6 |
| dwmac-generic | `snps,dwmac*` / `snps,dwxgmac*` 全家 | 通用 stmmac SoC 兜底 |

在 T153 上这 3 个 glue 是「部署但不加载」(冗余, BSP sunxi-stmmac 已胜出)。换到 H6 / V3s
这类走 mainline dwmac 绑定的板, 对应 glue 才会被 DT 拉起来。

**3. 硬约束: 预编译 deb 锁死内核版本** (如 `5.10.198-rt97-9i60a1`, vermagic/MODVERSIONS)。
别的芯片几乎必然带不同 BSP = 不同内核 = 现成的 .ko 加载不了 (vermagic mismatch)。

- **同内核版本**的全志板 → 直接装同一 deb, 只改 conffile 里的 MAC。
- **不同 BSP / 内核**的芯片 → 对着那块板的 BSP 内核重编 (同一套源码), 出新 .ko + 新 KVER 的 deb。

## 4. 给不同内核编译对应的驱动 deb

**可以。** 构建按内核参数化, `build-t153-deb.sh` 从 .ko 的 vermagic **自动推导 KVER**:

```
# 1) 交叉编译: build_branch.sh 的 --with-linux-dir 指向目标内核的 O= 构建目录
#    (产出该内核的 7 件 .ko + userspace, 落到 <OUT>)
# 2) 打包 (KVER 自动跟随目标内核):
bash build-t153-deb.sh <checkout> <OUT>
#    → 打印 "KVER (from module vermagic): <目标内核版本>"
#    → deb_armhf/*.deb, 安装路径 /lib/modules/<目标内核>/extra
# 可选: KVER=<ver> 环境变量覆盖自动推导
```

要点:
- 换内核 = 换传进去的 `OUT` (指向该内核的 build_branch 产物), 脚本自动跟上, 不用改脚本。
- **MODVERSIONS 代际纪律仍适用**: 新旧 .ko 上板前必须过 `__versions` 逐符号 CRC 闸门
  (见 `t153-config.md` §2), 不得混装不同内核构建的模块。
- 包名版本是 `${ECVER}-t153` (如 `1.7.0-t153`), **不含内核版本** — 一个 deb 对应一个内核;
  同机装两个不同内核的会互相覆盖, 但单板只跑一个内核, 无此问题。

## 5. 新板安装 / 旧板迁移

完整细节见 `t153-config.md` §8。

新板 (三步, 板 `/tmp` 是 tmpfs, 装完再 reboot):
```
scp deb_armhf/*.deb mika@<board>:/tmp/
ssh mika@<board> 'sudo dpkg -i /tmp/*.deb'
sudo reboot
```
旧 sncn13 板迁移: 先 `sudo dpkg -r ethercat-master ethercat-master-modules` 除旧
(旧包文件属主 + 旧 CLI ioctl 版本不兼容), 再 `sudo dpkg -i /tmp/*.deb`。

---
交叉引用: 原理 `t153-native.md` | 构建 / 部署 / 故障 / 回滚 / 打包 `t153-config.md`
