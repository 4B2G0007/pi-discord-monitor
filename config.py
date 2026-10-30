import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

@dataclass
class Config:
    token: str = field(repr=False)
    guild: int
    channel: int
    interval: float = 30
    warning: float = 70
    high: float = 80
    disk: str = '/'
    zone: ZoneInfo = field(default_factory=lambda: ZoneInfo('Asia/Taipei'))
    state: Path = Path('state/panel.json')
    panel: int = 0

    @classmethod
    def from_env(cls):
        try:
            c = cls(os.environ['DISCORD_BOT_TOKEN'].strip(), int(os.environ['DISCORD_GUILD_ID']),
                int(os.environ['DISCORD_CHANNEL_ID']), float(os.getenv('PANEL_UPDATE_SECONDS', '30')),
                float(os.getenv('TEMP_WARNING_C', '70')), float(os.getenv('TEMP_HIGH_C', '80')),
                os.getenv('DISK_PATH', '/'), ZoneInfo(os.getenv('TIMEZONE', 'Asia/Taipei')),
                Path(os.getenv('PANEL_STATE_FILE', 'state/panel.json')),
                int(os.getenv('PANEL_MESSAGE_ID') or '0'))
            assert c.token and c.guild > 0 and c.channel > 0 and c.panel >= 0
            assert all(math.isfinite(x) for x in (c.interval, c.warning, c.high))
            assert 15 <= c.interval <= 3600 and 0 < c.warning < c.high <= 150
            assert Path(c.disk).is_absolute() and Path(c.disk).exists()
            if c.state.exists():
                state = json.loads(c.state.read_text())
                if state['guild'] != c.guild or state['channel'] != c.channel:
                    raise ValueError('state scope mismatch')
                c.panel = int(state['PANEL_MESSAGE_ID'])
                assert c.panel > 0
            return c
        except Exception:
            raise ValueError('設定無效：檢查 Token、ID、更新秒數、溫度、路徑、時區與面板狀態檔；不輸出設定值。') from None

    def save_panel(self, message_id):
        self.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.state.parent, prefix='.panel-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'guild': self.guild, 'channel': self.channel, 'PANEL_MESSAGE_ID': message_id}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state)
            self.panel = message_id
        finally:
            if os.path.exists(name):
                os.unlink(name)
