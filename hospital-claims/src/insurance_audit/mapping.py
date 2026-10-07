"""Four-state description matcher. No billed price is ever an input.

A line's description is scored against the *names of the contracted services*
only. Nothing in this module reads `unit_price_cents`, `line_total_cents`,
`quantity`, `invoice_total_cents` or any rate: a description means what it
says, and a price that disagrees with the contract is the error being looked
for, never evidence about which service was delivered.

The four states:

- `MATCH`      best score >= MIN_SCORE and margin to the runner-up >= MIN_MARGIN
- `TIE`        best score >= MIN_SCORE but the margin is smaller
- `WEAK`       best score between NO_MATCH_FLOOR and MIN_SCORE
- `NO_MATCH`   best score below NO_MATCH_FLOOR; the description names no
               contracted service, and that is itself the error
"""
import re
import unicodedata

# Starting thresholds. NO_MATCH_FLOOR is applied to the share of the
# *billed* tokens a contracted name explains, and was set by a sweep on the
# Hospital 1 development partition.
MIN_SCORE = 0.60
MIN_MARGIN = 0.08
NO_MATCH_FLOOR = 0.90
# Candidates offered for a WEAK line, which are priced under every reading.
CANDIDATE_FLOOR = 0.40

CODE_SUFFIX = re.compile(r'\s*/(?:[a-z]{2})-\d+\s*$')


def tokens(text):
    """Lower-cased alphanumeric tokens, with any trailing service code removed."""
    text = unicodedata.normalize('NFKC', text or '').lower()
    text = CODE_SUFFIX.sub('', text)
    return re.findall(r'[a-z0-9]+', text)


def expansions(token, lexicon):
    """Every full form a description token may stand for, including itself."""
    return {token, *lexicon.get(token, ())}


def one_edit_apart(first, second):
    """A single substitution or a single dropped character, on long tokens only.

    Short tokens are excluded because at five characters and under a single edit
    separates genuinely different clinical words.
    """
    if min(len(first), len(second)) < 6 or abs(len(first) - len(second)) > 1:
        return False
    if len(first) == len(second):
        return sum(a != b for a, b in zip(first, second)) == 1
    short, long_ = (first, second) if len(first) < len(second) else (second, first)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def token_matches(billed, contracted, lexicon):
    """Whether one description token can denote one contracted-name token.

    Four ways, all of them wording-only: the same word, a reviewed abbreviation
    from the lexicon, a prefix of at least three characters, which is how these
    snapshots abbreviate a word the lexicon does not list, or a single-character
    difference in a long word, which is a typo rather than a different service.
    """
    if billed == contracted:
        return True
    if contracted in expansions(billed, lexicon):
        return True
    if len(billed) >= 3 and contracted.startswith(billed):
        return True
    return one_edit_apart(billed, contracted)


def paired(description_tokens, name_tokens, lexicon):
    """How many description tokens a contracted name can account for.

    Each contracted-name token is consumed at most once, so repeating a word
    cannot inflate the count.
    """
    remaining = list(name_tokens)
    matched = 0
    for billed in description_tokens:
        for index, contracted in enumerate(remaining):
            if token_matches(billed, contracted, lexicon):
                matched += 1
                remaining.pop(index)
                break
    return matched


def coverage(description_tokens, name_tokens, lexicon):
    """Share of the *billed* tokens this contracted name explains, in [0, 1].

    This is the quantity that decides NO_MATCH. A legitimate abbreviation omits
    words from the contracted name but never adds one: every token the provider
    wrote is accounted for. A description that leaves a token unexplained by
    every service in the contract names something the contract does not sell,
    and that fact is itself the error.
    """
    if not description_tokens:
        return 0.0
    return paired(description_tokens, name_tokens, lexicon) / len(description_tokens)


def score(description_tokens, name_tokens, lexicon):
    """Share of the longer side that can be paired off, in [0, 1].

    Each contracted-name token is consumed at most once, so repeating a word
    cannot inflate the score. Dividing by the longer side penalises both a
    description that omits words and one that adds them.
    """
    if not description_tokens or not name_tokens:
        return 0.0
    return paired(description_tokens, name_tokens, lexicon) / max(len(description_tokens), len(name_tokens))


def rank(description, services, lexicon):
    """Every service scored against one description, best first."""
    billed = tokens(description)
    scored = [(score(billed, tokens(service['name']), lexicon), service['id']) for service in services]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return scored


def canonical_multiset(description, lexicon):
    """The description's words, expanded and sorted, so wording order is irrelevant.

    Reviewers accepted a wording, not a spelling of it. Reordering the words or
    swapping an abbreviation for the word it stands for does not make it a
    different description, so the reviewed decision must survive both.
    """
    expanded = []
    for token in tokens(description):
        forms = lexicon.get(token)
        expanded.append(forms[0] if forms else token)
    return tuple(sorted(expanded))


def vocabulary(services, lexicon):
    """Every word the contract itself uses, plus the reviewed abbreviations."""
    words = {token for service in services for token in tokens(service['name'])}
    words.update(lexicon)
    words.update(form for forms in lexicon.values() for form in forms)
    return words


