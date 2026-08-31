#!/usr/bin/env bash
#
# Rebuild every service and refuse to succeed unless they all agree on the
# prompt contract.
#
# This exists because the same failure has been made three ways:
#
#   docker compose restart      reuses the running container, so a changed
#                               .env is not reloaded
#   docker compose up -d        recreates with new env but does NOT rebuild,
#                               so changed code is not picked up
#   docker compose up -d --build api replay
#                               rebuilds the named services and leaves the
#                               others on the old image - and the analyzer,
#                               which is what actually performs replay
#                               analyses, is easy to leave out
#
# The third one cost a 24-analysis replay campaign: the analyzer ran the
# previous prompt, stamped the previous fingerprint, and the run looked
# entirely normal until the fingerprints were read afterwards.
#
# So the check is not "did the build command succeed". It is "do all five
# services now report the same prompt_fingerprint", asked of the running
# containers, because that is the thing that was actually wrong.
#
#   scripts/deploy.sh            rebuild, recreate, verify
#   scripts/deploy.sh --verify   verify only, no rebuild
#
set -euo pipefail

SERVICES=(api analyzer ingest replay paper)

cd "$(dirname "$0")/.."

verify() {
    local expected="" fp ver mismatch=0
    echo "fingerprint check:"
    for svc in "${SERVICES[@]}"; do
        if ! fp=$(docker compose exec -T "$svc" python -c \
            'from candle_agent.orchestrator import prompt_fingerprint; print(prompt_fingerprint())' \
            2>/dev/null | tr -d "\r"); then
            printf "  %-9s UNREACHABLE\n" "$svc"
            mismatch=1
            continue
        fi
        ver=$(docker compose exec -T "$svc" python -c \
            'from candle_agent.scoring import SCORER_VERSION; print(SCORER_VERSION)' \
            2>/dev/null | tr -d "\r")
        [ -z "$expected" ] && expected="$fp"
        if [ "$fp" = "$expected" ]; then
            printf "  %-9s %s  scorer=%s\n" "$svc" "$fp" "$ver"
        else
            printf "  %-9s %s  scorer=%s   <== DIFFERS from %s\n" \
                   "$svc" "$fp" "$ver" "$expected"
            mismatch=1
        fi
    done

    if [ "$mismatch" -ne 0 ]; then
        echo
        echo "REFUSING: the services do not agree on the prompt contract."
        echo "An analysis written now would be stamped with whichever"
        echo "fingerprint happened to serve it, and the pooling guard would"
        echo "then separate rows that belong together - or worse, pool rows"
        echo "that do not. Rebuild before running anything that costs tokens."
        return 1
    fi

    echo
    echo "all ${#SERVICES[@]} services agree: $expected"
}

if [ "${1:-}" = "--verify" ]; then
    verify
    exit $?
fi

echo "building ${SERVICES[*]} ..."
docker compose build "${SERVICES[@]}"

echo
echo "recreating ..."
docker compose up -d "${SERVICES[@]}"

# A container that has just started may not answer yet, and a connection
# refused here would read as a mismatch rather than as "not ready".
echo
printf "waiting for the api to answer "
for _ in $(seq 1 60); do
    if curl -fsS -m 2 http://localhost:8000/healthz >/dev/null 2>&1; then
        echo "- ready"
        break
    fi
    printf "."
    sleep 2
done
echo

verify
