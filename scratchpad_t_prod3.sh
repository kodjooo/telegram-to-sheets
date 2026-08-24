#!/bin/bash
L=/var/www/app.sellerdata.ru/storage/logs/laravel.log

echo "### 1. getTransactionsByPeriod failed — today's 5 lines (truncated)"
grep -F "getTransactionsByPeriod failed" $L | grep "2026-08-23T" | cut -c1-420

echo
echo "### 2. CalculateAccountDayJob failed today — accounts"
grep -F "CalculateAccountDayJob failed" $L | grep "2026-08-23T" | grep -o '"account_id":[0-9]*' | sort | uniq -c | sort -rn | head -20
echo "-- count:"
grep -F "CalculateAccountDayJob failed" $L | grep -c "2026-08-23T"

echo
echo "### 3. DbConcurrencyRetry: exhausted today — op/context"
grep -F "DbConcurrencyRetry: exhausted" $L | grep "2026-08-23T" | grep -o '"op":"[^"]*"' | sort | uniq -c | sort -rn | head -10
grep -F "DbConcurrencyRetry: exhausted" $L | grep "2026-08-23T" | grep -o '"account_id":[0-9]*' | sort | uniq -c | sort -rn | head -15
echo "-- sample:"
grep -F "DbConcurrencyRetry: exhausted" $L | grep "2026-08-23T" | head -2 | cut -c1-500

echo
echo "### 4. WarehouseRegistry today (all 10 lines, truncated)"
grep -F "WarehouseRegistry" $L | grep "2026-08-23T" | cut -c1-460

echo
echo "### 5. ozon-cpo job failed today"
grep -F "[advertising][ozon-cpo] job failed" $L | grep "2026-08-23T" | cut -c1-500
echo "-- MaxAttempts lines:"
grep -F "SyncAdvertisingOzonCpoReportsJob has been attempted" $L | grep "2026-08-23T" | cut -c1-300 | head -3

echo
echo "### 6. ozon-ads-export 'в окне нет авторитетного расхода' today"
grep -F "в окне нет авторитетного расхода" $L | grep "2026-08-23T" | cut -c1-400

echo
echo "### 7. Unknown transaction type today (should be 0) + last occurrence date"
grep -F "DATA: Unknown transaction type" $L | grep -c "2026-08-23T"
grep -F "DATA: Unknown transaction type" $L | tail -1 | cut -c1-200
