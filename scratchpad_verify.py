import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])

d = sh.worksheet("Digest").get_all_values()
print("A1 =", d[0][0])
print("A2 length =", len(d[1][0]))
print("----- DIGEST -----")
print(d[1][0])
print("------------------")

g = sh.worksheet("Groups").get_all_values()
unproc = 0
today = 0
for r in g[1:]:
    r = r + [""] * (14 - len(r))
    if r[7].strip() != "обработано":
        unproc += 1
    if r[12].strip() == "2026-08-20":
        today += 1
print("групп с датой оценки 2026-08-20:", today)
print("осталось 'не обработано':", unproc)
