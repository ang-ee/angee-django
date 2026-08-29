import { graphql } from "@angee/gql/console";

export const OpenProposalRoundDocument = graphql(`
  mutation ProposalsOpenRound($round: ID!) {
    open_proposal_round(round: $round) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const CloseProposalRoundDocument = graphql(`
  mutation ProposalsCloseRound(
    $round: ID!
    $outcome: RoundOutcome!
    $accepted: [ID!]!
    $partial: [ID!]!
  ) {
    close_proposal_round(
      round: $round
      outcome: $outcome
      accepted: $accepted
      partial: $partial
    ) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const CancelProposalRoundDocument = graphql(`
  mutation ProposalsCancelRound($round: ID!) {
    cancel_proposal_round(round: $round) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const TransferProposalRoundDocument = graphql(`
  mutation ProposalsTransferRound($round: ID!, $facilitator: ID!) {
    transfer_proposal_round_facilitation(
      round: $round
      facilitator: $facilitator
    ) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const SubmitProposalDocument = graphql(`
  mutation ProposalsSubmit($proposal: ID!) {
    submit_proposal(proposal: $proposal) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const WithdrawProposalDocument = graphql(`
  mutation ProposalsWithdraw($proposal: ID!) {
    withdraw_proposal(proposal: $proposal) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const CreateProposalTrackDocument = graphql(`
  mutation ProposalsCreateTrack($proposal: ID!) {
    create_proposal_track(proposal: $proposal) {
      ok
      message
      id
      validation_errors
    }
  }
`);

export const PublishProposalTrackDocument = graphql(`
  mutation ProposalsPublishTrack($proposal: ID!) {
    publish_proposal_track(proposal: $proposal) {
      ok
      message
      id
      validation_errors
    }
  }
`);
