"""Construct complete owned inputs for independently verified exact-code siblings.

Code/header provenance is static. Runtime-dependent fields remain an explicitly
recorded, complete observed context, never a guessed descriptor or a declaration
that the target will execute in that context. The ordinary native recompiler
must verify specialization, SRT and SPIR-V again under the TARGET program hash.
"""
from pathlib import Path
import collections, copy, hashlib, json, struct
from game_module_inputs import need, freeze_module, source_code
from static_program_links import canonical, MAX_HEADERS

SUPPORTED = {'compute', 'fragment'}


def program_marker_equivalent(expected, actual, source, target):
    """Only the emitter's one OpString(program hash) may differ.

    Neither binary is modified. All semantic instructions, other debug data,
    result IDs and module header words must remain byte exact.
    """
    if (len(expected) % 4 or len(actual) % 4 or min(len(expected), len(actual)) < 20 or
            len(expected) > 512 * 1024 or len(actual) > 512 * 1024 or source == target):
        return False
    a = struct.unpack('<' + 'I' * (len(expected) // 4), expected)
    b = struct.unpack('<' + 'I' * (len(actual) // 4), actual)
    if a[:5] != b[:5] or a[0] != 0x07230203:
        return False
    def marker(words):
        if len(words) < 3 or words[0] & 65535 != 7:
            return None
        raw = struct.pack('<' + 'I' * (len(words) - 2), *words[2:])
        end = raw.find(b'\0')
        if end < 0 or any(raw[end:]):
            return None
        return raw[:end]
    old = f'0x{int(source, 16):x}'.encode(); new = f'0x{int(target, 16):x}'.encode()
    i = j = 5; changed = 0
    while i < len(a) and j < len(b):
        na, nb = a[i] >> 16, b[j] >> 16
        if not na or not nb or na > len(a)-i or nb > len(b)-j:
            return False
        x, y = a[i:i+na], b[j:j+nb]
        if marker(x) == old and marker(y) == new:
            if x[1] != y[1]:
                return False
            changed += 1
        elif x != y:
            return False
        i += na; j += nb
    return i == len(a) and j == len(b) and changed == 1


def header_contract(record):
    variants = record['variants']
    need(len(variants) == 1, 'one unambiguous complete header variant required')
    facts = copy.deepcopy(variants[0]['facts'])
    # These are verified FILE-relative code relocations, not compile inputs.
    # Every other field is retained, including opaque/uninterpreted fields.
    for field in ('program_low', 'program_high'):
        need(field in facts['registers'], 'complete relocation register pair')
        del facts['registers'][field]
    return hashlib.sha256(canonical(facts).encode()).hexdigest()


def source_groups(records):
    need(len(records) <= MAX_HEADERS, 'static source count budget')
    table = collections.defaultdict(list)
    for record in records:
        identity = record['identity']
        table[identity['program_hash'], identity['hardware_stage']].append(record)
    groups = collections.defaultdict(list)
    for matches in table.values():
        if len(matches) != 1 or len(matches[0]['variants']) != 1:
            continue
        record = matches[0]; identity = record['identity']
        if identity['hardware_stage'] in SUPPORTED:
            groups[identity['hardware_stage'], identity['code_sha256'], header_contract(record)].append(record)
    return table, groups


def candidates(units, records):
    """One deterministic real context per target; no context cartesian product."""
    table, groups = source_groups(records)
    chosen = {}
    for unit in units:
        if (unit['record_type'] != 'offline-pso-input' or
                unit['execution_policy']['candidate_preparation']['policy_eligible'] is not True):
            continue
        for stage in unit['stages']:
            matches = table.get((stage['program_hash'], stage['hardware_stage']), [])
            if (stage['hardware_stage'] not in SUPPORTED or len(matches) != 1 or
                    len(matches[0]['variants']) != 1):
                continue
            source = matches[0]; identity = source['identity']
            group = groups[identity['hardware_stage'], identity['code_sha256'], header_contract(source)]
            for target in group:
                if target['identity']['program_hash'] == identity['program_hash']:
                    continue
                key = (target['identity']['program_hash'], target['identity']['hardware_stage'],
                       unit['profile']['reference']['sha256'])
                rank = (stage['serialized_spec_sha256'], unit['profile']['reference']['sha256'],
                        stage['metadata']['sha256'], identity['program_hash'])
                item = dict(unit=unit, stage=stage, source=source, target=target)
                if key not in chosen or rank < chosen[key][0]:
                    chosen[key] = (rank, item)
                need(len(chosen)<=MAX_HEADERS,'bounded static-context candidate registry')
    return [chosen[key][1] for key in sorted(chosen)]


def freeze(candidate, *, layout, catalog, output):
    """Reuse the original owned constructor and retain all its native gates."""
    source, target = candidate['source'], candidate['target']
    original, wanted = source['identity'], target['identity']
    need(original['hardware_stage'] in SUPPORTED and
         original['hardware_stage'] == wanted['hardware_stage'], 'supported same hardware stage')
    need(original['program_hash'] != wanted['program_hash'], 'different target program required')
    need(original['code_sha256'] == wanted['code_sha256'], 'identical independently hashed code required')
    contract = header_contract(source)
    need(contract == header_contract(target), 'complete static header contract differs')
    stage, unit = candidate['stage'], candidate['unit']
    need((stage['program_hash'], stage['hardware_stage']) ==
         (original['program_hash'], original['hardware_stage']), 'observed context source identity')
    evidence = source['variants'][0]['evidence_refs'][0]
    packet = freeze_module(stage=stage, profile_reference=unit['profile']['reference'],
        layout=layout, game_root=catalog['game_root'], catalog=catalog,
        static_identity=original, evidence=evidence, output=output)
    target_evidence = target['variants'][0]['evidence_refs'][0]
    code, provenance = source_code(catalog['game_root'], catalog, wanted, target_evidence)
    need(code == Path(packet['code']['path']).read_bytes(), 'independent target/template code byte equality')
    packet['static_derivation'] = dict(schema=1, algorithm='exact-code-header-observed-context-v1',
        template_program_hash=original['program_hash'],
        template_source_provenance=packet['source_provenance'],
        template_header_variant=source['variants'][0]['id'], target_header_variant=target['variants'][0]['id'],
        complete_header_contract_sha256=contract, context_origin='complete-observed-input',
        descriptors_synthesized=False, effective_target_context_claim=False,
        native_target_retranslation_required=True, native_spirv_semantic_equality_required=True,
        allowed_reference_difference='one-program-source-OpString')
    packet['program_hash'] = wanted['program_hash']
    packet['kind'] = 'owned-gcn-static-variant-input'
    packet['source_provenance'] = provenance
    path = Path(output) / 'packet.json'
    path.write_text(json.dumps(packet, sort_keys=True, separators=(',', ':')) + '\n')
    return packet


def validate_derivation(packet, catalog, table):
    """Reconstruct the derivation from the immutable catalogue, not its label."""
    proof = packet['static_derivation']
    need(proof['schema'] == 1 and proof['algorithm'] == 'exact-code-header-observed-context-v1' and
         proof['context_origin'] == 'complete-observed-input' and
         proof['descriptors_synthesized'] is False and proof['effective_target_context_claim'] is False and
         proof['native_target_retranslation_required'] is True and proof['native_spirv_semantic_equality_required'] is True and
         proof['allowed_reference_difference']=='one-program-source-OpString',
         'complete static derivation policy')
    stage = packet['hardware_stage']; need(stage in SUPPORTED, 'static derivation stage')
    source = table.get((proof['template_program_hash'], stage), [])
    target = table.get((packet['program_hash'], stage), [])
    need(len(source) == len(target) == 1, 'unique independent derivation identities')
    source, target = source[0], target[0]
    need(source['identity']['program_hash'] != target['identity']['program_hash'], 'different target program required')
    need(header_contract(source) == header_contract(target) == proof['complete_header_contract_sha256'],
         'complete static header contract differs')
    need(source['variants'][0]['id'] == proof['template_header_variant'] and
         target['variants'][0]['id'] == proof['target_header_variant'], 'exact derivation header variants')
    code, provenance = source_code(catalog['game_root'], catalog, source['identity'],
                                   source['variants'][0]['evidence_refs'][0])
    need(provenance == proof['template_source_provenance'] and
         source['identity']['code_sha256'] == target['identity']['code_sha256'] == packet['code']['sha256'] and
         code == Path(packet['code']['path']).read_bytes(), 'independent derivation source bytes')
