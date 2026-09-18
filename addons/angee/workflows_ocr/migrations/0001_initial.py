"""Empty fresh-install anchor for the retired workflows_ocr app label.

Existing deployments load their exact generated migration module instead.  The
anchor gives new installations one stable graph leaf without creating retired
tables or model state.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []
    operations: list[migrations.operations.base.Operation] = []
