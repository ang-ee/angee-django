"""Packaged contact-domain data used by parties suggestion rules."""

GENERIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "aol.com",
        "fastmail.com",
        "gmail.com",
        "googlemail.com",
        "gmx.com",
        "gmx.de",
        "hey.com",
        "hotmail.co.uk",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "tutanota.com",
        "yahoo.co.uk",
        "yahoo.com",
        "yandex.com",
        "zoho.com",
    }
)
"""Mailbox-provider domains that must never imply organization membership."""
