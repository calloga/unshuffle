from collections import Counter, defaultdict
from dataclasses import dataclass

from unshuffle.core.constants import get_runtime_config_snapshot
from unshuffle.logic.classification import tokenize


@dataclass(frozen=True)
class AliasLookupResult:
    status: str
    rows: list[tuple[str, str, str, str]]
    cooccurrences: list[tuple[str, int]]
    allows_add: bool
    search_status: str


def candidate_token(alias: str) -> str:
    raw = (alias or "").strip().lower()
    tokens = tokenize(raw)
    return next(iter(tokens)) if len(tokens) == 1 else ""


def alias_map(controller) -> dict[str, tuple[str, float, str]]:
    bridge = controller._bridge()
    if bridge and bridge.has_session():
        return bridge.get_aliases_with_source() or {}
    runtime = get_runtime_config_snapshot()
    return {
        alias: (
            str(payload[0] if isinstance(payload, (list, tuple)) else payload),
            float(payload[1] if isinstance(payload, (list, tuple)) and len(payload) > 1 else 1.0),
            "system",
        )
        for alias, payload in (runtime.get("alias_table", {}) or {}).items()
    }


def aliases_containing_token(
    alias_map: dict[str, tuple[str, float, str]],
    token: str,
    category: str | None,
) -> list[tuple[str, str, str]]:
    matches: list[tuple[str, str, str]] = []
    for alias, payload in sorted(alias_map.items()):
        hit_category, _weight, source = payload
        if category is not None and hit_category != category:
            continue
        if token in tokenize(alias):
            matches.append((alias, hit_category, source))
    return matches


def cooccurrences_for_token(controller, token: str, alias_map: dict[str, tuple[str, float, str]]) -> list[tuple[str, int]]:
    model = getattr(controller.app, "model", None)
    records = list(getattr(model, "records", []) or [])
    entries = [
        {"name": rec.source_path.name, "tokens": sorted(tokenize(rec.source_path.name))}
        for rec in records
    ]
    weighted_tokens = controller.discovery_bridge.get_all_weighted_tokens(alias_map)
    return controller.discovery_bridge.scan_discovery_data(token, entries, weighted_tokens)[:100]


def alias_lookup(controller, alias: str, category: str) -> AliasLookupResult:
    token = controller._candidate_token(alias)
    if not token:
        return AliasLookupResult(
            status="Use one token for V1 alias lookup. Phrase variants can be selected after lookup.",
            rows=[],
            cooccurrences=[],
            allows_add=False,
            search_status="Taxonomy: lookup needs a single token.",
        )
    if not category or category.lower() == "all":
        return AliasLookupResult(
            status="Choose a concrete category before lookup.",
            rows=[],
            cooccurrences=[],
            allows_add=False,
            search_status="Taxonomy: choose a category.",
        )

    aliases = controller._alias_map()
    selected_matches = controller._aliases_containing_token(aliases, token, category)
    conflict_matches = [
        row for row in controller._aliases_containing_token(aliases, token, None)
        if row[1] != category
    ]

    rows: list[tuple[str, str, str, str]] = []
    for alias_hit, hit_category, source in selected_matches:
        rows.append(("Already covered", alias_hit, hit_category, source))
    for alias_hit, hit_category, source in conflict_matches:
        rows.append(("Possible meaning conflict", alias_hit, hit_category, source))

    if selected_matches:
        status = f"'{token}' already appears in {category}. No new alias is needed."
        cooccurrences: list[tuple[str, int]] = []
        allows_add = False
    else:
        status = f"'{token}' is a possible taxonomy gap for {category}."
        if conflict_matches:
            status += " Review possible meaning conflicts before adding it."
        cooccurrences = controller._cooccurrences_for_token(token, aliases)
        allows_add = True

    return AliasLookupResult(
        status=status,
        rows=rows,
        cooccurrences=cooccurrences,
        allows_add=allows_add,
        search_status="Taxonomy: lookup complete.",
    )


def discovery_rows(records) -> list[tuple[str, str, str, object]]:
    return [
        (
            rec.source_path.name,
            getattr(rec, "pack", ""),
            str(rec.source_path),
            getattr(rec, "confidence", ""),
        )
        for rec in records[:250]
    ]


def probable_gaps(controller, records) -> list[tuple[str, int, str]]:
    aliases = controller._alias_map()
    weighted_tokens = controller.discovery_bridge.get_all_weighted_tokens(aliases)
    noise = set(get_runtime_config_snapshot().get("noise_words", set()))

    engine = controller._engine()
    cluster_by_record_id = {}
    if engine and getattr(engine, "db", None):
        try:
            results = engine.db.list_coherence_results(engine.session_id)
            for res in results:
                rid = str(res.get("record_id") or "")
                cid = str(res.get("cluster_id") or "")
                if rid and cid:
                    cluster_by_record_id[rid] = cid
        except Exception:
            pass

    counts: Counter[str] = Counter()
    context: dict[str, Counter[str]] = defaultdict(Counter)
    token_to_clusters: dict[str, list[str]] = defaultdict(list)

    for idx, rec in enumerate(records):
        rec_id = str(getattr(rec, "staging_row_id", idx) if getattr(rec, "staging_row_id", None) is not None else idx)
        cluster_id = cluster_by_record_id.get(rec_id)

        tokens = tokenize(rec.source_path.name)
        known = tokens & weighted_tokens
        if not known:
            continue
        for token in tokens - weighted_tokens - noise:
            if len(token) < 2 or token.isdigit():
                continue
            counts[token] += 1
            context[token].update(known)
            if cluster_id:
                token_to_clusters[token].append(cluster_id)

    cohesive_tokens = {}
    for token, clusters in token_to_clusters.items():
        if not clusters:
            continue
        cluster_counts = Counter(clusters)
        _dominant_cluster, dom_count = cluster_counts.most_common(1)[0]
        purity = dom_count / len(clusters)
        if purity >= 0.50:
            cohesive_tokens[token] = purity

    if not counts:
        return []
    threshold = max(3, min(12, len(records) // 25))
    rows = []
    for token, count in counts.most_common(40):
        if count < threshold:
            continue

        if token_to_clusters[token] and token not in cohesive_tokens:
            continue

        common_context = ", ".join(item for item, _ in context[token].most_common(4))
        rows.append((token, count, common_context))
    return rows
