import os
from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from google import genai
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI()

# ==================== 憑證設定 ====================
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Google Sheets 設定
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
CREDS = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREADSHEET_KEY = '1Re3JZ41CB5_a9UrGdru0HfWxMCvoaRpYLfAwSDRgJxI'
# ====================================================

# 初始化 LINE 與 Gemini 客戶端
handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)


def get_sheets_summary():
    """自動讀取 Google 試算表所有分頁的資料，整理成文字摘要給 Gemini 參考"""
    try:
        spreadsheet = GC.open_by_key(SPREADSHEET_KEY)
        worksheets = spreadsheet.worksheets()
        
        summary_lines = []
        for ws in worksheets:
            rows = ws.get_all_values()
            if len(rows) <= 1:
                continue
            summary_lines.append(f"--- 分頁: {ws.title} ---")
            for row in rows[1:]:
                if len(row) < 11:
                    continue
                status = "已成交" if str(row[0]).upper() == "TRUE" else "未成交/詢價中"
                line = f"狀態: {status} | 日期: {row[1]} | 客戶: {row[2]} {row[3]} | 規格: {row[4]} | 廠牌: {row[5]} | 數量: {row[6]} | 成本: {row[7]} | 報價: {row[8]} | 折數: {row[9]} | 備註: {row[10]}"
                summary_lines.append(line)
                
        return "\n".join(summary_lines)
    except Exception as e:
        return f"讀取試算表發生錯誤: {str(e)}"


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")

    try:
        await handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    
    # 1. 每次收到訊息時，動態讀取最新的 Google 試算表內容
    sheets_data = get_sheets_summary()
    
    # 2. 設定 Gemini 的系統提示詞，把試算表內容作為背景知識
    system_prompt = f"""
你是一個專業的業務詢價單與訂單管理 AI 助理。
以下是目前從 Google 試算表同步過來的即時資料：
{sheets_data}

請嚴格根據以上資料回答使用者的問題。如果使用者詢問特定客戶、規格或未成交/已成交的訂單，請從資料中找出並以親切、清晰的繁體中文條列回覆。如果找不到相關資料，請據實以告。
"""

    try:
        # 3. 呼叫 Gemini 進行智慧分析與回答
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_message,
            config={
                "system_instruction": system_prompt
            }
        )
        reply_text = response.text
    except Exception as e:
        print(f"Gemini API Error: {str(e)}")
        reply_text = f"發生錯誤: {str(e)}"
        
    # 4. 透過 LINE Reply API 將 Gemini 的回答傳回給使用者
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )