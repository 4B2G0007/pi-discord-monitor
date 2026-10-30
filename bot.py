import asyncio
import logging
import os
from pathlib import Path
import sys
import discord
from discord import app_commands
from dotenv import load_dotenv
from config import Config
from monitor import sample, embed

log = logging.getLogger('pi-monitor')

class MonitorBot(discord.Client):
    def __init__(self, config):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.lock = asyncio.Lock()
        self.sample_lock = asyncio.Lock()
        self.worker = None
        self.missing = False
        self.last_error = None
        group = app_commands.Group(name='pi', description='樹莓派唯讀即時監控', guild_only=True)
        group.command(name='status', description='立即採樣並私下顯示狀態')(self.status)
        group.command(name='help', description='指令與資料說明')(self.help_command)
        group.command(name='panel-create', description='管理員：建立或恢復固定面板')(self.panel_create)
        group.command(name='panel-refresh', description='管理員：立即更新固定面板')(self.panel_refresh)
        self.tree.add_command(group, guild=discord.Object(id=config.guild))
        self.tree.on_error = self.command_error

    async def setup_hook(self):
        await self.tree.sync(guild=discord.Object(id=self.config.guild))
        self.worker = asyncio.create_task(self.loop_panel())

    async def on_ready(self):
        # Never print identities, payloads or credentials.
        log.info('Discord 已連線。')

    async def authorize(self, interaction, admin=False):
        if interaction.guild_id != self.config.guild:
            await interaction.response.send_message('僅限指定私人伺服器。', ephemeral=True)
            return False
        if admin and (interaction.channel_id != self.config.channel or not interaction.permissions.administrator):
            await interaction.response.send_message('需要管理員權限，並在設定的監控頻道執行。', ephemeral=True)
            return False
        return True

    async def reading(self):
        async with self.sample_lock:
            return embed(await asyncio.to_thread(sample, self.config.disk), self.config)

    async def channel(self):
        ch = await self.fetch_channel(self.config.channel)
        if not isinstance(ch, discord.TextChannel) or ch.guild.id != self.config.guild:
            raise ValueError('channel scope')
        me = ch.guild.me
        if me is None:
            me = await ch.guild.fetch_member(self.user.id)
        p = ch.permissions_for(me)
        if not all((p.view_channel, p.send_messages, p.embed_links, p.read_message_history)):
            raise PermissionError('channel permissions')
        return ch

    async def update_panel(self, create=False):
        async with self.lock:
            ch = await self.channel()
            msg = None
            if self.config.panel:
                try:
                    msg = await ch.fetch_message(self.config.panel)
                except discord.NotFound:
                    self.missing = True
                    if not create:
                        raise
                if msg and msg.author.id != self.user.id:
                    raise ValueError('panel owner')
            if not msg and not create:
                raise ValueError('panel not created')
            current = await self.reading()
            if msg:
                await msg.edit(embed=current)
            else:
                msg = await ch.send(embed=current)
                try:
                    self.config.save_panel(msg.id)
                except Exception:
                    # Avoid accumulating a new panel on repeated state-write failure.
                    try:
                        await msg.delete()
                    except discord.HTTPException:
                        log.error('面板設定寫入失敗且無法清除新訊息，請手動刪除後再試。')
                    raise
            self.missing = False
            self.last_error = None

    async def status(self, interaction: discord.Interaction):
        if await self.authorize(interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.followup.send(embed=await self.reading(), ephemeral=True)

    async def help_command(self, interaction: discord.Interaction):
        if await self.authorize(interaction):
            await interaction.response.send_message(
                '/pi status：立即採樣，僅自己可見。\n/pi panel-create：管理員在指定頻道建立／恢復面板；既有訊息會沿用。\n'
                '/pi panel-refresh：管理員立即刷新。\n綠＝採樣時正常；黃＝部分缺值、注意溫度或限制旗標；紅＝目前欠壓／降頻／高溫。\n'
                '請先看最後更新時間；斷電後無法主動顯示離線。不保存監控歷史。', ephemeral=True)

    async def panel_create(self, interaction: discord.Interaction):
        if await self.authorize(interaction, admin=True):
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.update_panel(create=True)
            await interaction.followup.send('固定面板已就緒，後續編輯同一則訊息。', ephemeral=True)

    async def panel_refresh(self, interaction: discord.Interaction):
        if await self.authorize(interaction, admin=True):
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.update_panel()
            await interaction.followup.send('已重新採樣並更新固定面板。', ephemeral=True)

    async def command_error(self, interaction, error):
        # Do not log exception text: upstream errors may carry request information.
        log.error('指令失敗：%s', type(getattr(error, 'original', error)).__name__)
        message = '操作失敗：請檢查頻道權限與本機狀態檔；面板遺失請用 /pi panel-create 重建。'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def loop_panel(self):
        await self.wait_until_ready()
        # Fail startup clearly on channel scope/permission errors, including before panel creation.
        try:
            await self.channel()
        except (discord.Forbidden, discord.NotFound, PermissionError, ValueError):
            log.error('啟動失敗：指定頻道不存在、範圍錯誤或缺少必要權限。')
            await self.close()
            return
        except discord.HTTPException:
            pass  # transient server error; periodic operation retries
        while not self.is_closed():
            await self.wait_until_ready()
            if self.config.panel and not self.missing:
                try:
                    await self.update_panel()
                except Exception as exc:
                    name = type(exc).__name__
                    if name != self.last_error:
                        log.error('面板更新失敗：%s。若訊息被刪除，請用 /pi panel-create 重建。', name)
                        self.last_error = name
            await asyncio.sleep(self.config.interval)

    async def close(self):
        if self.worker and self.worker is not asyncio.current_task():
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await super().close()


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('discord').setLevel(logging.CRITICAL)
    env = Path('.env')
    if env.exists():
        if os.name == 'posix' and env.stat().st_mode & 0o077:
            log.error('設定檔權限過寬，請執行 chmod 600 .env。')
            return 2
        load_dotenv(env, override=False)
    try:
        config = Config.from_env()
        MonitorBot(config).run(config.token, log_handler=None)
    except KeyboardInterrupt:
        return 0
    except discord.LoginFailure:
        log.error('啟動失敗：Bot Token 無效，請在本機更新。')
        return 2
    except Exception as exc:
        log.error('啟動失敗：%s；請檢查設定、網路與 Discord 權限。', type(exc).__name__)
        return 2
    return 1  # unexpected client close; systemd may restart

if __name__ == '__main__':
    sys.exit(main())
