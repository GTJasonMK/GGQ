// 配置（从全局配置读取，空字符串表示使用相对路径）
const API_BASE = window.APP_CONFIG ? window.APP_CONFIG.getMonitorApi() : 'http://localhost:3001';
const REFRESH_INTERVAL = 5000; // 5秒刷新一次实时数据

// 图表实例
let historyChart = null;
let currentHours = 24;

// 格式化字节数
function formatBytes(bytes, decimals = 2) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
}

// 格式化网络速度
function formatSpeed(bytesPerSec) {
  if (bytesPerSec < 1024) {
    return { value: bytesPerSec.toFixed(0), unit: 'B/s' };
  } else if (bytesPerSec < 1024 * 1024) {
    return { value: (bytesPerSec / 1024).toFixed(1), unit: 'KB/s' };
  } else {
    return { value: (bytesPerSec / 1024 / 1024).toFixed(2), unit: 'MB/s' };
  }
}

// 获取进度条颜色类
function getProgressClass(percent) {
  if (percent < 60) return 'low';
  if (percent < 85) return 'medium';
  return 'high';
}

// 获取系统信息
async function fetchSystemInfo() {
  try {
    const response = await fetch(`${API_BASE}/api/system/info`);
    const info = await response.json();

    document.getElementById('systemInfo').innerHTML = `
      <div class="info-item">
        <span class="info-label">Hostname</span>
        <span class="info-value">${info.hostname}</span>
      </div>
      <div class="info-item">
        <span class="info-label">OS</span>
        <span class="info-value">${info.distro} ${info.release}</span>
      </div>
      <div class="info-item">
        <span class="info-label">CPU</span>
        <span class="info-value">${info.cpuModel} (${info.cpuCores} cores)</span>
      </div>
      <div class="info-item">
        <span class="info-label">Memory</span>
        <span class="info-value">${formatBytes(info.totalMemory)}</span>
      </div>
      <div class="info-item">
        <span class="info-label">Swap</span>
        <span class="info-value">${formatBytes(info.totalSwap)}</span>
      </div>
    `;
  } catch (error) {
    console.error('Failed to fetch system info:', error);
    document.getElementById('systemInfo').innerHTML = `
      <div class="loading">Failed to load system info</div>
    `;
  }
}

// 获取实时指标
async function fetchMetrics() {
  try {
    const response = await fetch(`${API_BASE}/api/metrics`);
    const metrics = await response.json();
    updateMetricsUI(metrics);
  } catch (error) {
    console.error('Failed to fetch metrics:', error);
  }
}

// 更新实时指标UI
function updateMetricsUI(metrics) {
  // 更新时间
  const now = new Date();
  document.getElementById('lastUpdate').textContent =
    `Updated ${now.toLocaleTimeString()}`;

  // CPU
  const cpuPercent = metrics.cpu.usage;
  document.getElementById('cpuValue').textContent = cpuPercent.toFixed(1);
  const cpuProgress = document.getElementById('cpuProgress');
  cpuProgress.style.width = `${cpuPercent}%`;
  cpuProgress.className = `progress-fill ${getProgressClass(cpuPercent)}`;

  // Memory
  const memPercent = metrics.memory.usagePercent;
  document.getElementById('memValue').textContent = memPercent.toFixed(1);
  const memProgress = document.getElementById('memProgress');
  memProgress.style.width = `${memPercent}%`;
  memProgress.className = `progress-fill ${getProgressClass(memPercent)}`;
  document.getElementById('memUsed').textContent = `${formatBytes(metrics.memory.used)} used`;
  document.getElementById('memTotal').textContent = `${formatBytes(metrics.memory.total)} total`;

  // Swap
  const swapPercent = metrics.swap.usagePercent;
  document.getElementById('swapValue').textContent = swapPercent.toFixed(1);
  const swapProgress = document.getElementById('swapProgress');
  swapProgress.style.width = `${swapPercent}%`;
  swapProgress.className = `progress-fill ${getProgressClass(swapPercent)}`;
  document.getElementById('swapUsed').textContent = `${formatBytes(metrics.swap.used)} used`;
  document.getElementById('swapTotal').textContent = `${formatBytes(metrics.swap.total)} total`;

  // Disk
  const diskPercent = metrics.disk.usagePercent;
  document.getElementById('diskValue').textContent = diskPercent.toFixed(1);
  const diskProgress = document.getElementById('diskProgress');
  diskProgress.style.width = `${diskPercent}%`;
  diskProgress.className = `progress-fill ${getProgressClass(diskPercent)}`;
  document.getElementById('diskUsed').textContent = `${formatBytes(metrics.disk.used)} used`;
  document.getElementById('diskTotal').textContent = `${formatBytes(metrics.disk.total)} total`;

  // Network
  const rxSpeed = formatSpeed(metrics.network.rxPerSec);
  const txSpeed = formatSpeed(metrics.network.txPerSec);
  document.getElementById('networkRx').textContent = rxSpeed.value;
  document.getElementById('networkRxUnit').textContent = rxSpeed.unit;
  document.getElementById('networkTx').textContent = txSpeed.value;
  document.getElementById('networkTxUnit').textContent = txSpeed.unit;
}

