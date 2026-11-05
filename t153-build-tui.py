#!/usr/bin/env python3
"""t153-build-tui — IgH EtherCAT 全志 (T153 / sunxi) 交叉编译 TUI (Textual).

风格参考 E:\\vcpkg-registry\\vtui.py. 在 **.125 构建机** 上跑 (ARM toolchain +
5.10 内核树 + 板凭据都在本机, 和 vtui 同姿势):

    python3 t153-build-tui.py

四个页签:
  路径     checkout / KDIR / BOUT / toolchain / OUT / 板 IP —— 一键存读 profile
  选项     configure 驱动开关 (stmmac / generic / igc / ...) + stmmac-kernel +
           host + 并行度 + 额外参数; 实时拼出完整 configure 命令
  全志支持 支持矩阵 (实测 T153 / 源码 A20·H3·V3s·A83T·R40·A64·H6·通用) +
           实时检测 (本 BOUT 内核版本 / 本树可用 dwmac glue / 将构建的模块集)
  构建     ① configure → ② userspace → ③ kbuild 模块 → ④ 打 deb → ★ 一键全编;
           板端 (危险, 需板 IP): 部署 7 模块

底部常驻: 状态条 + 可复制日志 (异步实时流式 + 落盘 ~/.t153-build-tui/).

唯一依赖: pip install textual (缺则自动装, PyPI 直连失败换清华源).

支持任意 ethercat 树: 把本脚本拷进 (或指向) 任一 checkout —— 旧 sncn13
(ethercat_master) 或新主站 (ethercat-1.6 / 1.7), 路径页里改 checkout 即可。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── 自检: textual 缺则当场装 (vtui 同款) ─────────────────────────────
def _ensure_textual() -> None:
    try:
        import textual  # noqa: F401
        return
    except ImportError:
        pass
    print("textual 未安装，自动安装中 ...")
    idx = ["--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    attempts = [
        # PEP 668 (Debian/Ubuntu 23+): --user + --break-system-packages
        [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", "textual"],
        [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", *idx, "textual"],
        # 老 Python (无 PEP 668)
        [sys.executable, "-m", "pip", "install", "--user", "textual"],
        [sys.executable, "-m", "pip", "install", *idx, "textual"],
    ]
    ok = False
    for a in attempts:
        if subprocess.run(a).returncode == 0:
            ok = True
            break
    if not ok:
        sys.exit("textual 装不上。手工: " + sys.executable
                 + " -m pip install --user --break-system-packages textual")
    import importlib
    importlib.invalidate_caches()
    try:
        import textual  # noqa: F401
    except ImportError:
        sys.exit("textual 装好了但 import 不到（--user 装到了别处?）。"
                 f"重跑: {sys.executable} {os.path.basename(__file__)}")
    print("textual 安装完成。")


_ensure_textual()

from textual.app import App, ComposeResult  # noqa: E402
from textual.containers import Horizontal, Vertical  # noqa: E402
from textual.widgets import (  # noqa: E402
    Button, Checkbox, Footer, Input, Label, RichLog, Static,
    TabbedContent, TabPane)

LOG_DIR = Path.home() / ".t153-build-tui"
LOG_DIR.mkdir(exist_ok=True)
PROFILE_PATH = LOG_DIR / "profile.json"
_RE_MARKUP = re.compile(r"\[/?[a-zA-Z#0-9 ()=,]+\]")


# ── .125 上 T153 BSP 的既定默认路径 (UI 可改 + profile 持久) ─────────
def _default_checkout() -> str:
    if (Path(__file__).resolve().parent / "configure.ac").exists():
        return str(Path(__file__).resolve().parent)
    for cand in ("~/T153/ethercat-1.7", "~/T153/ethercat-1.6"):
        if (Path(os.path.expanduser(cand)) / "configure.ac").exists():
            return os.path.expanduser(cand)
    return str(Path(__file__).resolve().parent)


DEFAULTS = {
    "checkout": _default_checkout(),
    "kdir": "~/T153/T153_Linux_V1.0/kernel/linux-5.10-rt",
    "bout": "~/T153/T153_Linux_V1.0/out/t153/kernel/build",
    "tc": "~/T153/T153_Linux_V1.0/out/toolchain/"
          "gcc-linaro-11.3.1-2022.06-x86_64_arm-linux-gnueabihf",
    "out": "~/T153/igh-build-OUT",
    "board": "192.168.4.85",
    "host": "arm-linux-gnueabihf",
    "stmmac_kernel": "5.10",
    "jobs": "16",
    "extra": "",
    "enable_stmmac": True,
    "enable_generic": True,
    "enable_igc": False,
    "enable_8139too": False,
    "enable_e100": False,
    "enable_e1000": False,
    "enable_e1000e": False,
    "enable_r8169": False,
    "enable_genet": False,
    "enable_ccat": False,
    "enable_eoe": False,
    "disable_examples": True,
    "package_on_full": True,
}


def _xp(p: str) -> str:
    return os.path.expanduser(p or "")


# ── 全志支持矩阵 ────────────────────────────────────────────────────
SUNXI_MATRIX = [
    ("T153 (sun55i)", "BSP sunxi-stmmac 顶层", "—", "★ 实测台架 PASS (WKC 60s wc_bad=0)"),
    ("A20 (sun7i)",   "dwmac-sunxi",   "sun7i-a20-gmac",    "源码已带, 未实测"),
    ("H3 (sun8i)",    "dwmac-sun8i",   "sun8i-h3-emac",     "源码已带, 未实测"),
    ("V3s (sun8i)",   "dwmac-sun8i",   "sun8i-v3s-emac",    "源码已带, 未实测"),
    ("A83T (sun8i)",  "dwmac-sun8i",   "sun8i-a83t-emac",   "源码已带, 未实测"),
    ("R40 (sun8i)",   "dwmac-sun8i",   "sun8i-r40-gmac",    "源码已带, 未实测"),
    ("A64 (sun50i)",  "dwmac-sun8i",   "sun50i-a64-emac",   "源码已带, 未实测"),
    ("H6 (sun50i)",   "dwmac-sun8i",   "sun50i-h6-emac",    "源码已带, 未实测"),
    ("通用 stmmac",   "dwmac-generic", "snps,dwmac*/dwxgmac", "源码已带, 兜底"),
]

T153_MODULES = ["stmmac", "stmmac-platform", "dwmac-sunxi",
                "dwmac-sun8i", "dwmac-generic", "ec_master", "ec_generic"]


# ── 检测助手 ────────────────────────────────────────────────────────
def detect_kernel(bout: str) -> str:
    bout = _xp(bout)
    uts = Path(bout) / "include" / "generated" / "utsrelease.h"
    if uts.exists():
        m = re.search(r'#define\s+UTS_RELEASE\s+"([^"]+)"',
                      uts.read_text(errors="replace"))
        if m:
            return m.group(1)
    cfg = Path(bout) / ".config"
    if cfg.exists():
        m = re.search(r'^CONFIG_LOCALVERSION="([^"]*)"',
                      cfg.read_text(errors="replace"), re.M)
        if m:
            return "(localversion) " + m.group(1)
    return "未知 (BOUT 无 utsrelease.h / .config?)"


def detect_glues(checkout: str, kernel: str) -> list[str]:
    d = Path(_xp(checkout)) / "devices" / "stmmac"
    if not d.exists():
        return []
    found = set()
    pat = re.compile(r"(.+)-" + re.escape(kernel) + r"-(?:orig|ethercat)\.(c|h)$")
    for f in d.iterdir():
        m = pat.match(f.name)
        if m:
            found.add(m.group(1))
    return sorted(found)


def detect_deb_script(checkout: str) -> str | None:
    c = Path(_xp(checkout))
    for name in ("build-t153-deb.sh", "build-armhf-deb.sh"):
        if (c / name).exists():
            return str(c / name)
    return None


class CopyLog(RichLog):
    """RichLog + 右键复制全部日志 (读 app.log_lines)."""

    def on_mouse_down(self, event) -> None:  # noqa: N802
        if getattr(event, "button", 1) == 3:
            self._copy("\n".join(getattr(self.app, "log_lines", [])))

    @staticmethod
    def _copy(text: str) -> None:
        try:
            if sys.platform == "win32":
                subprocess.run(["clip.exe"], input=text, text=True, check=False)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text, text=True, check=False)
            else:
                for tool in (["xclip", "-selection", "clipboard"],
                             ["xsel", "--clipboard", "--input"]):
                    if shutil.which(tool[0]):
                        subprocess.run(tool, input=text, text=True, check=False)
                        break
        except OSError:
            pass


class PathsTab(Static):
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("── 交叉编译路径 (.125 本机) ──", classes="sec")
            yield Input(placeholder="ethercat checkout 树 (任意: sncn13/1.6/1.7)",
                        id="p-checkout")
            yield Input(placeholder="KDIR 内核源码 (linux-5.10-rt)", id="p-kdir")
            yield Input(placeholder="BOUT 内核构建目录 O= (含 .config / 交叉配置)",
                        id="p-bout")
            yield Input(placeholder="toolchain 前缀 (.../bin 的父目录)", id="p-tc")
            yield Input(placeholder="OUT 产物目录 (userspace/ + modules/)", id="p-out")
            yield Label("── 板端 (仅部署需要) ──", classes="sec")
            yield Input(placeholder="板 IP (Lume T153)", id="p-board")
            with Horizontal(classes="btnrow"):
                yield Button("存 profile", id="save-profile")
                yield Button("读 profile", id="load-profile")
                yield Button("体检路径", id="doctor")

    def read(self, app: "IghBuildApp") -> dict:
        g = lambda i: app.query_one(f"#{i}", Input).value.strip()  # noqa: E731
        return {"checkout": g("p-checkout"), "kdir": g("p-kdir"),
                "bout": g("p-bout"), "tc": g("p-tc"), "out": g("p-out"),
                "board": g("p-board")}


class OptionsTab(Static):
    DRIVERS = [
        ("enable_stmmac", "T153 / 全志 native stmmac (本 TUI 主角)"),
        ("enable_generic", "ec_generic (通用 EC 设备)"),
        ("enable_igc", "Intel igc (PCI, 普通平台)"),
        ("enable_8139too", "Realtek 8139too"),
        ("enable_e100", "Intel e100"),
        ("enable_e1000", "Intel e1000"),
        ("enable_e1000e", "Intel e1000e"),
        ("enable_r8169", "Realtek r8169"),
        ("enable_genet", "broadcom genet"),
        ("enable_ccat", "ccat"),
        ("enable_eoe", "EOE (EtherCAT over Ethernet)"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("── configure 驱动开关 ──", classes="sec")
            for key, desc in self.DRIVERS:
                yield Checkbox(desc, id=f"opt-{key}")
            yield Label("── 参数 ──", classes="sec")
            yield Input(placeholder="host 三元组 (arm-linux-gnueabihf)", id="o-host")
            yield Input(placeholder="stmmac-kernel (5.10)", id="o-stmmac-kernel")
            yield Input(placeholder="并行度 -j (16)", id="o-jobs")
            yield Input(placeholder="额外 configure 参数 (空格分隔, 可空)", id="o-extra")
            yield Checkbox("关闭 examples (T153 建议勾选)", id="opt-disable_examples")
            yield Checkbox("一键全编后自动打 deb", id="opt-package_on_full")
            yield Label("── 将执行的 configure 命令 ──", classes="sec")
            yield Label("", id="o-cmd", classes="cmdview")

    def read(self, app: "IghBuildApp") -> dict:
        d: dict = {key: app.query_one(f"#opt-{key}", Checkbox).value
                   for key, _ in self.DRIVERS}
        d["disable_examples"] = app.query_one("#opt-disable_examples", Checkbox).value
        d["package_on_full"] = app.query_one("#opt-package_on_full", Checkbox).value
        g = lambda i: app.query_one(f"#{i}", Input).value.strip()  # noqa: E731
        d["host"] = g("o-host") or "arm-linux-gnueabihf"
        d["stmmac_kernel"] = g("o-stmmac-kernel") or "5.10"
        d["jobs"] = g("o-jobs") or "16"
        d["extra"] = g("o-extra")
        return d

    def show_cmd(self, app: "IghBuildApp") -> None:
        app.query_one("#o-cmd", Label).update("$ " + " ".join(app.configure_argv()))


class SunxiTab(Static):
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("── 全志芯片支持矩阵 ──", classes="sec")
            yield Static("", id="s-matrix")
            yield Label("── 实时检测 (当前 checkout + BOUT) ──", classes="sec")
            with Horizontal(classes="btnrow"):
                yield Button("重新检测", id="s-refresh", variant="primary")
            yield Static("", id="s-detect")

    def render_matrix(self) -> str:
        lines = ["[bold]芯片[/]        [bold]驱动路径[/]                "
                 "[bold]DT compatible[/]        [bold]状态[/]", "─" * 84]
        for chip, path, comp, status in SUNXI_MATRIX:
            lines.append(f"{chip:<13}  {path:<22}  {comp:<22}  {status}")
        return "\n".join(lines)

    def run_detect(self, app: "IghBuildApp") -> None:
        p = app.paths()
        sk = app.opts().get("stmmac_kernel", "5.10")
        k = detect_kernel(p["bout"])
        glues = detect_glues(p["checkout"], sk)
        deb = detect_deb_script(p["checkout"])
        det = [f"目标内核 (BOUT): [bold]{k}[/]",
               f"本树可用 stmmac 模块 ({sk} 文件对):"]
        det.append("  " + (", ".join(f"[green]{g}[/]" for g in glues)
                           if glues else
                           "[red]未检测到 -%s-orig/ethercat 文件对 (checkout 路径对吗?)[/]"
                           % sk))
        det.append(f"将构建的 T153 模块集 (7 件): {', '.join(T153_MODULES)}")
        det.append(f"deb 打包脚本: {deb or '[red]无 (本树需先放 build-t153-deb.sh)[/]'}")
        det.append(f"内核闸门: 预编译 .ko 绑定 {k}; 换内核 = 换 BOUT/KDIR 重编")
        app.query_one("#s-detect", Static).update("\n".join(det))


class BuildTab(Static):
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("── 交叉编译 (本机, 安全) ──", classes="sec")
            with Horizontal(classes="btnrow"):
                yield Button("① configure", id="b-configure")
                yield Button("② userspace", id="b-userspace")
                yield Button("③ kbuild 模块", id="b-kbuild")
                yield Button("④ 打 deb", id="b-deb")
            with Horizontal(classes="btnrow"):
                yield Button("★ 一键全编 ①→④", id="b-full", variant="primary")
            yield Label("── 板端 (危险: 停 mics + reboot) ──", classes="sec")
            with Horizontal(classes="btnrow"):
                yield Button("部署 7 模块到板", id="b-deploy", variant="warning")
            yield Label("需板 IP + ~/lume-creds.env + .125 上的 deploy7.sh。", id="b-warn")
            yield Label("纪律: 单会话串行 / 停 mics 再动 end0 / pkill 用 pat[t]ern / "
                        "板 /tmp=tmpfs。", id="b-warn2")


class IghBuildApp(App):
    TITLE = "IgH 全志 (T153) 交叉编译"
    CSS = """
    Screen { background: $surface; }
    #status { height: 1; padding: 0 1; background: $boost; color: $text; }
    TabbedContent { height: 2fr; min-height: 12; }
    #log { height: 1fr; min-height: 8; border-top: heavy $primary-darken-2; }
    #bar-row { height: 3; padding: 0 1; align: right middle; }
    #bar-row Button { margin-left: 1; }
    TabPane { padding: 0; }
    .sec { margin-top: 1; color: $primary; text-style: bold; }
    .btnrow { height: auto; align: left middle; }
    .btnrow Button { margin: 0 1 1 0; min-width: 18; }
    #b-full { min-width: 22; }
    PathsTab Input, OptionsTab Input { height: 3; margin-bottom: 1; }
    OptionsTab .cmdview { height: auto; min-height: 3; width: 100%;
        background: $surface; border: round $primary-darken-2; padding: 0 1; }
    SunxiTab, BuildTab, PathsTab, OptionsTab { padding: 0 1; }
    """

    BINDINGS = [("q", "quit", "退出")]

    def __init__(self) -> None:
        super().__init__()
        self.proc: asyncio.subprocess.Process | None = None
        self.log_lines: list[str] = []
        self.log_path = LOG_DIR / f"t153build-{time.strftime('%Y%m%d-%H%M%S')}.log"
        self._profile = dict(DEFAULTS)
        if PROFILE_PATH.exists():
            try:
                self._profile.update(json.loads(PROFILE_PATH.read_text()))
            except (json.JSONDecodeError, OSError):
                pass

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        with TabbedContent(initial="tab-paths"):
            with TabPane("路径", id="tab-paths"):
                yield PathsTab()
            with TabPane("选项", id="tab-options"):
                yield OptionsTab()
            with TabPane("全志支持", id="tab-sunxi"):
                yield SunxiTab()
            with TabPane("构建", id="tab-build"):
                yield BuildTab()
        with Horizontal(id="bar-row"):
            yield Button("停止", id="stop", variant="error")
            yield Button("清屏", id="clear")
            yield Button("开日志", id="openlog")
        yield CopyLog(id="log", wrap=True, markup=True, auto_scroll=True)
        yield Footer()

    # ── 数据访问 ──
    def paths(self) -> dict:
        return self.query_one(PathsTab).read(self)

    def opts(self) -> dict:
        return self.query_one(OptionsTab).read(self)

    def configure_argv(self) -> list[str]:
        p, o = self.paths(), self.opts()
        a = ["./configure", "--host", o["host"],
             "--prefix", f"{_xp(p['out'])}/userspace",
             "--with-linux-dir", _xp(p["bout"])]
        if o["enable_stmmac"]:
            a += ["--enable-stmmac", "--with-stmmac-kernel", o["stmmac_kernel"]]
        for key in ("generic", "igc", "8139too", "e100", "e1000", "e1000e",
                    "r8169", "genet", "ccat", "eoe"):
            if o[f"enable_{key}"]:
                a.append(f"--enable-{key}")
        if o["disable_examples"]:
            a.append("--disable-examples")
        if o["extra"]:
            a += o["extra"].split()
        return a

    def cross_env(self) -> dict:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        tc = _xp(self.paths()["tc"])
        if tc and (Path(tc) / "bin").exists():
            env["PATH"] = f"{tc}/bin:" + env.get("PATH", "")
        return env

    # ── 日志 ──
    def log_line(self, line: str) -> None:
        self.query_one("#log", RichLog).write(line)
        self.log_lines.append(_RE_MARKUP.sub("", line))
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(_RE_MARKUP.sub("", line) + "\n")
        except OSError:
            pass

    def _busy(self, b: bool) -> None:
        self.query_one("#stop", Button).disabled = not b
        self.query_one("#status", Static).update(
            f" checkout: {self.paths().get('checkout','')}"
            + ("   [bold green]构建中…[/]" if b else ""))

    # ── 命令执行 (异步流式, 事件循环保持响应) ──
    async def run_cmd(self, argv: list[str], cwd: str, label: str = "") -> int:
        if self.proc is not None:
            self.log_line("[red]已有任务在跑, 先「停止」.[/]")
            return -1
        self.log_line(f"\n[bold cyan]══ {label or argv[0]} ══[/]")
        self.log_line("$ " + " ".join(argv))
        self._busy(True)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=self.cross_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL)
            assert self.proc.stdout is not None
            while True:
                raw = await self.proc.stdout.readline()
                if not raw:
                    break
                self.log_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            rc = await self.proc.wait()
            self.log_line(f"[{'green' if rc == 0 else 'red'}]══ 退出码 {rc} ══[/]")
            return rc
        except Exception as exc:  # noqa: BLE001
            self.log_line(f"[red]启动失败: {type(exc).__name__}: {exc}[/]")
            return -1
        finally:
            self.proc = None
            self._busy(False)

    # ── 构建步骤 ──
    async def step_configure(self) -> int:
        co = _xp(self.paths()["checkout"])
        if not (Path(co) / "configure").exists():
            self.log_line("[yellow]无 configure, 先 ./bootstrap ...[/]")
            if await self.run_cmd(["./bootstrap"], co, "bootstrap"):
                return -1
        return await self.run_cmd(self.configure_argv(), co, "configure")

    async def step_userspace(self) -> int:
        co = _xp(self.paths()["checkout"])
        j = self.opts()["jobs"]
        if await self.run_cmd(["make", f"-j{j}"], co, "make userspace"):
            return -1
        return await self.run_cmd(["make", "install"], co, "make install")

    async def step_kbuild(self) -> int:
        p = self.paths()
        co, kdir, bout = _xp(p["checkout"]), _xp(p["kdir"]), _xp(p["bout"])
        for d in (co, kdir, bout):
            if not Path(d).exists():
                self.log_line(f"[red]路径不存在: {d}[/]")
                return -1
        j = self.opts()["jobs"]
        if await self.run_cmd(["make", "-C", kdir, "O=" + bout, "M=" + co,
                               "modules", f"-j{j}"], co, "kbuild 模块"):
            return -1
        return self._collect_modules()

    def _collect_modules(self) -> int:
        p = self.paths()
        co = _xp(p["checkout"])
        moddir = Path(_xp(p["out"])) / "modules"
        moddir.mkdir(parents=True, exist_ok=True)
        kos = list(Path(co).rglob("*.ko"))
        for src in kos:
            shutil.copy2(src, moddir / src.name)
        self.log_line(f"[green]收集 {len(kos)} 个 .ko → {moddir}[/]")
        ok = True
        for m in T153_MODULES:
            has = (moddir / f"{m}.ko").exists()
            ok = ok and has
            self.log_line(f"  [{'green' if has else 'red'}]{'✓' if has else '缺'}[/] "
                          f"{m}.ko")
        return 0 if ok else 2

    async def step_deb(self) -> int:
        p = self.paths()
        co, out = _xp(p["checkout"]), _xp(p["out"])
        deb = detect_deb_script(co)
        if not deb:
            self.log_line("[red]本树无 build-t153-deb.sh / build-armhf-deb.sh[/]")
            return -1
        if not (Path(out) / "modules").exists():
            self.log_line("[yellow]OUT/modules 不存在 — 先跑 ③ kbuild 模块[/]")
        rc = await self.run_cmd(["bash", deb, co, out], co, "打 deb")
        self.log_line(f"[dim]产物: {co}/deb_armhf/ (或脚本内目录)[/]")
        return 0 if rc == 0 else -1

    def step_deploy(self) -> None:
        p = self.paths()
        if not p["board"]:
            self.log_line("[red]未设板 IP (路径页) — 部署跳过[/]")
            return
        self.log_line("[yellow]⚠ 板端部署: 停 mics + reboot. 单会话串行![/]")
        for cand in ("~/T153/deploy7.sh", "/tmp/deploy7.sh"):
            d = _xp(cand)
            if Path(d).exists():
                self.run_cmd(["bash", d, _xp(p["out"]), p["board"]],
                             _xp(p["out"]), "部署到板")
                return
        self.log_line("[red]未找到 deploy7.sh — 手工 scp 7 .ko + depmod + "
                      "stop mics + reboot[/]")

    async def full_build(self) -> None:
        co = _xp(self.paths()["checkout"])
        self.log_line(f"[bold]★ 一键全编 @ {co}[/]")
        if await self.step_configure():
            return
        if await self.step_userspace():
            return
        if await self.step_kbuild():
            return
        if self.opts().get("package_on_full", True):
            await self.step_deb()
        self.log_line("[green]══ 全编完成 ══[/]")

    # ── 启动填充 ──
    def _fill_inputs(self) -> None:
        pr = self._profile
        for wid, key in (("p-checkout", "checkout"), ("p-kdir", "kdir"),
                         ("p-bout", "bout"), ("p-tc", "tc"), ("p-out", "out"),
                         ("p-board", "board"), ("o-host", "host"),
                         ("o-stmmac-kernel", "stmmac_kernel"), ("o-jobs", "jobs"),
                         ("o-extra", "extra")):
            if pr.get(key):
                self.query_one(f"#{wid}", Input).value = str(pr[key])
        for key in ("enable_stmmac", "enable_generic", "enable_igc",
                    "enable_8139too", "enable_e100", "enable_e1000",
                    "enable_e1000e", "enable_r8169", "enable_genet", "enable_ccat",
                    "enable_eoe", "disable_examples", "package_on_full"):
            try:
                self.query_one(f"#opt-{key}", Checkbox).value = bool(pr.get(key))
            except Exception:
                pass
        self.query_one(OptionsTab).show_cmd(self)

    def _save_profile(self) -> None:
        self._profile.update(self.paths())
        self._profile.update(self.opts())
        PROFILE_PATH.write_text(
            json.dumps(self._profile, indent=2, ensure_ascii=False))
        self.log_line(f"[green]profile 已存 → {PROFILE_PATH}[/]")

    def _load_profile(self) -> None:
        if PROFILE_PATH.exists():
            self._profile = dict(DEFAULTS)
            self._profile.update(json.loads(PROFILE_PATH.read_text()))
            self._fill_inputs()
            self.log_line("[green]profile 已载入[/]")
        else:
            self.log_line("[yellow]无 profile 文件[/]")

    def _doctor(self) -> None:
        p = self.paths()
        self.log_line("[bold]路径体检:[/]")
        for name, path in (("checkout/configure.ac",
                            Path(_xp(p["checkout"])) / "configure.ac"),
                           ("KDIR/Makefile", Path(_xp(p["kdir"])) / "Makefile"),
                           ("BOUT/.config", Path(_xp(p["bout"])) / ".config"),
                           ("toolchain/bin", Path(_xp(p["tc"])) / "bin")):
            ok = path.exists()
            self.log_line(f"  [{'green' if ok else 'red'}]{'✓' if ok else '✗'}[/] "
                          f"{name}: {path}")
        self.log_line(f"  目标内核: {detect_kernel(p['bout'])}")

    def on_mount(self) -> None:
        self._fill_inputs()
        self.query_one("#s-matrix", Static).update(
            self.query_one(SunxiTab).render_matrix())
        self.query_one(SunxiTab).run_detect(self)
        self.log_line("[bold]IgH 全志 (T153) 交叉编译 TUI[/]")
        self.log_line(f"profile: {PROFILE_PATH}")
        self.log_line("流程: ① configure → ② userspace → ③ kbuild 模块 → ④ 打 deb")
        self.log_line("[dim]选项页改参数会实时刷新 configure 命令; "
                      "全志支持页看本树/本内核能编什么.[/]")

    # ── 事件 ──
    async def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: A003
        bid = event.button.id
        if bid == "save-profile":
            self._save_profile()
        elif bid == "load-profile":
            self._load_profile()
        elif bid == "doctor":
            self._doctor()
        elif bid == "s-refresh":
            self.query_one(SunxiTab).run_detect(self)
        elif bid == "b-configure":
            await self.step_configure()
        elif bid == "b-userspace":
            await self.step_userspace()
        elif bid == "b-kbuild":
            await self.step_kbuild()
        elif bid == "b-deb":
            await self.step_deb()
        elif bid == "b-full":
            await self.full_build()
        elif bid == "b-deploy":
            self.step_deploy()
        elif bid == "clear":
            self.query_one("#log", RichLog).clear()
        elif bid == "openlog":
            try:
                if sys.platform == "win32":
                    os.startfile(str(self.log_path))  # noqa: S606
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(self.log_path)])
                else:
                    subprocess.Popen(["xdg-open", str(self.log_path)])
                self.log_line(f"[dim]日志: {self.log_path}[/]")
            except OSError as e:
                self.log_line(f"[red]打不开日志: {e}[/]")
        elif bid == "stop":
            if self.proc is not None:
                try:
                    self.proc.terminate()
                except ProcessLookupError:
                    pass
                self.log_line("[red]已发送终止 (SIGTERM).[/]")

    def on_input_changed(self, event) -> None:  # noqa: A003
        try:
            self.query_one(OptionsTab).show_cmd(self)
            self.query_one(SunxiTab).run_detect(self)
        except Exception:
            pass


async def _selftest() -> int:
    """--selftest: headless 自检 (不真编译), 验 compose/检测/configure 预览/输入刷新。
    环境无关 — 在 Windows 或 .125 都过, 只需 textual 装好。"""
    from textual.widgets import Label, Static, Input
    app = IghBuildApp()
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.pause(0.3)
        checks: list[tuple[bool, str]] = []
        cmd = str(app.query_one("#o-cmd", Label).content)
        checks.append(("--host" in cmd and "--with-linux-dir" in cmd,
                       "configure 预览已拼 (--host/--with-linux-dir)"))
        det = str(app.query_one("#s-detect", Static).content)
        checks.append(("目标内核" in det, "全志检测页已填充"))
        mat = str(app.query_one("#s-matrix", Static).content)
        checks.append(("T153" in mat and "H3" in mat, "全志支持矩阵渲染"))
        checks.append((any("TUI" in l for l in app.log_lines), "日志 banner 写入"))
        app.query_one("#o-host", Input).value = "test-host-xyz"
        await pilot.pause(0.1)
        new = str(app.query_one("#o-cmd", Label).content)
        checks.append(("test-host-xyz" in new, "输入变更 → 命令实时刷新"))
        ok = True
        for passed, name in checks:
            print(f"  {'PASS' if passed else 'FAIL'}  {name}")
            ok = ok and passed
        return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(asyncio.run(_selftest()))
    IghBuildApp().run()
