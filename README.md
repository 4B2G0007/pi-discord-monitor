# 樹莓派 Discord 即時監控 V1.0

獨立、唯讀 Python Bot。每 30 秒編輯同一則面板；不載入 YOLO、不碰攝影機、不控制語音或震動，不保存監控歷史。不需要公開 IP、轉發連接埠或網頁伺服器。需要樹莓派可連線 Discord。

## 1. 先準備 Discord（在自己的帳號操作）

1. 開啟 https://discord.com/developers/applications ，按 New Application，取名 `Pi Monitor`。
2. 到 Bot 頁面建立／確認 Bot。關閉 Public Bot（若介面要求，先將預設安裝連結設為 None，再以 OAuth2 URL Generator 產生自己的邀請連結）。所有 Privileged Gateway Intents 保持關閉。
3. Bot 頁面用 Reset Token 取得 Token，只在本機填入 `.env`。不要提供給開發者、不要截圖、不要放 GitHub。如曾外洩，立即重設。
4. Installation 選 Guild Install；不需要 User Install。也可使用 OAuth2 → URL Generator，Scopes 勾 `bot`、`applications.commands`。
5. Bot Permissions 只選 View Channels、Send Messages、Embed Links、Read Message History。**不要選 Administrator。** `applications.commands` 是安裝 scope；頻道中的 Use Application Commands 權限需允許使用指令的成員使用，不是要求 Bot 有管理員權限。
6. 開啟生成的安裝連結，選自己的私人伺服器授權。操作帳號需可管理伺服器。
7. 建立私人文字頻道，例如 `樹莓派狀態`。讓 Bot 與指定成員可見；檢查頻道覆寫沒有拒絕 Bot 的四項必要權限。
8. Discord 使用者設定 → 進階 → 開啟開發者模式。右鍵伺服器複製 Server ID；右鍵監控頻道複製 Channel ID。

本程式採 Discord Gateway 主動連線；Developer Portal 的 Interactions Endpoint URL 留空，不需要照官方 HTTP 範例架設公開 endpoint。
只有指定 Guild 註冊 `/pi`。一般成員可用 status/help；管理指令在程式內強制檢查使用者 Administrator 權限及指定 Channel ID。管理子指令可能仍出現在一般成員選單，但執行會被拒絕。可額外在伺服器設定 → 整合 → Bot 的指令權限限制可用頻道。

## 2. 樹莓派安裝

目標：Raspberry Pi 5、Raspberry Pi OS、Python 3.10+（建議現有 Bookworm/Trixie 64 位元）。不需要重裝作業系統或變動 YOLO 環境。

