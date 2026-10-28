"""Read-only checks for a portable release tree, using only the standard library.

``check_release`` works equally on staging and a separately extracted ZIP. It
never repairs files, searches outside the release, downloads, or follows links.
An accepted migration is an explicit, hash-bound exception for one historical
manifest entry, not a change to the historical manifest's bytes.
"""
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import unicodedata
import zipfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET


SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
PATH_FIELDS = {"relative_path", "file_path", "local_path", "path", "relative_paths",
               "file_paths", "local_paths", "paths", "manifest_path", "target_path", "actual_path",
               "registration_relative_path", "original_record_path"}
HASH_FOR_PATH = {"manifest_path": "manifest_sha256", "registration_relative_path": "registration_sha256",
                 "original_record_path": "original_record_sha256"}
HASH_FIELDS = ("sha256", "hash", "file_sha256")
SOURCE_FIELDS = ("source_id", "source_ids", "release_source_id", "release_source_ids")
# Complete visible tokens only: preserve REL variants (e.g. UNACQUIRED/WB),
# and resolve bare S IDs exactly as registered rather than infer a school.
SOURCE_TOKEN = re.compile(r"(?<![A-Za-z0-9_/-])(?:REL-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+|[A-Z]{2,10}-S\d{3,}|S\d{3,})(?![A-Za-z0-9_.-])")
# Complete source forms in prose remain active references. Short S1/S2/S3
# can denote semesters/storage; incomplete old/bare forms require explicit
# citation brackets or namespace qualification below, not a semantic guess.
REFERENCE_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_/-])(?:REL-[A-Za-z0-9-]*|[A-Z]{2,10}-S[0-9]{3,}|S[0-9]{3,})(?![A-Za-z0-9_.-])")
EXTENSIONS = r"(?:json|csv|tsv|txt|md|pdf|docx|xlsx|html?|png|jpe?g|zip)"
FILE_TOKEN = re.compile(r"[^\n\r\t，、。；：！？<>\"“”‘’`（）()\[\]{}|｜《》]+?\." + EXTENSIONS + r"(?=$|[^A-Za-z0-9_.-])", re.I)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _key(value):
    return unicodedata.normalize("NFC", str(value)).casefold()


def _external(value):
    return bool(re.match(r"^(?:https?|mailto|data|tel):", str(value), re.I)) or str(value).startswith("//")


