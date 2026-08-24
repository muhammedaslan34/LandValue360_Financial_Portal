# النسخ الاحتياطي والاستعادة

## نسخة تشغيلية دورية

```bash
PYTHONPATH=app python scripts/backup.py --output /secure/backups --include-files
```

يدعم SQLite محلياً وPostgreSQL عبر `pg_dump`. عند Local Storage ينسخ الملفات الخاصة. في S3/MinIO يجب تضمين سياسة Mirror/Snapshot على مستوى بيئة النشر.

## نسخة ما قبل الترقية

تختلف عن النسخة اليومية لأنها تحفظ المصدر الحي أيضاً:

```bash
./scripts/pre_deploy_backup.sh /secure/off-server/backups
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\pre_deploy_backup.ps1 -OutputRoot D:\SecureBackups
```

افحص `manifest.json` و`SHA256SUMS`. يجب أن تكون النسخة خارج مسار التطبيق وخارج الخادم عند الإمكان.

## الاستعادة

```bash
PYTHONPATH=app python scripts/restore.py /path/to/backup --yes
```

PostgreSQL يستخدم `pg_restore --clean --if-exists`. نفّذ الاستعادة في نافذة صيانة، وأوقف الكتابة أولاً، وخذ Safety Backup قبل الاستعادة. بعد الاستعادة شغّل Health checks وGolden Cases وتحقق من الملفات والتشغيلات التاريخية.
