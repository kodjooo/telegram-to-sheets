import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
cfg = json.load(open(BASE + "config.json"))
sh = gc.open_by_key(cfg["google_sheet_id"])
d = sh.worksheet("Digest")
print("A1:", repr(d.acell("A1").value))
print("A2 длина:", len(d.acell("A2").value or ""))
vals = sh.worksheet("Groups").get_all_values()
print("не обработано:", sum(1 for r in vals[1:] if r[7].strip() != "обработано"))
print("оценено сегодня:", sum(1 for r in vals[1:] if len(r) > 12 and r[12].strip() == "2026-08-19"))
