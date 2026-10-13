import os
import time
from datetime import datetime, timedelta
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
APP_PASSWORD = os.getenv("APP_PASSWORD", "1234")

# Google Sheets 設定
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
CREDS = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREADSHEET_KEY = '1Re3JZ41CB5_a9UrGdru0HfWxMCvoaRpYLfAwSDRgJxI'
# ====================================================

# 初始化 Gemini 客戶端
client = genai.Client(api_key=GEMINI_API_KEY)

# ==================== 快取變數 (Cache) ====================
_cached_rows_data = []
_cache_expire_time = datetime.min
CACHE_DURATION_MINUTES = 10  # 延長快取至 10 分鐘
# ====================================================


def verify_password(credentials: HTTPBasicCredentials = Depends(security)):
    """簡單的密碼驗證函式"""
    correct_password = APP_PASSWORD.encode("utf8")
    is_correct = credentials.password.encode("utf8") == correct_password
    
    if not is_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="密碼錯誤，請重新輸入",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def load_sheets_to_memory():
    """背景或首次載入所有分頁資料到記憶體（只在過期時重新載入）"""
    global _cached_rows_data, _cache_expire_time
    now = datetime.now()
    
    if _cached_rows_data and now < _cache_expire_time:
        return _cached_rows_data

    try:
        spreadsheet = GC.open_by_key(SPREADSHEET_KEY)
        worksheets = spreadsheet.worksheets()
        
        all_rows = []
        for ws in worksheets:
            rows = ws.get_all_values()
            if len(rows) <= 1:
                continue
            for row in rows[1:]:
                if len(row) < 11:
                    continue
                # 儲存結構化資料：(工作表名稱, row資料)
                all_rows.append((ws.title, row))
                
        _cached_rows_data = all_rows
        _cache_expire_time = now + timedelta(minutes=CACHE_DURATION_MINUTES)
        return _cached_rows_data
    except Exception as e:
        print(f"讀取試算表錯誤: {e}")
        return []


def search_relevant_data(user_query: str):
    """根據使用者的提問關鍵字，從 3000 筆資料中過濾出最相關的資料，避免塞爆 AI"""
    all_rows = load_sheets_to_memory()
    if not all_rows:
        return "目前無法讀取試算表資料。"

    # 如果使用者沒有輸入特別關鍵字或是查全體統計，預設回傳最近的 50 筆或全部過濾
    # 這裡我們簡單做關鍵字比對：只要 row 裡面包含查詢字串的任何一個詞就保留
    query_keywords = [kw.strip() for kw in user_query.split() if len(kw.strip()) > 0]
    
    matched_lines = []
    for ws_title, row in all_rows:
        row_str = " ".join(str(val) for val in row)
        
        # 如果沒有輸入關鍵字，預設抓取部分最新或全部；如果有關鍵字，必須包含其中一個
        is_match = True
        if query_keywords:
            is_match = any(kw in row_str for kw in query_keywords)
            
        if is_match:
            status_val = "成交" if str(row[0]).upper() == "TRUE" else "未成交"
            line = f"工作表: {ws_title} | 狀態: {status_val} | 日期: {row[1]} | 客戶: {row[2]} {row[3]} | 規格: {row[4]} | 廠牌: {row[5]} | 數量: {row[6]} | 成本: {row[7]} | 報價: {row[8]} | 折數: {row[9]} | 備註: {row[10]}"
            matched_lines.append(line)

    # 如果符合的資料太多（例如大於 100 筆），截取前 100 筆避免過載
    if len(matched_lines) > 100:
        matched_lines = matched_lines[:100]
        matched_lines.append("... (資料過多，僅顯示前 100 筆相關結果)")

    if not matched_lines:
        return "沒有找到符合該條件的資料。"

    return "\n".join(matched_lines)


