"""Deterministic local candidate experiments. No availability assumptions or APIs."""
from collections import Counter, defaultdict, deque
from itertools import product
from pathlib import Path
import csv
import hashlib
import json
import random
import re

from . import store
from .ranking import WORDS

VERSION = 'generator-v1'
BRIEFS = {
    'solid': {'company': 'Workflow software for established operations teams',
              'audience': 'Business buyers who value reliability and clarity',
              'impression': 'Capable, grounded, dependable; room beyond one feature'},
    'catchy': {'company': 'A consumer app for discovering things to do with friends',
               'audience': 'People sharing recommendations in everyday conversation',
               'impression': 'Lively, approachable, easy to recall and say aloud'},
    'scientific': {'company': 'Research tools for laboratory and engineering teams',
                   'audience': 'Researchers and technical buyers',
                   'impression': 'Precise, curious, credible; avoid inflated claims'},
}
METAPHORS = {
    'solid': 'anchor anvil arch atlas ballast beacon bridge compass cornerstone crest forge harbor haven helm keystone ledger mast oak pillar plinth ridge root rudder slate span steel stone timber trestle vault vessel ward',
    'catchy': 'acorn berry bloom bounce breeze bubble cherry chirp clover confetti cricket dapple doodle feather finch fizz flicker fox glimmer hop kite lemon lilt lime otter pebble picnic pip plum poppy ripple skip spark sprout twirl wren',
    'scientific': 'aperture arc axis beam cadence cell chord cobalt comet crystal delta echo element field flux focus fractal gradient helios ion lattice lens lumen matrix meridian micron node nova orbit phase photon prism pulse quantum radial ray signal solar spectrum vector vertex wave',
}
RARE = {
    'solid': 'ashlar cairn cantle coign copse corbel ferrule fulcrum gable girder grange groat lintel lodestar mooring mullion palisade parapet quoin ravelin rivet scrip stanchion tiller truss vellum verdure weald wold',
    'catchy': 'amble aubade bobbin bower brio burble caper catkin coda corolla fable fettle floret glee halcyon jollity knoll linnet mallow mote murmur nimbus posy quaver rill siskin sorrel sylvan tangle tendril thimble tuft yarrow',
    'scientific': 'albedo aliquot apogee aster azimuth caustic centroid cilium clade codon conic corona eigen ephemeris equinox etalon facula geode gnomon lamina lentic locus lunule meson moraine nacre nadir nebula oculus parallax penumbra perihelion phasor quanta radian reticle sidereal solstice spectra stamen stratum tensor umbra vernier vesper voxel zephyr',
}
MODIFIERS = {
    'solid': 'able amber ash blue bold broad bronze calm cedar clear deep east elder even fair firm first flint golden grand green high iron level long maple north open plain quiet red round silver solid south steady steel stone tall true warm west white wide wild wise',
    'catchy': 'apple apricot berry bright brisk cherry coral cozy crisp curious dandy eager fresh gentle glad gleeful golden happy honey jaunty jolly keen lemon light lilac lime little lively lucky mellow merry mint nimble peach pink playful plum poppy quick rosy round ruby silver small soft spry sunny sweet swift teal tiny velvet vivid warm wild yellow zippy',
    'scientific': 'amber arc astral axial azure binary blue bright clear cobalt cosmic crystal delta dual echo even far fine fluid focal flux fractal fused glass golden gradient inert inner ion ivory light linear lucid lunar micro near neutral nova open orbit outer pale phase photon polar prime prism pure radial red silver solar spectral static stellar terra ultra violet wave white',
}
# Distinct noun vocabulary makes combinations more useful than arbitrary suffixes.
NOUNS = ('arch arrow bank basin bay beacon bell bend birch bird bloom bough branch bridge brook canvas canyon '
         'cedar chord circle cloud coast compass coral cove crane creek crest crown dawn delta dock drift dune '
         'echo elm ember fern field finch fleet flint flow forest forge fox frame garden gate glade glass glen '
         'glow grain grove harbor haven hazel heath hill hollow horizon island jade juniper key kite lake lamp '
         'lark leaf ledger light linen loft loom lotus maple marsh meadow mill mint mirror mist moon moss nest '
         'oak ocean olive orbit otter owl paper path peak pebble pine plane plum pond port prism pulse quarry '
         'rain raven reed reef ridge river robin rock root rose rowan sage sail salt sand seed shore signal '
         'sky slate snow sound spark spring spruce star stone stream summit sun tide timber trail tree vale '
         'valley vault vector vessel violet vista wave well willow wind wing winter wolf wood wren').split()
