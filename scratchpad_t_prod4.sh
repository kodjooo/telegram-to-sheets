#!/bin/bash
APP=/var/www/app.sellerdata.ru
L=$APP/storage/logs/laravel.log

echo "### 1. DbConcurrencyRetry exhausted today — accountId breakdown"
grep -F "DbConcurrencyRetry: exhausted" $L | grep "2026-08-23T" | grep -o '"accountId":[0-9]*' | sort | uniq -c | sort -rn | head -20

echo
echo "### 2. Deadlock lines today — hour breakdown"
grep -F "Deadlock found" $L | grep -o "2026-08-23T[0-9][0-9]" | sort | uniq -c

echo
echo "### 3. ozon-cpo: underlying failures for accounts 8397/5681/8271 today (non-MaxAttempts)"
grep -F "ozon-cpo" $L | grep "2026-08-23T" | grep -vF "attempted too many times" | grep -E "ERROR|WARNING" | cut -c1-320 | head -15

echo
echo "### 4. stale_after_days in deployed code/config"
grep -rn "stale_after_days\|staleAfterDays\|STALE_AFTER_DAYS" $APP/config $APP/app 2>/dev/null | head -10
grep -n "STALE" $APP/.env 2>/dev/null | head -10

echo
echo "### 5. SYNC transactions today"
echo -n "Syncing for more than: "; grep -F "SYNC: [transactions] Syncing for more than" $L | grep -c "2026-08-23T"
echo -n "status was cleaned:    "; grep -F "SYNC: [transactions] Account updating status was cleaned" $L | grep -c "2026-08-23T"

echo
echo "### 6. WBReportCoverage today"
echo -n "empty report despite activity: "; grep -F "WBReportCoverage: empty report despite activity" $L | grep -c "2026-08-23T"
echo -n "marked after repeated empty:   "; grep -F "WBReportCoverage: marked after repeated empty" $L | grep -c "2026-08-23T"
grep -F "WBReportCoverage: marked after repeated empty" $L | grep "2026-08-23T" | cut -c1-300 | head -5

echo
echo "### 7. residual unallocated advert today"
grep -F "residual unallocated advert" $L | grep "2026-08-23T" | cut -c1-330

echo
echo "### 8. CheckTransactionDups today"
grep -F "CheckTransactionDups" $L | grep "2026-08-23T" | grep -o '"account_id":[0-9]*' | sort -u | tr '\n' ' '
echo
grep -F "CheckTransactionDups" $L | grep "2026-08-23T" | grep -c "account scan failed"

echo
echo "### 9. cost-price empty PRICE rejected today (hanging item 78f46099)"
grep -F "[import][cost-price] empty PRICE rejected" $L | grep -c "2026-08-23T"

echo
echo "### 10. OrdersSyncStuck today (9fe2ab6b)"
grep -F "OrdersSyncStuck" $L | grep "2026-08-23T" | cut -c1-330 | head -5

echo
echo "### 11. total ERROR / WARNING lines today"
echo -n "ERROR:   "; grep -c "2026-08-23T.*production.ERROR" $L
echo -n "WARNING: "; grep -c "2026-08-23T.*production.WARNING" $L
