#!/bin/bash
APP=/var/www/app.sellerdata.ru
L=$APP/storage/logs/laravel.log

echo "### 1. ozon-cpo account 8397 — все строки за сегодня до 05:10 (первопричина)"
grep -F "8397" $L | grep "2026-08-23T0[0-5]" | grep -iE "cpo|advertis" | cut -c1-300 | head -20

echo
echo "### 2. Контекст вокруг первого MaxAttempts 05:10:04 (ozon-cpo)"
grep -n "2026-08-23T05:10:04" $L | head -3
grep -B6 -A2 "2026-08-23T05:10:04.684669" $L | cut -c1-300

echo
echo "### 3. SyncProductsPricesFromOrdersJob сегодня"
grep -F "SyncProductsPricesFromOrdersJob" $L | grep "2026-08-23T" | cut -c1-300 | head -5

echo
echo "### 4. OrdersSyncStuck — последние записи (любая дата)"
grep -F "OrdersSyncStuck" $L | tail -3 | cut -c1-350
grep -F "OrdersSyncStuck" $APP/storage/logs/laravel.log.1 2>/dev/null | tail -2 | cut -c1-350

echo
echo "### 5. ozon-ads-export 'расход не разнесён по товарам' сегодня"
echo -n "count: "; grep -F "[advertising][ozon-ads-export] расход не разнесён по товарам" $L | grep -c "2026-08-23T"
grep -F "[advertising][ozon-ads-export] расход не разнесён по товарам" $L | grep "2026-08-23T" | grep -o '"account_id":[0-9]*' | sort -u | wc -l

echo
echo "### 6. Топ-15 ERROR-шаблонов сегодня"
grep "2026-08-23T.*production.ERROR" $L | sed 's/^.*production.ERROR: //' | cut -c1-70 | sort | uniq -c | sort -rn | head -15

echo
echo "### 7. Топ-12 WARNING-шаблонов сегодня"
grep "2026-08-23T.*production.WARNING" $L | sed 's/^.*production.WARNING: //' | cut -c1-70 | sort | uniq -c | sort -rn | head -12