def one_edit_neighbours(token, words):
    """Words that differ from this token by a single character.

    Unlike `one_edit_apart` this has no length floor: it is used to ask whether
    a word could be a mistyping of a contract word, not whether two words mean
    the same thing.
    """
    found = set()
    for word in words:
        if abs(len(word) - len(token)) > 1 or len(token) < 3:
            continue
        if word == token:
            continue
        if len(word) == len(token):
            if sum(a != b for a, b in zip(word, token)) == 1:
                found.add(word)
        else:
            short, long_ = (token, word) if len(token) < len(word) else (word, token)
            if any(long_[:i] + long_[i + 1:] == short for i in range(len(long_))):
                found.add(word)
    return found


def repairable(description, services, lexicon, floor=NO_MATCH_FLOOR):
    """Whether changing one character of one word would explain the description.

    A description the contract cannot account for may be a claim about a service
    that does not exist, or it may be a mistyping of one that does. Where a
    single character separates it from a description the contract fully
    explains, it is read as the mistyping.
    """
    billed = tokens(description)
    words = vocabulary(services, lexicon)
    names = [tokens(service['name']) for service in services]
    for position, token in enumerate(billed):
        for candidate in one_edit_neighbours(token, words):
            repaired = billed[:position] + [candidate] + billed[position + 1:]
            if any(coverage(repaired, name, lexicon) >= floor for name in names):
                return True, {'position': position, 'billed_token': token, 'contract_word': candidate}
    return False, None


def unexplained(description, services, lexicon):
    """The billed tokens the closest contracted service cannot account for."""
    billed = tokens(description)
    if not billed or not services:
        return billed
    best, _ = max(((coverage(billed, tokens(s['name']), lexicon), s['id']) for s in services),
                  key=lambda pair: (pair[0], pair[1]))
    closest = max(services, key=lambda s: (coverage(billed, tokens(s['name']), lexicon), s['id']))
    remaining = list(tokens(closest['name']))
    left = []
    for token in billed:
        for index, contracted in enumerate(remaining):
            if token_matches(token, contracted, lexicon):
                remaining.pop(index)
                break
        else:
            left.append(token)
    return left


def best_coverage(description, services, lexicon):
    """The most of this description any single contracted service can explain."""
    billed = tokens(description)
    if not billed or not services:
        return 0.0, None
    best = max(((coverage(billed, tokens(s['name']), lexicon), s['id']) for s in services),
               key=lambda pair: (pair[0], pair[1]))
    return best


def names_no_contracted_service(description, services, lexicon, floor=NO_MATCH_FLOOR):
    """True when no service in the contract accounts for what was billed.

    A word left unexplained only counts when the contract uses that word
    somewhere else. "Adv Renal Consultation" leaves "renal" unexplained, and
    renal is a specialty this contract sells in other combinations, so the
    provider has named a service the contract does not offer. A word the
    contract never uses anywhere is far more likely to be a corrupted or
    reworded description than a claim about a service, and reporting it would
    turn ordinary wording drift into a false positive.
    """
    share, service_id = best_coverage(description, services, lexicon)
    if share >= floor:
        return False, {'best_explained_share': round(share, 6), 'closest_service_id': service_id,
                       'no_match_floor': floor, 'unexplained_tokens': []}
    left = unexplained(description, services, lexicon)
    known = vocabulary(services, lexicon)
    foreign = sorted(set(left) - known)
    evidence = {'best_explained_share': round(share, 6), 'closest_service_id': service_id,
                'no_match_floor': floor, 'unexplained_tokens': sorted(set(left)),
                'tokens_the_contract_never_uses': foreign}
    if foreign:
        evidence['withheld_reason'] = ('A word the contract never uses is wording drift, not a claim about a '
                                       'service the contract lacks.')
        return False, evidence
    mistyped, repair = repairable(description, services, lexicon, floor)
    if mistyped:
        evidence['withheld_reason'] = ('One character separates this from a description the contract fully '
                                       'explains, so it is read as a mistyping.')
        evidence['single_character_repair'] = repair
        return False, evidence
    return True, evidence


def classify(description, services, lexicon, min_score=MIN_SCORE, min_margin=MIN_MARGIN,
             floor=NO_MATCH_FLOOR):
    """The four-state decision plus the evidence behind it."""
    scored = rank(description, services, lexicon)
    if not scored:
        return {'state': 'NO_MATCH', 'service_id': None, 'candidates': [], 'best_score': 0.0, 'margin': 0.0}
    best, best_id = scored[0]
    runner_up = next((value for value, ident in scored[1:] if ident != best_id), 0.0)
    margin = best - runner_up
    tied = [ident for value, ident in scored if value >= min_score and best - value < min_margin]
    explained, _ = best_coverage(description, services, lexicon)
    if explained < floor:
        # Even a description the contract cannot account for has readings worth
        # pricing: if every one of them finds the same fault, the fault holds
        # whichever service was meant.
        state, service_id = 'NO_MATCH', None
        candidates = [ident for value, ident in scored if value >= CANDIDATE_FLOOR][:8]
    elif best < min_score:
        state, service_id, candidates = 'WEAK', None, [ident for value, ident in scored if value >= CANDIDATE_FLOOR][:8]
    elif margin >= min_margin:
        state, service_id, candidates = 'MATCH', best_id, [best_id]
    else:
        state, service_id, candidates = 'TIE', None, tied[:8]
    return {'state': state, 'service_id': service_id, 'candidates': sorted(candidates),
            'best_score': round(best, 6), 'best_service_id': best_id, 'margin': round(margin, 6),
            'best_explained_share': round(explained, 6),
            'thresholds': {'min_score': min_score, 'min_margin': min_margin, 'no_match_floor': floor}}
