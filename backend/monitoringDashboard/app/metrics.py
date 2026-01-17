"""
系统指标采集模块

使用 psutil 采集 CPU、内存、Swap、磁盘、网络指标
"""
import time
import platform
import psutil


def collect_metrics() -> dict:
    """
    采集系统指标

    返回:
        dict: 包含 CPU、内存、Swap、磁盘、网络指标
    """
    # CPU 使用率
    cpu_percent = psutil.cpu_percent(interval=0.1)

    # 内存信息
    mem = psutil.virtual_memory()

    # Swap 信息
    swap = psutil.swap_memory()

    # 磁盘信息（取根分区或 C 盘）
    if platform.system() == "Windows":
        disk = psutil.disk_usage("C:\\")
    else:
        disk = psutil.disk_usage("/")

    # 网络信息
    net = psutil.net_io_counters()

    # 计算网络速率（需要两次采样）
    # 简化处理：这里只返回累计值，前端计算速率
    # 或者使用全局变量存储上次采样值

    return {
        "timestamp": int(time.time() * 1000),
        "cpu": {
            "usage": round(cpu_percent, 2)
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "free": mem.available,
            "usagePercent": round(mem.percent, 2)
        },
        "swap": {
            "total": swap.total,
            "used": swap.used,
            "free": swap.free,
            "usagePercent": round(swap.percent, 2) if swap.total > 0 else 0
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "usagePercent": round(disk.percent, 2),
            "mount": "C:\\" if platform.system() == "Windows" else "/"
        },
        "network": {
            "rxTotal": net.bytes_recv,
            "txTotal": net.bytes_sent,
            "rxPerSec": 0,  # 需要前端计算或存储上次值
            "txPerSec": 0
        }
    }


# 用于计算网络速率的全局变量
_last_net_io = None
_last_net_time = None


def collect_metrics_with_rate() -> dict:
    """
    采集系统指标（包含网络速率计算）
    """
    global _last_net_io, _last_net_time

    metrics = collect_metrics()

    # 计算网络速率
    current_net = psutil.net_io_counters()
    current_time = time.time()

    if _last_net_io is not None and _last_net_time is not None:
        time_diff = current_time - _last_net_time
        if time_diff > 0:
            rx_rate = (current_net.bytes_recv - _last_net_io.bytes_recv) / time_diff
            tx_rate = (current_net.bytes_sent - _last_net_io.bytes_sent) / time_diff
            metrics["network"]["rxPerSec"] = int(rx_rate)
            metrics["network"]["txPerSec"] = int(tx_rate)

    _last_net_io = current_net
    _last_net_time = current_time

    return metrics


def get_system_info() -> dict:
    """
    获取系统基本信息
    """
    uname = platform.uname()
    cpu_count = psutil.cpu_count(logical=True)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # 尝试获取 CPU 型号
    cpu_model = ""
    if platform.system() == "Windows":
        import subprocess
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "name"],
                capture_output=True,
                text=True,
                timeout=5
            )
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                cpu_model = lines[1].strip()
        except Exception:
            cpu_model = f"{uname.processor}"
    else:
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        cpu_model = line.split(":")[1].strip()
                        break
        except Exception:
            cpu_model = uname.processor or "Unknown"

    return {
        "hostname": uname.node,
        "platform": uname.system,
        "distro": f"{uname.system} {uname.release}",
        "release": uname.version,
        "arch": uname.machine,
        "cpuModel": cpu_model or "Unknown",
        "cpuCores": cpu_count,
        "cpuFreq": cpu_freq.current if cpu_freq else 0,
        "totalMemory": mem.total,
        "totalSwap": swap.total
    }