def _values(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    if value is None or value == "":
        return []
    return [x.strip() for x in re.split(r"\s*\|\s*|[;,；]", str(value)) if x.strip()]


class ReleaseTree:
    """Inventory once; every later resolution is bounded by this inventory."""
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Release root must be an existing directory")
        self.files = {}
        self.normalized = defaultdict(list)
        self.hashes = {}
        self.issues = []
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for name in sorted(dirs + files):
                path = Path(directory) / name
                relative = path.relative_to(self.root).as_posix()
                if path.is_symlink():
                    try:
                        path.resolve().relative_to(self.root)
                        code = "symlink_dependency"
                    except (ValueError, OSError, RuntimeError):
                        code = "symlink_external"
                    self.issues.append({"code": code, "location": relative,
                                        "message": "Release dependencies must be regular packaged files"})
                    if name in dirs:
                        dirs.remove(name)
                    continue
                if path.is_file():
                    self.files[relative] = path
                    self.normalized[_key(relative)].append(relative)
        for aliases in self.normalized.values():
            if len(aliases) > 1:
                self.issues.append({"code": "path_ambiguous", "location": " | ".join(aliases),
                                    "message": "Unicode/case normalization yields multiple files"})

    def resolve(self, reference, bases=("",), *, basename=False):
        """Return (relative path, error code); never choose among collisions."""
        value = unquote(str(reference).strip().strip('"\'`“”')).replace("\\", "/")
        if _external(value):
            return None, "external_reference"
        scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
        if value.startswith("/") or re.match(r"^[A-Za-z]:/", value) or scheme:
            return None, "absolute_path"
        value = value.split("#", 1)[0].split("?", 1)[0]
        if not value or "\x00" in value:
            return None, "path_invalid"
        found = set()
        for base in bases:
            combined = posixpath.normpath(posixpath.join(str(base), value))
            if combined == ".." or combined.startswith("../") or combined.startswith("/"):
                return None, "path_escape"
            matches = self.normalized.get(_key(combined), [])
            if len(matches) > 1:
                return None, "path_ambiguous"
            found.update(matches)
        if basename and "/" not in value and not found:
            found.update(p for p in self.files if _key(Path(p).name) == _key(value))
        if len(found) > 1:
            return None, "path_ambiguous"
        return (next(iter(found)), None) if found else (None, "path_missing")

    def digest(self, relative):
        if relative not in self.hashes:
            self.hashes[relative] = _hash(self.files[relative])
        return self.hashes[relative]


def build_manifest(root, manifest_path="MANIFEST_SHA256.json"):
    """Return a full-tree manifest; callers explicitly save/freeze it themselves."""
    tree = ReleaseTree(root)
    if tree.issues:
        raise ValueError("Unsafe or ambiguous release tree: " + json.dumps(tree.issues, ensure_ascii=False))
    own = posixpath.normpath(str(manifest_path).replace("\\", "/"))
    if own.startswith("/") or own == ".." or own.startswith("../"):
        raise ValueError("Manifest path must be inside the release root")
    return {"schema_version": "release_manifest_v1", "files": [
        {"path": relative, "sha256": tree.digest(relative), "bytes": path.stat().st_size}
        for relative, path in sorted(tree.files.items()) if _key(relative) != _key(own)]}


def _manifest_entries(text, suffix):
    # Historical NUS _manifest_sha256.txt contains a JSON filename-to-hash
    # object. Format is determined by its bytes, never just its extension.
    if suffix.lower() == ".json" or text.lstrip().startswith("{"):
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            return data["files"]
        if isinstance(data, dict) and all(isinstance(v, str) and SHA256.fullmatch(v) for v in data.values()):
            return [{"path": k, "sha256": v} for k, v in data.items()]
        raise ValueError("Manifest requires files [{path, sha256}] or path-to-hash object")
    entries = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if not match:
            raise ValueError("Invalid SHA256 manifest line: " + line[:120])
        entries.append({"path": match[2], "sha256": match[1]})
    return entries


def _load_records(value, tree):
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        relative, error = tree.resolve(str(value))
        if error:
            raise ValueError(error + ": " + str(value))
        value = json.loads(tree.files[relative].read_text(encoding="utf-8-sig"))
    if isinstance(value, dict):
        value = value.get("records", [])
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise ValueError("Expected records list")
    return value


def _document_reference(token, tree, document):
    """Trim a prose prefix only at a boundary before an actual packaged path.

    Directory spelling is not repaired and absolute/traversal prefixes are
    never discarded. Spaces inside a real filename remain part of the path.
    """
    token = unicodedata.normalize("NFC", token.strip())
    if token.startswith(("/", "../", "./")) or re.match(r"^[A-Za-z]:", token):
        return token
    directory = Path(document).parent.as_posix()
    candidates = set()
    for relative in tree.files:
        candidates.add(relative)
        candidates.add(Path(relative).name)
        if directory != "." and relative.startswith(directory + "/"):
            candidates.add(relative[len(directory) + 1:])
    matched = []
    for candidate in candidates:
        candidate = unicodedata.normalize("NFC", candidate)
        if _key(token).endswith(_key(candidate)):
            prefix = token[:-len(candidate)]
            if prefix and "/" not in prefix and "\\" not in prefix and (prefix[-1].isspace() or prefix.endswith("见")):
                matched.append(candidate)
    return max(matched, key=len) if matched else token


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(); self.hidden = 0; self.text = []; self.external = []; self.local = []
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        for key, value in attrs:
            if key in {"src", "href"} and value:
                (self.external if _external(value) else self.local).append(value)
    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def check_release(root, manifest_path="MANIFEST_SHA256.json", *, indexes=None,
                  migrations=None, source_map=None, required_documents=None,
                  offline_html=None, exclude_indexes=(), document_roles=None, required_directories=()):
    """Validate a release and return a JSON-serializable report without writes.

    indexes: optional paths or dicts {path, base, path_fields, require_hash}; None discovers
      CSVs with path columns and JSON filenames containing index/索引/manifest/映射.
      A JSON can also explicitly declare controlled_release_index=true.
      Original archive JSON/HTML fields are not interpreted as file paths. A
      controlled single-file index needs its SHA256 unless require_hash=false
      explicitly declares a path-only navigation index.
    migrations: records {manifest_path, manifest_sha256, old_path,
      relative_path, sha256}; all hashes are required and must match originals.
    source_map: records {release_source_id, namespace, source_id, relative_paths,
      source_sha256? or source_version?}. One global identity may have many files.
    offline_html: optional {relative_path: {min_text_chars, required_local_paths}}
      or list of explicitly promised offline pages. External images never become
      required local files merely because they occur in an archived HTML page.
    """
    tree = ReleaseTree(root)
    issues = list(tree.issues); suggestions = []; migrated = []; classifications = []
    counts = defaultdict(int)
    def bad(code, location, message, **details):
        issues.append({"code": code, "location": location, "message": message, **details})
    for relative in required_directories:
        if (not isinstance(relative, str) or not relative or relative.startswith('/')
                or '\\' in relative or ':' in relative or '..' in Path(relative).parts
                or any(ord(c) < 32 for c in relative)):
            bad('directory_invalid', str(relative), 'Required directory must be a safe release-relative path')
            continue
        path = tree.root / relative
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_dir():
            bad('directory_missing', relative, 'Explicit required directory must exist, including empty directories')
        else:
            counts['required_directories_checked'] += 1
    def resolve(ref, location, bases=("",), expected=None, basename=False):
        relative, error = tree.resolve(ref, bases, basename=basename)
        if error:
            bad(error, location, "Local dependency does not resolve uniquely within release", reference=ref)
            return None
        if expected:
            if not isinstance(expected, str) or not SHA256.fullmatch(expected):
                bad("hash_invalid", location, "Expected SHA256 must contain 64 hexadecimal characters")
            elif tree.digest(relative) != expected.lower():
                bad("hash_mismatch", location, "Packaged bytes differ from indexed SHA256", reference=ref,
                    actual_sha256=tree.digest(relative), expected_sha256=expected.lower())
        counts["local_references_checked"] += 1
        return relative
    try:
        migration_records = _load_records(migrations, tree)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        bad("migration_schema", "migrations", str(exc)); migration_records = []
    migration_lookup = defaultdict(list)
    for number, record in enumerate(migration_records):
        location = f"migrations#{number + 1}"
        if (not all(isinstance(record.get(k), str) and record[k] for k in ("manifest_path", "old_path", "relative_path")) or
                not all(isinstance(record.get(k), str) and SHA256.fullmatch(record[k]) for k in ("manifest_sha256", "sha256"))):
            bad("migration_schema", location, "Migration needs original manifest/entry hashes and explicit old/new paths"); continue
        manifest_ref, error = tree.resolve(record["manifest_path"])
        if error:
            bad(error, location, "Migration refers to a manifest absent from this release", reference=record["manifest_path"]); continue
        migration_lookup[(_key(manifest_ref), _key(record["old_path"]))].append(record)

    own, own_error = tree.resolve(manifest_path)
    if own_error:
        bad(own_error, str(manifest_path), "Full-file release manifest is required")
    manifests = {p for p in tree.files if re.search(r"(?:manifest.*sha256|sha256.*manifest|^manifest\.(?:txt|json)$)", Path(p).name, re.I)}
    if own:
        manifests.add(own)
    for manifest in sorted(manifests):
        try:
            entries = _manifest_entries(tree.files[manifest].read_text(encoding="utf-8-sig"), Path(manifest).suffix)
        except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            bad("manifest_invalid", manifest, str(exc)); continue
        seen = set(); coverage = set()
        for number, entry in enumerate(entries):
            location = f"{manifest}#{number + 1}"
            if not isinstance(entry, dict) or not entry.get("path"):
                bad("manifest_entry_invalid", location, "Manifest entry requires path and SHA256"); continue
            ref = entry["path"]; expected = entry.get("sha256")
            if _key(ref) in seen:
                bad("manifest_duplicate", location, "Manifest repeats a normalized path", reference=ref)
            seen.add(_key(ref))
            bases = ("",) if manifest == own else (Path(manifest).parent.as_posix(),)
            if manifest == own and _key(str(ref)) == _key(own):
                bad("manifest_self_hash", location, "Full manifest must exclude itself"); continue
            relative, error = tree.resolve(ref, bases)
            if manifest == own and relative == own:
                bad("manifest_self_hash", location, "Full manifest must exclude itself"); continue
            mapping = migration_lookup.get((_key(manifest), _key(ref)), [])
            if mapping:
                if len(mapping) != 1:
                    bad("migration_ambiguous", location, "More than one migration for an original entry"); continue
                record = mapping[0]
                if (record.get("manifest_sha256", "").lower() != tree.digest(manifest) or
                        not isinstance(expected, str) or record.get("sha256", "").lower() != expected.lower()):
                    bad("migration_hash_binding", location, "Migration must bind original manifest and original entry hashes"); continue
                target = resolve(record.get("relative_path", ""), location, expected=expected)
                if target and SHA256.fullmatch(expected) and tree.digest(target) == expected.lower():
                    coverage.add(target)
                    migrated.append({"manifest_path": manifest, "manifest_sha256": tree.digest(manifest),
                                     "old_path": ref, "relative_path": target, "sha256": expected})
                continue
            if error:
                bad(error, location, "Manifest entry does not resolve within its declared directory", reference=ref)
                if isinstance(expected, str) and SHA256.fullmatch(expected):
                    candidates = [p for p in sorted(tree.files) if p != manifest and tree.digest(p) == expected.lower()]
                    if candidates:
                        suggestions.append({"kind": "manifest_migration_candidates", "manifest_path": manifest,
                            "manifest_sha256": tree.digest(manifest), "old_path": ref, "sha256": expected.lower(),
                            "candidate_relative_paths": candidates, "requires_explicit_selection": True})
                continue
            checked = resolve(ref, location, bases, expected)
            if checked:
                coverage.add(checked)
                if "bytes" in entry and tree.files[checked].stat().st_size != entry["bytes"]:
                    bad("size_mismatch", location, "Packaged size differs from manifest bytes")
        if manifest == own:
            for missing in sorted(set(tree.files) - {own} - coverage):
                bad("manifest_omission", manifest, "Packaged file is absent from full manifest", reference=missing)
        counts["manifests_checked"] += 1

    excluded = {_key(p) for p in exclude_indexes}
    controlled = []; json_cache = {}
    if indexes is None:
        for relative, path in sorted(tree.files.items()):
            if _key(relative) in excluded or relative in manifests:
                continue
            try:
                if path.suffix.lower() == ".csv":
                    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
                    if rows and set(rows[0]) & PATH_FIELDS:
                        controlled.append({"path": relative, "rows": rows})
                elif path.suffix.lower() == ".json":
                    data = json.loads(path.read_text(encoding="utf-8-sig")); json_cache[relative] = data
                    if (re.search(r"index|索引|映射", path.name, re.I) or
                            isinstance(data, dict) and data.get("controlled_release_index") is True):
                        controlled.append({"path": relative, "data": data})
            except (OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
                # Non-index original JSON is opaque archive content; only named
                # controlled indexes make parse failure a release error.
                if re.search(r"index|索引|映射", path.name, re.I):
                    bad("index_invalid", relative, str(exc))
    else:
        for config in indexes:
            config = {"path": str(config)} if isinstance(config, (str, Path)) else dict(config)
            relative = resolve(config.get("path", ""), "indexes")
            if not relative:
                continue
            config["path"] = relative
            try:
                text = tree.files[relative].read_text(encoding="utf-8-sig")
                if Path(relative).suffix.lower() == ".csv":
                    config["rows"] = list(csv.DictReader(io.StringIO(text)))
                else:
                    config["data"] = json.loads(text)
                controlled.append(config)
            except (OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
                bad("index_invalid", relative, str(exc))

    try:
        source_records = _load_records(source_map, tree)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        bad("source_map_schema", "source_map", str(exc)); source_records = []
    by_release = defaultdict(set); by_short = defaultdict(set); identities = set(); global_urls = defaultdict(set)
    for number, record in enumerate(source_records):
        location = f"source_map#{number + 1}"
        namespace = record.get("namespace", record.get("source_namespace"))
        source_id = record.get("source_id"); visible = record.get("release_source_id")
        if not all(isinstance(x, str) and x.strip() for x in (namespace, source_id, visible)):
            bad("source_identity_missing", location, "Source map needs release_source_id, namespace and source_id"); continue
        url = str(record.get("original_url") or record.get("source_url") or record.get("url") or "")
        identity = (namespace, source_id, str(record.get("source_sha256") or record.get("source_version") or ""), url)
        if url:
            global_urls[(namespace, source_id)].add(url)
        by_release[visible].add(identity); by_short[source_id].add(identity); identities.add(identity)
        hash_by_path = record.get("file_sha256_by_path", {})
        if not isinstance(hash_by_path, dict):
            bad("source_map_schema", location, "file_sha256_by_path must map packaged paths to SHA256 values")
            hash_by_path = {}
        for ref in _values(record.get("relative_paths", record.get("relative_path"))):
            resolve(ref, location, expected=hash_by_path.get(ref))
    for visible, targets in by_release.items():
        if len(targets) > 1:
            bad("release_source_id_collision", visible, "One visible release ID points to different source identities/versions",
                identities=[list(x) for x in sorted(targets)])
    for global_id, urls in global_urls.items():
        if len(urls) > 1:
            bad("global_source_identity_collision", "::".join(global_id),
                "Same namespace/source_id describes different source URLs; disambiguation is required",
                source_urls=sorted(urls))

    def source_refs(record, location):
        namespace = record.get("namespace", record.get("source_namespace"))
        for field in SOURCE_FIELDS:
            for value in _values(record.get(field)):
                if field.startswith("release_"):
                    targets = by_release.get(value, set())
                elif value in by_release:
                    targets = by_release[value]
                elif namespace:
                    targets = {x for x in identities if x[:2] == (namespace, value)}
                else:
                    targets = by_release.get(value, by_short.get(value, set()))
                if namespace and (field.startswith("release_") or value in by_release):
                    targets = {x for x in targets if x[0] == namespace}
                if not targets:
                    bad("source_reference_unknown", location + "." + field, "Source reference is absent from release mapping", reference=value, namespace=namespace)
                elif len(targets) > 1:
                    bad("source_reference_ambiguous", location + "." + field, "Source reference has more than one identity/version", reference=value)

    def check_record(record, location, bases, fields, require_hash):
        if source_map is None and any(_values(record.get(k)) for k in SOURCE_FIELDS):
            bad("source_map_required", location, "Published source references require an explicit release identity map")
        if source_map is not None and set(record) & set(SOURCE_FIELDS):
            # Definitions in a source-map file are already checked above.
            if not {"release_source_id", "namespace", "source_id"} <= set(record):
                source_refs(record, location)
        for field in fields & set(record):
            refs = _values(record[field])
            expected = record.get(HASH_FOR_PATH[field]) if field in HASH_FOR_PATH else next((record.get(k) for k in HASH_FIELDS if record.get(k)), None)
            definition = {"release_source_id", "namespace", "source_id"} <= set(record)
            if require_hash and refs and len(refs) == 1 and not expected and not definition:
                bad("index_hash_missing", location + "." + field, "Controlled file index needs its expected SHA256")
            for ref in refs:
                if _external(ref):
                    bad("index_external_path", location + "." + field, "Controlled local path cannot be an external URL", reference=ref)
                    continue
                resolve(ref, location + "." + field, bases, expected if len(refs) == 1 else None)
    def walk(data, location, bases, fields, require_hash):
        if isinstance(data, dict):
            check_record(data, location, bases, fields, require_hash)
            for key, value in data.items():
                if isinstance(value, (dict, list)) and key not in fields:
                    walk(value, location + "." + key, bases, fields, require_hash)
        elif isinstance(data, list):
            for i, value in enumerate(data):
                walk(value, f"{location}[{i}]", bases, fields, require_hash)
    for config in controlled:
        relative = config["path"]
        bases = (config["base"],) if "base" in config else ("", Path(relative).parent.as_posix())
        fields = set(config.get("path_fields", PATH_FIELDS))
        walk(config.get("rows", config.get("data")), relative, bases, fields, config.get("require_hash", True))
        counts["indexes_checked"] += 1

    documents = [p for p in tree.files if Path(p).suffix.lower() == ".docx"] if required_documents is None else list(required_documents)
    # Explicit archival/example files may be checked for container/path safety
    # while their raw historical citations are not promoted to active sources.
    # They cannot be any current required deliverable; an exact SHA prevents a
    # later current file from silently inheriting the archival exception.
    roles = document_roles or {}
    if not isinstance(roles, dict):
        bad("document_role_invalid", "document_roles", "Expected exact-path role records")
        roles = {}
    history = set()
    for document, role in roles.items():
        relative = resolve(document, "document_roles")
        if not relative:
            continue
        if not isinstance(role, dict) or role.get("role") not in ("historical", "example") or not role.get("reason"):
            bad("document_role_invalid", relative, "Historical/example designation requires a reason")
            continue
        if relative in documents:
            bad("current_document_exclusion", relative, "A current required deliverable cannot be excluded as history/example")
            continue
        if role.get("sha256") != tree.digest(relative):
            bad("document_role_hash_mismatch", relative, "History/example exception belongs to different bytes")
            continue
        history.add(relative)
        if relative not in documents:
            documents.append(relative)
    for document in documents:
        relative = resolve(document, "documents")
        if not relative:
            continue
        try:
            with zipfile.ZipFile(tree.files[relative]) as archive:
                texts = []; scanned_parts = []
                for name in archive.namelist():
                    if name.startswith("word/") and name.endswith(".xml") and not name.endswith(".rels"):
                        xml = ET.fromstring(archive.read(name))
                        para_count = 0
                        for para_count, para in enumerate(xml.iter(W + "p"), 1):
                            texts.append((name, para_count, "".join(x.text or "" for x in para.iter(W + "t"))))
                        if para_count:
                            scanned_parts.append(name)
                        if any(x.tag.rsplit("}", 1)[-1] in ("altChunk", "instrText") for x in xml.iter()):
                            classifications.append({"path": relative, "part": name, "kind": "docx_nonparagraph_or_field_content",
                                "scope": "Visible w:t scanned; field instructions/embedded alternative content not evaluated"})
                    if name.startswith("word/") and name.endswith(".rels"):
                        for relation in ET.fromstring(archive.read(name)):
                            if relation.get("Type", "").endswith("/hyperlink"):
                                target = relation.get("Target", "")
                                if _external(target) or target.startswith("#"):
                                    counts["external_hyperlinks_classified"] += 1
                                else:
                                    resolve(target, relative + "#hyperlink", ("", Path(relative).parent.as_posix()))
                classifications.append({"path": relative, "kind": "docx_reference_scan_scope", "parts": scanned_parts,
                    "file_role": "historical_or_example" if relative in history else "current",
                    "historical_reason": roles.get(relative, {}).get("reason")})
                for part, number, text in texts:
                    location = f"{relative}#{part}#paragraph-{number}"
                    if relative in history:
                        continue
                    # Remove URLs before recognizing explicit file-suffix text.
                    local_text = re.sub(r"(?:https?://|mailto:)[^\s，。；<>]+", "", text)
                    tokens = set()
                    if re.search(r"(?<![A-Za-z0-9_])result\.json\b", local_text, re.I):
                        tokens.add("result.json")
                    for match in FILE_TOKEN.finditer(local_text):
                        token = match[0].strip()
                        if token.endswith("result.json"):
                            tokens.add("result.json"); continue
                        # Ordinary prose immediately before an unquoted basename
                        # is not a path. A basename token, quoted token, or slash
                        # is an explicit local file reference.
                        if "*" in token or re.search(r"(?:^|\s)\." + EXTENSIONS + r"$", token, re.I) or re.search(r"(?:^|\s)\.extracted\.", token):
                            classifications.append({"path": relative, "kind": "document_filename_pattern", "reference": token})
                            continue
                        token = _document_reference(token, tree, relative)
                        tokens.add(token)
                    for token in sorted(tokens):
                        resolve(token, location, ("", Path(relative).parent.as_posix()), basename=True)
                    # Bracketed malformed suffixes (e.g. S001extra/REL-..._)
                    # must not disappear merely because token boundaries fail.
                    for bracket in re.finditer(r"[\[［【]\s*((?:REL-|[A-Z]{2,10}-S|S\d)[^\]］】\s]*)\s*[\]］】]", local_text):
                        marker = bracket[1]
                        if not SOURCE_TOKEN.fullmatch(marker):
                            bad("source_reference_malformed", location, "Bracketed active source marker is malformed", reference=marker)
                    for qualified_marker in re.finditer(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+)::\s*(REL-[A-Za-z0-9_-]*|[A-Z]{2,10}-S[A-Za-z0-9_]*|S[0-9][A-Za-z0-9_]*)", local_text):
                        namespace, marker = qualified_marker.groups()
                        if not SOURCE_TOKEN.fullmatch(marker) and not any(x["code"] == "source_reference_malformed" and x["location"] == location and x.get("reference") == marker for x in issues):
                            bad("source_reference_malformed", location, "Namespace-qualified source marker is malformed", reference=marker, namespace=namespace)
                    if source_map is not None:
                        for match in REFERENCE_CANDIDATE.finditer(local_text):
                            source_id = match[0]
                            if not SOURCE_TOKEN.fullmatch(source_id):
                                if not any(x["code"] == "source_reference_malformed" and x["location"] == location and x.get("reference") == source_id for x in issues):
                                    bad("source_reference_malformed", location, "Active source marker is incomplete/malformed", reference=source_id)
                                continue
                            qualified = re.search(r"([A-Za-z0-9_.-]+)::?\s*$", local_text[:match.start()])
                            record = {"source_id": source_id}
                            if qualified:
                                record["namespace"] = qualified[1]
                            source_refs(record, location)
                    elif REFERENCE_CANDIDATE.search(local_text):
                        bad("source_map_required", location, "Visible source references require an explicit release identity map")
            counts["documents_checked"] += 1
        except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
            bad("docx_invalid", relative, str(exc))

    offline = {p: {} for p in offline_html} if isinstance(offline_html, (list, tuple, set)) else (offline_html or {})
    for relative, path in sorted(tree.files.items()):
        if path.suffix.lower() not in {".html", ".htm"}:
            continue
        try:
            parser = _HTMLText(); parser.feed(path.read_text(encoding="utf-8-sig", errors="replace"))
            classifications.append({"path": relative, "kind": "archived_html_external_resources",
                                    "external_resources": len(parser.external), "required_local": relative in offline})
            if relative in offline:
                options = offline[relative] or {}
                length = len(re.sub(r"\s+", "", "".join(parser.text)))
                if length < options.get("min_text_chars", 80):
                    bad("offline_html_body_missing", relative, "Explicitly offline page lacks minimum readable text", text_characters=length)
                for ref in options.get("required_local_paths", []):
                    resolve(ref, relative + "#offline", (Path(relative).parent.as_posix(),))
        except (OSError, ValueError) as exc:
            bad("html_invalid", relative, str(exc))
    for page in offline:
        if page not in tree.files:
            resolve(page, "offline_html")
    counts["physical_files"] = len(tree.files)
    counts["source_identities"] = len(identities)
    counts["release_source_ids"] = len(by_release)
    return {"schema_version": "release_checks_v1", "passed": not issues,
            "manifest_path": own or str(manifest_path), "issues": issues,
            "migration_suggestions": suggestions, "accepted_migrations": migrated,
            "classifications": classifications, "counts": dict(counts),
            "limitations": ["DOCX scans w:t paragraphs (including tables, hyperlink text, header/footer, footnotes/endnotes) in all listed Word XML parts; field instructions, altChunk payloads, drawing a:t and visual render QA are not evaluated.",
                            "Paths and source identities are checked; natural-language factual applicability is not proved.",
                            "Archived HTML external URLs are classified, not fetched or asserted offline-readable."]}
