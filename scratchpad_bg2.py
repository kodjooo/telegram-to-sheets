import json, gspread, datetime
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
cfg = json.load(open(BASE + "config.json"))
sh = gc.open_by_key(cfg["google_sheet_id"])
vals = sh.worksheet("Groups").get_all_values()

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

bg = sum(num(r[3]) for r in vals[1:] if r[8].strip() == "игнорировать")
tot = sum(num(r[3]) for r in vals[1:])
noteval = sum(1 for r in vals[1:] if r[7].strip() != "обработано")
print("фон (игнорировать):", bg, "| всего 1д:", tot, "| не оценено:", noteval)

print("\n=== 'действовать' с 1д=0 и датой впервые ===")
today = datetime.date(2026, 8, 19)
for r in vals[1:]:
    if r[8].strip() == "действовать" and num(r[3]) == 0 and len(r) > 13 and r[13].strip():
        try:
            d = datetime.date.fromisoformat(r[13].strip())
        except Exception:
            continue
        days = (today - d).days
        if days <= 30 and num(r[5]) >= 0:
            print(f"#{r[0]} 1d={r[3]} 7d={r[4]} 30d={r[5]} дней={days} | {r[2][:90]}")

print("\n=== текущие 'действовать' с 1д>0 ===")
for r in sorted(vals[1:], key=lambda x: -num(x[3])):
    if r[8].strip() == "действовать" and num(r[3]) > 0:
        print(f"#{r[0]} 1d={r[3]} | {r[2][:80]}")