# 1. 首頁：顯示網頁查詢介面
@app.get("/", response_class=HTMLResponse)
async def home(username: str = Depends(verify_password)):
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>業務訂單與詢價 AI 查詢助理</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; max-width: 1200px; margin: 40px auto; padding: 0 20px; background: #f4f7f6; font-size: 17px; }
            .container { background: white; padding: 35px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
            h2 { color: #333; margin-bottom: 20px; font-size: 26px; }
            textarea { width: 100%; height: 130px; padding: 15px; font-size: 17px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; resize: vertical; }
            button { background: #007bff; color: white; border: none; padding: 14px 28px; font-size: 17px; border-radius: 4px; cursor: pointer; margin-top: 15px; }
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


# 2. 處理查詢請求並顯示結果頁面
@app.post("/ask", response_class=HTMLResponse)
async def ask_gemini(user_message: str = Form(...), username: str = Depends(verify_password)):
    # 透過智慧關鍵字過濾，只抓取相關資料給 AI
    filtered_sheets_data = search_relevant_data(user_message)
    
    system_prompt = f"""
你是一個專業的業務詢價單與訂單管理 AI 助理。
以下是從 Google 試算表中過濾出來與使用者查詢相關的即時資料：
{filtered_sheets_data}

請嚴格根據以上資料回答使用者的問題。
【回覆排版與格式規範（極重要）】：
1. 請以親切、清晰的繁體中文簡單回覆摘要說明後，**必須**使用標準的 HTML `<table>` 格式來呈現查詢結果。
2. 表格的欄位順序與標題必須嚴格對應以下 10 個欄位順序：
   - 來源工作表
   - 狀態（請填入「成交」或「未成交」）
   - 日期
   - 規格
   - 廠牌
   - 數量
   - 報價
   - 折數
   - 進貨/成本
   - 備註
3. 請勿使用 Markdown 的表格語法（例如 |---|），請直接輸出乾淨的 HTML 標籤（如 `<table border="1">`, `<tr>`, `<th>`, `<td>` 等）。
4. 為了讓指定欄位呈現特定的樣式與置中，請在輸出 `<th>` 或 `<td>` 時加上對應的 class：
   - 「來源工作表」欄位請輸出：`<td class="col-source">...</td>`
   - 若狀態為成交，請輸出：`<td class="status-yes">成交</td>`
   - 若狀態為未成交，請輸出：`<td class="status-no">未成交</td>`
5. 如果有多筆資料，請分別列在不同的 `<tr>` 中。找不到資料時請據實以告。
"""

    reply_text = ""
    max_retries = 2
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=user_message,
                config={
                    "system_instruction": system_prompt
                }
            )
            reply_text = response.text
            break
        except Exception as e:
            if "503" in str(e) and attempt < max_retries - 1:
                time.sleep(2)
                continue
            reply_text = f"<p>系統發生錯誤 ({str(e)})，請稍後點擊返回重新查詢。</p>"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>查詢結果 - 業務訂單 AI 助理</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 1350px; margin: 40px auto; padding: 0 20px; background: #f4f7f6; font-size: 17px; }}
            .container {{ background: white; padding: 35px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }}
            h2 {{ color: #333; font-size: 26px; }}
            .back-btn {{ display: inline-block; margin-bottom: 20px; color: #007bff; text-decoration: none; font-size: 17px; }}
            .back-btn:hover {{ text-decoration: underline; }}
            .question-box {{ background: #e9ecef; padding: 15px; border-radius: 4px; margin-bottom: 20px; color: #495057; font-size: 17px; }}
            .result-box {{ padding: 15px; background: #fff; border: 1px solid #dee2e6; border-radius: 4px; line-height: 1.6; color: #212529; overflow-x: auto; }}
            
            /* 表格與欄位美化樣式 */
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 16px; }}
            th, td {{ border: 1px solid #cbd5e1; padding: 12px 14px; text-align: left; }}
            th {{ background-color: #f1f5f9; color: #334155; font-weight: bold; }}
            tr:nth-child(even) {{ background-color: #f8fafc; }}
            tr:hover {{ background-color: #f1f5f9; }}

            /* 來源工作表樣式：縮小寬度並置中 */
            td.col-source, th:nth-child(1) {{
                width: 90px;
                text-align: center;
                white-space: nowrap;
            }}

            /* 狀態格子專屬樣式：縮小寬度並加上鮮明色彩 */
            td.status-yes {{
                background-color: #d1fae5;
                color: #065f46;
                font-weight: bold;
                text-align: center;
                width: 70px;
                white-space: nowrap;
            }}
            td.status-no {{
                background-color: #fee2e2;
                color: #991b1b;
                font-weight: bold;
                text-align: center;
                width: 70px;
                white-space: nowrap;
            }}

            /* 指定欄位置中對齊 (來源工作表、狀態、日期、廠牌、數量、報價、折數) */
            th:nth-child(1), th:nth-child(2), th:nth-child(3), th:nth-child(5), th:nth-child(6), th:nth-child(7), th:nth-child(8),
            td:nth-child(1), td:nth-child(3), td:nth-child(5), td:nth-child(6), td:nth-child(7), td:nth-child(8) {{
                text-align: center;
            }}
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