ONSETS = 'b c d f g h j k l m n p r s t v w z br cl cr dr fl fr gl gr pl pr sl sp st tr'.split()
VOWELS = 'a e i o u'.split()
TAILS = {
    'solid': 'ban ber bon dan den der don len ler lin lon man mar mer mon nor ren ron tan ten ton tor van ven ver vin'.split(),
    'catchy': 'ba bi bo da di do fa fi fo ka ki ko la li lo ma mi mo na ni no pa pi po ra ri ro sa si so ta ti to va vi vo za zi zo'.split(),
    'scientific': 'dar del dor lar len lex lin lor lum nar nel nex nor nus ral ran rel ren rex rin ris ron rus tal tan tel ter tis tor val var vel ven ver'.split(),
}


def acceptable(label):
    return (4 <= len(label) <= 13 and label.isascii() and label.isalpha()
            and not re.search(r'(.)\1\1|[aeiou]{3}|[bcdfghjklmnpqrstvwxz]{5}', label))


def pool(seed):
    groups = {}
    known = set(WORDS) | set(NOUNS)
    for vocabulary in (METAPHORS, RARE, MODIFIERS):
        for words in vocabulary.values():
            known.update(words.split())
    for brief in BRIEFS:
        endings = {'solid': ['a', 'o', 'en'], 'catchy': ['la', 'na', 'ro', 'vi', 'mo', 'ya'],
                   'scientific': ['a', 'o', 'ia']}[brief]
        raw = {
            'metaphor': [(word,) for word in METAPHORS[brief].split()],
            'uncommon': [(word,) for word in RARE[brief].split()],
            'compound': [(a, b) for a, b in product(MODIFIERS[brief].split(), NOUNS)
                         if a != b and not a.endswith(b) and not b.startswith(a)],
            'invented': [(a + v, tail, ending) for a, v, tail, ending in product(ONSETS, VOWELS, TAILS[brief], endings)
                         if 6 <= len(a + v + tail + ending) <= 9 and a + v + tail + ending not in known],
        }
        for strategy, parts in raw.items():
            rows = [dict(domain=''.join(p) + '.com', brief=brief, strategy=strategy, components=list(p))
                    for p in parts if acceptable(''.join(p))]
            random.Random(f'{VERSION}:{seed}:{brief}:{strategy}').shuffle(rows)
            groups[brief, strategy] = deque(rows)
    return groups


def generate(db, *, count=25000, seed=42, run='experiment-001'):
    if not 1 <= count <= 100000 or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', run):
        raise ValueError('Count must be 1–100000; run must be a short alphanumeric identifier')
    if db.execute('SELECT 1 FROM candidates WHERE run=? LIMIT 1', (run,)).fetchone():
        raise ValueError('This run already exists; choose a new --run identifier')
    groups, seen, rows = pool(seed), set(), []
    while groups and len(rows) < count:
        for key in list(groups):
            queue = groups[key]
            while queue and queue[0]['domain'] in seen:
                queue.popleft()
            if not queue:
                del groups[key]
                continue
            row = queue.popleft()
            seen.add(row['domain'])
            rows.append(row)
            if len(rows) == count:
                break
    for row in rows:
        store.put(db, row['domain'], source=f'generated:{run}', kind='generated')
        metadata = {'parts': row['components'], 'seed': seed, 'version': VERSION}
        db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)',
                   (row['domain'], run, row['brief'], row['strategy'], json.dumps(metadata)))
    return {'run': run, 'requested': count, 'generated': len(rows), 'seed': seed,
            'pool_exhausted': len(rows) < count,
            'strategies': dict(Counter(r['strategy'] for r in rows)), 'briefs': BRIEFS}


