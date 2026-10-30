"""Read-only system sampling and presentation; no monitoring history."""
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import discord
import psutil


def vc(command):
    return subprocess.run(['vcgencmd', command], capture_output=True, text=True,
                          timeout=3, check=True).stdout.strip()


def temperature():
    try:
        value = float(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
    except (OSError, ValueError):
        match = re.fullmatch(r"temp=([0-9.]+)'C", vc('measure_temp'))
        if not match:
            raise ValueError('temperature format')
        value = float(match[1])
    if not math.isfinite(value) or not -40 <= value <= 150:
        raise ValueError('temperature range')
    return value


def flags():
    match = re.fullmatch(r'throttled=(0x[0-9a-fA-F]+)', vc('get_throttled'))
    if not match:
        raise ValueError('flag format')
    return int(match[1], 16) & 0xF  # deliberately ignore historical bits


def sample(disk_path='/'):
    result = {}
    readers = {
        'temperature': temperature,
        'cpu': lambda: psutil.cpu_percent(interval=0.3),
        'memory': psutil.virtual_memory,
        'disk': lambda: psutil.disk_usage(disk_path),
        'boot': psutil.boot_time,
        'flags': flags,
    }
    for name, reader in readers.items():
        try:
            result[name] = reader()
        except Exception:
            result[name] = None
    result['at'] = datetime.now(timezone.utc)
    return result


def severity(data, warning=70, high=80):
    temp, power = data['temperature'], data['flags']
    if (temp is not None and temp >= high) or (power is not None and power & 5):
        return 0xED4245, '警示'
    if any(data[k] is None for k in ('temperature', 'cpu', 'memory', 'disk', 'boot', 'flags')) or (temp is not None and temp >= warning) or (power is not None and power & 10):
        return 0xFEE75C, '注意'
    return 0x57F287, '正常'


def embed(data, config):
    color, state = severity(data, config.warning, config.high)
    missing = any(data[k] is None for k in ('temperature', 'cpu', 'memory', 'disk', 'boot', 'flags'))
    e = discord.Embed(title='Raspberry Pi 5 即時狀態', color=color,
        description=f'採樣時狀態：**{state}**｜' + ('部分資料無法取得' if missing else '讀值完整') +
        '\n這是最後一次採樣；停止更新不代表仍在線。')
    def scalar(value, unit):
        return '無法取得' if value is None else f'{value:.1f} {unit}'
    e.add_field(name='處理器', value=f"溫度：{scalar(data['temperature'], '°C')}\n使用率：{scalar(data['cpu'], '%')}")
    for key, title in [('memory', '記憶體'), ('disk', '儲存空間')]:
        v = data[key]
        # RAM used matches percent: total minus available, rather than Linux cache accounting.
        used = (v.total - v.available if key == 'memory' else v.used) if v else 0
        value = '無法取得' if v is None else f'{used/2**30:.2f} / {v.total/2**30:.2f} GiB\n使用率：{v.percent:.1f} %'
        e.add_field(name=title, value=value)
    boot = data['boot']
    value = '無法取得'
    if boot is not None:
        started = datetime.fromtimestamp(boot, config.zone)
        seconds = max(0, int(data['at'].timestamp() - boot))
        value = f"開機：{started:%Y-%m-%d %H:%M:%S}\n運作：{seconds//86400} 天 {seconds//3600%24} 小時 {seconds//60%60} 分"
    e.add_field(name='系統', value=value, inline=False)
    bits = data['flags']
    e.add_field(name='目前供電／限制旗標', inline=False, value='\n'.join(
        f'{label}：' + ('無法取得' if bits is None else ('已回報' if bits & (1 << bit) else '未回報'))
        for bit, label in enumerate(('欠壓', '頻率受限', '降頻', '軟溫度限制'))))
    e.add_field(name='更新說明', value=f'預設每 {config.interval:g} 秒更新；超過 {config.interval*3:g} 秒未更新請視為過期。\n旗標只代表韌體回報；軟溫度限制是否支援依機型／韌體而定。', inline=False)
    label = '台北時間' if config.zone.key == 'Asia/Taipei' else config.zone.key
    e.set_footer(text=f"最後更新：{data['at'].astimezone(config.zone):%Y-%m-%d %H:%M:%S}（{label}）")
    return e
