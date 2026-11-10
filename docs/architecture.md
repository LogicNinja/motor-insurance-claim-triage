# Architecture

Paste the final diagram here and keep it in sync with docker-compose.yml.

```
client -> api -> redis queue -> worker (analyst agent) -> retrieval -> chroma
                                   |+-> verifier (second agent)
                                   +-> llm (stub or provider)
postgres: runs, traces, quarantine, feedback
```

## Deployment boundaries (justify each on scaling, failure isolation, release, resources)

| Boundary | Why it is separate |
|----------|--------------------|
| api | Scales with request rate; must stay up while workers are busy or restarting |
| worker | Scales with agent load (`--scale worker=3`); crashes must not take the API down |
| verifier | Independent second agent; can be released and failed independently |
| retrieval | Holds the embedding model in memory; keeping it out of scaled workers saves RAM |
| redis / postgres / chroma | Stateful stores with their own lifecycle and volumes |
| stub-llm | Test double; never ships with the real provider |

## Run states

queued -> analysing -> verifying -> completed | escalated | degraded | failed
(`failed` never carries a coverage position.)
