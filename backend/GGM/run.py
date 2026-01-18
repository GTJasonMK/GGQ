"""
启动脚本

功能：
- 启动前自动检测端口占用
- 支持清理占用端口的进程
- 支持命令行参数控制

用法：
  python run.py              # 正常启动，端口占用时询问是否清理
  python run.py -f           # 强制清理端口占用进程
  python run.py -p 9000      # 使用指定端口
  python run.py --no-clear   # 不清理端口，直接尝试启动
"""
import sys
import os
import json
import socket
import subprocess
import argparse
import time
import importlib.util
from pathlib import Path
from typing import List, Tuple, Optional

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 配置文件路径
CONFIG_FILE = project_root / "config.json"


def get_port_from_config() -> int:
    """从配置文件读取端口，支持环境变量覆盖"""
    # 环境变量优先
    env_port = os.environ.get("GGM_PORT") or os.environ.get("GEMINI_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass

    unified_port = get_port_from_unified_config()
    if unified_port is not None:
        return unified_port

    # 读取配置文件
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                return config.get("port", 8000)
        except Exception:
            pass

    return 8000


def get_port_from_unified_config() -> Optional[int]:
    """从统一配置读取端口"""
    config_path = project_root.parent / "config.py"
    if not config_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("unified_config", config_path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "GGM_PORT"):
            return int(getattr(module, "GGM_PORT"))
    except Exception:
        return None
    return None


def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def find_process_using_port_windows(port: int) -> List[Tuple[int, str]]:
    """Windows: 查找占用指定端口的进程"""
    processes = []
    seen_pids = set()

    try:
        # 使用 netstat 查找占用端口的 PID
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        for line in result.stdout.splitlines():
            # 匹配 LISTENING 或 ESTABLISHED 状态的连接
            if f":{port}" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        pid = int(parts[-1])
                        if pid > 0 and pid not in seen_pids:
                            seen_pids.add(pid)
                            proc_name = get_process_name_windows(pid)
                            processes.append((pid, proc_name))
                    except ValueError:
                        continue
    except Exception as e:
        print(f"查找进程时出错: {e}")

    return processes


def get_process_name_windows(pid: int) -> str:
    """Windows: 获取进程名称"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = result.stdout.strip()
        if output and not output.startswith("INFO:"):
            # 格式: "python.exe","1234","Console","1","123,456 K"
            parts = output.split(",")
            if parts:
                return parts[0].strip('"')
    except Exception:
        pass
    return "unknown"


def find_process_using_port_unix(port: int) -> List[Tuple[int, str]]:
    """Unix/Mac: 查找占用指定端口的进程"""
    processes = []
    seen_pids = set()

    try:
        # 使用 lsof 查找
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t"],
            capture_output=True,
            text=True
        )

        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    pid = int(line)
                    if pid not in seen_pids:
                        seen_pids.add(pid)
                        proc_name = get_process_name_unix(pid)
                        processes.append((pid, proc_name))
                except ValueError:
                    continue
    except FileNotFoundError:
        # lsof 不可用，尝试 ss
        try:
            result = subprocess.run(
                ["ss", "-tlnp", f"sport = :{port}"],
                capture_output=True,
                text=True
            )
            # 解析 ss 输出较复杂，这里简化处理
        except Exception:
            pass
    except Exception as e:
        print(f"查找进程时出错: {e}")

    return processes


def get_process_name_unix(pid: int) -> str:
    """Unix/Mac: 获取进程名称"""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def find_process_using_port(port: int) -> List[Tuple[int, str]]:
    """查找占用指定端口的进程（跨平台）"""
    if sys.platform == "win32":
        return find_process_using_port_windows(port)
    else:
        return find_process_using_port_unix(port)


def kill_process(pid: int) -> bool:
    """终止进程"""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return result.returncode == 0
        else:
            result = subprocess.run(
                ["kill", "-9", str(pid)],
                capture_output=True
            )
            return result.returncode == 0
    except Exception as e:
        print(f"终止进程 {pid} 时出错: {e}")
        return False


def clear_port(port: int, force: bool = False) -> bool:
    """
    清理占用端口的进程

    Args:
        port: 端口号
        force: 是否强制清理（不询问）

    Returns:
        True 如果端口已清理或本来就空闲
    """
    if not is_port_in_use(port):
        return True

    processes = find_process_using_port(port)

    if not processes:
        print(f"端口 {port} 被占用，但无法找到占用进程")
        print("可能需要管理员权限运行此脚本")
        return False

    print(f"\n端口 {port} 被以下进程占用:")
    for pid, name in processes:
        print(f"  - PID {pid}: {name}")

    if not force:
        try:
            response = input("\n是否终止这些进程? [y/N]: ").strip().lower()
            if response not in ("y", "yes"):
                print("已取消启动")
                return False
        except (EOFError, KeyboardInterrupt):
            print("\n已取消启动")
            return False

    # 终止所有占用进程
    all_killed = True
    for pid, name in processes:
        if kill_process(pid):
            print(f"已终止进程 PID {pid} ({name})")
        else:
            print(f"无法终止进程 PID {pid} ({name})")
            all_killed = False

    if not all_killed:
        print("部分进程无法终止，可能需要管理员权限")
        return False

    # 等待端口释放
    print("等待端口释放...")
    for _ in range(10):
        time.sleep(0.3)
        if not is_port_in_use(port):
            print(f"端口 {port} 已清理完成\n")
            return True

    print(f"警告: 端口 {port} 仍然被占用")
    return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Gemini Business API 代理服务启动脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run.py              # 正常启动
  python run.py -f           # 强制清理端口
  python run.py -p 9000      # 使用端口 9000
  python run.py -f -p 9000   # 强制清理并使用端口 9000
        """
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="强制清理占用端口的进程（不询问确认）"
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        help="指定服务端口（覆盖配置文件）"
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="不清理端口，直接尝试启动（端口占用时会报错）"
    )
    args = parser.parse_args()

    # 确定使用的端口
    port = args.port if args.port else get_port_from_config()

    print(f"Gemini Business API 代理服务")
    print(f"服务地址: http://127.0.0.1:{port}")

    # 检查并清理端口
    if not args.no_clear:
        if is_port_in_use(port):
            print(f"检测到端口 {port} 被占用")
            if not clear_port(port, force=args.force):
                sys.exit(1)
        else:
            print(f"端口 {port} 可用")

    # 如果命令行指定了端口，设置环境变量让 app 使用
    if args.port:
        os.environ["GEMINI_PORT"] = str(args.port)

    print("正在启动服务...\n")

    # 启动服务
    from app.main import main as app_main
    app_main()


if __name__ == "__main__":
    main()
