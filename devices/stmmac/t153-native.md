# T153 native stmmac EtherCAT 驱动 — 原理说明

本目录的 `-5.10-*` 文件对是 T153 (Luckfox Pico / Lume, Allwinner T153,
linux-5.10-rt sunxi BSP) 的 native stmmac 驱动:把内核 stmmac 网卡驱动
直接变成 IgH EtherCAT Master 的传输后端。验收记录与踩坑见
`t153-config.md` 与 mcad vault `Knowledge/igh-native-stmmac-build.md`。

## 为什么用 native (而不是 generic 驱动)

IgH 提供两类 master 后端:

| | generic (`ec_generic`) | native stmmac (本驱动) |
|---|---|---|
| 数据路径 | netdev → io_uring / socket → 内核网络栈 | `ec_poll()` 直调 stmmac RX/TX 函数 |
| 延迟/抖动 | 受调度、NAPI、协议栈影响 | 周期性调用,路径固定 |
| RT 适配 | 需要额外工作 | 天然:一个 SCHED_FIFO 线程喂 1 kHz |
| 硬件要求 | 任意 netdev | 网卡驱动必须实现 ecdev 接口 |

native 驱动在 probe 时向 master 核心 **offer** 自己;master 接受后,
网卡 **不再注册为 netdev** — 它只服务 EtherCAT 流量。

## ecdev 接口:offer / claim

驱动侧 (`stmmac_main-5.10-ethercat.c`, API 在 `devices/ecdev.h`):

```c
priv->ecdev = ecdev_offer(ndev, ec_poll, THIS_MODULE);
if (!priv->ecdev)
    register_netdev(ndev);          // 没人要 → 普通网卡,透明回退
else
    dev_info(..., "claimed by IgH native EtherCAT master");
```

master 侧 (`master/module.c` 的 `ecdev_offer()`):按 **MAC 地址匹配**
模块参数 `main_devices` (module_param_array):

- 匹配 → claim:挂入 master 设备列表、netdev 改名 `ec%c%u` (如 ecm0)、
  不建 netdev;
- 不匹配 → 返回 NULL,驱动走普通 `register_netdev`;
- `ec_master` 没加载 / 参数为空 → 所有网卡保持普通行为。

这是"多网卡选口"的机制:**同一个 stmmac 驱动,哪个口被 master 用,
完全由 modprobe 配置决定**(见 `t153-config.md`)。Intel igc 驱动
(`devices/igc/`) 走同一接口。

## 数据路径

claim 之后:

```
1 kHz RT 线程 (用户态 ecrt_master_receive/send 的 RT IPC)
   → master 核心
   → ecdev 接口:
       ecdev_open()            probe 尾部,初始化 EC 模式
       ec_poll(ndev)           每个周期调用:
           stmmac_rx()         — 收帧:直接走 stmmac DMA RX 描述环,
                                 帧交给 ecdev_receive() (→ WKC 累加)
           stmmac_tx_clean()   — 发帧:master 写入的 TX 描述环清理,
                                 推进 dirty_tx
       ecdev_set_link(state)   链路 up/down 通知 (netif_carrier 的替代)
```

关键改动点 (相对 stock stmmac,全部集中在 `stmmac_main` + `stmmac.h`):

1. **IRQ 不再驱动收发**:中断 handler 在 `priv->ecdev` 存在时提前返回
   (不进 NAPI) — 避免中断上下文与周期线程竞争 DMA 环。
2. **RX 路径** `stmmac_rx()`:帧从 DMA 取出后,EC 模式下交
   `ecdev_receive()` 而不是 `netif_receive_skb()`。
3. **TX 路径** `stmmac_handle_tx_done()` / `stmmac_tx_clean()`:
   EC 模式下不做 `netdev_tx_completed_queue()` / `dev_consume_skb()`
   的常规簿记 (帧生命周期归 master)。
4. **coalesce / 队列启停**:EC 模式下跳过 `stmmac_init_coalesce()`、
   `netif_tx_start_all_queues()` 等 netdev 专属动作。
5. **link 状态**:`ecdev_set_link()` 替代 `netif_carrier_on/off`。

`stmmac.h` 只加一个字段 `ec_device_t *ecdev` + `get_ecdev()` 内联访问器,
其余 stmmac 对象 (dwmac4/5/1000 core、DMA 描述环、PTP、ethtool)
**原样复用** — 这就是 "native" 的含义:DMA 引擎、RGMII/GMII 配置、
时钟、PTP 硬件全部是内核驱动自己的,只有"谁消费帧"被换掉。

## WKC (Working Counter) 语义

每帧 EtherCAT 报文带 WKC;从站按收到的帧数累加。master 每个周期校验:

- `wc_state == EC_WC_COMPLETE` 且 `working_counter` 等于期望值
  (本台架:1 从站 × out/in LRW 对 = **3**)。
