#!/bin/bash
APP=/var/www/app.sellerdata.ru
DBU=$(grep -m1 '^DB_USERNAME=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
DBP=$(grep -m1 '^DB_PASSWORD=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
DBN=$(grep -m1 '^DB_DATABASE=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
q() { mysql -u"$DBU" -p"$DBP" "$DBN" -N -B -e "$1" 2>/dev/null; }

echo "### A. TransactionsJob in failed_jobs: today / last7d / total"
q "SELECT SUM(failed_at >= CURDATE()) today, SUM(failed_at >= CURDATE() - INTERVAL 7 DAY) last7, COUNT(*) total FROM failed_jobs WHERE payload LIKE '%Ozon%TransactionsJob%' LIMIT 1;"

echo
echo "### B. CalculateAccountDayJob today: accounts (from payload 'account_id')"
q "SELECT REGEXP_SUBSTR(payload,'account_id[^0-9]{1,12}[0-9]+') AS acc, COUNT(*) c FROM failed_jobs WHERE failed_at >= CURDATE() AND payload LIKE '%CalculateAccountDayJob%' GROUP BY acc ORDER BY c DESC LIMIT 15;"

echo
echo "### C. SyncAdvertisingOzonCpoReportsJob today: payload snippet"
q "SELECT LEFT(payload, 400) FROM failed_jobs WHERE failed_at >= CURDATE() AND payload LIKE '%OzonCpoReports%' LIMIT 1;"

echo
echo "### D. warehouses: verdict staleness (WarehouseRegistry fail-open check)"
q "SELECT COUNT(*) total, SUM(inactive=1) inact FROM warehouses LIMIT 1;"

echo
echo "### E. log: today's line counts by key pattern"
grep -c "2026-08-23" $APP/storage/logs/laravel.log 2>/dev/null || echo "n/a"
for p in "getTransactionsByPeriod failed" "Deadlock found" "DbConcurrencyRetry: exhausted" "Lock wait timeout exceeded" "DATA: Unknown transaction type" "WarehouseRegistry" "ozon-cpo" "residual unallocated advert" "CheckTransactionDups" "в окне нет авторитетного расхода"; do
  n=$(grep -F "$p" $APP/storage/logs/laravel.log 2>/dev/null | grep -c "2026-08-23T")
  echo "$n  <= $p"
done

echo
echo "### F. log files present"
ls -la $APP/storage/logs/ | grep -E "laravel|account-2026-08-2" | head -20
