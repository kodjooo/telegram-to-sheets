#!/bin/bash
APP=/var/www/app.sellerdata.ru
L=$APP/storage/logs/laravel.log

echo "### Таймлайн CPO для 8397 / 5681 / 8271 сегодня (успех vs отказ)"
for a in 8397 5681 8271; do
  echo "--- acc $a"
  grep -E "ozon-cpo" $L | grep "2026-08-23T" | grep "\"account_id\":$a," | grep -vF "отчёт отложен" | cut -c1-230
done

echo
echo "### retryUntil / backoff в джобе"
grep -nE "retryUntil|backoff|failOnTimeout|release\(" $APP/app/Modules/Advertising/Jobs/SyncAdvertisingOzonCpoReportsJob.php | head -20

echo
echo "### комментарий F6 в AdvertisingOzonSyncService.php:445-470"
sed -n '445,470p' $APP/app/Modules/Advertising/Services/AdvertisingOzonSyncService.php
