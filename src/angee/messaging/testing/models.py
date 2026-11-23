"""Shared concrete messaging/parties FK targets for the bare-Django test runtime.

Managed posts fixtures refer to these models even when messaging tests are not
collected. Register this connected model graph from conftest, before Django
creates the test database, without depending on a test module's import order.
"""

from typing import Any, cast

from django.db import models

from angee.intake.models import ChannelIntake
from angee.integrate.testing.integration import Integration
from angee.messaging.models import Channel as AbstractChannel
from angee.messaging.models import Fragment as AbstractFragment
from angee.messaging.models import Message as AbstractMessage
from angee.messaging.models import MessageEdge as AbstractMessageEdge
from angee.messaging.models import MessageStar as AbstractMessageStar
from angee.messaging.models import MessageSubtype as AbstractMessageSubtype
from angee.messaging.models import Part as AbstractPart
from angee.messaging.models import Participant as AbstractParticipant
from angee.messaging.models import Reaction as AbstractReaction
from angee.messaging.models import Thread as AbstractThread
from angee.messaging.models import ThreadAttachment as AbstractThreadAttachment
from angee.messaging.models import ThreadFollower as AbstractThreadFollower
from angee.messaging.models import ThreadNotification as AbstractThreadNotification
from angee.messaging_integrate_imap.models import ImapChannelSampling
from angee.parties.models import Address as AbstractAddress
from angee.parties.models import Circle as AbstractCircle
from angee.parties.models import CircleMember as AbstractCircleMember
from angee.parties.models import Directory as AbstractDirectory
from angee.parties.models import Folder as AbstractContactFolder
from angee.parties.models import Handle as AbstractHandle
from angee.parties.models import Organization as AbstractOrganization
from angee.parties.models import PartiesRole as AbstractPartiesRole
from angee.parties.models import Party as AbstractParty
from angee.parties.models import Person as AbstractPerson
from angee.parties.models import Relationship as AbstractRelationship
from angee.parties.models import RelationshipKind as AbstractRelationshipKind
from angee.posts.models import MessagePublic, ThreadPublic
from angee.projects.models import ThreadProjects
from angee.provenance.statements import Aspect, SourceKind, StatementRecords
from angee.spaces.models import ChannelSpace, ThreadSpace
from angee.spaces.testing import models as spaces_models  # noqa: F401 -- register Thread's group relation target


class Directory(AbstractDirectory, Integration):
    """Concrete contacts directory (Integration child) used by messaging tests."""

    rebac_grantable = AbstractDirectory.rebac_grantable

    class Meta(AbstractDirectory.Meta):
        """Django model options for the canonical test directory."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_directory"
        rebac_resource_type = "parties/directory"


class Folder(AbstractContactFolder):
    """Concrete parties folder used by messaging tests."""

    class Meta(AbstractContactFolder.Meta):
        """Django model options for the canonical test contacts folder."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_folder"
        rebac_resource_type = "parties/folder"


class Party(AbstractParty):
    """Concrete party used by messaging tests."""

    rebac_grantable = AbstractParty.rebac_grantable

    class Meta(AbstractParty.Meta):
        """Django model options for the canonical test party."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_party"
        rebac_resource_type = "parties/party"


class PersonFavourites(models.Model):
    """A consumer donor's own person columns and their statement groups, composed into the test person.

    Directory mappings (``angee.parties.backends.DirectoryMapping``) state them
    from a source's fields; a person's own statement outranks the directory's.
    """

    favourite_colour = models.CharField(max_length=40, blank=True, default="")
    favourite_number = models.IntegerField(null=True, blank=True)
    # A donor's declaration, read by the subject's own (``StatementRecords.aspects_for``). Composed here
    # by hand, not through ``extends``, so the composition typing plugin never sees it: name its type.
    statements: Any = StatementRecords(
        aspects={
            "colour": Aspect(
                fields=("favourite_colour",),
                precedence=cast(tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.DIRECTORY)),
            ),
            "number": Aspect(
                fields=("favourite_number",),
                precedence=cast(tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.DIRECTORY)),
            ),
        },
    )

    class Meta:
        abstract = True


class Person(PersonFavourites, AbstractPerson, Party):
    """Concrete identity required by party-keyed messaging followers, with a consumer donor's columns."""

    class Meta(AbstractPerson.Meta):
        """Django model options for the canonical test person."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_person"
        rebac_resource_type = "parties/person"


class Handle(AbstractHandle):
    """Concrete handle (a message sender/recipient) used by messaging tests."""

    rebac_grantable = AbstractHandle.rebac_grantable

    class Meta(AbstractHandle.Meta):
        """Django model options for the canonical test handle."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_handle"
        rebac_resource_type = "parties/handle"


class Fragment(AbstractFragment):
    """Concrete content-addressed fragment used by messaging tests.

    Unscoped substrate (no REBAC type), like the abstract source model.
    """

    class Meta(AbstractFragment.Meta):
        """Django model options for the canonical test fragment."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_fragment"


class Channel(ChannelIntake, ChannelSpace, ImapChannelSampling, AbstractChannel, Integration):
    """Concrete Integration child used to verify channel-owned message access."""

    rebac_grantable = AbstractChannel.rebac_grantable

    class Meta(AbstractChannel.Meta):
        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_channel"
        rebac_resource_type = "messaging/channel"


class Thread(ThreadProjects, ThreadSpace, ThreadPublic, AbstractThread):
    """Concrete thread used by messaging tests.

    Folds spaces' group pointer and posts' public-post payload onto the one table,
    mirroring the composer output for the installed base addons.
    """

    class Meta(AbstractThread.Meta):
        """Django model options for the canonical test thread."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread"
        rebac_resource_type = "messaging/thread"


