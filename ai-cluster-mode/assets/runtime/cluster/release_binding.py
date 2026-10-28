"""Exact accepted-byte release association. No provider calls or database schema.

The manifest is the sole generated, non-accepted tree file. Binding receipts live
in controller evidence outside the release to avoid a self-hash cycle. A ZIP is
optional, but when declared its actual entries and current bytes are checked.
"""
import hashlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from .release_checks import ReleaseTree, SHA256, SOURCE_TOKEN, W


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _replace(text, mapping):
    # Simultaneous replacement: no output of one replacement becomes another
    # input, and substring rewriting of ordinary prose is never allowed.
    table = {x['from']: x['to'] for x in mapping}
    if not table:
        return text
    regex = re.compile(r'(?<![A-Za-z0-9_/-])(?:' + '|'.join(re.escape(x) for x in sorted(table, key=len, reverse=True)) + r')(?![A-Za-z0-9_.-])')
    return regex.sub(lambda m: table[m[0]], text)


def accepted_source_identities(candidate_data):
    """Identity facts only from exact accepted candidate bytes, never source_map.

    Text-only/DOCX candidates without structured provenance have no automatic
    source-map exemption. Missing namespace or URL is not guessed from context.
    """
    try:
        value = json.loads(candidate_data)
    except (ValueError, UnicodeDecodeError, TypeError):
        return []
    if not isinstance(value, dict) or not isinstance(value.get('sources'), list):
        return []
    records = []
    for source in value['sources']:
        if not isinstance(source, dict):
            continue
        namespace = source.get('source_namespace', source.get('namespace'))
        source_id = source.get('source_id')
        original_url = source.get('original_url') or source.get('source_url') or source.get('url')
        if all(isinstance(x, str) and x for x in (namespace, source_id, original_url)):
            records.append({'namespace': namespace, 'source_id': source_id, 'original_url': original_url})
    return records


def _reference_map(transform, options, source_authority):
    mapping = transform.get('mapping', [])
    if not isinstance(mapping, list) or not all(isinstance(x, dict) for x in mapping):
        raise ValueError('Mechanical reference mapping requires explicit records')
    sources = options.get('source_map')
    if not isinstance(sources, list):
        # Root-relative source_map paths are resolved by the caller first.
        raise ValueError('Reference transform requires explicit resolved source_map')
    seen = set()
    for entry in mapping:
        old, new, namespace = (entry.get(k) for k in ('from', 'to', 'namespace'))
        if not all(isinstance(x, str) and x for x in (old, new, namespace)) or old in seen:
            raise ValueError('Reference map needs unique from/to/namespace')
        if not SOURCE_TOKEN.fullmatch(old) or not SOURCE_TOKEN.fullmatch(new) or not new.startswith('REL-'):
            raise ValueError('Reference transform only permits source identifier tokens, never semantic words')
        seen.add(old)
        matches = [r for r in sources if r.get('namespace', r.get('source_namespace')) == namespace
                   and r.get('source_id') == old and r.get('release_source_id') == new]
        if len(matches) != 1:
            raise ValueError('Mechanical reference mapping is not an exact registered identity')
        trusted = [r for r in (source_authority or []) if isinstance(r, dict)
                   and r.get('namespace') == namespace and r.get('source_id') == old]
        if len(trusted) != 1 or not isinstance(trusted[0].get('original_url'), str) or not trusted[0]['original_url']:
            raise ValueError('Mechanical reference mapping lacks unique accepted source authority: '+old)
        registered_url = matches[0].get('original_url') or matches[0].get('source_url') or matches[0].get('url')
        if registered_url != trusted[0]['original_url']:
            raise ValueError('Mechanical reference mapping changes accepted original_url attribution: '+old)
    return mapping