// 获取历史数据并渲染图表
async function fetchHistory(hours) {
  try {
    const response = await fetch(`${API_BASE}/api/metrics/history?hours=${hours}`);
    const history = await response.json();
    renderHistoryChart(history);
  } catch (error) {
    console.error('Failed to fetch history:', error);
  }
}

// 渲染历史图表
function renderHistoryChart(history) {
  const ctx = document.getElementById('historyChart').getContext('2d');

  // 准备数据
  const labels = history.map(item =>
    new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  );

  const cpuData = history.map(item => item.cpu.usage);
  const memData = history.map(item => item.memory.usagePercent);
  const swapData = history.map(item => item.swap.usagePercent);

  // 销毁旧图表
  if (historyChart) {
    historyChart.destroy();
  }

  // 创建新图表
  historyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'CPU',
          data: cpuData,
          borderColor: '#f0b429',
          backgroundColor: 'rgba(240, 180, 41, 0.1)',
          fill: true,
          tension: 0.4,
          pointRadius: 0
        },
        {
          label: 'Memory',
          data: memData,
          borderColor: '#3fb950',
          backgroundColor: 'rgba(63, 185, 80, 0.1)',
          fill: true,
          tension: 0.4,
          pointRadius: 0
        },
        {
          label: 'Swap',
          data: swapData,
          borderColor: '#d29922',
          backgroundColor: 'rgba(210, 153, 34, 0.1)',
          fill: true,
          tension: 0.4,
          pointRadius: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        intersect: false,
        mode: 'index'
      },
      plugins: {
        legend: {
          position: 'top',
          labels: {
            color: '#8b949e',
            usePointStyle: true,
            pointStyle: 'circle',
            padding: 20
          }
        },
        tooltip: {
          backgroundColor: '#161b22',
          titleColor: '#e6edf3',
          bodyColor: '#e6edf3',
          borderColor: '#30363d',
          borderWidth: 1,
          padding: 12,
          callbacks: {
            label: function(context) {
              return `${context.dataset.label}: ${context.parsed.y.toFixed(1)}%`;
            }
          }
        }
      },
      scales: {
        x: {
          grid: {
            color: '#21262d',
            drawBorder: false
          },
          ticks: {
            color: '#6e7681',
            maxTicksLimit: 12
          }
        },
        y: {
          min: 0,
          max: 100,
          grid: {
            color: '#21262d',
            drawBorder: false
          },
          ticks: {
            color: '#6e7681',
            callback: function(value) {
              return value + '%';
            }
          }
        }
      }
    }
  });
}

// 初始化时间选择器
function initTimeSelector() {
  const buttons = document.querySelectorAll('.time-btn');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentHours = parseInt(btn.dataset.hours);
      fetchHistory(currentHours);
    });
  });
}

// 初始化
async function init() {
  initTimeSelector();

  // 获取系统信息
  await fetchSystemInfo();

  // 获取初始数据
  await fetchMetrics();
  await fetchHistory(currentHours);

  // 定时刷新实时数据
  setInterval(fetchMetrics, REFRESH_INTERVAL);

  // 定时刷新历史图表（每分钟）
  setInterval(() => fetchHistory(currentHours), 60000);
}

// 启动
init();
