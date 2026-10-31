# Proposal question routing

The bridge's `Round.setup_values` hook consumes a native `clarification_queue` row.
The queue must be saved in the round provisioning database; the donor rejects a foreign database identity before converting it to a field value.
Its schema donor adds that reference to `ProposalRoundSetupInput`. Project setup applies it within the same transaction as round provisioning;
resuming a round fills a missing route without changing a configured route.
The setup-state condition includes the route when this bridge is composed.
Queue-free proposals remain supported without the bridge.
