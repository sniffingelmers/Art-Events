# Bay Area Art & Events RSS

This repository turns the verified sources in `sources.json` into a single RSS feed.

## Automatic updates

GitHub Actions rebuilds the feed every 3 hours and can also be run manually from the Actions tab. GitHub scheduled workflows use cron schedules. See GitHub's documentation for details.

## Feed

The generated file is `feed.xml`. A raw GitHub URL can be used by RSS readers:

https://raw.githubusercontent.com/sniffingelmers/Art-Events/main/feed.xml

## Adding sources

Add an object containing `name` and `url` to `sources.json`. The scraper first looks for RSS/Atom feeds and then for Schema.org Event data, with a conservative HTML-card fallback.

Some sources in the original Art List Bay Area are names without a URL. Those are intentionally not guessed: they should be verified and added to `sources.json` rather than silently pointing the feed at the wrong site.

## Notes

- Sources are fetched independently; one failure does not stop the run.
- Events are limited to the next 180 days.
- Duplicate title/link combinations are collapsed.
- The feed keeps the next 300 upcoming items.
- Site terms, robots policies, and rate limits should be respected.