def select_batch(db, *, run, count, seed=42, max_component=100):
    """Balance brief/strategy cells; interleave 80% ranked and 20% exploration."""
    if not 1 <= count <= 5000 or max_component < 1:
        raise ValueError('Batch count must be 1–5000; component cap must be positive')
    groups = defaultdict(list)
    rows = db.execute('''SELECT c.*,r.score FROM candidates c JOIN rankings r
        ON r.domain=c.domain AND r.profile=c.brief JOIN domains d ON d.domain=c.domain
        WHERE c.run=? AND (d.review IS NULL OR d.review!='reject') ORDER BY c.domain''', (run,))
    for raw in rows:
        row = dict(raw)
        row['parts'] = json.loads(row['components'])['parts']
        # Tiny endings are phonetic glue, not repeated semantic roots.
        row['diversity_parts'] = row['parts'][:-1] if row['strategy'] == 'invented' else row['parts']
        groups[row['brief'], row['strategy']].append(row)
    if not groups:
        raise ValueError('No eligible candidates for this run')
    queues = {}
    for key, group in sorted(groups.items()):
        rng = random.Random(f'{seed}:{key}')
        rng.shuffle(group)  # Randomized ties, not alphabetical bias.
        ranked = sorted(group, key=lambda r: r['score'], reverse=True)
        exploration = list(group)
        rng.shuffle(exploration)
        queues[key] = (deque(ranked), deque(exploration))
    selected, seen, usage, steps = [], set(), Counter(), Counter()
    while queues and len(selected) < count:
        for key in list(queues):
            ranked, exploratory = queues[key]
            explore = steps[key] % 5 == 4
            queue = exploratory if explore else ranked
            steps[key] += 1
            chosen = None
            while queue:
                row = queue.popleft()
                if row['domain'] in seen or any(usage[p] >= max_component for p in set(row['diversity_parts'])):
                    continue
                chosen = dict(row, selection='exploration' if explore else 'ranked')
                break
            if chosen:
                selected.append(chosen)
                seen.add(chosen['domain'])
                usage.update(set(chosen['diversity_parts']))
            if not ranked and not exploratory:
                del queues[key]
            if len(selected) == count:
                break
    random.Random(seed).shuffle(selected)  # Blind review order does not reveal strategy blocks.
    return selected


def export_check(db, *, run, output, count=5000, seed=42, max_component=100):
    rows = select_batch(db, run=run, count=count, seed=seed, max_component=max_component)
    output = Path(output)
    # Never silently replace an earlier experiment or a hand-filled results file.
    output.mkdir(parents=True, exist_ok=False)
    names = ''.join(row['domain'] + '\n' for row in rows)
    (output / 'domains.txt').write_text(names)
    fields = ['domain', 'status', 'price', 'currency', 'checked_at', 'term_months', 'evidence_url']
    with (output / 'results-template.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows({'domain': row['domain']} for row in rows)
    manifest = {'run': run, 'seed': seed, 'requested': count, 'exported': len(rows),
                'created_at': store.now(), 'max_component': max_component,
                'sha256': hashlib.sha256(names.encode()).hexdigest(),
                'briefs': BRIEFS, 'candidates': rows}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    review = ['# Naming review', '', 'All names are unchecked. Scores and strategies are hidden.',
              'Judge fit for the brief; mark keep / pass / unsure and add a reason.', '']
    for brief, description in BRIEFS.items():
        review.extend([f'## {brief.title()}', '', description['company'] + '. ' + description['impression'] + '.', '',
                       '| Name | Judgment | Reason |', '|---|---|---|'])
        review.extend(f"| {row['domain']} | | |" for row in rows if row['brief'] == brief)
        review.append('')
    (output / 'review.md').write_text('\n'.join(review))
    return {'output': str(output), 'exported': len(rows), 'requested': count,
            'underfilled': len(rows) < count,
            'strategies': dict(Counter(row['strategy'] for row in rows)),
            'selection': dict(Counter(row['selection'] for row in rows))}
