import json, re, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
orig = json.load(open(BASE + "scratchpad_orig.json"))
last_dt = max(r[1] for r in orig[1:] if r[1])
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
recent = [r for r in orig[1:] if r[1] and r[1] >= cut]

def agg(needle, title):
    hits = [r for r in recent if needle in r[2]]
    accs = set()
    total = 0.0
    reasons = collections.Counter()
    for r in hits:
        m = re.search(r'"account_id":(\d+)', r[2])
        if m:
            accs.add(m.group(1))
        m2 = re.search(r'"unallocated_rub":([0-9.]+)', r[2])
        if m2:
            total += float(m2.group(1))
        m3 = re.search(r'"by_reason":\{([^}]*)\}', r[2])
        if m3:
            for k, v in re.findall(r'"([a-z_]+)":([0-9.]+)', m3.group(1)):
                reasons[k] += float(v)
    print(f"\n### {title}: логов {len(hits)}, аккаунтов {len(accs)}, unallocated_rub {total:,.0f}")
    print("   аккаунты:", ", ".join(sorted(accs)))
    if reasons:
        print("   причины:", {k: round(v) for k, v in reasons.most_common()})

agg("расход не разнесён по товарам", "#f907d4a7 ozon-ads-export расход не разнесён")
agg("в окне нет авторитетного расхода кампаний", "#16ad5659 нет авторитетного расхода")
agg("popup-кэш расхода не обновлён", "#fd92d782 popup-кэш")
agg("источник пуст при непустых расходах", "#eb3d6426 источник пуст")
agg("разнос по товарам не сходится с promo", "#6799ce35 не сходится с promo")
agg("расход не разнесён из-за границы окна экспорта", "#e16e1665 граница окна")

# WeeklyInternalReport
h = [r for r in recent if "WeeklyInternalReport: data not ready" in r[2]]
accs = collections.Counter(re.search(r'"account_id":(\d+)', r[2]).group(1) for r in h if re.search(r'"account_id":(\d+)', r[2]))
print(f"\n### #247083c8 WeeklyInternalReport: {len(h)} логов, аккаунтов {len(accs)}")
print("   ", dict(accs.most_common(12)))
print("   пример:", h[0][2][:500] if h else "-")

# Unknown tx проверка
unk = [r for r in recent if "Unknown transaction type" in r[2]]
print(f"\n### DATA Unknown transaction type за 24ч: {len(unk)}")
cnt = collections.Counter()
for r in unk:
    m = re.search(r'"operation_type":\s*"((?:[^"\\]|\\.)*)"', r[2])
    cnt[m.group(1) if m else r[2][:160]] += 1
for k, v in cnt.most_common(30):
    print("  ", v, "::", k[:150])
