"""
Topics of the week: finds science topics with public attention and features them on the landing page.

The weekly run (`python -m trends weekly`, see __main__.py) goes through these steps, each of which can also
be run on its own:

1. collect   sources.py   headlines and posts from news, social media and video (signals.json)
2. propose   select.py    an LLM groups the signals into candidate research topics, each with an OpenAlex query;
                          OpenAlex checks that the field is large enough and how fast it grows (candidates.json)
3. queue     runs.py      the three best candidates are queued as OpenAlex docsets for the worker (picked.json)
4. write     write.py     once the worker has finished them, an LLM writes each topic's introduction from the
                          signals and the docset's top-level clusters (featured/<week>.json, featured/current.json)

Working files live in data/trends/<week>/ (not served); the landing page reads data/featured/current.json.
Nothing here imports torch: the pipeline runs happen in the worker.
"""
