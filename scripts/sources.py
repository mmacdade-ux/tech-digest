"""Source list for the tech-news digest, grouped into newsletter sections.

Edit this file to add/remove feeds. Each RSS/Atom source is (name, url).
Community-buzz sources (Hacker News, Reddit) are handled specially in
build_digest.py because they need score parsing / throttling.
"""

# Standard RSS/Atom feeds, grouped by the section they appear under.
RSS_SECTIONS = [
    ("Top tech", [
        ("The Verge",    "https://www.theverge.com/rss/index.xml"),
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("Engadget",     "https://www.engadget.com/rss.xml"),
    ]),
    ("Apple", [
        ("MacRumors", "https://feeds.macrumors.com/MacRumors-All"),
        ("9to5Mac",   "https://9to5mac.com/feed/"),
    ]),
    ("Hardware & reviews", [
        ("RTINGS", "https://www.rtings.com/latest-rss.xml"),
    ]),
]

# Community buzz. HN front page filtered to higher-signal stories; Reddit as a
# combined multi-subreddit Atom feed (best-effort — Reddit rate-limits cloud IPs).
HN_FEED = "https://hnrss.org/frontpage?points=100"
REDDIT_FEED = "https://www.reddit.com/r/technology+gadgets+apple+hardware/.rss"

# Per-section cap so the email stays scannable.
SECTION_CAP = 7
# Only consider items published within this many hours.
FRESHNESS_HOURS = 48

# Resend free tier (no verified domain) only delivers to the address the Resend
# account was created with — here, m.macdade@griffith.edu.au. To send anywhere
# else (me.com / gmail), verify a domain in Resend or recreate the account there.
RECIPIENT = "m.macdade@griffith.edu.au"
FROM_ADDR = "onboarding@resend.dev"
