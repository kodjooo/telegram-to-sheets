#!/bin/bash
APP=/var/www/app.sellerdata.ru
L=$APP/storage/logs/laravel.log

echo "### 1. Успешная загрузка CPO для 8397/5681/8271/9230 сегодня?"
for a in 8397 5681 8271 9230 3142; do
  n=$(grep -F "отчёты «Оплата за заказ» загружены" $L | grep "2026-08-23T" | grep -c "\"account_id\":$a,")
  echo "  acc $a: успешных загрузок сегодня = $n"
done

echo
echo "### 2. Конфиг ретраев/бэкоффа CPO-джобы"
sed -n '1,80p' $APP/app/Modules/Advertising/Jobs/SyncAdvertisingOzonCpoReportsJob.php | grep -nE 'tries|timeout|backoff|maxExceptions|public \$|public int|class '

echo
echo "### 3. Где формируется «Превышен лимит активных запросов»"
grep -rn "лимит активных запросов" $APP/app 2>/dev/null | head -5

echo
echo "### 4. Всего строк per-file сегодня (для оценки фона)"
echo -n "laravel.log всего строк 2026-08-23: "; grep -c "2026-08-23T" $L
