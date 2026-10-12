import os
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from google import genai
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI()
security = HTTPBasic()

# ==================== 憑證與設定 ====================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# 設定密碼（優先讀取環境變數 APP_PASSWORD，如果沒有則預設為 "1234"）
APP_PASSWORD = os.getenv("APP_PASSWORD", "1234")

# Google Sheets 設定
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
CREDS = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREADSHEET_KEY = '1Re3JZ41CB5_a9UrGdru0HfWxMCvoaRpYLfAwSDRgJxI'
# ====================================================

# 初始化 Gemini 客戶端
client = genai.Client(api_key=GEMINI_API_KEY)


def verify_password(credentials: HTTPBasicCredentials = Depends(security)):
    """簡單的密碼驗證函式"""
    # 帳號可以設定固定的或任意（例如預設帳號 admin），密碼比對 Render 裡的設定
    correct_password = APP_PASSWORD.encode("utf8")
    is_correct = credentials.password.encode("utf8") == correct_password
    
    if not is_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="密碼錯誤，請重新輸入",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


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


# 1. 首頁：顯示網頁查詢介面（需要通過密碼驗證）
@app.get("/", response_class=HTMLResponse)
async def home(username: str = Depends(verify_password)):
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>業務訂單與詢價 AI 查詢助理</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 0 20px; background: #f4f7f6; }
            .container { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
            h2 { color: #333; margin-bottom: 20px; }
            textarea { width: 100%; height: 120px; padding: 12px; font-size: 16px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; resize: vertical; }
            button { background: #007bff; color: white; border: none; padding: 12px 24px; font-size: 16px; border-radius: 4px; cursor: pointer; margin-top: 15px; }
            button:hover { background: #0056b3; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📊 業務訂單與詢價 AI 查詢助理</h2>
            <form action="/ask" method="post">
                <textarea name="user_message" placeholder="請輸入您想查詢的內容（例如：某某客戶的報價是多少？最近有哪些已成交的訂單？）..."></textarea><br>
                <button type="submit">送出查詢</button>
            </form>
        </div>
    </body>
    </html>
    """


# 2. 處理查詢請求並顯示結果頁面（同樣需要通過密碼驗證）
@app.post("/ask", response_class=HTMLResponse)
async def ask_gemini(user_message: str = Form(...), username: str = Depends(verify_password)):
    # 每次收到查詢時，動態讀取最新的 Google 試算表內容
    sheets_data = get_sheets_summary()
    
    system_prompt = f"""
你是一個專業的業務詢價單與訂單管理 AI 助理。
以下是目前從 Google 試算表同步過來的即時資料：
{sheets_data}

請嚴格根據以上資料回答使用者的問題。
【回覆排版與風格規範】：
1. 請以親切、清晰的繁體中文回答。
2. 請使用乾淨、整齊的條列式（Bullet points）來呈現查詢結果。
3. 請勿使用過多的大標題符號（如 ###），直接用粗體或條列分項即可，讓畫面看起來清爽易讀。
4. 如果找不到相關資料，請據實以告。
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=user_message,
            config={
                "system_instruction": system_prompt
            }
        )
        reply_text = response.text
    except Exception as e:
        reply_text = f"發生錯誤: {str(e)}"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>查詢結果 - 業務訂單 AI 助理</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 0 20px; background: #f4f7f6; }}
            .container {{ background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }}
            h2 {{ color: #333; }}
            .back-btn {{ display: inline-block; margin-bottom: 20px; color: #007bff; text-decoration: none; font-size: 15px; }}
            .back-btn:hover {{ text-decoration: underline; }}
            .question-box {{ background: #e9ecef; padding: 12px; border-radius: 4px; margin-bottom: 20px; color: #495057; }}
            .result-box {{ padding: 15px; background: #fff; border: 1px solid #dee2e6; border-radius: 4px; white-space: pre-wrap; line-height: 1.6; color: #212529; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>🔍 查詢結果</h2>
            <a class="back-btn" href="/">← 返回重新查詢</a>
            <div class="question-box"><strong>您的提問：</strong> {user_message}</div>
            <div class="result-box">{reply_text}</div>
        </div>
    </body>
    </html>
    """
