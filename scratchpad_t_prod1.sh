#!/bin/bash
APP=/var/www/app.sellerdata.ru
DBU=$(grep -m1 '^DB_USERNAME=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
DBP=$(grep -m1 '^DB_PASSWORD=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
DBN=$(grep -m1 '^DB_DATABASE=' $APP/.env | cut -d= -f2- | tr -d '"'"'"'')
q() { mysql -u"$DBU" -p"$DBP" "$DBN" -N -B -e "$1" 2>/dev/null; }

echo "### 1. failed_jobs today by job class (window: failed_at >= CURDATE())"
q "SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(payload,'\"displayName\":\"',-1),'\"',1) AS job, COUNT(*) c FROM failed_jobs WHERE failed_at >= CURDATE() GROUP BY job ORDER BY c DESC LIMIT 30;"

echo
echo "### 2. failed_jobs today by exception head"
q "SELECT LEFT(exception,90) AS ex, COUNT(*) c FROM failed_jobs WHERE failed_at >= CURDATE() GROUP BY ex ORDER BY c DESC LIMIT 25;"

echo
echo "### 3. failed_jobs today by hour"
q "SELECT HOUR(failed_at) h, COUNT(*) c FROM failed_jobs WHERE failed_at >= CURDATE() GROUP BY h ORDER BY h LIMIT 24;"

echo
echo "### 4. failed_jobs today by account_id in payload"
q "SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(payload,'account_id\\\\\":',-1),',',1) AS acc, COUNT(*) c FROM failed_jobs WHERE failed_at >= CURDATE() GROUP BY acc ORDER BY c DESC LIMIT 20;"

echo
echo "### 5. failed_jobs total table + oldest (historical context)"
q "SELECT COUNT(*) total, MIN(failed_at) oldest, MAX(failed_at) newest FROM failed_jobs;"

echo
echo "### 6. failed_jobs yesterday count (window 22.08)"
q "SELECT COUNT(*) FROM failed_jobs WHERE failed_at >= CURDATE() - INTERVAL 1 DAY AND failed_at < CURDATE();"