def _check_known_attributions(options, source_authority):
    """A byte-identity transform cannot relabel an already known registry URL."""
    for record in options.get('source_map', []) or []:
        if not isinstance(record, dict):
            continue
        namespace = record.get('namespace', record.get('source_namespace'))
        trusted = [r for r in (source_authority or []) if isinstance(r, dict)
                   and r.get('namespace') == namespace and r.get('source_id') == record.get('source_id')]
        if not trusted:
            continue  # No new ownership is inferred for another namespace.
        if len(trusted) != 1:
            raise ValueError('Source registry lacks unique accepted source authority: '+str(record.get('source_id')))
        registered_url = record.get('original_url') or record.get('source_url') or record.get('url')
        if registered_url != trusted[0].get('original_url'):
            raise ValueError('Source registry changes accepted original_url attribution: '+str(record.get('source_id')))


def _xml_reference_transform(data, mapping):
    xml = ET.fromstring(data)
    for paragraph in xml.iter(W + 'p'):
        nodes = list(paragraph.iter(W + 't'))
        text = ''.join(x.text or '' for x in nodes)
        changed = _replace(text, mapping)
        if changed != text:
            # Explicit deterministic version: retain run/format structure and
            # put the transformed paragraph text in its first existing t node.
            nodes[0].text = changed
            for node in nodes[1:]:
                node.text = ''
    return ET.tostring(xml, encoding='utf-8')