- 不完整 = 总线事件 (帧丢、从站脱链、FMMU 错配)。

### 台架曾遇 WKC=0 全周期,两层根因 (都已验证)

1. **attach 静默失败**:用户态用错 `product_code` (0x0100) 去
   `ecrt_master_slave_config()` 而板载从站 SII 是 0x0001 →
   `ec_slave_config_attach()` 返 -EINVAL,但**只打 DBG 级日志**,
   lib 层不检查返回值 → `sc->slave == NULL` → SM/FMMU 永不配置 →
   从站永停 PREOP → 所有周期 WKC=0。
   诊断口:`ecrt_slave_config_state().online` (= `sc->slave ? 1 : 0`)。
2. **SM 看门狗**:无 domain 注册 → 无 FMMU → 从站 SM2 (SII ctrl
   0x64 带 WD 位) 区域无人写 → ~5 s 后从站 AL **0x001B
   "Sync manager watchdog"** → SAFEOP+ERROR,进不了 OP。
   正解:domain 注册 PDO 条目 → FMMU 建立 → 周期喂 SM → WD 满足。

另注意:activate 后从站走 INIT→PREOP→SAFEOP→OP 状态机需要几十 ms,
期间 WKC<期望值物理必然 — 验收必须两段式 (grace 泵到 OP,再计完整性)。

## 模块结构 (T153, 11 个 .ko)

```
加载链 (lsmod 实测):
sunxi_stmmac      ← BSP 顶层 platform 驱动 (compatible 匹配 gmac0/1)
  ├─ stmmac_platform   (IgH 构建, stock 源码)
  ├─ stmmac            (IgH 构建, 含 ecdev hook) ← ec_master, phylink
  ├─ sunxi_addr        (SDK stock, MAC 地址管理)
  └─ (ec_master 由 stmmac 依赖拉入)

部署但不加载 (冗余安全件, 与 BSP 驱动竞争同一 compatible, 后者赢):
  dwmac-sunxi.ko / dwmac-sun8i.ko / dwmac-generic.ko (mainline 系 glue)

SDK stock (内核构建产物, 永不 patch):
  sunxi-stmmac.ko  ← $KDIR/bsp/drivers/stmmac/
  sunxi_addr.ko    ← $KDIR/bsp/drivers/misc/sunxi-addr/
  phylink.ko / mdio-mux.ko
```

**T153 特殊性**:sunxi BSP 在 mainline 式 stmmac 目录之外另有一份
BSP stmmac (`bsp/drivers/stmmac/`),它构建的 `sunxi-stmmac.ko`
才是实际绑定的顶层驱动。mainline 目录里 `dwmac-sunxi.c` 等 glue 同样
能匹配 compatible,但 probe 顺序上 BSP 模块胜出 — 所以部署集里两者
并存、只加载一份。

**MODVERSIONS 纪律** (台架事故根因):同一次 kbuild 出的模块组
(stmmac 5 件 + ec_master) CRC 互洽;stock 件 (sunxi-stmmac 等)
引用 stmmac 符号的 CRC 是**按其构建时的 $BOUT symvers** 定的。
混代安装 → `disagrees about version of symbol stmmac_dvr_probe`
→ 顶层驱动加载失败 → 双网口无 netdev (板不挂,但网络全死,只能串口进)。
规则:**替换 7 件 IgH 构建件时,4 件 stock 保持板上原代**,或 11 件
同代整体换 (同一 $BOUT 状态产出)。

## 文件约定 (与 upstream update.sh 一致)

- `<name>-5.10-orig.c/h` — SDK 树 pristine 副本 (EC patch 未打)。
- `<name>-5.10-ethercat.c/h` — 构建用的变体。45 个文件与 -orig 完全
  相同 (仅 local `#include "X.h"` 重写为 `"X-5.10-ethercat.h"`);
  **只有 `stmmac_main` 与 `stmmac.h` 真正带 EC hook**
  (即 `patches/0001-t153-ecdev.patch` 的内容)。
- 重新生成:`./update-t153.sh <SDK 内核源目录>`
  (从 SDK 树拉 pristine,自动处理"树已被 build 打补丁"的状态)。
- `Kbuild.in` 的 5.10 分支:**stock 模块名** (stmmac / stmmac-platform /
  dwmac-sunxi / dwmac-sun8i / dwmac-generic),保证与板上
  modules-load.d、Depends 链、代际 CRC 完全兼容 — 与 upstream 6.x 的
  `ec_*` 合并模块 (PCI 场景) 不同,那是平台驱动场景的有意差异。
- `ecdev.h` 用共享的 `devices/ecdev.h` (Kbuild 里 `-I$(src)/..`)。

## 配置与部署

见 [t153-config.md](t153-config.md)。
