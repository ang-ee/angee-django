# Notes example

Notes composes the shared record UI and its Context aside through public addon
contracts. Its permissions extensions declare `notes.Note` as a target of
`storage.FileAttachment` and `knowledge.RecordBinding`, so resource metadata
exposes both edges and Related offers Files and Pages.

Note writers can attach files and bind pages they can also write. Record-scoped
uploads inherit note read/write through the model's `file_attachments` reverse
relation and the visibility-filtered `storage/file` arm; attaching an existing
private or inherited file does not change its access. The shared groups own
Upload and Bind; the example adds no custom pane or mutation.

See the [storage](../../../../addons/angee/storage/README.md) and
[knowledge](../../../../addons/angee/knowledge/README.md) contracts.


The local-template demo keeps its bridge, repository, template source, templates,
and example agents, but a portable addon cannot choose a project's checkout.
The bridge starts disconnected. After loading demo resources, the project sets
`config.local_root` and `config.local_name` on `notes.vcs_local_templates` through the VCS bridge's
configuration form or its own provisioning step, then connects that bridge.
Relative paths resolve against the project's `BASE_DIR`; an installed project
without a checkout can supply another explicit template repository instead.

For a project whose framework source is `workspaces/src/angee`, its provisioning
step can compose the existing resource identity and bridge lifecycle owners:

```python
from django.apps import apps
from rebac import system_context

VcsBridge = apps.get_model("integrate_vcs", "VcsBridge")
Resource = apps.get_model("resources", "Resource")
with system_context(reason="project.configure_local_templates"):
    bridge = Resource.objects.resolve("notes.vcs_local_templates", model=VcsBridge)
    bridge.config = {**bridge.config, "local_root": "workspaces/src/angee", "local_name": "angee-django"}
    bridge.save(update_fields=("config",))
    if bridge.lifecycle == bridge.Lifecycle.DISCONNECTED:
        bridge.connect()
```

The path and local repository name are project configuration. The portable seed
omits `config` entirely, so ordinary and forced resource replay preserve the
project-authored configuration even when another demo field changes.
