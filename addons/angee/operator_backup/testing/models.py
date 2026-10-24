"""The source suite's single native backup model composition."""

from angee.operator_backup.models import Backup as AbstractBackup
from angee.operator_backup.models import BackupPlan as AbstractBackupPlan


class BackupPlan(AbstractBackupPlan):
    """Concrete plan, shared by authorization and workflow contract tests."""

    rebac_grantable = AbstractBackupPlan.rebac_grantable

    class Meta(AbstractBackupPlan.Meta):
        abstract = False
        app_label = "operator_backup"
        db_table = "test_operator_backup_plan"


class Backup(AbstractBackup):
    """Concrete immutable completed evidence."""

    class Meta(AbstractBackup.Meta):
        abstract = False
        app_label = "operator_backup"
        db_table = "test_operator_backup_backup"
