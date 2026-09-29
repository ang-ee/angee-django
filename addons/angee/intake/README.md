# Intake access decisions

The decisions web addon supplies frozen access actions on Need decision records.
Intake composes its generic subject tab on Need and an Access tab on the Task
owning each Need. Access references retain their existing server read gate.

Revisit is available for a current declined access seat. It rechecks decision
act, Need write and target share, then admits a successor under the Need lock.
The prior verdict and resolution remain intact. Approved access is final.
