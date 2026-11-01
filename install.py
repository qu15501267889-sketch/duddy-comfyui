#!/usr/bin/env python3
"""duddy-comfyui 安装器 —— 零依赖，Windows / macOS / Linux 通用。

它做四件事：
  1. 探测 ComfyUI 安装位置（问后端 /system_stats 拿 argv，最准）
  2. 把本包装进 custom_nodes（优先目录联接/软链，改动即时生效）
  3. 往客户端的 MCP 配置里登记 duddy（自动识别 WorkBuddy / Claude Desktop）
  4. 打印「接下来做什么」

用法：
    python install.py                 # 全自动
    python install.py --dry-run       # 只看不动
    python install.py --no-mcp        # 只装 ComfyUI 侧
    python install.py --comfy "D:/AI/ComfyUI_windows_portable" --force
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_NAME = "comfyui_duddy"
LINK_NAME = "duddy-comfyui"
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")

IS_WIN = platform.system() == "Windows"


# --------------------------------------------------------------------- 输出

def say(msg: str = "") -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}", flush=True)


def bad(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}", flush=True)


def head(msg: str) -> None:
    print(f"\n\033[1m{msg}\033[0m", flush=True)


# ------------------------------------------------------------------ 探测

def http_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _as_comfy_dir(path: str) -> str:
    """把任意一个 ComfyUI 子目录往上溯，找到含 main.py 的那一层。"""
    if not path:
        return ""
    p = os.path.abspath(path.replace("\\", os.sep).replace("/", os.sep))
    for _ in range(6):  # 最多上溯 6 层，防止跑到盘根
        if os.path.isfile(os.path.join(p, "main.py")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return ""


def detect_from_api() -> dict:
    """最可靠：问运行中的 ComfyUI 要目录信息。

    /system_stats 的 argv 里 main.py 往往是相对路径，拿不到绝对位置；
    而 /internal/folder_paths 返回的全是绝对路径，从 models/ 反推最准。
    """
    out = {}

    # 路径一：folder_paths（首选）
    fp = http_json(COMFY_URL + "/internal/folder_paths")
    if isinstance(fp, dict):
        for _key, paths in fp.items():
            if not isinstance(paths, list):
                continue
            for p in paths:
                if not isinstance(p, str):
                    continue
                cand = _as_comfy_dir(p)
                if cand:
                    out["comfy_dir"] = cand
                    out["parent"] = os.path.dirname(cand)
                    return out

    # 路径二：argv（只在它是绝对路径时可信）
    info = http_json(COMFY_URL + "/system_stats")
    if info:
        argv = (info.get("system") or {}).get("argv") or []
        for a in argv:
            if not isinstance(a, str):
                continue
            norm = a.replace("\\", "/")
            if norm.endswith("main.py") and os.path.isabs(norm):
                cand = _as_comfy_dir(os.path.dirname(os.path.abspath(norm)))
                if cand:
                    out["comfy_dir"] = cand
                    out["parent"] = os.path.dirname(cand)
                    return out
    return out


def detect_fallback() -> dict:
    """兜底：常见安装路径。"""
    candidates = []
    if IS_WIN:
        drives = [f"{d}:\\" for d in "CDEFGH"]
        for d in drives:
            candidates += [
                os.path.join(d, "AI", "ComfyUI_windows_portable", "ComfyUI"),
                os.path.join(d, "ComfyUI_windows_portable", "ComfyUI"),
                os.path.join(d, "ComfyUI"),
                os.path.join(d, "AI", "ComfyUI"),
            ]
    else:
        home = os.path.expanduser("~")
        candidates += [
            os.path.join(home, "ComfyUI"),
            os.path.join(home, "comfy", "ComfyUI"),
            "/opt/ComfyUI",
        ]
    for c in candidates:
        if os.path.isfile(os.path.join(c, "main.py")):
            return {"comfy_dir": c, "parent": os.path.dirname(c)}
    return {}


def find_embedded_python(comfy_dir: str) -> str:
    parent = os.path.dirname(comfy_dir)
    cands = [
        os.path.join(parent, "python_embeded", "python.exe"),
        os.path.join(parent, "python_embedded", "python.exe"),
        os.path.join(comfy_dir, "venv", "Scripts", "python.exe"),
        os.path.join(comfy_dir, ".venv", "Scripts", "python.exe"),
        os.path.join(parent, "venv", "bin", "python"),
        os.path.join(comfy_dir, "venv", "bin", "python"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return sys.executable


# ---------------------------------------------------------------- 安装包

def install_package(comfy_dir: str, dry: bool, force: bool) -> bool:
    target = os.path.join(comfy_dir, "custom_nodes", LINK_NAME)
    src = os.path.join(HERE, PKG_NAME)

    if not os.path.isdir(src):
        bad(f"源码目录不存在：{src}")
        return False

    if os.path.exists(target):
        if force:
            say(f"    移除旧的 {target}")
            if not dry:
                remove_path(target)
        else:
            warn(f"目标已存在，跳过：{target}（加 --force 覆盖）")
            return True

    say(f"    {src}")
    say(f"    → {target}")
    if dry:
        return True

    try:
        if IS_WIN:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    f"New-Item -ItemType Junction -Path '{target}' -Target '{src}' | Out-Null",
                ],
                check=True, capture_output=True,
            )
            ok("已创建目录联接（改代码即时生效，无需重复复制）")
        else:
            os.symlink(src, target, target_is_directory=True)
            ok("已创建软链接")
        return True
    except Exception as e:
        warn(f"链接失败（{e}），改为复制")
        try:
            shutil.copytree(src, target)
            ok("已复制（改代码后需要重新安装）")
            return True
        except Exception as e2:
            bad(f"复制也失败了：{e2}")
            return False


def remove_path(path: str) -> None:
    """安全移除：软链/联接只删链接本身，不碰目标目录。"""
    try:
        if os.path.islink(path):
            os.unlink(path)
            return
        if hasattr(os.path, "isjunction") and getattr(os.path, "isjunction")(path):
            os.rmdir(path)
            return
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------- MCP 配置

MCP_ENTRY = {
    "command": None,  # 运行时填
    "args": [os.path.join(HERE, "mcp_server", "server.py")],
    "env": {"COMFY_URL": COMFY_URL},
}


def mcp_entry(python_exe: str) -> dict:
    e = dict(MCP_ENTRY)
    e["command"] = python_exe
    return e


def client_configs() -> list:
    """返回 [(名字, 配置文件路径, 格式)]，只处理 JSON 类，YAML 给指引。"""
    home = os.path.expanduser("~")
    out = []
    if IS_WIN:
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        out.append(("WorkBuddy", os.path.join(home, ".workbuddy", "mcp.json"), "mcpServers"))
        out.append((
            "Claude Desktop",
            os.path.join(appdata, "Claude", "claude_desktop_config.json"),
            "mcpServers",
        ))
    else:
        out.append(("WorkBuddy", os.path.join(home, ".workbuddy", "mcp.json"), "mcpServers"))
        out.append((
            "Claude Desktop",
            os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json"),
            "mcpServers",
        ))
    return out


def write_mcp_configs(python_exe: str, dry: bool) -> None:
    entry = mcp_entry(python_exe)
    for name, path, key in client_configs():
        d = os.path.dirname(path)
        existed = os.path.isfile(path)
        if not existed and not os.path.isdir(d):
            continue  # 没装这个客户端，不管
        say(f"    [{name}] {path}")
        if dry:
            continue
        data = {}
        if existed:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                warn(f"      现有配置不是合法 JSON，已跳过（请手动合并）")
                continue
        servers = data.get(key)
        if not isinstance(servers, dict):
            servers = {}
            data[key] = servers
        if "duddy" in servers:
            warn("      已存在 duddy 条目，已更新")
        servers["duddy"] = entry
        try:
            os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            ok(f"      已写入")
        except Exception as e:
            bad(f"      写入失败：{e}")


def hermes_snippet(python_exe: str) -> str:
    return (
        "  mcp_servers:\n"
        "    duddy:\n"
        f"      command: '{python_exe}'\n"
        f"      args: ['{os.path.join(HERE, 'mcp_server', 'server.py')}']\n"
        "      env:\n"
        f"        COMFY_URL: '{COMFY_URL}'\n"
    )


# ------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="安装 duddy-comfyui")
    ap.add_argument("--comfy", help="ComfyUI 根目录（含 main.py）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-mcp", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    say("\033[1m╭─────────────────────────────────────────────╮")
    say("│  duddy-comfyui 安装器                        │")
    say("│  AI 在你的画布上：先规划，你批准，再动手      │")
    say("╰─────────────────────────────────────────────╯\033[0m")

    head("[1/4] 定位 ComfyUI")
    info = {}
    if args.comfy:
        c = os.path.abspath(args.comfy)
        if os.path.isfile(os.path.join(c, "ComfyUI", "main.py")):
            c = os.path.join(c, "ComfyUI")
        info = {"comfy_dir": c, "parent": os.path.dirname(c)}
        ok(f"用户指定：{c}")
    else:
        info = detect_from_api()
        if info.get("comfy_dir"):
            ok(f"从运行中的 ComfyUI 探测到：{info['comfy_dir']}")
        else:
            info = detect_fallback()
            if info.get("comfy_dir"):
                ok(f"从常见路径探测到：{info['comfy_dir']}")
    if not info.get("comfy_dir"):
        bad("找不到 ComfyUI。请用 --comfy \"D:/你的/ComfyUI\" 指定")
        return 1

    comfy_dir = info["comfy_dir"]
    custom_nodes = os.path.join(comfy_dir, "custom_nodes")
    if not os.path.isdir(custom_nodes):
        bad(f"缺少 custom_nodes 目录：{custom_nodes}")
        return 1

    python_exe = find_embedded_python(comfy_dir)
    ok(f"Python：{python_exe}")

    head("[2/4] 安装扩展包")
    if not install_package(comfy_dir, args.dry_run, args.force):
        return 1

    head("[3/4] 注册 MCP")
    if args.no_mcp:
        warn("已按 --no-mcp 跳过")
    else:
        write_mcp_configs(python_exe, args.dry_run)

    head("[4/4] 下一步")
    say("  1. 重启 ComfyUI（自定义节点只在启动时扫描）")
    say("  2. 浏览器刷新页面（前端扩展也要重新加载）")
    say(f"  3. 验证：curl {COMFY_URL}/duddy/ping")
    say("")
    if not args.no_mcp:
        say("  Hermes 用户手动加这段到 ~/.hermes/config.yaml：")
        say(hermes_snippet(python_exe))
    say("  强烈建议：重启后在浏览器按 F12，控制台应出现")
    say("           [duddy] extension setup, clientId=...")
    say("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
