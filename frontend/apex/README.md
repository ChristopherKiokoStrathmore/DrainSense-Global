# frontend/apex/
APEX application export (app id 100 **[Assumption]**). Update with:
```bash
apex export -applicationid 100 -split -skipExportDate -dir frontend/apex
```
Import: App Builder → Import, or `@f100/install.sql` in SQLcl. See [ORACLE_APEX.md §7](../../docs/ORACLE_APEX.md#7-version-control-exporting-the-app-into-git).
