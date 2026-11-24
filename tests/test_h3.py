from __future__ import annotations

import hashlib
import json
import datetime as dt
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from daemon_vessel import access_index, h3, registry


FIXTURES = Path(__file__).with_name("fixtures")
REGISTRY_SCHEMA = json.loads((FIXTURES / "resource-registry.schema.json").read_text(encoding="utf-8"))
INDEX_SCHEMA = json.loads((FIXTURES / "access-index.schema.json").read_text(encoding="utf-8"))

PINNED_HISTORICAL_BLOBS = {
    "resource-registry.schema.json": ("62c84e966099b2c7d67032172905235329f6bc77", "d2cbb3f1b5244d1ad91b5fd2256b25bb5e75c61a71eb939bba2bd577a96d1e91"),
    "access-index.schema.json": ("3e7793b0dcecd3d779ccdfe686beae0f2bb4277e", "e5c15161d4c9e882f67c653ebfc3dd27e3ff4bc23ef439679eaff6e4f7b65cd1"),
    "context-packet-return-point-v0.1.schema.json": ("be589b87155bbf12a330f8dd46933228f374fd34", "7f82b89edbfd2d3749e0e59fbcd8d043b3eb624f34be7afd1232b0d3639a4ea2"),
}


def dump(path: Path, value: object) -> str:
    path.write_bytes((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")); return hashlib.sha256(path.read_bytes()).hexdigest()


def request() -> dict:
    return {"schema":"gltch.context-request/0.1.0","request_id":"request:test","created_at":"2026-11-22T00:00:00Z","task":"synthetic","intent":"current","surface_id":"surface:test","room_id":"room:test","namespaces":["self"],"allowed_privacy":["private"],"seed_ids":["entry:one"],"budgets":{"max_hops":4,"max_resources":12,"max_chars":99,"max_tokens":0},"mutation_authorized":False}


def resource(identity: str, uri: str, *, privacy: str = "private", content_hash: str | None = None, status: str = "active", mode: str = "local", state: str = "resolved") -> dict:
    value = {"id":identity,"kind":"document","title":identity,"privacy":privacy,"continuity_role":"evidence","locator_state":state,"locators":[]}
    if state == "resolved": value["locators"] = [{"surface":"external-home","uri":uri,"authority":"original","access_mode":mode,"status":status, **({"content_hash":content_hash} if content_hash else {})}]
    return value


def entry(identity: str, resource_ids: list[str], *, namespaces: list[str] | None = None, fallback: list[dict] | None = None, links: dict | None = None, status: str = "active") -> dict:
    return {"id":identity,"kind":"document","title":identity,"resource_ids":resource_ids,"namespaces":namespaces or ["self"],"status":status,"follow_if_thin":fallback or [], **({"links":links} if links else {})}


class H3Tests(unittest.TestCase):
    def test_historical_fixtures_are_exact_pinned_blobs(self) -> None:
        targets = [
            (FIXTURES / "resource-registry.schema.json", *PINNED_HISTORICAL_BLOBS["resource-registry.schema.json"]),
            (FIXTURES / "access-index.schema.json", *PINNED_HISTORICAL_BLOBS["access-index.schema.json"]),
            (FIXTURES / "context-packet-return-point-v0.1.schema.json", *PINNED_HISTORICAL_BLOBS["context-packet-return-point-v0.1.schema.json"]),
            (Path(h3.__file__).with_name("schemas") / "context-packet-return-point-v0.1.schema.json", *PINNED_HISTORICAL_BLOBS["context-packet-return-point-v0.1.schema.json"]),
        ]
        for path, blob, digest in targets:
            with self.subTest(path=path):
                raw = path.read_bytes()
                self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
                self.assertEqual(b"\n", raw[-1:])
                actual = subprocess.check_output(["git", "hash-object", "--no-filters", str(path)], text=True).strip()
                self.assertEqual(blob, actual)

    def test_public_installed_snapshot_boundaries_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.home(Path(temp))
            registry_snapshot = registry.validate_installed_snapshot(home=home)
            index_snapshot = access_index.validate_installed_snapshot(registry_snapshot.value, home=home)
            self.assertEqual(hashlib.sha256(registry_snapshot.raw).hexdigest(), registry_snapshot.sha256)
            self.assertEqual(hashlib.sha256(index_snapshot.raw).hexdigest(), index_snapshot.sha256)
            with self.assertRaises(TypeError):
                registry_snapshot.value["resources"] = ()
            with self.assertRaises(TypeError):
                index_snapshot.value["entries"] = ()

    def test_public_installed_snapshot_boundaries_refuse_receipt_tampering(self) -> None:
        with self.subTest(boundary="registry"), tempfile.TemporaryDirectory() as temp:
            home=self.home(Path(temp)); registry.resolve_home(home).receipt.write_bytes(b"{}\n")
            with self.assertRaises(registry.RegistryError): registry.validate_installed_snapshot(home=home)
        with self.subTest(boundary="access_index"), tempfile.TemporaryDirectory() as temp:
            home=self.home(Path(temp)); registry_snapshot=registry.validate_installed_snapshot(home=home); access_index.resolve_home(home).receipt_digest.write_bytes(b"0"*64+b"\n")
            with self.assertRaises(access_index.AccessIndexError): access_index.validate_installed_snapshot(registry_snapshot.value, home=home)

    def test_public_installed_snapshot_constituent_tamper_matrix(self) -> None:
        for boundary, cases in (("registry",("data","schema","digest","receipt","linkage")),("access_index",("data","schema","digest","receipt","linkage"))):
            for case in cases:
                with self.subTest(boundary=boundary, case=case), tempfile.TemporaryDirectory() as temp:
                    home=self.home(Path(temp)); registry_snapshot=registry.validate_installed_snapshot(home=home)
                    if boundary == "registry":
                        paths=registry.resolve_home(home)
                        if case == "data": paths.registry.write_bytes(b"{}\n")
                        elif case == "schema": paths.schema.write_bytes(b"{}\n")
                        elif case == "digest": paths.digest.write_bytes(b"0"*64+b"\n")
                        elif case == "receipt": paths.receipt.write_bytes(b"{}\n")
                        else:
                            value=json.loads(paths.receipt.read_text(encoding="utf-8")); value["registry_sha256"]="0"*64; paths.receipt.write_bytes((json.dumps(value,sort_keys=True,indent=2)+"\n").encode())
                        with self.assertRaises(registry.RegistryError): registry.validate_installed_snapshot(home=home)
                    else:
                        paths=access_index.resolve_home(home)
                        if case == "data": paths.index.write_bytes(b"{}\n")
                        elif case == "schema": paths.schema.write_bytes(b"{}\n")
                        elif case == "digest": paths.digest.write_bytes(b"0"*64+b"\n")
                        elif case == "receipt": paths.receipt.write_bytes(b"{}\n")
                        else:
                            value=json.loads(paths.receipt.read_text(encoding="utf-8")); value["index_sha256"]="0"*64; raw=(json.dumps(value,sort_keys=True,indent=2)+"\n").encode(); paths.receipt.write_bytes(raw); paths.receipt_digest.write_bytes(hashlib.sha256(raw).hexdigest().encode()+b"\n")
                        with self.assertRaises(access_index.AccessIndexError): access_index.validate_installed_snapshot(registry_snapshot.value, home=home)

    def test_grounded_nodes_still_follow_relevant_typed_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); (home / "one.txt").write_text("one", encoding="utf-8"); (home / "two.txt").write_text("two", encoding="utf-8")
            values = [resource("resource:one", "one.txt"), resource("resource:two", "two.txt")]
            index = {"entries":[entry("entry:one", ["resource:one"], links={"relates_to":["entry:two"], "first_appears_in":["entry:three"]}), entry("entry:two", ["resource:two"]), entry("entry:three", [])]}
            result = h3._hydrate(request(), home, index, {"resources":values})
            self.assertEqual(["entry:one", "entry:two"], result[1])
            self.assertEqual(["relates_to"], [row["edge"] for row in result[3]])

    def test_locator_mirror_pointer_and_source_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); (home / "mirror.txt").write_text("mirror", encoding="utf-8"); (home / "large.txt").write_bytes(b"x" * (h3.MAX_SOURCE_BYTES + 1))
            mirrored = resource("resource:mirror", "missing.txt")
            mirrored["locators"].append({"surface":"external-home","uri":"mirror.txt","authority":"authoritative_mirror","access_mode":"local","status":"active"})
            pointer = resource("resource:pointer", "ignored.txt"); pointer["locators"] = [{"surface":"external-home","uri":"ignored.txt","authority":"pointer","access_mode":"local","status":"active"}]
            large = resource("resource:large", "large.txt")
            result = h3._hydrate(request(), home, {"entries":[entry("entry:one", ["resource:mirror", "resource:pointer", "resource:large"])]}, {"resources":[mirrored, pointer, large]})
            self.assertEqual("mirror", result[0][0]["content"]["inline"])
            self.assertIn("pointer_dead_end", {row["code"] for row in result[4]})
            self.assertIn("budget_exhausted", {row["code"] for row in result[4]})

    def test_noncanonical_request_bytes_are_refused_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "request.json"; source.write_text(json.dumps(request(), indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(h3.H3Error) as caught:
                h3.request_validate(source, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual("noncanonical_input", caught.exception.code)

    def test_public_build_request_boundary_reads_once_and_refuses_invalid_inputs(self) -> None:
        with self.subTest(case="single_read"), tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); source=root/"request.json"; digest=dump(source,request()); original=Path.read_bytes; reads=[0]
            def count(path: Path) -> bytes:
                if path == source: reads[0]+=1
                return original(path)
            with mock.patch.object(Path,"read_bytes",autospec=True,side_effect=count): h3.build(source,digest,home)
            self.assertEqual(1,reads[0])
        for case, raw, expected_code in (("wrong_digest",h3._canonical(request()),"source_digest_mismatch"),("invalid_utf8",b"\xff","invalid_json"),("schema",h3._canonical({"schema":"gltch.context-request/0.1.0"}),"schema_validation")):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); source=root/"request.json"; source.write_bytes(raw); digest=("0"*64 if case == "wrong_digest" else hashlib.sha256(raw).hexdigest())
                with self.assertRaises(h3.H3Error) as caught: h3.build(source,digest,home)
                self.assertEqual(expected_code,caught.exception.code); self.assertFalse((home/"context").exists())

    def home(self, root: Path) -> Path:
        home = root / "synthetic-home"; content = root / "source.txt"; content.write_text("synthetic evidence", encoding="utf-8")
        resource = globals()["resource"]("resource:one", "source.txt", content_hash=hashlib.sha256(content.read_bytes()).hexdigest())
        rp = root / "registry.json"; rs = root / "registry.schema.json"; dump(rp,{"schema":"gltch.resource-registry/0.2.0","generated_at":"2026-11-22T00:00:00Z","resources":[resource]}); dump(rs,REGISTRY_SCHEMA)
        registry.install(source=rp,schema_source=rs,expected_sha256=hashlib.sha256(rp.read_bytes()).hexdigest(),home=home)
        # Sources are copied only for this synthetic external home.
        (home / "source.txt").write_bytes(content.read_bytes())
        entries=[entry("entry:one", ["resource:one"])]
        entries += [entry(f"entry:unused-{number}", ["resource:one"]) for number in range(11)]
        ip=root / "index.json"; ins=root / "index.schema.json"; dump(ip,{"schema":"gltch.access-index/0.2.0","generated_at":"2026-11-22T00:00:00Z","entries":entries}); dump(ins,INDEX_SCHEMA)
        access_index.install(source=ip,schema_source=ins,expected_sha256=hashlib.sha256(ip.read_bytes()).hexdigest(),home=home)
        return home

    def candidate(self, packet: dict) -> dict:
        value={"schema":"gltch.candidate-delta/0.1.0","candidate_id":"candidate:test","created_at":"2026-11-22T00:01:00Z","packet_id":packet["packet_id"],"request_id":"request:test","surface_id":"surface:test","room_id":"room:test","proposals":[{"proposal_id":"proposal:test","operation":"add","target_ref":None,"statement":"synthetic","evidence_refs":[packet["sources"][0]["source_id"]]}],"evidence_source_refs":[packet["sources"][0]["source_id"]],"uncertainty":[],"unresolved":[],"canonical_effect":"none","promotion_required":True,"mutation_authorized":False,"sha256":"0"*64}
        value["sha256"]=h3._digest({key:child for key,child in value.items() if key!="sha256"}); return value

    def test_build_candidate_verify_and_idempotent_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; digest=dump(rp,request()); packet=h3.build(rp,digest,home); self.assertEqual(packet["schema"],"gltch.context-packet/0.2.0"); self.assertEqual(packet,h3.build(rp,digest,home))
            cp=root/"candidate.json"; cd=dump(cp,self.candidate(packet)); self.assertEqual(h3.append_h3_candidate(cp,cd,home)["candidate_id"],"candidate:test"); self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_candidate_collision_and_tamper_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,hashlib.sha256(cp.read_bytes()).hexdigest(),home)
            changed=self.candidate(packet); changed["proposals"][0]["statement"]="other"; changed["sha256"]=h3._digest({key:child for key,child in changed.items() if key!="sha256"}); altered=dump(cp,changed)
            with self.assertRaises(h3.H3Error): h3.append_h3_candidate(cp,altered,home)
            changed["sha256"]="0"*64; self_digest_tamper=dump(cp,changed)
            with self.assertRaises(h3.H3Error): h3.candidate_validate(cp,self_digest_tamper)
            packet_path=next((home/"context"/"packets").glob("*/*/*.json")); packet_path.write_bytes(b"{")
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_verifier_reconstructs_used_hops_and_omissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home)
            path=next((home/"context"/"packets").glob("*/*/*.json")); value=json.loads(path.read_text(encoding="utf-8")); value["budgets"]["used_hops"]=4; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_verifier_reconstructs_verified_inline_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home)
            path=next((home/"context"/"packets").glob("*/*/*.json")); value=json.loads(path.read_text(encoding="utf-8")); value["sources"][0]["content"]["inline"]="forged"; value["sources"][0]["content"]["chars"]=6; value["budgets"]["used_chars"]=6; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_verifier_refuses_duplicate_id_and_unknown_tree_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home)
            packet=next((home/"context"/"packets").glob("*/*/*.json")); duplicate=packet.with_name("same-id.json"); shutil.copyfile(packet,duplicate)
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_verifier_refuses_schema_valid_orphan_request_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home); orphan=request(); orphan["request_id"]="request:orphan"; orphan["created_at"]="2026-11-23T00:00:00Z"
            path=h3._date_path(home,"requests",orphan["created_at"],orphan["request_id"]); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(h3._canonical(orphan))
            with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
            self.assertEqual("receipt_integrity",caught.exception.code)

    def test_reconstructive_phase_tree_mutations_refuse(self) -> None:
        for case in ("moved_phase", "extra_phase_field", "orphan_transaction"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home); self.assertEqual("ok",h3.verify(home)["integrity"])
                transaction=next((home/"context"/"transactions").iterdir())
                if case == "moved_phase":
                    phase=transaction/"packet.json"; moved=home/"context"/"transactions"/"transaction-h3-other"; moved.mkdir(); shutil.move(str(phase),str(moved/"packet.json"))
                elif case == "extra_phase_field":
                    phase=transaction/"packet.json"; value=json.loads(phase.read_text(encoding="utf-8")); value["extra"]="attacker"; phase.write_bytes(h3._canonical(value))
                else:
                    (home/"context"/"transactions"/"transaction-h3-orphan").mkdir()
                with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
                self.assertEqual("receipt_integrity",caught.exception.code)

    def test_reconstructive_receipt_reference_mutations_refuse(self) -> None:
        for case in ("missing_target", "self_reference", "wrong_kind_target"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; h3.append_h3_candidate(cp,dump(cp,self.candidate(packet)),home); self.assertEqual("ok",h3.verify(home)["integrity"])
                receipt_path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
                value=json.loads(receipt_path.read_text(encoding="utf-8"))
                if case == "missing_target": value["artifact_paths"][0]="context/packets/2099/01/missing.json"
                elif case == "self_reference": value["artifact_paths"]=[str(receipt_path.relative_to(home)).replace("\\","/")]
                else:
                    construction=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="construction")
                    value["artifact_paths"][0]=str(construction.relative_to(home)).replace("\\","/")
                value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); receipt_path.write_bytes(h3._canonical(value))
                with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
                self.assertEqual("receipt_integrity",caught.exception.code)

    def test_reconstructive_candidate_ownership_mutations_refuse(self) -> None:
        for case in ("zero_owner", "multiple_owners", "cross_transaction_phase"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; h3.append_h3_candidate(cp,dump(cp,self.candidate(packet)),home); self.assertEqual("ok",h3.verify(home)["integrity"])
                receipt_path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
                if case == "zero_owner": receipt_path.unlink()
                elif case == "multiple_owners":
                    value=json.loads(receipt_path.read_text(encoding="utf-8")); value["receipt_id"]="receipt:h3-extra-owner"; extra=h3._date_path(home,"receipts",value["created_at"],value["receipt_id"]); value["artifact_paths"]=[value["artifact_paths"][0],value["artifact_paths"][1],str(extra.relative_to(home)).replace("\\","/")]; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); extra.write_bytes(h3._canonical(value))
                else:
                    phase=next((home/"context"/"transactions").glob("*/candidate-*.json")); other=home/"context"/"transactions"/"transaction-h3-other"; other.mkdir(); shutil.move(str(phase),str(other/phase.name))
                with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
                self.assertEqual("receipt_integrity",caught.exception.code)

    def test_reconstructive_cross_transaction_reference_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); first=root/"first.json"; one=h3.build(first,dump(first,request()),home)
            second_request=request(); second_request["request_id"]="request:other"; second_request["created_at"]="2026-11-23T00:00:00Z"; second=root/"second.json"; two=h3.build(second,dump(second,second_request),home)
            cp=root/"candidate.json"; h3.append_h3_candidate(cp,dump(cp,self.candidate(one)),home); self.assertEqual("ok",h3.verify(home)["integrity"])
            receipt_path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
            value=json.loads(receipt_path.read_text(encoding="utf-8")); other_packet=next(path for path in (home/"context"/"packets").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["packet_id"]==two["packet_id"]); value["artifact_paths"][0]=str(other_packet.relative_to(home)).replace("\\","/"); value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); receipt_path.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
            self.assertEqual("receipt_integrity",caught.exception.code)

    def test_duplicate_and_refusal_receipts_cannot_claim_canonical_artifacts(self) -> None:
        with self.subTest(case="duplicate_receipt_claims_canonical"), tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
            original_path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate"); original=json.loads(original_path.read_text(encoding="utf-8")); value=dict(original); value.update({"receipt_id":"receipt:h3-duplicate-owner","kind":"duplicate","outcome":"duplicate","original_receipt_id":original["receipt_id"]}); path=h3._date_path(home,"receipts",value["created_at"],value["receipt_id"]); packet_path=next((home/"context"/"packets").glob("*/*/*.json")); value["artifact_paths"]=[original["artifact_paths"][1],str(path.relative_to(home)).replace("\\","/"),str(packet_path.relative_to(home)).replace("\\","/")]; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
            self.assertEqual("receipt_integrity",caught.exception.code)
        with self.subTest(case="refusal_receipt_claims_canonical"), tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home); changed=request(); changed["task"]="changed"; changed_digest=dump(rp,changed)
            with self.assertRaises(h3.H3Error): h3.build(rp,changed_digest,home)
            path=next((home/"context"/"dead-letter").glob("*/*/*.json")); value=json.loads(path.read_text(encoding="utf-8")); request_path=next((home/"context"/"requests").glob("*/*/*.json")); value["artifact_paths"].append(str(request_path.relative_to(home)).replace("\\","/")); value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
            self.assertEqual("receipt_integrity",caught.exception.code)

        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home); (home/"context"/"unknown").mkdir()
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_packet_publication_interruption_recovers_same_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; digest=dump(rp,request()); packet=h3.build(rp,digest,home)
            receipt=next((home/"context"/"receipts").glob("*/*/*.json")); receipt.unlink()
            recovered=h3.build(rp,digest,home)
            self.assertEqual(packet["packet_id"],recovered["packet_id"])
            self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_existing_packet_recovery_reconstructs_before_repair(self) -> None:
        for case in ("missing_receipt", "missing_phase", "packet_redigest", "source_redigest", "request_tamper"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; digest=dump(rp,request()); packet=h3.build(rp,digest,home)
                packet_path=next((home/"context"/"packets").glob("*/*/*.json")); receipt=next((home/"context"/"receipts").glob("*/*/*.json")); phase=next((home/"context"/"transactions").glob("*/construction.json")); before_packet=packet_path.read_bytes(); before_receipt=receipt.read_bytes()
                if case == "missing_receipt":
                    receipt.unlink(); h3.build(rp,digest,home); self.assertEqual(before_packet,packet_path.read_bytes()); self.assertTrue(receipt.exists()); self.assertEqual("ok",h3.verify(home)["integrity"])
                elif case == "missing_phase":
                    phase.unlink(); h3.build(rp,digest,home); self.assertEqual(before_packet,packet_path.read_bytes()); self.assertEqual(before_receipt,receipt.read_bytes()); self.assertTrue(phase.exists()); self.assertEqual("ok",h3.verify(home)["integrity"])
                else:
                    if case == "packet_redigest":
                        value=json.loads(packet_path.read_text(encoding="utf-8")); value["purpose"]="forged"; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); packet_path.write_bytes(h3._canonical(value))
                    elif case == "source_redigest":
                        value=json.loads(packet_path.read_text(encoding="utf-8")); text="forged source bytes"; value["sources"][0]["content"].update({"inline":text,"chars":len(text),"sha256":hashlib.sha256(text.encode()).hexdigest()}); value["budgets"]["used_chars"]=len(text); value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); packet_path.write_bytes(h3._canonical(value))
                    else:
                        request_path=next((home/"context"/"requests").glob("*/*/*.json")); value=json.loads(request_path.read_text(encoding="utf-8")); value["task"]="forged"; request_path.write_bytes(h3._canonical(value))
                    receipt_before=receipt.read_bytes()
                    with self.assertRaises(h3.H3Error) as caught: h3.build(rp,digest,home)
                    self.assertIn(caught.exception.code,{"receipt_integrity","immutable_collision"}); self.assertEqual(receipt_before,receipt.read_bytes())

    def test_genuine_four_to_five_hop_omission_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"source.txt").write_text("evidence",encoding="utf-8")
            item=resource("resource:one","source.txt")
            entries=[]
            for number in range(6):
                links={"relates_to":[f"entry:{number + 1}"]} if number < 5 else {}
                entries.append(entry(f"entry:{number}",["resource:one"] if number == 0 else [],links=links))
            rq=request(); rq["seed_ids"]=["entry:0"]
            sources,nodes,_,traversal,unresolved,_=h3._hydrate(rq,home,{"entries":entries},{"resources":[item]})
            self.assertEqual([f"entry:{number}" for number in range(5)],nodes); self.assertEqual(4,max(row["hop"] for row in traversal)); self.assertEqual("entry:4",traversal[-1]["to"])
            omissions=[row for row in unresolved if row["code"]=="budget_exhausted"]
            self.assertTrue(any(row["source_refs"]==["entry:4","entry:5"] for row in omissions)); self.assertEqual("partial",h3._make_packet(rq,home,{"entries":entries},{"resources":[item]})["status"]); self.assertTrue(sources)

    def test_genuine_thin_fallback_four_to_five_omission_is_accounted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); entries=[]
            for number in range(6):
                fallback=[{"entry_id":f"entry:{number + 1}"}] if number < 5 else []
                entries.append(entry(f"entry:{number}",[],fallback=fallback))
            rq=request(); rq["seed_ids"]=["entry:0"]
            _,nodes,_,traversal,unresolved,_=h3._hydrate(rq,home,{"entries":entries},{"resources":[]})
            self.assertEqual([f"entry:{number}" for number in range(5)],nodes); self.assertEqual(4,max(row["hop"] for row in traversal)); self.assertNotIn("entry:5",nodes)
            self.assertTrue(any(row["detail"]=="hop ceiling reached before thin fallback traversal" and row["source_refs"]==["entry:4","entry:5"] for row in unresolved))
            packet=h3._make_packet(rq,home,{"entries":entries},{"resources":[]}); self.assertEqual("unresolved",packet["status"]); self.assertEqual(4,packet["budgets"]["used_hops"])

    def test_exhausted_budget_records_prevented_thin_fallback(self) -> None:
        for budget in ("resources","chars","tokens"):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as temp:
                home=Path(temp); (home/"source.txt").write_text("one two",encoding="utf-8")
                item=resource("resource:one","source.txt"); entries=[entry("entry:one",["resource:one"],fallback=[{"entry_id":"entry:two"}]),entry("entry:two",["resource:one"])]
                rq=request()
                if budget == "resources": rq["budgets"]["max_resources"]=0
                elif budget == "chars": rq["budgets"]["max_chars"]=0
                else:
                    (home/"first.txt").write_text("one",encoding="utf-8"); (home/"second.txt").write_text("two",encoding="utf-8")
                    first=resource("resource:first","first.txt"); second=resource("resource:second","second.txt")
                    entries=[entry("entry:one",["resource:first"],links={"relates_to":["entry:two"]}),entry("entry:two",["resource:second"],fallback=[{"entry_id":"entry:three"}]),entry("entry:three",["resource:second"])]
                    rq["budgets"]["max_tokens"]=1
                    item=[first,second]
                resources=item if isinstance(item,list) else [item]
                _,_,_,_,unresolved,_=h3._hydrate(rq,home,{"entries":entries},{"resources":resources})
                self.assertTrue(any(row["detail"]=="budget ceiling reached before thin fallback traversal" for row in unresolved))

    def test_four_to_five_hop_omission_redigest_refuses_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); entries=[]
            for number in range(6): entries.append(entry(f"entry:{number}",["resource:one"],links={"relates_to":[f"entry:{number + 1}"]} if number < 5 else {}))
            entries += [entry(f"entry:unused-chain-{number}",["resource:one"]) for number in range(6)]
            index_source=root/"chain-index.json"; index_schema=root/"chain-index.schema.json"; dump(index_source,{"schema":"gltch.access-index/0.2.0","generated_at":"2026-11-22T00:00:00Z","entries":entries}); dump(index_schema,INDEX_SCHEMA)
            access_index.install(source=index_source,schema_source=index_schema,expected_sha256=hashlib.sha256(index_source.read_bytes()).hexdigest(),home=home,replace=True)
            rq=request(); rq["seed_ids"]=["entry:0"]; source=root/"request.json"; packet=h3.build(source,dump(source,rq),home); self.assertEqual("partial",packet["status"]); self.assertEqual(4,packet["budgets"]["used_hops"])
            path=next((home/"context"/"packets").glob("*/*/*.json")); value=json.loads(path.read_text(encoding="utf-8")); value["unresolved"]=[row for row in value["unresolved"] if row["code"] != "budget_exhausted"]; value["omissions"]=[row for row in value["omissions"] if row["reason"] != "hop ceiling reached before relevant edge traversal"]; value["status"]="grounded"; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
            self.assertEqual("receipt_integrity",caught.exception.code)

    def test_identical_complete_candidate_retry_publishes_one_attributable_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
            cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home); before={path:path.read_bytes() for path in (home/"context").rglob("*.json")}; h3.append_h3_candidate(cp,digest,home)
            receipts=[json.loads(path.read_text(encoding="utf-8")) for path in (home/"context"/"receipts").glob("*/*/*.json")]
            duplicates=[value for value in receipts if value["kind"]=="duplicate"]
            self.assertEqual(1,len(duplicates)); self.assertEqual("candidate:test",duplicates[0]["candidate_id"]); self.assertEqual("ok",h3.verify(home)["integrity"])
            for path, raw in before.items(): self.assertEqual(raw,path.read_bytes())
            after={path:path.read_bytes() for path in (home/"context").rglob("*.json")}; h3.append_h3_candidate(cp,digest,home)
            self.assertEqual(after,{path:path.read_bytes() for path in (home/"context").rglob("*.json")})

    def test_duplicate_candidate_receipt_interruption_recovers_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
            original=h3._write_immutable; interrupted=[False]
            def stop(path: Path, content: bytes) -> bool:
                if not interrupted[0] and "receipts" in path.parts:
                    interrupted[0]=True; raise h3.H3Error("refused: synthetic duplicate interruption",code="transaction_write_failed")
                return original(path,content)
            with mock.patch("daemon_vessel.h3._write_immutable",side_effect=stop):
                with self.assertRaises(h3.H3Error): h3.append_h3_candidate(cp,digest,home)
            h3.append_h3_candidate(cp,digest,home)
            receipts=[json.loads(path.read_text(encoding="utf-8")) for path in (home/"context"/"receipts").glob("*/*/*.json")]
            self.assertEqual(1,sum(value["kind"]=="duplicate" for value in receipts)); self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_duplicate_receipt_phase_only_publication_boundary_repairs_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
            original=h3._write_immutable; interrupted=[False]
            def stop(path: Path, content: bytes) -> bool:
                if not interrupted[0] and "transactions" in path.parts and path.name.startswith("duplicate-"):
                    interrupted[0]=True; raise h3.H3Error("refused: synthetic duplicate phase interruption",code="transaction_write_failed")
                return original(path,content)
            with mock.patch("daemon_vessel.h3._write_immutable",side_effect=stop):
                with self.assertRaises(h3.H3Error): h3.append_h3_candidate(cp,digest,home)
            duplicate_paths=[path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="duplicate"]
            phases=list((home/"context"/"transactions").glob("*/duplicate-*.json")); self.assertEqual(1,len(duplicate_paths)); self.assertEqual([],phases); before=duplicate_paths[0].read_bytes()
            h3.append_h3_candidate(cp,digest,home)
            self.assertEqual(before,duplicate_paths[0].read_bytes()); self.assertEqual(1,len(list((home/"context"/"transactions").glob("*/duplicate-*.json")))); self.assertEqual(1,len([path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="duplicate"])); self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_duplicate_phase_recovery_exception_is_narrow(self) -> None:
        for case in ("altered_receipt","wrong_phase_missing","altered_phase","surplus_duplicate"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home); h3.append_h3_candidate(cp,digest,home)
                duplicate=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="duplicate"); phase=next((home/"context"/"transactions").glob("*/duplicate-*.json"))
                if case == "altered_receipt":
                    value=json.loads(duplicate.read_text(encoding="utf-8")); value["packet_id"]="packet:other"; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); duplicate.write_bytes(h3._canonical(value)); phase.unlink()
                elif case == "wrong_phase_missing":
                    next((home/"context"/"transactions").glob("*/candidate-*.json")).unlink()
                elif case == "altered_phase":
                    value=json.loads(phase.read_text(encoding="utf-8")); value["extra"]="forged"; phase.write_bytes(h3._canonical(value))
                else:
                    value=json.loads(duplicate.read_text(encoding="utf-8")); value["receipt_id"]="receipt:h3-"+"f"*40; extra=h3._date_path(home,"receipts",value["created_at"],value["receipt_id"]); value["artifact_paths"]=[value["artifact_paths"][0],str(extra.relative_to(home)).replace("\\","/")]; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); extra.write_bytes(h3._canonical(value)); phase.unlink()
                with self.assertRaises(h3.H3Error) as caught: h3.append_h3_candidate(cp,digest,home)
                self.assertEqual("receipt_integrity",caught.exception.code)

    def test_duplicate_receipt_reconstruction_matrix(self) -> None:
        for case in ("copied_id_path","surplus","request_transaction","packet","candidate","original","timestamp_phase"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home); h3.append_h3_candidate(cp,digest,home)
                self.assertEqual("ok",h3.verify(home)["integrity"])
                duplicate_path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="duplicate")
                duplicate=json.loads(duplicate_path.read_text(encoding="utf-8")); candidate_path=next((home/"context"/"candidates").glob("*/*/*.json")); phase=next((home/"context"/"transactions").glob("*/duplicate-*.json"))
                if case in {"copied_id_path","surplus"}:
                    copied=dict(duplicate); copied["receipt_id"]="receipt:h3-"+("a" if case=="copied_id_path" else "b")*40; copied_path=h3._date_path(home,"receipts",copied["created_at"],copied["receipt_id"]); copied["artifact_paths"]=[str(candidate_path.relative_to(home)).replace("\\","/"),str(copied_path.relative_to(home)).replace("\\","/")]; copied["sha256"]=h3._digest({key:value for key,value in copied.items() if key!="sha256"}); copied_path.write_bytes(h3._canonical(copied))
                    if case == "copied_id_path": duplicate_path.unlink()
                else:
                    if case == "request_transaction":
                        duplicate["request_id"]="request:other"; duplicate["transaction_id"]=h3._transaction_id("request:other")
                    elif case == "packet": duplicate["packet_id"]="packet:other"
                    elif case == "candidate": duplicate["candidate_id"]="candidate:other"
                    elif case == "original": duplicate["original_receipt_id"]="receipt:h3-"+"c"*40
                    else:
                        duplicate["created_at"]="2026-11-23T00:00:00Z"; moved=h3._date_path(home,"receipts",duplicate["created_at"],duplicate["receipt_id"]); moved.parent.mkdir(parents=True,exist_ok=True); duplicate_path.rename(moved); duplicate_path=moved
                        value=json.loads(phase.read_text(encoding="utf-8")); value["receipt_path"]=str(duplicate_path.relative_to(home)).replace("\\","/"); phase.write_bytes(h3._canonical(value))
                    duplicate["sha256"]=h3._digest({key:value for key,value in duplicate.items() if key!="sha256"}); duplicate_path.write_bytes(h3._canonical(duplicate))
                with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
                self.assertEqual("receipt_integrity",caught.exception.code)

    def test_divergent_request_id_is_refused_and_dead_lettered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; digest=dump(rp,request()); h3.build(rp,digest,home)
            divergent=request(); divergent["task"]="different"; digest=dump(rp,divergent)
            with self.assertRaises(h3.crossing.CrossingError): h3.build(rp,digest,home)
            with self.assertRaises(h3.crossing.CrossingError): h3.build(rp,digest,home)
            self.assertTrue(list((home/"context"/"dead-letter").glob("*/*/*.json")))

    def test_refusal_attempt_identity_reconstructs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home)
            altered=request(); altered["task"]="different"; digest=dump(rp,altered)
            with self.assertRaises(h3.H3Error): h3.build(rp,digest,home)
            marker=next((home/"context"/"transactions").glob("*/refusal-*.json")); value=json.loads(marker.read_text(encoding="utf-8")); value["attempt_digest"]="0"*64; marker.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_refusal_identity_binds_attempt_and_failure_condition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); first="1"*64; second="2"*64
            _, one=h3._refusal(home,"request:test",None,None,"a"*64,"b"*64,"request_collision",attempt_digest=first,created_at="2026-11-22T00:00:00Z")
            first_bytes=next((home/"context"/"dead-letter").glob("*/*/*.json")).read_bytes()
            _, same=h3._refusal(home,"request:test",None,None,"a"*64,"b"*64,"request_collision",attempt_digest=first,created_at="2026-11-22T00:00:00Z")
            self.assertEqual(first_bytes,next(path for path in (home/"context"/"dead-letter").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["receipt_id"]==one["receipt_id"]).read_bytes())
            _, different_body=h3._refusal(home,"request:test",None,None,"a"*64,"b"*64,"request_collision",attempt_digest=second,created_at="2026-11-22T00:00:00Z")
            _, different_code=h3._refusal(home,"request:test",None,None,"a"*64,"b"*64,"schema_validation",attempt_digest=first,created_at="2026-11-22T00:00:00Z")
            self.assertEqual(one["receipt_id"],same["receipt_id"])
            self.assertNotEqual(one["receipt_id"],different_body["receipt_id"])
            self.assertNotEqual(one["receipt_id"],different_code["receipt_id"])

    def test_refusal_only_trees_reconstruct_for_build_and_candidate_attempts(self) -> None:
        with self.subTest(case="build_refusal_and_retry"), tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); failed=request(); failed["seed_ids"]=["entry:missing"]; source=root/"failed-request.json"; digest=dump(source,failed)
            with self.assertRaises(h3.H3Error): h3.build(source,digest,home)
            before={path:path.read_bytes() for path in (home/"context").rglob("*.json")}; self.assertEqual("ok",h3.verify(home)["integrity"])
            with self.assertRaises(h3.H3Error): h3.build(source,digest,home)
            self.assertEqual(before,{path:path.read_bytes() for path in (home/"context").rglob("*.json")}); self.assertEqual("ok",h3.verify(home)["integrity"])
        with self.subTest(case="candidate_unavailable_packet"), tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); source=root/"candidate.json"; missing={"packet_id":"packet:missing","sources":[{"source_id":"source:h3-missing"}]}; value=self.candidate(missing); digest=dump(source,value)
            with self.assertRaises(h3.H3Error) as caught: h3.append_h3_candidate(source,digest,home)
            self.assertEqual("packet_link_invalid",caught.exception.code); self.assertEqual("ok",h3.verify(home)["integrity"])
            before={path:path.read_bytes() for path in (home/"context").rglob("*.json")}
            with self.assertRaises(h3.H3Error): h3.append_h3_candidate(source,digest,home)
            self.assertEqual(before,{path:path.read_bytes() for path in (home/"context").rglob("*.json")})

    def test_refusal_only_tamper_matrix_refuses(self) -> None:
        for case in ("failure","receipt_id","phase","placement","reference"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); failed=request(); failed["seed_ids"]=["entry:missing"]; source=root/"failed.json"; digest=dump(source,failed)
                with self.assertRaises(h3.H3Error): h3.build(source,digest,home)
                self.assertEqual("ok",h3.verify(home)["integrity"])
                receipt_path=next((home/"context"/"dead-letter").glob("*/*/*.json")); phase=next((home/"context"/"transactions").glob("*/refusal-*.json"))
                if case == "phase":
                    value=json.loads(phase.read_text(encoding="utf-8")); value["failure"]="forged"; phase.write_bytes(h3._canonical(value))
                elif case == "placement":
                    moved=receipt_path.with_name("receipt-h3-moved.json"); receipt_path.rename(moved)
                else:
                    value=json.loads(receipt_path.read_text(encoding="utf-8"))
                    if case == "failure": value["failure"]="forged"
                    elif case == "receipt_id": value["receipt_id"]="receipt:h3-"+"f"*40
                    else: value["artifact_paths"].append(str(next((home/"context"/"requests").glob("*/*/*.json")).relative_to(home)).replace("\\","/"))
                    value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); receipt_path.write_bytes(h3._canonical(value))
                with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
                self.assertEqual("receipt_integrity",caught.exception.code)

    def test_source_read_handles_injected_partial_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"partial.txt").write_text("partial evidence",encoding="utf-8")
            item=resource("resource:partial","partial.txt"); locator, relative=h3._resource_locator(item)  # type: ignore[misc]
            actual=os.read
            with mock.patch("daemon_vessel.h3.os.read",side_effect=lambda fd,count: actual(fd,min(1,count))):
                source, unresolved, _, _=h3._source_read(home,item,locator,relative,99,None)
            self.assertEqual("partial evidence",source["content"]["inline"])
            self.assertEqual([],unresolved)

    @unittest.skipUnless(os.name == "nt", "Windows junction probe")
    def test_windows_ancestor_junction_substitution_refuses_external_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=root/"home"; safe=home/"safe"; outside=root/"outside"; safe.mkdir(parents=True); outside.mkdir(); (safe/"source.txt").write_text("inside",encoding="utf-8"); (outside/"source.txt").write_text("EXTERNAL-SENTINEL",encoding="utf-8")
            item=resource("resource:junction","safe/source.txt"); locator, relative=h3._resource_locator(item)  # type: ignore[misc]
            real_open=os.open; swapped=[False]
            def substitute(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if not swapped[0] and str(path).endswith("source.txt"):
                    swapped[0]=True; (safe/"source.txt").unlink(); safe.rmdir(); subprocess.run(["cmd","/c","mklink","/J",str(safe),str(outside)],check=True,capture_output=True)
                return real_open(path,flags,*args,**kwargs)
            with mock.patch("daemon_vessel.h3.os.open",side_effect=substitute):
                source, unresolved, _, _=h3._source_read(home,item,locator,relative,99,None)
            self.assertIsNone(source); self.assertNotIn("EXTERNAL-SENTINEL",str(unresolved))

    @unittest.skipUnless(os.name == "nt", "Windows handle probe")
    def test_windows_handle_resolution_failure_and_swap_back_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=root/"home"; safe=home/"safe"; saved=home/"saved"; outside=root/"outside"; safe.mkdir(parents=True); outside.mkdir()
            (safe/"source.txt").write_text("inside",encoding="utf-8"); (outside/"source.txt").write_text("EXTERNAL-SENTINEL",encoding="utf-8")
            item=resource("resource:swap","safe/source.txt"); locator, relative=h3._resource_locator(item)  # type: ignore[misc]
            with mock.patch("daemon_vessel.h3._windows_handle_path",side_effect=OSError("unavailable")):
                source, unresolved, _, _=h3._source_read(home,item,locator,relative,99,None)
            self.assertIsNone(source); self.assertIn("known_unreachable",{row["code"] for row in unresolved})
            real_open=os.open; swapped=[False]
            def swap_back(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if not swapped[0] and str(path).endswith("source.txt"):
                    swapped[0]=True; safe.rename(saved); subprocess.run(["cmd","/c","mklink","/J",str(safe),str(outside)],check=True,capture_output=True)
                    fd=real_open(path,flags,*args,**kwargs); safe.rmdir(); saved.rename(safe); return fd
                return real_open(path,flags,*args,**kwargs)
            with mock.patch("daemon_vessel.h3.os.open",side_effect=swap_back):
                source, unresolved, _, _=h3._source_read(home,item,locator,relative,99,None)
            self.assertIsNone(source); self.assertNotIn("EXTERNAL-SENTINEL",str(unresolved))

    @unittest.skipIf(os.name == "nt", "POSIX openat probe")
    def test_posix_ancestor_substitution_refuses_external_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=root/"home"; safe=home/"safe"; outside=root/"outside"; safe.mkdir(parents=True); outside.mkdir()
            (safe/"source.txt").write_text("inside",encoding="utf-8"); (outside/"source.txt").write_text("EXTERNAL-SENTINEL",encoding="utf-8")
            item=resource("resource:posix-race","safe/source.txt"); locator, relative=h3._resource_locator(item)  # type: ignore[misc]
            real_open=os.open; swapped=[False]
            def substitute(component: object, flags: int, *args: object, **kwargs: object) -> int:
                if not swapped[0] and component == "safe":
                    swapped[0]=True; safe.rename(home/"saved"); safe.symlink_to(outside, target_is_directory=True)
                return real_open(component,flags,*args,**kwargs)
            with mock.patch("daemon_vessel.h3.os.open",side_effect=substitute):
                source, unresolved, _, _=h3._source_read(home,item,locator,relative,99,None)
            self.assertIsNone(source); self.assertIn("known_unreachable",{row["code"] for row in unresolved}); self.assertNotIn("EXTERNAL-SENTINEL",str((source,unresolved)))

    def test_source_growth_during_read_refuses_bounded_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); path=home/"grow.txt"; path.write_text("seed",encoding="utf-8"); item=resource("resource:grow","grow.txt"); locator, relative=h3._resource_locator(item)  # type: ignore[misc]
            actual=os.read; grew=[False]
            def grow(fd: int, count: int) -> bytes:
                value=actual(fd,count)
                if not grew[0]: grew[0]=True; path.write_bytes(b"x"*(h3.MAX_SOURCE_BYTES+1))
                return value
            with mock.patch("daemon_vessel.h3.os.read",side_effect=grow): source, unresolved, _, _=h3._source_read(home,item,locator,relative,99,None)
            self.assertIsNone(source); self.assertIn("known_unreachable",{row["code"] for row in unresolved})

    def test_final_component_link_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); target=home/"target.txt"; target.write_text("sentinel",encoding="utf-8"); link=home/"link.txt"
            try: link.symlink_to(target)
            except OSError: self.skipTest("symlink unavailable")
            item=resource("resource:link-final","link.txt"); locator, relative=h3._resource_locator(item)  # type: ignore[misc]
            source, unresolved, _, _=h3._source_read(home,item,locator,relative,99,None)
            self.assertIsNone(source); self.assertNotIn("sentinel",str(unresolved))

    def test_unsafe_locator_and_digest_refuse_without_content_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); (home/"registry"/"resources.json").write_text("PRIVATE-SENTINEL",encoding="utf-8")
            rp=root/"request.json"; digest=dump(rp,request())
            with self.assertRaises(h3.H3Error) as caught: h3.build(rp,digest,home)
            self.assertNotIn("PRIVATE-SENTINEL",str(caught.exception))

    def test_verifier_refuses_missing_moved_truncated_duplicate_and_phase_artifacts(self) -> None:
        def prepared(root: Path) -> tuple[Path, Path]:
            home=self.home(root); rp=root/"request.json"; return home, rp
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home,rp=prepared(root); h3.build(rp,dump(rp,request()),home)
            packet=next((home/"context"/"packets").glob("*/*/*.json")); packet.unlink()
            with self.assertRaises(h3.H3Error): h3.verify(home)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home,rp=prepared(root); h3.build(rp,dump(rp,request()),home)
            marker=next((home/"context"/"transactions").glob("*/construction.json")); marker.unlink()
            with self.assertRaises(h3.H3Error): h3.verify(home)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home,rp=prepared(root); h3.build(rp,dump(rp,request()),home)
            packet=next((home/"context"/"packets").glob("*/*/*.json")); duplicate=packet.with_name("duplicate.json"); shutil.copyfile(packet,duplicate)
            with self.assertRaises(h3.H3Error): h3.verify(home)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home,rp=prepared(root); h3.build(rp,dump(rp,request()),home)
            packet=next((home/"context"/"packets").glob("*/*/*.json")); packet.write_bytes(b"{")
            with self.assertRaises(h3.H3Error): h3.verify(home)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home,rp=prepared(root); h3.build(rp,dump(rp,request()),home)
            extra=home/"context"/"packets"/"2026"/"11"/"nested"; extra.mkdir(); (extra/"extra.txt").write_text("x",encoding="utf-8")
            with self.assertRaises(h3.H3Error): h3.verify(home)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home,rp=prepared(root); h3.build(rp,dump(rp,request()),home)
            packet=next((home/"context"/"packets").glob("*/*/*.json")); value=json.loads(packet.read_text(encoding="utf-8")); value["request"]["task"]="mismatch"; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); packet.write_bytes(h3._canonical(value))
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_installed_input_tampering_refuses_before_hydration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); (home/"index"/"access-index.json").write_text("{}",encoding="utf-8")
            rp=root/"request.json"
            with self.assertRaises(h3.H3Error): h3.build(rp,dump(rp,request()),home)

    def test_deterministic_seed_authority_fallback_and_cap_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"good.txt").write_text("good",encoding="utf-8"); (home/"derived.txt").write_text("wrong",encoding="utf-8")
            item=resource("resource:one", "good.txt")
            item["locators"].append({"surface":"external-home","uri":"derived.txt","authority":"derived","access_mode":"local","status":"active"})
            index={"entries":[entry("entry:one",["resource:one"],fallback=[{"entry_id":"entry:two"}]),entry("entry:two",[])]}
            first=h3._hydrate(request(),home,index,{"resources":[item]}); second=h3._hydrate(request(),home,index,{"resources":[item]})
            self.assertEqual(first,second); self.assertEqual("good",first[0][0]["content"]["inline"]); self.assertEqual(["entry:one"],first[1])
            limited=request(); limited["budgets"]["max_hops"]=0; self.assertEqual(["entry:one"],h3._hydrate(limited,home,index,{"resources":[item]})[1])

    def test_namespace_privacy_refuse_before_local_source_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"private.txt").write_text("PRIVATE-SENTINEL",encoding="utf-8")
            item=resource("resource:one", "private.txt")
            result=h3._hydrate(request(),home,{"entries":[entry("entry:one",["resource:one"],namespaces=["julie"])]},{"resources":[item]})
            self.assertEqual([],result[0]); self.assertEqual("namespace_refused",result[4][0]["code"]); self.assertNotIn("PRIVATE-SENTINEL",str(result))

    def test_locator_refusals_are_sanitized(self) -> None:
        for locator in ("C:/secret", "\\\\server\\share", "../escape", "/absolute", "file:///secret", "a?token=value", "a#signature"):
            with self.subTest(locator=locator):
                with self.assertRaises(h3.H3Error) as caught: h3._safe_relative(locator)
                self.assertNotIn(locator,str(caught.exception))

    def test_source_fidelity_hash_utf8_empty_and_unavailable_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"utf8.txt").write_text("exact",encoding="utf-8"); (home/"binary.bin").write_bytes(b"\xff"); (home/"empty.txt").write_bytes(b"")
            items=[resource("resource:exact","utf8.txt",content_hash=hashlib.sha256(b"exact").hexdigest()),resource("resource:unverified","utf8.txt"),resource("resource:bad","utf8.txt",content_hash="0"*64),resource("resource:binary","binary.bin"),resource("resource:empty","empty.txt")]
            index={"entries":[entry("entry:one",[item["id"] for item in items])]} ; sources,_,_,_,unresolved,_=h3._hydrate(request(),home,index,{"resources":items})
            self.assertEqual(["exact_bytes_verified","exact_bytes_unverified"],[row["fidelity"] for row in sources]); self.assertEqual({"digest_mismatch","unsupported_media","known_unreachable"},{row["code"] for row in unresolved})

    def test_h3_live_lock_refuses_and_stale_lock_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; digest=dump(rp,request()); lock=home/"context"/".h3.lock"; lock.parent.mkdir(parents=True,exist_ok=True)
            lock.write_text(json.dumps({"pid":os.getpid(),"created_at":dt.datetime.now(dt.timezone.utc).isoformat(),"token":"live"}),encoding="utf-8")
            with self.assertRaises(h3.crossing.CrossingError): h3.build(rp,digest,home)
            lock.write_text(json.dumps({"pid":99999999,"created_at":(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=1)).isoformat(),"token":"stale"}),encoding="utf-8")
            with mock.patch("daemon_vessel.crossing._pid_alive",return_value=False): self.assertEqual("gltch.context-packet/0.2.0",h3.build(rp,digest,home)["schema"])

    def test_publication_boundaries_recover_deterministically(self) -> None:
        for group in ("requests", "packets", "receipts"):
            with self.subTest(group=group), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; digest=dump(rp,request())
                original=h3._write_immutable; failed=[False]
                def interrupt(path: Path, content: bytes) -> bool:
                    if not failed[0] and group in path.parts:
                        failed[0]=True; raise h3.H3Error("refused: synthetic interruption",code="transaction_write_failed")
                    return original(path,content)
                with mock.patch("daemon_vessel.h3._write_immutable",side_effect=interrupt):
                    with self.assertRaises(h3.H3Error): h3.build(rp,digest,home)
                packet=h3.build(rp,digest,home)
                self.assertEqual("gltch.context-packet/0.2.0",packet["schema"])
                self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_candidate_lineage_and_receipt_tampering_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
            bad=self.candidate(packet); bad["surface_id"]="surface:other"; bad["sha256"]=h3._digest({key:value for key,value in bad.items() if key!="sha256"})
            cp=root/"candidate.json"; digest=dump(cp,bad)
            with self.assertRaises(h3.H3Error): h3.append_h3_candidate(cp,digest,home)
            good=self.candidate(packet); digest=dump(cp,good); h3.append_h3_candidate(cp,digest,home)
            receipt_path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
            receipt=json.loads(receipt_path.read_text(encoding="utf-8")); receipt["packet_id"]="packet:other"; receipt["sha256"]=h3._digest({key:value for key,value in receipt.items() if key!="sha256"}); receipt_path.write_bytes(h3._canonical(receipt))
            with self.assertRaises(h3.H3Error): h3.verify(home)

    def test_legacy_packet_validators_reject_wrong_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
            with self.assertRaises(h3.H3Error): h3._valid(h3.PACKET, {"schema":"gltch.context-packet/0.1.0"}, "wrong packet")
            with self.assertRaises(h3.crossing.CrossingError): h3.crossing.validate_packet(packet)

    def test_actual_historical_packet_boundaries_and_explicit_dispatch(self) -> None:
        daemon_packet={"schema":"gltch.context-packet/0.1.0","packet_id":"packet:legacy","orientation":"legacy","purpose":"legacy","created_at":"2026-11-22T00:00:00Z","sha256":"0"*64,"access_index_nodes":[],"registry_resources":[],"sources":[],"corrections":[],"open_commitments":[],"unresolved":[],"runtime_seam":[],"budgets":{"hops":0,"resources":0,"tokens":0},"omissions":[],"compaction_ledger":[],"mutation_authorized":False,"construction_receipt_id":"receipt:legacy"}
        return_packet={"schema":"gltch.context-packet/0.1.0","id":"legacy","created_at":"2026-11-22T00:00:00Z","query":"q","budget":{"max_hops":0,"max_resources":1},"seeds":[],"resources":[],"traversal":[],"status":"unresolved"}
        self.assertEqual(daemon_packet,h3.validate_packet_version(daemon_packet,"daemon-vessel-0.1"))
        self.assertEqual(return_packet,h3.validate_packet_version(return_packet,"return-point-0.1"))
        for packet, boundary in ((daemon_packet,"return-point-0.1"),(daemon_packet,"h3"),(return_packet,"daemon-vessel-0.1"),(return_packet,"h3")):
            with self.subTest(boundary=boundary), self.assertRaises(h3.H3Error): h3.validate_packet_version(packet,boundary)
        with self.assertRaises(h3.H3Error): h3.validate_packet_version({"schema":"gltch.context-packet/9.9.9"},"h3")

    def test_installed_snapshot_survives_replace_between_acquisition_and_use(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; digest=dump(rp,request())
            original=h3._make_packet
            def replace_after_snapshot(*args: object) -> dict:
                (home/"index"/"access-index.json").write_bytes(b"{}")
                return original(*args)
            with mock.patch("daemon_vessel.h3._make_packet",side_effect=replace_after_snapshot):
                packet=h3.build(rp,digest,home)
            self.assertEqual("gltch.context-packet/0.2.0",packet["schema"])
            receipts=[json.loads(path.read_text(encoding="utf-8")) for path in (home/"context"/"receipts").glob("*/*/*.json")]
            self.assertTrue(receipts[0]["access_index_sha256"] != hashlib.sha256(b"{}").hexdigest())

    def test_unknown_seed_active_entry_typed_edge_and_budget_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"one.txt").write_text("one two",encoding="utf-8")
            catalog={"resources":[resource(f"resource:{n}","one.txt") for n in range(13)]}
            active=entry("entry:one",[f"resource:{n}" for n in range(13)],links={"relates_to":["entry:two"],"first_appears_in":["entry:three"]})
            index={"entries":[active,entry("entry:two",[]),entry("entry:three",[]),entry("entry:inactive",["resource:0"],status="archived")]}
            with self.assertRaises(h3.H3Error): h3._hydrate({**request(),"seed_ids":["entry:missing"]},home,index,catalog)
            result=h3._hydrate(request(),home,index,catalog)
            self.assertEqual(12,len(result[2])); self.assertIn("budget_exhausted",{x["code"] for x in result[4]})
            self.assertTrue(all(row["edge"] != "first_appears_in" for row in result[3]))
            inactive=h3._hydrate({**request(),"seed_ids":["entry:inactive"]},home,index,catalog)
            self.assertEqual([],inactive[1]); self.assertEqual("access_not_available",inactive[4][0]["code"])

    def test_bounded_extraction_token_json_and_invalid_json_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"long.txt").write_text("alpha beta gamma delta",encoding="utf-8"); (home/"good.json").write_text('{"a":1}',encoding="utf-8"); (home/"bad.json").write_text("{",encoding="utf-8")
            values=[resource("resource:long","long.txt"),resource("resource:json","good.json"),resource("resource:bad-json","bad.json")]
            rq={**request(),"budgets":{**request()["budgets"],"max_chars":7,"max_tokens":2}}
            result=h3._hydrate(rq,home,{"entries":[entry("entry:one",[v["id"] for v in values])]}, {"resources":values})
            self.assertEqual(7,result[0][0]["content"]["chars"]); self.assertEqual("extract",result[5][0]["operation"])
            self.assertIn("budget_exhausted",{row["code"] for row in result[4]})

    def test_candidate_receipt_and_phase_recover_with_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
            cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
            candidate_path=next((home/"context"/"candidates").glob("*/*/*.json")); original=candidate_path.read_bytes()
            receipt_path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
            receipt_path.unlink(); next((home/"context"/"transactions").glob("*/candidate-*.json")).unlink()
            h3.append_h3_candidate(cp,digest,home)
            self.assertEqual(original,candidate_path.read_bytes()); self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_candidate_receipt_missing_phase_repairs_only_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
            cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
            receipt=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
            receipt_bytes=receipt.read_bytes(); phase=next((home/"context"/"transactions").glob("*/candidate-*.json")); phase.unlink()
            h3.append_h3_candidate(cp,digest,home)
            self.assertEqual(receipt_bytes,receipt.read_bytes())
            self.assertTrue(phase.exists()); self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_recovery_exception_wrong_candidate_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; h3.append_h3_candidate(cp,dump(cp,self.candidate(packet)),home)
            next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate").unlink(); next((home/"context"/"transactions").glob("*/candidate-*.json")).unlink()
            with self.assertRaises(h3.H3Error) as caught: h3._verify_under_lock(home,recovery_candidate_id="candidate:other")
            self.assertEqual("receipt_integrity",caught.exception.code)

    def test_phase_only_candidate_boundary_is_impossible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home); cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
            next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate").unlink()
            with self.assertRaises(h3.H3Error) as caught: h3.append_h3_candidate(cp,digest,home)
            self.assertEqual("receipt_integrity",caught.exception.code)

    def test_candidate_recovery_boundaries_are_exact_and_byte_preserving(self) -> None:
        """Only candidate_only and receipt_only are recoverable publication states."""
        for boundary in ("candidate_only", "receipt_only", "complete"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
                cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
                candidate_path=next((home/"context"/"candidates").glob("*/*/*.json")); candidate_bytes=candidate_path.read_bytes()
                receipt=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
                phase=next((home/"context"/"transactions").glob("*/candidate-*.json")); receipt_bytes=receipt.read_bytes()
                if boundary == "candidate_only":
                    receipt.unlink(); phase.unlink()
                elif boundary == "receipt_only":
                    phase.unlink()
                candidate_id="candidate:test"
                for wrong in {"candidate_only", "receipt_only", "complete", "phase_only"} - {boundary}:
                    with self.assertRaises(h3.H3Error) as caught:
                        h3._verify_under_lock(home,recovery_candidate_id=candidate_id,recovery_boundary=wrong)
                    self.assertEqual("receipt_integrity",caught.exception.code)
                h3.append_h3_candidate(cp,digest,home)
                self.assertEqual(candidate_bytes,candidate_path.read_bytes())
                if boundary != "candidate_only": self.assertEqual(receipt_bytes,receipt.read_bytes())
                self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_unrelated_incomplete_candidate_blocks_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
            first=root/"candidate-one.json"; first_value=self.candidate(packet); first_digest=dump(first,first_value); h3.append_h3_candidate(first,first_digest,home)
            second_value=self.candidate(packet); second_value["candidate_id"]="candidate:other"; second_value["proposals"][0]["proposal_id"]="proposal:other"; second_value["sha256"]=h3._digest({key:value for key,value in second_value.items() if key!="sha256"})
            second=root/"candidate-two.json"; second_digest=dump(second,second_value); h3.append_h3_candidate(second,second_digest,home)
            next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8")).get("candidate_id")=="candidate:test").unlink()
            next((home/"context"/"transactions").glob("*/candidate-candidate-test.json")).unlink()
            with self.assertRaises(h3.H3Error) as caught: h3.append_h3_candidate(second,second_digest,home)
            self.assertEqual("receipt_integrity",caught.exception.code)

    def test_recovery_refuses_damaged_durable_lineage_at_each_boundary(self) -> None:
        for boundary in ("candidate_only", "receipt_only"):
            for damaged in ("request", "construction", "packet", "candidate"):
                with self.subTest(boundary=boundary, damaged=damaged), tempfile.TemporaryDirectory() as temp:
                    root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
                    cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
                    receipt=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="candidate")
                    phase=next((home/"context"/"transactions").glob("*/candidate-*.json"))
                    if boundary == "candidate_only": receipt.unlink(); phase.unlink()
                    else: phase.unlink()
                    if damaged == "request":
                        path=next((home/"context"/"requests").glob("*/*/*.json")); value=json.loads(path.read_text(encoding="utf-8")); value["task"]="forged request"; path.write_bytes(h3._canonical(value))
                    elif damaged == "construction":
                        path=next(path for path in (home/"context"/"receipts").glob("*/*/*.json") if json.loads(path.read_text(encoding="utf-8"))["kind"]=="construction"); value=json.loads(path.read_text(encoding="utf-8")); value["packet_id"]="packet:forged"; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
                    elif damaged == "packet":
                        path=next((home/"context"/"packets").glob("*/*/*.json")); value=json.loads(path.read_text(encoding="utf-8")); value["request"]["task"]="forged packet"; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
                    else:
                        path=next((home/"context"/"candidates").glob("*/*/*.json")); value=json.loads(path.read_text(encoding="utf-8")); value["proposals"][0]["statement"]="forged candidate"; value["sha256"]=h3._digest({key:item for key,item in value.items() if key!="sha256"}); path.write_bytes(h3._canonical(value))
                    with self.assertRaises(h3.H3Error) as caught: h3.append_h3_candidate(cp,digest,home)
                    self.assertIn(caught.exception.code,{"receipt_integrity","immutable_collision"})

    def test_recovery_exception_rejects_complete_absent_and_wrong_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=self.home(root); rp=root/"request.json"; packet=h3.build(rp,dump(rp,request()),home)
            cp=root/"candidate.json"; digest=dump(cp,self.candidate(packet)); h3.append_h3_candidate(cp,digest,home)
            for candidate_id, boundary in (("candidate:test","complete"),("candidate:missing","candidate_only")):
                with self.subTest(candidate_id=candidate_id, boundary=boundary), self.assertRaises(h3.H3Error) as caught:
                    h3._verify_under_lock(home,recovery_candidate_id=candidate_id,recovery_boundary=boundary)
                self.assertEqual("receipt_integrity",caught.exception.code)
            phase=next((home/"context"/"transactions").glob("*/candidate-*.json")); phase.unlink()
            with self.assertRaises(h3.H3Error) as caught:
                h3._verify_under_lock(home,recovery_candidate_id="candidate:test",recovery_boundary="candidate_only")
            self.assertEqual("receipt_integrity",caught.exception.code)

    def test_refusal_publication_boundaries_recover_with_identical_bytes(self) -> None:
        for boundary in ("dead-letter", "transactions"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home)
                _,_,index_sha,registry_sha=h3._installed(home)
                original=h3._write_immutable; interrupted=[False]
                def stop(path: Path, content: bytes) -> bool:
                    if not interrupted[0] and boundary in path.parts:
                        interrupted[0]=True; raise h3.H3Error("refused: synthetic interruption",code="transaction_write_failed")
                    return original(path,content)
                with mock.patch("daemon_vessel.h3._write_immutable",side_effect=stop):
                    with self.assertRaises(h3.H3Error): h3._refusal(home,"request:test",None,None,index_sha,registry_sha,"request_collision",attempt_digest="1"*64,created_at="2026-11-22T00:00:00Z")
                before={path:path.read_bytes() for path in (home/"context"/"dead-letter").rglob("*.json")} if (home/"context"/"dead-letter").exists() else {}
                h3._refusal(home,"request:test",None,None,index_sha,registry_sha,"request_collision",attempt_digest="1"*64,created_at="2026-11-22T00:00:00Z")
                for path, raw in before.items(): self.assertEqual(raw,path.read_bytes())
                self.assertEqual("ok",h3.verify(home)["integrity"])

    def test_reconstructive_accounting_mutations_refuse_after_redigest(self) -> None:
        for case in ("hops", "resources", "tokens", "source_hash", "omissions", "ledger"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); home=self.home(root); rp=root/"request.json"; h3.build(rp,dump(rp,request()),home); self.assertEqual("ok",h3.verify(home)["integrity"])
                path=next((home/"context"/"packets").glob("*/*/*.json")); packet=json.loads(path.read_text(encoding="utf-8"))
                if case == "hops":
                    packet["budgets"]["used_hops"]=4
                elif case == "resources":
                    packet["registry_resources"]=[]; packet["budgets"]["used_resources"]=0
                elif case == "tokens":
                    text="one two three four"; packet["sources"][0]["content"]["inline"]=text; packet["sources"][0]["content"]["chars"]=len(text); packet["sources"][0]["content"]["sha256"]=hashlib.sha256(text.encode("utf-8")).hexdigest(); packet["budgets"]["used_chars"]=len(text)
                elif case == "source_hash":
                    packet["sources"][0]["content"]["sha256"]="0"*64
                elif case == "omissions":
                    row={"code":"budget_exhausted","detail":"forged omission","source_refs":["resource:one"]}; packet["unresolved"].append(row); packet["omissions"].append({"kind":"source","reason":row["detail"],"source_refs":row["source_refs"]}); packet["status"]="partial"
                else:
                    packet["compaction_ledger"][0]["fidelity_loss"]="forged ledger accounting"
                packet["sha256"]=h3._digest({key:value for key,value in packet.items() if key!="sha256"}); path.write_bytes(h3._canonical(packet))
                with self.assertRaises(h3.H3Error) as caught: h3.verify(home)
                self.assertEqual("receipt_integrity",caught.exception.code)

    def test_unavailable_nonlocal_and_symlink_sources_remain_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); (home/"target.txt").write_text("PRIVATE-SENTINEL",encoding="utf-8")
            resources=[resource("resource:manual","target.txt",mode="manual")]
            expected={"access_not_available"}
            try:
                (home/"link.txt").symlink_to(home/"target.txt")
                resources.append(resource("resource:link","link.txt"))
                expected.add("known_unreachable")
            except OSError:
                pass
            index={"entries":[entry("entry:one",[resource["id"] for resource in resources])]}
            sources,_,_,_,unresolved,_=h3._hydrate(request(),home,index,{"resources":resources})
            self.assertEqual([],sources); self.assertEqual(expected,{row["code"] for row in unresolved}); self.assertNotIn("PRIVATE-SENTINEL",str(unresolved))


if __name__ == "__main__": unittest.main()
