import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])
print("SHEET_ID:", cfg["google_sheet_id"])

ws = sh.worksheet("Unknown tx")
for r in ws.get_all_values()[:12]:
    print([c[:60] for c in r])

print()
print("=== DIGEST current ===")
d = sh.worksheet("Digest").get_all_values()
for r in d[:3]:
    print(r[0][:80] if r else "", "|", (r[1][:200] if len(r) > 1 else ""))

print()
print("=== FULL ROWS of interest ===")
rows = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups.json"))
want = {4, 5, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 21, 23, 24, 27, 28, 31, 46, 63, 65}
for r in rows:
    if r[0] in want:
        r = r + [""] * (15 - len(r))
        print("ROW", r[0], "| ID", r[1], "| d1", r[4], "| verdict", r[9], "| urg", r[10], "| cause", r[11], "| ev", r[13], "| firstact", r[14])
        print("   PAT:", (r[3] or "")[:150].replace("\n", " "))
        print("   ACT:", r[12])
        print()
