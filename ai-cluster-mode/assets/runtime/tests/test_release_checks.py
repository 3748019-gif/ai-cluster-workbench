"""Portable-tree fixtures, no external clients or LibreOffice renderer."""
import csv
import hashlib
import io
import importlib.util
import json
import shutil
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from cluster.release_checks import build_manifest, check_release


def digest(value):
    return hashlib.sha256(value).hexdigest()


class ReleaseChecksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "发布 中文目录"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))
        return path

    def json(self, relative, value):
        return self.put(relative, json.dumps(value, ensure_ascii=False, indent=2))

    def csv(self, relative, rows):
        stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
        return self.put(relative, "\ufeff" + stream.getvalue())

    def docx(self, relative, paragraphs, hyperlinks=()):
        path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        # Minimal OOXML fixture contains only visible body paragraphs and a
        # Normal style; it does not need a renderer or a document-authoring SDK.
        body = "".join("<w:p><w:pPr><w:pStyle w:val='Normal'/></w:pPr><w:r><w:t>" + escape(text) + "</w:t></w:r></w:p>" for text in paragraphs)
        xml = "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>" + body + "</w:body></w:document>"
        relations = "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>" + "".join(
            f"<Relationship Id='rId{i}' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink' Target='{escape(target, {chr(39): '&apos;'})}' TargetMode='External'/>"
            for i, target in enumerate(hyperlinks)) + "</Relationships>"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)
            archive.writestr("word/_rels/document.xml.rels", relations)
        return path

    def freeze(self):
        self.json("MANIFEST_SHA256.json", build_manifest(self.root))

    def codes(self, report):
        return {item["code"] for item in report["issues"]}

    def source_map(self):
        return {"records": [{"release_source_id": "REL-NUS-001", "namespace": "NUS-body",
            "source_id": "NUS-S008", "source_sha256": "2026-27-v1", "original_url": "https://nus.example/module/CM1131",
            "relative_paths": ["新加坡/课程 中文.json", "新加坡/全文.txt"]}]}

    def source_files(self):
        a = self.put("新加坡/课程 中文.json", '{"moduleCode":"CM1131"}')
        self.put("新加坡/全文.txt", "原始文字")
        return a

    def test_correct_csv_json_index_and_manifest(self):
        path = self.put("学校/素材 中文.txt", "真实原件")
        row = {"relative_path": "学校/素材 中文.txt", "sha256": digest(path.read_bytes())}
        self.csv("00_总览/总索引.csv", [row])
        self.json("学校/controlled.json", {"controlled_release_index": True, "files": [row]})
        self.freeze()
        report = check_release(self.root)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["counts"]["indexes_checked"], 2)

    def test_modified_file_hash_and_missing_manifest_entry_are_reported_together(self):
        self.put("a.txt", "original"); self.freeze()
        self.put("a.txt", "changed"); self.put("b.txt", "new")
        report = check_release(self.root)
        self.assertFalse(report["passed"])
        self.assertTrue({"hash_mismatch", "manifest_omission"} <= self.codes(report))

    def test_csv_index_hash_mismatch_even_when_full_manifest_current(self):
        self.put("a.txt", "correct")
        self.csv("index.csv", [{"relative_path": "a.txt", "sha256": "0" * 64}]); self.freeze()
        self.assertIn("hash_mismatch", self.codes(check_release(self.root)))
        self.csv("index.csv", [{"relative_path": "a.txt"}]); self.freeze()
        self.assertIn("index_hash_missing", self.codes(check_release(self.root)))
        self.assertTrue(check_release(self.root, indexes=[{"path": "index.csv", "require_hash": False}])["passed"])

    def test_manifest_self_exclusion_and_self_reference_rejection(self):
        self.put("a.txt", "correct"); self.freeze()
        manifest = build_manifest(self.root)
        self.assertEqual([x["path"] for x in manifest["files"]], ["a.txt"])
        manifest["files"].append({"path": "./MANIFEST_SHA256.json", "sha256": "0" * 64})
        self.json("MANIFEST_SHA256.json", manifest)
        self.assertIn("manifest_self_hash", self.codes(check_release(self.root)))

    def test_nested_json_bytes_in_txt_manifest_migration_is_hash_bound_and_read_only(self):
        path = self.put("新加坡/补查/2026-2027_CM1131.json", "原始内容")
        original = self.json("新加坡/旧目录/_manifest_sha256.txt", {"CM1131.json": digest(path.read_bytes())})
        original_bytes = original.read_bytes(); self.freeze()
        failed = check_release(self.root)
        self.assertIn("path_missing", self.codes(failed))
        self.assertEqual(failed["migration_suggestions"][0]["candidate_relative_paths"], ["新加坡/补查/2026-2027_CM1131.json"])
        mapping = {"records": [{"manifest_path": "新加坡/旧目录/_manifest_sha256.txt",
            "manifest_sha256": digest(original_bytes), "old_path": "CM1131.json",
            "relative_path": "新加坡/补查/2026-2027_CM1131.json", "sha256": digest(path.read_bytes())}]}
        report = check_release(self.root, migrations=mapping)
        self.assertTrue(report["passed"], report)
        self.assertEqual(len(report["accepted_migrations"]), 1)
        self.assertEqual(original.read_bytes(), original_bytes)
        mapping["records"][0]["manifest_sha256"] = "0" * 64
        self.assertIn("migration_hash_binding", self.codes(check_release(self.root, migrations=mapping)))
        mapping["records"][0]["manifest_sha256"] = 3
        self.assertIn("migration_schema", self.codes(check_release(self.root, migrations=mapping)))

    def test_gnu_nested_manifest_checks_its_own_directory(self):
        path = self.put("school/file with spaces.txt", "archive")
        self.put("school/MANIFEST.txt", digest(path.read_bytes()) + "  file with spaces.txt\n"); self.freeze()
        self.assertTrue(check_release(self.root)["passed"])
        self.put("school/file with spaces.txt", "altered"); self.freeze()
        self.assertIn("hash_mismatch", self.codes(check_release(self.root)))

    def test_missing_word_result_and_absolute_reference_have_nearby_passing_reference(self):
        self.put("99_工作记录/准确映射.json", "{}")
        self.docx("comparison.docx", ["事实映射：99_工作记录/准确映射.json"]); self.freeze()
        self.assertTrue(check_release(self.root)["passed"])
        self.docx("comparison.docx", ["依据为本任务result.json。", "文件：/Users/private/source.json"]); self.freeze()
        codes = self.codes(check_release(self.root))
        self.assertTrue({"path_missing", "absolute_path"} <= codes)

    def test_docx_hyperlinks_percent_encoding_external_and_missing_local(self):
        self.put("原件/素材 中文.txt", "original")
        self.docx("comparison.docx", [], ["原件/素材%20中文.txt", "https://example.org/img.png"]); self.freeze()
        good = check_release(self.root)
        self.assertTrue(good["passed"], good)
        self.assertEqual(good["counts"]["external_hyperlinks_classified"], 1)
        self.docx("comparison.docx", [], ["result.json"]); self.freeze()
        self.assertIn("path_missing", self.codes(check_release(self.root)))

    def test_docx_required_file_reference_adjacent_to_chinese_is_checked(self):
        self.put("report.md", "实际成员节录")
        self.docx("summary.docx", ["正文见report.md引文"]); self.freeze()
        self.assertTrue(check_release(self.root)["passed"])
        (self.root / "report.md").unlink(); self.freeze()
        self.assertIn("path_missing", self.codes(check_release(self.root)))
        self.docx("summary.docx", ["见result.json之来源卡"]); self.freeze()
        self.assertIn("path_missing", self.codes(check_release(self.root)))

    def test_unicode_casefold_spaces_and_zip_move_remain_portable(self):
        self.put("学校/CAFÉ 中文.txt", "original")
        self.csv("index.csv", [{"relative_path": "学校/" + unicodedata.normalize("NFD", "café 中文.txt"), "sha256": digest(b"original")}])
        self.docx("comparison.docx", ["原件：学校/CAFÉ 中文.txt", "路径 学校/CAFÉ 中文.txt", "完整素材见CAFÉ 中文.txt"]); self.freeze()
        before = check_release(self.root)
        self.assertTrue(before["passed"], before)
        zip_path = Path(self.temp.name) / "release.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in self.root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(self.root))
        extracted = Path(self.temp.name) / "独立解压 新位置"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extracted)
        shutil.rmtree(self.root)
        self.assertEqual(check_release(extracted), before)

    def test_casefold_basename_collision_is_not_guessed_and_exact_paths_pass(self):
        # Distinct directories allow this real ambiguity fixture even on the
        # Mac's case-insensitive filesystem; no synthetic inventory is used.
        self.put("one/a.txt", "one"); self.put("two/A.TXT", "two")
        self.docx("comparison.docx", ["原件：a.txt"]); self.freeze()
        self.assertIn("path_ambiguous", self.codes(check_release(self.root)))
        self.docx("comparison.docx", ["原件：one/a.txt", "另件：two/a.txt"]); self.freeze()
        self.assertTrue(check_release(self.root)["passed"])

    def test_escape_absolute_and_external_symlink_are_not_resolved_outside_root(self):
        outside = Path(self.temp.name) / "private.txt"; outside.write_text("private")
        self.csv("index.csv", [{"relative_path": "../private.txt", "sha256": digest(b"private")}]); self.freeze()
        self.assertIn("path_escape", self.codes(check_release(self.root)))
        self.csv("index.csv", [{"relative_path": str(outside), "sha256": digest(b"private")}]); self.freeze()
        self.assertIn("absolute_path", self.codes(check_release(self.root)))
        (self.root / "linked.txt").symlink_to(outside)
        self.assertIn("symlink_external", self.codes(check_release(self.root)))

    def test_unique_release_source_map_allows_multi_file_global_identity(self):
        self.source_files(); self.docx("comparison.docx", ["来源：REL-NUS-001"]); self.freeze()
        mapping = self.source_map()
        mapping["records"].append(dict(mapping["records"][0], relative_paths=["新加坡/全文.txt"]))
        report = check_release(self.root, source_map=mapping)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["counts"]["source_identities"], 1)
        mapping["records"][0]["file_sha256_by_path"] = {"新加坡/课程 中文.json": "0" * 64}
        self.assertIn("hash_mismatch", self.codes(check_release(self.root, source_map=mapping)))

    def test_docx_release_ids_registered_unknown_and_special_forms(self):
        self.source_files(); mapping = self.source_map()
        known = "REL-UCB-111111111111"
        mapping["records"].extend([
            {"release_source_id": known, "namespace": "UCB-body", "source_id": "UCB-S001",
             "relative_paths": []},
            {"release_source_id": "REL-NUS-UNACQUIRED-75347e0ef51b", "namespace": "NUS-history",
             "source_id": "NUS-S025", "relative_paths": []},
            {"release_source_id": "REL-UCB-WB-3873314c890f5c46", "namespace": "WB-review",
             "source_id": "WB-SRC-01", "relative_paths": []}])
        for token in (known, "REL-NUS-001", "REL-NUS-UNACQUIRED-75347e0ef51b",
                      "REL-UCB-WB-3873314c890f5c46"):
            with self.subTest(registered=token):
                self.docx("comparison.docx", ["来源：[" + token + "]"]); self.freeze()
                self.assertTrue(check_release(self.root, source_map=mapping)["passed"])
        # A registered prefix must not hide an unknown complete release ID.
        for token in ("REL-UCB-aaaaaaaaaaaa", known + "a", known + "-extra"):
            with self.subTest(unknown=token):
                self.docx("comparison.docx", ["来源：[" + token + "]"]); self.freeze()
                issues = check_release(self.root, source_map=mapping)["issues"]
                self.assertTrue(any(i["code"] == "source_reference_unknown" and
                                    i.get("reference") == token for i in issues), issues)

    def test_docx_bare_short_ids_resolve_exactly_without_inferred_school(self):
        self.source_files(); mapping = self.source_map()
        mapping["records"].append({"release_source_id": "REL-UCB-111111111111",
            "namespace": "UCB-body", "source_id": "S049", "relative_paths": []})
        self.docx("comparison.docx", ["来源：[S049]"]); self.freeze()
        self.assertTrue(check_release(self.root, source_map=mapping)["passed"])
        for token in ("S999", "S008"):
            with self.subTest(unknown=token):
                # NUS-S008 is registered, but does not define the bare S008.
                self.docx("comparison.docx", ["来源：[" + token + "]"]); self.freeze()
                issues = check_release(self.root, source_map=mapping)["issues"]
                self.assertTrue(any(i["code"] == "source_reference_unknown" and
                                    i.get("reference") == token for i in issues), issues)
        mapping["records"].append({"release_source_id": "REL-UCB-222222222222",
            "namespace": "UCB-materials", "source_id": "S049", "relative_paths": []})
        self.docx("comparison.docx", ["来源：[S049]"]); self.freeze()
        self.assertIn("source_reference_ambiguous", self.codes(check_release(self.root, source_map=mapping)))
        self.docx("comparison.docx", ["来源：[UCB-body:S049]", "来源：[UCB-materials::S049]"]); self.freeze()
        self.assertTrue(check_release(self.root, source_map=mapping)["passed"])
        self.docx("comparison.docx", ["来源：[wrong:S049]"]); self.freeze()
        self.assertIn("source_reference_unknown", self.codes(check_release(self.root, source_map=mapping)))

    def test_docx_new_source_tokens_require_map_and_do_not_match_files_or_urls(self):
        self.source_files()
        for token in ("REL-UCB-aaaaaaaaaaaa", "S999"):
            with self.subTest(no_mapping=token):
                self.docx("comparison.docx", ["来源：[" + token + "]"]); self.freeze()
                self.assertIn("source_map_required", self.codes(check_release(self.root, indexes=[])))
        self.put("原件/REL-UCB-aaaaaaaaaaaa.txt", "original")
        self.put("原件/S999.txt", "original")
        self.docx("comparison.docx", ["原件：原件/REL-UCB-aaaaaaaaaaaa.txt", "原件：原件/S999.txt",
            "https://example.org/?source=REL-UCB-aaaaaaaaaaaa&other=S999"]); self.freeze()
        self.assertTrue(check_release(self.root, source_map=self.source_map())["passed"])

    def test_published_index_ids_use_their_explicit_namespace_and_file_names_are_not_source_refs(self):
        path = self.source_files()
        self.csv("index.csv", [{"relative_path": "新加坡/课程 中文.json", "sha256": digest(path.read_bytes()),
                               "source_namespace": "NUS-body", "source_ids": "REL-NUS-001"}])
        self.put("新加坡/NUS-S008_original.pdf", b"archive")
        self.docx("comparison.docx", ["原件：新加坡/NUS-S008_original.pdf"]); self.freeze()
        self.assertTrue(check_release(self.root, source_map=self.source_map())["passed"])
        self.docx("comparison.docx", ["来源：NUS-S008"]); self.freeze()
        self.assertIn("source_map_required", self.codes(check_release(self.root, indexes=[])))

    def test_span_rewrite_preserves_source_id_inside_local_filename(self):
        script = Path(__file__).resolve().parents[1] / "work/agent_collab_20260930/stage_release.py"
        spec = importlib.util.spec_from_file_location("stage_release_span_fixture", script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        original = "[NUS-S008] 原件：新加坡/NUS-S008_original.pdf"
        paragraph = module.ET.fromstring(("<w:p xmlns:w='" + module.W[1:-1] + "'><w:r><w:t>" + original + "</w:t></w:r></w:p>").encode())
        updated = module.replace_text_nodes(paragraph, [(1, 9, "REL-NUS-001")])
        self.assertEqual(updated, "[REL-NUS-001] 原件：新加坡/NUS-S008_original.pdf")

    def test_short_id_namespace_ambiguity_and_wrong_namespace_are_rejected(self):
        self.source_files(); self.docx("comparison.docx", ["来源：NUS-S008"]); self.freeze()
        mapping = self.source_map()
        mapping["records"].append(dict(mapping["records"][0], release_source_id="REL-NUS-002", namespace="NUS-materials"))
        self.assertIn("source_reference_ambiguous", self.codes(check_release(self.root, source_map=mapping)))
        self.docx("comparison.docx", ["来源：NUS-body:NUS-S008"]); self.freeze()
        self.assertTrue(check_release(self.root, source_map=mapping)["passed"])
        self.json("index.json", {"references": [{"namespace": "NUS-body", "source_id": "NUS-S008"}]}); self.freeze()
        self.assertTrue(check_release(self.root, source_map=mapping)["passed"])
        self.json("index.json", {"references": [{"namespace": "wrong", "source_id": "NUS-S008"}]}); self.freeze()
        self.assertIn("source_reference_unknown", self.codes(check_release(self.root, source_map=mapping)))

    def test_one_visible_id_cannot_map_to_different_namespace_or_source_version(self):
        self.source_files(); self.freeze(); mapping = self.source_map()
        mapping["records"].append(dict(mapping["records"][0], namespace="other"))
        self.assertIn("release_source_id_collision", self.codes(check_release(self.root, source_map=mapping)))
        mapping["records"][-1] = dict(mapping["records"][0], source_sha256="another-version")
        self.assertIn("release_source_id_collision", self.codes(check_release(self.root, source_map=mapping)))

    def test_same_namespace_short_id_different_urls_require_disambiguation(self):
        self.source_files(); self.freeze(); mapping = self.source_map()
        mapping["records"].append(dict(mapping["records"][0], release_source_id="REL-NUS-002", original_url="https://nus.example/module/CM4199A"))
        self.assertIn("global_source_identity_collision", self.codes(check_release(self.root, source_map=mapping)))
        for i, record in enumerate(mapping["records"]):
            record.update(origin_namespace=record["namespace"], namespace=f"NUS-body.disambiguated-{i}")
        self.assertTrue(check_release(self.root, source_map=mapping)["passed"])

    def test_original_html_external_resources_and_original_json_fields_are_not_local_dependencies(self):
        self.put("archive/page.html", "<html><body>原始网页文字<img src='https://example.org/missing-image.png'></body></html>")
        self.json("archive/CM1131.json", {"title": "Course result.json", "url": "https://example.org/not-local.pdf", "path": "/api/CM1131"})
        self.json("archive/old_index.json", {"relative_path": "/private/historical/file.txt"}); self.freeze()
        report = check_release(self.root, exclude_indexes=["archive/old_index.json"])
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["classifications"][0]["external_resources"], 1)
        offline = check_release(self.root, exclude_indexes=["archive/old_index.json"], offline_html=["archive/page.html"])
        self.assertIn("offline_html_body_missing", self.codes(offline))

    def test_explicit_offline_html_required_asset_has_passing_nearby_control(self):
        self.put("archive/page.html", "<html><body>" + "真实正文内容" * 30 + "</body></html>")
        self.put("archive/body.txt", "原件正文"); self.freeze()
        options = {"archive/page.html": {"required_local_paths": ["body.txt"]}}
        self.assertTrue(check_release(self.root, offline_html=options)["passed"])
        options["archive/page.html"]["required_local_paths"] = ["missing.txt"]
        self.assertIn("path_missing", self.codes(check_release(self.root, offline_html=options)))

    def test_interrupted_publication_missing_manifest_is_blocked_then_refreeze_passes(self):
        self.put("a.txt", "original")
        self.assertFalse(check_release(self.root)["passed"])
        self.freeze(); self.assertTrue(check_release(self.root)["passed"])


if __name__ == "__main__":
    unittest.main()
