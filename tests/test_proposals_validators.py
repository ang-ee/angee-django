"""The stable topic key has one normalizer for declarations and persisted rows."""

import pytest
from django.core.exceptions import ValidationError
from pydantic import ValidationError as InputValidationError

from angee.proposals.inputs import TopicTemplate
from angee.proposals.validators import normalize_topic_key
from tests.proposals_models import Topic


def test_topic_key_normalizes_case_and_whitespace() -> None:
    assert normalize_topic_key("  Scope ") == "scope"
    with pytest.raises(ValueError, match="Topic key is required."):
        normalize_topic_key("   ")


def test_round_template_and_topic_row_share_the_normalizer() -> None:
    assert TopicTemplate(key=" Scope ", name="Scope").key == "scope"
    with pytest.raises(InputValidationError, match="Topic key is required."):
        TopicTemplate(key=" ", name="Blank")
    topic = Topic(key=" Scope ", name="Scope")
    topic.normalize_key()
    assert topic.key == "scope"
    blank = Topic(key=None, name="Blank")
    with pytest.raises(ValidationError) as refused:
        blank.normalize_key()
    assert refused.value.message_dict == {"key": ["Topic key is required."]}
