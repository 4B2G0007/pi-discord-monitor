import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import discord
from config import Config
from monitor import flags, sample, severity, embed
from bot import MonitorBot


def config():
    return Config('', 123, 456)


def data():
    return dict(temperature=45, cpu=25, memory=SimpleNamespace(total=8*2**30, available=4*2**30, percent=50),
                disk=SimpleNamespace(total=64*2**30, used=16*2**30, percent=25), boot=1700000000,
                flags=0, at=datetime.now(timezone.utc))

class SamplingTests(unittest.TestCase):
    def test_flags_current_only(self):
        for raw, expected in [('0x50000',0), ('0x50005',5), ('0x80008',8), ('0x2',2)]:
            with patch('monitor.vc', return_value='throttled='+raw):
                self.assertEqual(flags(), expected)
    def test_bad_flags(self):
        with patch('monitor.vc', return_value='error'):
            self.assertRaises(ValueError, flags)
    def test_partial_failure_is_not_zero(self):
        with patch('monitor.temperature', side_effect=OSError), patch('monitor.flags', side_effect=OSError):
            d=sample('/')
        self.assertIsNone(d['temperature'])
        self.assertIsNone(d['flags'])
        self.assertIsNotNone(d['memory'])
    def test_severity(self):
        d=data()
        self.assertEqual(severity(d)[1],'正常')
        for temp, expected in [(70,'注意'), (80,'警示')]:
            d['temperature']=temp
            self.assertEqual(severity(d)[1],expected)
        d['temperature']=None
        self.assertEqual(severity(d)[1],'注意')
        for mask, expected in [(1,'警示'),(4,'警示'),(2,'注意'),(8,'注意')]:
            d['flags']=mask
            self.assertEqual(severity(d)[1], expected)
    def test_embed_missing_and_time(self):
        d=data(); d['temperature']=None
        e=embed(d,config()).to_dict()
        self.assertIn('無法取得',e['fields'][0]['value'])
        self.assertIn('台北時間',e['footer']['text'])
        self.assertIn('部分資料',e['description'])
    def test_state_roundtrip_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            c=config(); c.state=Path(directory)/'panel.json'; c.save_panel(789)
            self.assertEqual(c.state.stat().st_mode & 0o777,0o600)
            env={'DISCORD_BOT_TOKEN':'test-only', 'DISCORD_GUILD_ID':'123','DISCORD_CHANNEL_ID':'456', 'PANEL_STATE_FILE':str(c.state)}
            with patch.dict(os.environ,env,clear=True):
                self.assertEqual(Config.from_env().panel,789)
                os.environ['DISCORD_CHANNEL_ID']='457'
                self.assertRaises(ValueError,Config.from_env)
    def test_invalid_config(self):
        env={'DISCORD_BOT_TOKEN':'test-only', 'DISCORD_GUILD_ID':'123','DISCORD_CHANNEL_ID':'456'}
        for key,value in [('PANEL_UPDATE_SECONDS','nan'),('PANEL_UPDATE_SECONDS','2'),('TEMP_HIGH_C','60'),('TIMEZONE','No/Zone')]:
            with patch.dict(os.environ,{**env,key:value},clear=True):
                self.assertRaises(ValueError,Config.from_env)

class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c=config(); self.tmp=tempfile.TemporaryDirectory(); self.c.state=Path(self.tmp.name)/'panel.json'
        self.b=MonitorBot(self.c); self.b._connection.user=SimpleNamespace(id=999)
        self.msg=SimpleNamespace(id=789,author=SimpleNamespace(id=999),edit=AsyncMock(),delete=AsyncMock())
        self.ch=SimpleNamespace(fetch_message=AsyncMock(return_value=self.msg),send=AsyncMock(return_value=self.msg))
        self.b.channel=AsyncMock(return_value=self.ch); self.b.reading=AsyncMock(return_value=discord.Embed(title='test'))
    async def asyncTearDown(self):
        self.tmp.cleanup()
    async def test_create_then_edit_concurrently(self):
        await asyncio.gather(self.b.update_panel(True), self.b.update_panel(True))
        self.ch.send.assert_awaited_once()
        self.msg.edit.assert_awaited_once()
        self.assertEqual(self.c.panel,789)
    async def test_missing_never_auto_creates(self):
        self.c.panel=789
        self.ch.fetch_message.side_effect=discord.NotFound(SimpleNamespace(status=404,reason='Not Found'), 'deleted')
        with self.assertRaises(discord.NotFound):
            await self.b.update_panel()
        self.ch.send.assert_not_awaited()
        self.assertTrue(self.b.missing)
        await self.b.update_panel(True)
        self.ch.send.assert_awaited_once()
    async def test_wrong_author(self):
        self.c.panel=789; self.msg.author.id=111
        with self.assertRaises(ValueError):
            await self.b.update_panel(True)
        self.ch.send.assert_not_awaited(); self.msg.edit.assert_not_awaited()
    async def test_write_failure_rolls_back(self):
        with patch.object(self.c,'save_panel',side_effect=OSError):
            with self.assertRaises(OSError):
                await self.b.update_panel(True)
        self.msg.delete.assert_awaited_once(); self.assertEqual(self.c.panel,0)
    async def test_authorization(self):
        for guild, channel, admin, required, expected in [(123,456,False,False,True),(123,456,False,True,False),(124,456,True,False,False),(123,457,True,True,False),(123,456,True,True,True)]:
            i=SimpleNamespace(guild_id=guild,channel_id=channel,permissions=SimpleNamespace(administrator=admin),response=SimpleNamespace(send_message=AsyncMock()))
            self.assertEqual(await self.b.authorize(i,required),expected)
    async def test_status_ephemeral(self):
        i=SimpleNamespace(guild_id=123,response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await self.b.status(i)
        self.assertTrue(i.response.defer.call_args.kwargs['ephemeral'])
        self.assertTrue(i.followup.send.call_args.kwargs['ephemeral'])
    async def test_guild_only_commands_and_intents(self):
        self.assertEqual(self.b.tree.get_commands(),[])
        group=self.b.tree.get_commands(guild=discord.Object(id=123))[0]
        self.assertEqual(len(group.commands),4)
        self.assertFalse(self.b.intents.message_content)
        self.assertFalse(self.b.intents.members)
    async def test_transient_failure_can_retry_same_message(self):
        self.c.panel=789
        self.msg.edit.side_effect=[discord.HTTPException(SimpleNamespace(status=503,reason='Unavailable'),'temporary'),None]
        with self.assertRaises(discord.HTTPException):
            await self.b.update_panel()
        await self.b.update_panel()
        self.assertEqual(self.msg.edit.await_count,2)
        self.ch.send.assert_not_awaited()

if __name__=='__main__':
    unittest.main()
