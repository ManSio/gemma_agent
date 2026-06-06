#!/usr/bin/env bash
# Патч .env на сервере: новости только из поиска + narrative + кэш LLM.
set -euo pipefail
ENV_FILE="${1:-/opt/gemma_agent/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE" >&2
  exit 1
fi
declare -A PAIRS=(
  [NEWS_DIGEST_SEARCH_ONLY]=true
  [NEWS_DIGEST_NARRATIVE_STYLE]=per_item
  [NEWS_DIGEST_NARRATIVE_SENTENCES_PER_ITEM]=4
  [NEWS_DIGEST_CACHE_ENABLED]=true
  [NEWS_DIGEST_CACHE_TTL_SEC]=3600
  [NEWS_STORY_DEEP_FOLLOWUP_ENABLED]=true
  [NEWS_PIPELINE_RSS_ON_SEARCH_FAIL]=false
  [NEWS_ITEM_RSS_RESOLVE_ENABLED]=false
  [NEWS_RSS_FALLBACK_ENABLED]=false
  [NEWS_SEARCH_SEARX_NEWS_CATEGORY]=false
  [NEWS_DIGEST_MIN_ITEMS]=2
  [NEWS_DIGEST_MAX_SEARCH_QUERIES]=5
  [UNIVERSAL_SEARCH_LOCAL_FIRST]=true
  [BRAIN_PIPELINE_NEWS_SHORT_CIRCUIT]=true
  [NEWS_DIGEST_LLM_MODEL]=deepseek/deepseek-v4-flash
)
for key in "${!PAIRS[@]}"; do
  val="${PAIRS[$key]}"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '\n# news search-only (patch_news_env.sh)\n%s=%s\n' "$key" "$val" >>"$ENV_FILE"
  fi
done
echo "=== patched $(grep -E '^NEWS_DIGEST_|^NEWS_STORY_|^NEWS_PIPELINE_RSS|^NEWS_ITEM_RSS|^NEWS_RSS_FALLBACK' "$ENV_FILE" | grep -v '^#')"
