"""
数据库模块

使用 SQLite 存储历史指标数据
"""
import sqlite3
import time
from pathlib import Path
from typing import List

# 数据库路径
DB_PATH = Path(__file__).parent.parent / "data" / "metrics.db"


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """初始化数据库"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            cpu_usage REAL,
            memory_total INTEGER,
            memory_used INTEGER,
            memory_free INTEGER,
            memory_usage_percent REAL,
            swap_total INTEGER,
            swap_used INTEGER,
            swap_free INTEGER,
            swap_usage_percent REAL,
            disk_total INTEGER,
            disk_used INTEGER,
            disk_usage_percent REAL,
            network_rx_total INTEGER,
            network_tx_total INTEGER,
            network_rx_sec INTEGER,
            network_tx_sec INTEGER
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_metrics_timestamp
        ON metrics(timestamp)
    """)

    conn.commit()
    conn.close()


def save_metrics(metrics: dict):
    """保存指标数据"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO metrics (
            timestamp, cpu_usage,
            memory_total, memory_used, memory_free, memory_usage_percent,
            swap_total, swap_used, swap_free, swap_usage_percent,
            disk_total, disk_used, disk_usage_percent,
            network_rx_total, network_tx_total, network_rx_sec, network_tx_sec
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metrics["timestamp"],
        metrics["cpu"]["usage"],
        metrics["memory"]["total"],
        metrics["memory"]["used"],
        metrics["memory"]["free"],
        metrics["memory"]["usagePercent"],
        metrics["swap"]["total"],
        metrics["swap"]["used"],
        metrics["swap"]["free"],
        metrics["swap"]["usagePercent"],
        metrics["disk"]["total"],
        metrics["disk"]["used"],
        metrics["disk"]["usagePercent"],
        metrics["network"]["rxTotal"],
        metrics["network"]["txTotal"],
        metrics["network"]["rxPerSec"],
        metrics["network"]["txPerSec"]
    ))

    conn.commit()
    conn.close()


def get_history_metrics(hours: int = 24) -> List[dict]:
    """获取历史指标数据"""
    conn = get_connection()
    cursor = conn.cursor()

    since = int(time.time() * 1000) - hours * 60 * 60 * 1000

    cursor.execute("""
        SELECT * FROM metrics
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
    """, (since,))

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "timestamp": row["timestamp"],
            "cpu": {
                "usage": row["cpu_usage"]
            },
            "memory": {
                "total": row["memory_total"],
                "used": row["memory_used"],
                "free": row["memory_free"],
                "usagePercent": row["memory_usage_percent"]
            },
            "swap": {
                "total": row["swap_total"],
                "used": row["swap_used"],
                "free": row["swap_free"],
                "usagePercent": row["swap_usage_percent"]
            },
            "disk": {
                "total": row["disk_total"],
                "used": row["disk_used"],
                "usagePercent": row["disk_usage_percent"]
            },
            "network": {
                "rxTotal": row["network_rx_total"],
                "txTotal": row["network_tx_total"],
                "rxPerSec": row["network_rx_sec"],
                "txPerSec": row["network_tx_sec"]
            }
        }
        for row in rows
    ]


def clean_old_data(days: int = 7):
    """清理旧数据"""
    conn = get_connection()
    cursor = conn.cursor()

    cutoff = int(time.time() * 1000) - days * 24 * 60 * 60 * 1000

    cursor.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    if deleted > 0:
        print(f"[清理] 删除了 {deleted} 条过期数据")
