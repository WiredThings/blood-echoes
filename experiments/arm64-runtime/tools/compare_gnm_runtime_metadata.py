"""Compare local static GNM defaults with already decoded cache metadata.

Pure JSON analysis: never deserialize native cache objects or execute walkers.
Agreement does not prove a complete specialization or permit cross-cache merges.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path

from gnm_shader_metadata import catalog_identity, require

PROPERTIES = ("num_user_data", "num_input_vgprs", "num_allocated_vgprs")


def rows(path):
    with path.open() as stream:
        while True:
            line = stream.readline(64 * 1024 * 1024 + 1)
            if not line:
                return
            require(len(line) <= 64 * 1024 * 1024, "metadata row budget")
            yield json.loads(line)


def collect_defaults(path):
    defaults = collections.defaultdict(set)
    codes = collections.defaultdict(set)
    count = 0
    for row in rows(path):
        for header in row["headers"]:
            count += 1
            require(count <= 100000, "header record budget")
            key = (header["program_hash"], header["stage"])
            values = header["runtime_property_defaults"]
            defaults[key].add(tuple(values.get(name) for name in PROPERTIES))
            codes[key].add(header["code_sha256"])
    return defaults, codes, count


def compare(headers, decoded):
    defaults, codes, header_count = collect_defaults(headers)
    counts = collections.Counter()
    mismatches = collections.Counter()
    unique_specs = set()
    unique_mismatches = collections.defaultdict(set)
    variants = collections.Counter()
    files = []
    for path in sorted(decoded.glob("*/decoded.json")):
        require(not path.is_symlink() and path.stat().st_size <= 32 * 1024 * 1024,
                "decoded metadata file budget")
        data = path.read_bytes()
        content = json.loads(data)
        files.append(dict(name=path.parent.name, sha256=hashlib.sha256(data).hexdigest()))
        require(len(content.get("metadata", [])) <= 16384, "cache metadata count budget")
        for meta in content.get("metadata", []):
            counts["metadata_records"] += 1
            key = (meta["program_hash"], meta["stage"])
            if key not in defaults:
                counts["without_static_header"] += 1
                continue
            counts["with_static_header"] += 1
            identity = (*key, meta["serialized_spec_sha256"])
            unique_specs.add(identity)
            if len(codes[key]) > 1:
                counts["multiple_static_code_digests"] += 1
            active = PROPERTIES if meta["stage"] != "compute" else (PROPERTIES[0], PROPERTIES[2])
            values = meta["runtime_properties"]
            require(all(type(values[name]) is int for name in active), "runtime scalar shape")
            matching = any(all(candidate[PROPERTIES.index(name)] == values[name]
                               for name in active) for candidate in defaults[key])
            counts["one_default_variant_matches" if matching else "defaults_require_modification"] += 1
            for name in active:
                index = PROPERTIES.index(name)
                if not any(candidate[index] == values[name] for candidate in defaults[key]):
                    field = meta["stage"] + "." + name
                    mismatches[field] += 1
                    unique_mismatches[field].add(identity)
            variants[len(defaults[key])] += 1
    return dict(schema=1, static_header_records=header_count,
                static_program_stage_pairs=len(defaults), counts=dict(counts),
                distinct_matched_serialized_specs=len(unique_specs),
                mismatching_fields=dict(mismatches),
                distinct_specs_with_mismatching_field={k: len(v) for k, v in unique_mismatches.items()},
                default_variant_counts=dict(variants), source_files=files,
                header_metadata_sha256=catalog_identity(headers),
                qualification="Field comparison of already decoded metadata. Duplicate cache records counted explicitly. Static defaults may be modified by fetch/draw setup. Matching program hashes with multiple code digests remain ambiguous; no native loads, full-PSO completeness or cache merge approval.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headers", type=Path, required=True)
    parser.add_argument("--decoded-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.headers, args.decoded_directory)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "source_files"}, indent=2))


if __name__ == "__main__":
    main()
