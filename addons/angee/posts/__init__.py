"""Posts addon: public feeds, engagement, following, and the public-thread surface.

Built on ``messaging`` — it *extends* the messaging Thread/Message rows through the
Angee ``extends`` seams (never by forking messaging) and reuses the one idempotent
``Message.objects.ingest`` write path. A ``Feed`` is an ``integrate.Integration``
child (a ``Bridge``) that polls an external platform for public posts; the posts
overlay (engagement ``PostMetrics``, per-actor reactions on the reused
``messaging.Reaction`` table, following via
``FeedFollow``, and per-integration API ``Quota``) rides on top. The dependency points
one way (posts → messaging → parties/integrate/storage). Public-post hashtags rest
losslessly in the ingested message's ``metadata["tags"]`` envelope pending the
backlogged vocabulary-governance decision; posts does not normalize them into Tag
rows or TagAssignment edges.
"""
