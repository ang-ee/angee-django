"""Names shared by the ACP route, endpoint and message projections."""

ACP_PATH = "/acp/agents/"
ANGEE_META_KEY = "angee"
TURN_FAILED_STOP_REASON = "_angee/failed"
TURN_FAILURE_CODE = "AGENT_TURN_FAILED"


def message_id(turn: object, part: str) -> str:
    """Return the stable id used by acknowledgments, echoes and replay upserts."""

    return f"{turn}:{part}"
