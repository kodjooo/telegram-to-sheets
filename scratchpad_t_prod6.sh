#!/bin/bash
APP=/var/www/app.sellerdata.ru
L=$APP/storage/logs/laravel.log
DBU=$(grep -m1 '^DB_USERNAME=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
DBP=$(grep -m1 '^DB_PASSWORD=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
DBN=$(grep -m1 '^DB_DATABASE=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
q() { mysql -u"$DBU" -p"$DBP" "$DBN" -N -B -e "$1" 2>/dev/null; }

echo "### 1. WbSpecializedDenials сегодня"
grep -F "WbSpecializedDenials" $L | grep "2026-08-23T" | cut -c1-450

echo
echo "### 2. WbReportGap сегодня"
grep -F "WbReportGap" $L | grep "2026-08-23T" | cut -c1-450

echo
echo "### 3. Аккаунт 9477: незакрытые заказы (uncalculated)"
q "SELECT COUNT(*) uncalculated FROM orders WHERE account_id = 9477 AND is_calculated = 0 LIMIT 1;"
q "SELECT COUNT(*) total FROM orders WHERE account_id = 9477 LIMIT 1;"

echo
echo "### 4. Аккаунт 9477: сколько дней подряд падает CalculateAccountDayJob (по логам 21-23.08)"
for f in laravel.log.2 laravel.log.1 laravel.log; do
  n=$(grep -F 'CalculateAccountDayJob failed {"account_id":9477' $APP/storage/logs/$f 2>/dev/null | wc -l)
  echo "$f: $n"
done

echo
echo "### 5. failed_jobs по CalculateAccountDayJob: today / 7d / total"
q "SELECT SUM(failed_at >= CURDATE()) today, SUM(failed_at >= CURDATE() - INTERVAL 7 DAY) last7, COUNT(*) total FROM failed_jobs WHERE payload LIKE '%CalculateAccountDayJob%' LIMIT 1;"

echo
echo "### 6. Ozon CPO: 3 аккаунта — были ли успешные отчёты сегодня"
grep -F "ozon-cpo" $L | grep "2026-08-23T" | grep -iE "готов|success|загруж|сохран" | cut -c1-250 | head -5
echo -n "postponed lines today: "; grep -F "отчёт отложен внешним лимитом Ozon" $L | grep -c "2026-08-23T"
grep -F "отчёт отложен внешним лимитом Ozon" $L | grep "2026-08-23T" | grep -o '"account_id":[0-9]*' | sort | uniq -c | sort -rn | head
