# Plan

## Goals

1. **Topics of the week.** A weekly job finds three science topics that currently get public attention
   (science news, social media, video, forums), runs each through the pipeline as an OpenAlex docset and writes a
   short introduction that connects the research field to the current news. The landing page shows these three
   topics, each with a way into the topic landscape and the citation network.
2. **Public site.** A public address behind the Cloudflare tunnel, Impressum and privacy page, release images
   (`:release`) instead of `:main`.
3. **Open submissions with limits.** Anyone may submit an OpenAlex query: a server-side cap on the docset size, a
   daily and per-IP limit, a maximum queue length, no Fraunhofer source on the public site, user-chosen names kept
   off the public list.

## Not now

- An archive page of past weeks (the weekly files are kept, so it can be built later).
- Topic alerts (newsletter, feed).
- Comparing a topic's landscape week over week.

## Status

Goal 1 in progress (branch `weekly-topics`, not merged yet):
- `trends/`: daily headlines (science news feeds, Hacker News, Mastodon trends; Reddit, YouTube, Google Trends and
  Altmetric left out, see `trends/sources.py`), topics picked by Claude and ranked by coverage and Wikipedia
  pageviews, then per topic a Claude Code agent that finds the study behind the news and refines an OpenAlex query
  (200 to 2000 works, the study included) with `python -m trends probe`. Tried on four topics of 2026-W42: all
  found their study, 293 to 1849 works.
- All LLM calls go through headless Claude Code on the Claude subscription (`ClaudeCode.py`), the cluster names
  in `TopicModeling.py` too; OpenRouter is gone.
- Landing page at `/` (the docset form moved to `/new`), the visualisation opens a view from `?view=`.
- Timers and the `trends` job service: infra branch `thesis-trends`.

Before merging: the Claude token (`claude setup-token`) must be in the thesis `.env` on the server, because the
server deploys `:main` within minutes and the cluster names need it.