將 ZIP 解壓縮，將裡面的 `pi-discord-monitor` 資料夾放在自己的家目錄，最終為 `~/pi-discord-monitor/bot.py`。

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip python3-dev gcc
cd ~/pi-discord-monitor
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
nano .env
```

填入前三個欄位，等號後不要加多餘空白：

```ini
DISCORD_BOT_TOKEN=在本機填入自己的BotToken
DISCORD_GUILD_ID=自己的伺服器ID
DISCORD_CHANNEL_ID=自己的文字頻道ID
```

其他保留預設，`PANEL_MESSAGE_ID` 留空。儲存：Ctrl+O、Enter；離開：Ctrl+X。

```bash
vcgencmd measure_temp
vcgencmd get_throttled
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python bot.py
```

如果 `vcgencmd` 不存在，可嘗試安裝 Raspberry Pi OS 的 `raspi-utils` 套件。若該版本無此套件，查詢系統套件庫的 vcgencmd 提供套件；不要為讀取旗標把整個 Bot 改成 root。讀值工具缺少／無權限時會顯示「無法取得」，其餘欄位繼續更新。

到指定 Discord 頻道：

1. 執行 `/pi status`，確認只有自己看到回覆。
2. 用管理員帳號執行 `/pi panel-create`，建立固定面板。
3. 等約 30 秒確認頁尾時間更新，訊息數量不增加。
4. Ctrl+C 停止前景程式，再設定下節開機服務。不要同時開兩份 Bot。

## 3. 開機自動啟動

先確認 `whoami` 顯示的帳號名稱；不要假設一定是 pi。

```bash
whoami
cd ~/pi-discord-monitor
cp pi-monitor.service.example pi-monitor.service
nano pi-monitor.service
```

將範本所有 `YOUR_USER` 換成實際使用者名稱。如果安裝位置不同，同時改 WorkingDirectory、EnvironmentFile、ExecStart 的完整路徑。User 必須是一般使用者。家目錄位置可用 `pwd` 確認。

```bash
sudo cp pi-monitor.service /etc/systemd/system/pi-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-monitor
sudo systemctl status pi-monitor --no-pager
```

常用指令：

```bash
sudo systemctl restart pi-monitor
sudo systemctl stop pi-monitor
sudo systemctl start pi-monitor
journalctl -u pi-monitor -n 50 --no-pager
```

持續啟動失敗會觸發 systemd 啟動次數限制。修正設定後：

```bash
sudo systemctl reset-failed pi-monitor
sudo systemctl start pi-monitor
```

`network-online.target` 只協調開機順序，不保證網際網路可用；暫時無網路由 Discord 客戶端重連及 systemd 重啟處理。API 暫時失敗會下個週期重試。重新連線後最遲約一個週期重新採樣，不補傳離線資料。

## 4. 面板與設定

| 設定 | 預設／意義 |
| --- | --- |
| DISCORD_BOT_TOKEN | 必填；只保留本機 |
| DISCORD_GUILD_ID | 必填；只在此伺服器註冊與執行 |
| DISCORD_CHANNEL_ID | 必填；固定面板與管理指令唯一頻道 |
| PANEL_MESSAGE_ID | 初次留空；可指定既有的本 Bot 訊息 ID |
| PANEL_STATE_FILE | state/panel.json；只保存 Guild、Channel、Message ID |
| PANEL_UPDATE_SECONDS | 30；允許 15–3600 秒 |
| TEMP_WARNING_C / TEMP_HIGH_C | 70 / 80；需注意值小於高溫值 |
| DISK_PATH | /；必須是存在的絕對路徑 |
| TIMEZONE | Asia/Taipei |

建立面板後以權限 600 原子寫入 `state/panel.json`；該檔的 Message ID 優先於環境設定。Token 不會寫入狀態檔。改 Guild 或 Channel 前先停止服務、移除舊面板訊息並刪除 `state/panel.json`，清空 `.env` 的 PANEL_MESSAGE_ID，再啟動並重新建立。這是設定資料，沒有時間序列或監控紀錄。

`/pi panel-create` 在原面板存在時沿用；被刪除才建立新訊息。管理員可先刪除原訊息再執行此指令。自動更新不會自行新增訊息。

溫度與 CPU 百分比、RAM／磁碟 GiB、開機時間及運作時間分別顯示。CPU 採樣約 0.3 秒，置於背景執行緒，避免阻塞 Discord。磁碟百分比採 psutil 定義，Linux 保留區塊可能造成與 used/total 直除稍有差異。

綠：讀值完整、目前旗標無異常且低於注意溫度。黃：資料缺失、溫度達注意值、頻率受限或軟溫度限制。紅：溫度達高溫值、目前欠壓或目前降頻。未知不等於 0。旗標只解讀 bit 0–3，忽略「曾發生」bit 16–19。軟溫度限制為韌體旗標，不宣稱 Pi 5 一定支援此機制。

訊息顏色代表採樣時狀態，**不表示現在一定在線**。預設超過 90 秒未更新請當作過期。突然斷電無法更新離線狀態，應看最後更新時間。

## 5. 排除問題

| 現象 | 檢查方式 |
| --- | --- |
| 沒有 /pi | 確認 Guild ID、安裝 applications.commands scope、Bot 已上線；重新開啟 Discord；指令只在指定伺服器出現 |
| 啟動 LoginFailure | 在 Portal 重設 Bot Token，更新本機 .env，重啟；不要貼 Token 求助 |
| Forbidden / PermissionError | 檢查私人頻道覆寫與四項 Bot 權限、Guild/Channel ID；不必授予 Bot 管理員 |
| 設定 ValueError | 檢查空欄、數字、溫度順序、路徑、時區、state/panel.json 是否屬於舊頻道 |
| 溫度／旗標無法取得 | 用相同一般使用者手動測試 vcgencmd；檢查 OS 工具與裝置讀取權限 |
| NotFound、面板不更新 | 面板可能被刪除；管理員在指定頻道執行 panel-create |
| state 寫入失敗 | 確認安裝目錄／state 可由服務 User 寫入；不要把專案整包設成 root 所有 |
| 斷線後停在舊時間 | 確認網路與 journalctl；服務被啟動限制擋住時 reset-failed |

程式只記錄必要啟動／失敗類型，不輸出 Token、完整 exception 內容或定期讀值。不要使用 shell 除錯 `set -x` 載入 Token。systemd/journald 可能保存服務錯誤日誌，這不屬於監控歷史。

## 6. 實機驗收

見 `TEST_REPORT.md`。目前完成本機模擬測試，尚未使用真實 Token、私人 Discord 或 Pi 5。第一次實機請檢查讀值、權限、斷線恢復、重開機沿用訊息，以及同時執行 YOLO 的效能差異。

## 官方參考

- Discord Bot 建立：https://docs.discord.com/developers/quick-start/getting-started
- Guild Application Commands：https://docs.discord.com/developers/interactions/application-commands
- discord.py 指令 API：https://discordpy.readthedocs.io/en/stable/interactions/api.html
- Raspberry Pi 韌體旗標：https://www.raspberrypi.com/documentation/computers/os.html#get_throttled
