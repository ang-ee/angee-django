import { graphql } from "@angee/gql/console";

export const TASK_RESPONDER_AUDIENCE = graphql(`
  query ProposalTaskResponderAudience($id: String!) {
    project_tasks_by_pk(id: $id) {
      id revision permissions shared_with_responders
      project { source_proposal { id } }
    }
  }
`);

export const TASK_RESPONDER_SHARE = graphql(`
  mutation SetProposalTaskResponderShare($task: ID!, $revision: Int, $shared: Boolean!) {
    set_task_responder_share(task: $task, expected_revision: $revision, shared: $shared) {
      ok message id code validation_errors
    }
  }
`);

export const OPEN_ROUND = graphql(`
  mutation OpenProposalRound($round: ID!, $revision: Int) {
    open_proposal_round(round: $round, expected_revision: $revision) {
      ok message id code validation_errors
    }
  }
`);

export const CANCEL_ROUND = graphql(`
  mutation CancelProposalRound($round: ID!, $revision: Int) {
    cancel_proposal_round(round: $round, expected_revision: $revision) {
      ok message id code validation_errors
    }
  }
`);

export const CLOSE_ROUND = graphql(`
  mutation CloseProposalRound($round: ID!, $revision: Int, $outcome: RoundOutcome!, $accepted: [ID!]!, $partial: [ID!]!) {
    close_proposal_round(round: $round, expected_revision: $revision, outcome: $outcome, accepted: $accepted, partial: $partial) {
      ok message id code validation_errors
    }
  }
`);

export const TRANSFER_ROUND = graphql(`
  mutation TransferProposalRound($round: ID!, $revision: Int, $facilitator: ID!) {
    transfer_proposal_round_facilitation(round: $round, expected_revision: $revision, facilitator: $facilitator) {
      ok message id code validation_errors
    }
  }
`);

export const ADMIT_RESPONDER = graphql(`
  mutation AdmitProposalResponder($round: ID!, $responder: ID!, $track: Boolean!) {
    admit_proposal_round_responder(round: $round, responder: $responder, track: $track) {
      ok message id code validation_errors
    }
  }
`);

export const REMOVE_RESPONDER = graphql(`
  mutation RemoveProposalResponder($round: ID!, $revision: Int, $responder: ID!) {
    remove_proposal_round_responder(round: $round, expected_revision: $revision, responder: $responder) {
      ok message id code validation_errors removed_shares reported_shares
    }
  }
`);

export const WIDEN_ROUND = graphql(`
  mutation WidenProposalRound($round: ID!, $revision: Int, $policy: String!) {
    widen_proposal_round_opening_policy(round: $round, expected_revision: $revision, policy: $policy) {
      ok message id code validation_errors
    }
  }
`);

export const SUBMIT_PROPOSAL = graphql(`
  mutation SubmitProposal($proposal: ID!, $revision: Int) {
    submit_proposal(proposal: $proposal, expected_revision: $revision) {
      ok message id code validation_errors
    }
  }
`);

export const WITHDRAW_PROPOSAL = graphql(`
  mutation WithdrawProposal($proposal: ID!, $revision: Int) {
    withdraw_proposal(proposal: $proposal, expected_revision: $revision) {
      ok message id code validation_errors
    }
  }
`);

export const PUBLISH_TRACK = graphql(`
  mutation PublishProposalTrack($proposal: ID!, $revision: Int) {
    publish_proposal_track(proposal: $proposal, expected_revision: $revision) {
      ok message id code validation_errors
    }
  }
`);

export const ANSWER_VISIBILITY = graphql(`
  mutation SetProposalAnswerVisibility($answer: ID!, $revision: Int, $visibility: String!) {
    set_proposal_answer_visibility(answer: $answer, expected_revision: $revision, visibility: $visibility) {
      ok message id code validation_errors
    }
  }
`);

export const ANSWER_SHARE = graphql(`
  mutation SetProposalAnswerShare($answer: ID!, $revision: Int, $shared: Boolean!) {
    set_proposal_answer_responder_share(answer: $answer, expected_revision: $revision, shared: $shared) {
      ok message id code validation_errors
    }
  }
`);
