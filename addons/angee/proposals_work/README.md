# Proposal question routing

The bridge's `Round.setup_values` hook consumes a `clarification_queue` public
ID. Project setup applies it within the same transaction as round provisioning;
resuming a round fills a missing route without changing a configured route.
The setup-state condition includes the route when this bridge is composed.
Queue-free proposals remain supported without the bridge.