def transformed_bytes(input_data, transform, options=None, *, source_authority=None):
    """Narrow replayable transforms. Arbitrary scripts/layout/body edits denied."""
    options = options or {}
    if not isinstance(transform, dict) or transform.get('version') != 1:
        raise ValueError('Transform version must be explicitly 1')
    kind = transform.get('type')
    if kind == 'identity_v1':
        if transform.get('mapping'):
            raise ValueError('Identity has no mapping')
        return input_data
    mapping = _reference_map(transform, options, source_authority)
    if kind == 'utf8_reference_map_v1':
        return _replace(input_data.decode('utf-8'), mapping).encode('utf-8')
    if kind == 'docx_reference_map_v1':
        # The return is a per-member content representation to avoid trusting
        # container timestamps/compression as semantic evidence.
        import io
        with zipfile.ZipFile(io.BytesIO(input_data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Duplicate DOCX entries')
            return {name: (_xml_reference_transform(archive.read(name), mapping)
                           if name.startswith('word/') and name.endswith('.xml')
                           else archive.read(name)) for name in names}
    raise ValueError('Unsupported mechanical transform; new content requires review')


def content_matches(input_data, output_data, transform, options, *, source_authority=None):
    expected = transformed_bytes(input_data, transform, options, source_authority=source_authority)
    if isinstance(expected, bytes):
        return output_data == expected
    import io
    with zipfile.ZipFile(io.BytesIO(output_data)) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != set(expected):
            return False
        for name, data in expected.items():
            actual = archive.read(name)
            if name.startswith('word/') and name.endswith('.xml'):
                actual = ET.tostring(ET.fromstring(actual), encoding='utf-8')
            if actual != data:
                return False
    return True


def inspect_binding(root, manifest_path, bindings, read_accepted, options=None, *, zip_path=None, archive_prefix=''):
    """Read every current byte. read_accepted verifies authority, never caller hash."""
    tree = ReleaseTree(root)
    if tree.issues:
        raise ValueError('Unsafe release tree')
    manifest, error = tree.resolve(manifest_path)
    if error:
        raise ValueError('Manifest does not resolve')
    if not isinstance(bindings, list) or not bindings:
        raise ValueError('At least one exact accepted-file binding required')
    seen = set(); checked = []
    resolved_options = dict(options or {})
    if isinstance(resolved_options.get('source_map'), (str, Path)):
        sm, error = tree.resolve(str(resolved_options['source_map']))
        if error:
            raise ValueError('Source map does not resolve')
        value = json.loads(tree.files[sm].read_text(encoding='utf-8-sig'))
        resolved_options['source_map'] = value.get('records', []) if isinstance(value, dict) else value
    elif isinstance(resolved_options.get('source_map'), dict):
        resolved_options['source_map'] = resolved_options['source_map'].get('records', [])
    for entry in bindings:
        if not isinstance(entry, dict):
            raise ValueError('Binding must be a record')
        output, error = tree.resolve(entry.get('output_path', ''))
        if error or output == manifest or output in seen:
            raise ValueError('Binding output missing, duplicate, or manifest')
        seen.add(output)
        expected_output = entry.get('output_sha256')
        if not isinstance(expected_output, str) or not SHA256.fullmatch(expected_output) or tree.digest(output) != expected_output.lower():
            raise ValueError('Binding output SHA256 differs from current bytes: ' + output)
        input_data, authority = read_accepted(entry)
        source_authority = authority.get('accepted_source_identities')
        _check_known_attributions(resolved_options, source_authority)
        if not content_matches(input_data, tree.files[output].read_bytes(), entry.get('transform'), resolved_options,
                               source_authority=source_authority):
            raise ValueError('Release is not the accepted bytes or allowed exact transform: ' + output)
        if entry.get('transform', {}).get('type') == 'identity_v1':
            # Internal context is checked but omitted from a legacy identity
            # receipt, avoiding gratuitous invalidation of exact-byte receipts.
            authority = {k:v for k,v in authority.items() if k!='accepted_source_identities'}
        checked.append({**entry, 'output_path': output, 'verified_acceptance': authority,
                        'check': 'exact_replay_passed'})
    if seen != set(tree.files) - {manifest}:
        raise ValueError('Every non-manifest release file must have one accepted binding; missing=' + ','.join(sorted(set(tree.files) - {manifest} - seen)))
    zip_record = None
    if zip_path is not None:
        path = Path(zip_path)
        if path.is_symlink() or not path.is_file() or path.resolve().is_relative_to(tree.root):
            raise ValueError('ZIP must be a regular file outside the release tree')
        raw_prefix = str(archive_prefix)
        prefix = raw_prefix.rstrip('/')
        if (raw_prefix.startswith('/') or re.match(r'^[A-Za-z]:', raw_prefix)
                or '..' in Path(raw_prefix).parts or '\\' in raw_prefix
                or any(ord(x) < 32 for x in raw_prefix)):
            raise ValueError('Invalid archive prefix')
        expected_entries = {(prefix + '/' if prefix else '') + p: tree.digest(p) for p in tree.files}
        import io
        zip_data = path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
            entries = archive.infolist()
            if any(x.filename.startswith('/') or re.match(r'^[A-Za-z]:', x.filename) or '..' in Path(x.filename).parts
                   or '\\' in x.filename or any(ord(c) < 32 for c in x.filename) for x in entries):
                raise ValueError('Unsafe ZIP entry path')
            if any(((x.external_attr >> 16) & 0o170000) == 0o120000 for x in entries):
                raise ValueError('ZIP symlinks are not regular release entries')
            names = [x.filename for x in entries if not x.is_dir()]
            directory_names = [x.filename for x in entries if x.is_dir()]
            for relative in resolved_options.get('required_directories', []):
                if (not isinstance(relative, str) or not relative or relative.startswith('/')
                        or '\\' in relative or ':' in relative or '..' in Path(relative).parts
                        or any(ord(c) < 32 for c in relative)):
                    raise ValueError('Invalid required directory')
                target = tree.root / relative
                if not target.is_dir() or any(p.is_symlink() for p in (target, *target.parents)):
                    raise ValueError('Required directory missing from release tree')
                required = (prefix + '/' if prefix else '') + relative.rstrip('/') + '/'
                if directory_names.count(required) != 1:
                    raise ValueError('Required directory missing or duplicated in ZIP: ' + required)
            if len(names) != len(set(names)) or set(names) != set(expected_entries):
                raise ValueError('ZIP entries differ from exact current release tree')
            for name in names:
                if sha(archive.read(name)) != expected_entries[name]:
                    raise ValueError('ZIP content differs from accepted tree: ' + name)
        zip_record = {'path': str(path.resolve()), 'sha256': sha(zip_data), 'archive_prefix': prefix,
                      'entries': len(expected_entries), 'check': 'exact_tree_passed'}
    return {'schema_version': 'accepted_release_binding_v1', 'manifest_sha256': tree.digest(manifest),
            'bindings': checked, 'zip': zip_record, 'passed': True,
            'excluded_generated_files': [manifest],
            'limitations': ['Byte/replay and accepted scope association only; unknown facts remain unknown.',
                            'No generic layout or arbitrary script equivalence is claimed.']}