class MessageSubtype(AbstractMessageSubtype):
    """Concrete message subtype used by messaging tests."""

    class Meta(AbstractMessageSubtype.Meta):
        """Django model options for the canonical test message subtype."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message_subtype"


class Message(MessagePublic, AbstractMessage):
    """Concrete message used by messaging tests.

    Folds posts' same-row ``MessagePublic`` extension (``is_original_post``) onto
    the one table, the way the composer emits ``Message(MessageExtension1,
    AbstractMessage)`` now that posts is a composed base addon.
    """

    rebac_grantable = AbstractMessage.rebac_grantable

    class Meta(AbstractMessage.Meta):
        """Django model options for the canonical test message."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message"
        rebac_resource_type = "messaging/message"


class ThreadAttachment(AbstractThreadAttachment):
    """Concrete record-thread attachment shared by the bare test runtime."""

    class Meta(AbstractThreadAttachment.Meta):
        """Django model options for the canonical test thread attachment."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread_attachment"
        rebac_resource_type = "messaging/thread_attachment"


class ThreadFollower(AbstractThreadFollower):
    """Concrete record-thread follower shared by the bare test runtime."""

    class Meta(AbstractThreadFollower.Meta):
        """Django model options for the canonical test thread follower."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread_follower"
        rebac_resource_type = "messaging/thread_follower"


class Part(AbstractPart):
    """Concrete message part shared by the bare test runtime."""

    class Meta(AbstractPart.Meta):
        """Django model options for the canonical test message part."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_part"
        rebac_resource_type = "messaging/part"


class ThreadNotification(AbstractThreadNotification):
    """Concrete notification shared by the bare test runtime."""

    class Meta(AbstractThreadNotification.Meta):
        """Django model options for the canonical test notification."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread_notification"
        rebac_resource_type = "messaging/thread_notification"


class Organization(AbstractOrganization, Party):
    """Concrete organization matching the composer inheritance shape."""

    class Meta(AbstractOrganization.Meta):
        """Django model options for the canonical test organization."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_organization"
        rebac_resource_type = "parties/organization"


class Address(AbstractAddress):
    """Concrete party address used by contact-ingest tests."""

    class Meta(AbstractAddress.Meta):
        """Django model options for the canonical test address."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_address"
        rebac_resource_type = "parties/address"


class Circle(AbstractCircle):
    """Concrete circle used by parties-schema imports across the suite."""

    class Meta(AbstractCircle.Meta):
        """Django model options for the canonical test circle."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_circle"
        rebac_resource_type = "parties/circle"


class CircleMember(AbstractCircleMember):
    """Concrete circle membership used by parties-schema imports across the suite."""

    class Meta(AbstractCircleMember.Meta):
        """Django model options for the canonical test circle membership."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_circle_member"
        rebac_resource_type = "parties/circle_member"


class RelationshipKind(AbstractRelationshipKind):
    """Concrete relationship kind used by parties-schema imports across the suite."""

    class Meta(AbstractRelationshipKind.Meta):
        """Django model options for the canonical test relationship kind."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_relationship_kind"
        rebac_resource_type = "parties/relationship_kind"


class Relationship(AbstractRelationship):
    """Concrete relationship edge used by parties-schema imports across the suite."""

    class Meta(AbstractRelationship.Meta):
        """Django model options for the canonical test relationship."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_relationship"
        rebac_resource_type = "parties/relationship"


class Reaction(AbstractReaction):
    """Concrete message reaction used by messaging tests."""

    class Meta(AbstractReaction.Meta):
        """Django model options for the canonical test reaction."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_reaction"
        rebac_resource_type = "messaging/reaction"


class MessageStar(AbstractMessageStar):
    """Concrete message star used by messaging tests."""

    class Meta(AbstractMessageStar.Meta):
        """Django model options for the canonical test message star."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message_star"
        rebac_resource_type = "messaging/message_star"


class MessageEdge(AbstractMessageEdge):
    """Concrete cross-message edge used by messaging tests."""

    class Meta(AbstractMessageEdge.Meta):
        """Django model options for the canonical test message edge."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message_edge"
        rebac_resource_type = "messaging/message_edge"


class Participant(AbstractParticipant):
    """Concrete participant used by messaging tests."""

    class Meta(AbstractParticipant.Meta):
        """Django model options for the canonical test participant."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_participant"
        rebac_resource_type = "messaging/participant"


class PartiesRole(AbstractPartiesRole):
    """Concrete table-less REBAC anchor for the ``parties/role`` namespace.

    The composer emits this anchor in the runtime; the bare test env registers it
    too so the const-backed role arms of ``parties/*`` resolve. ``managed = False``:
    never a table, only a type anchor.
    """

    class Meta(AbstractPartiesRole.Meta):
        """Django model options for the canonical test parties role anchor."""

        abstract = False
        managed = False
        app_label = "parties"
        rebac_resource_type = "parties/role"
